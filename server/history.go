package main

import (
	"bufio"
	"bytes"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"time"

	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/encoding/protojson"
)

// A message that was edited or deleted, with every edit and the deletion that
// happened to it. Built from the event log, which kept the original even when
// the chat was muted or the message has since been trimmed from the store.
type Change struct {
	MessageID string `json:"message_id"`
	// Where the original came from: "log", "store", or "missing" when the
	// message predates the log and the store no longer has it.
	Source      string    `json:"source"`
	Timestamp   int64     `json:"timestamp"`
	ChatJID     string    `json:"chat_jid"`
	ChatName    string    `json:"chat_name"`
	IsGroup     bool      `json:"is_group"`
	SenderJID   string    `json:"sender_jid"`
	SenderName  string    `json:"sender_name"`
	IsFromMe    bool      `json:"is_from_me"`
	MessageType string    `json:"message_type"`
	Text        string    `json:"text"`
	Decision    string    `json:"decision"`
	Edits       []Edit    `json:"edits"`
	Deletion    *Deletion `json:"deletion"`
}

type Edit struct {
	Timestamp int64  `json:"timestamp"`
	Text      string `json:"text"`
}

type Deletion struct {
	Timestamp int64  `json:"timestamp"`
	ByJID     string `json:"by_jid"`
	ByName    string `json:"by_name"`
	ByMe      bool   `json:"by_me"`
}

type ChangesData struct {
	// The oldest record the log still holds. A window reaching further back
	// than this is only partly covered.
	LogStarts int64    `json:"log_starts"`
	Changes   []Change `json:"changes"`
	Error     string   `json:"error,omitempty"`
}

// Everything in the event log that bears on edits and deletions, keyed by the
// id of the message each one targets.
type changeLog struct {
	originals map[string]*events.Message
	decisions map[string]string
	edits     map[string][]*events.Message
	revokes   map[string]*events.Message
	oldest    time.Time
}

type logLine struct {
	Time  time.Time       `json:"time"`
	Kind  string          `json:"kind"`
	Event string          `json:"event"`
	Data  json.RawMessage `json:"data"`
}

// The log holds messages as encoding/json wrote them. That cannot be read back
// into protobuf oneof fields, so the payload goes through protojson, which
// skips what it cannot place instead of failing the whole record.
type loggedMessage struct {
	Info    types.MessageInfo
	Message json.RawMessage
}

func parseChangeLog(files [][]byte) (*changeLog, error) {
	cl := &changeLog{
		originals: make(map[string]*events.Message),
		decisions: make(map[string]string),
		edits:     make(map[string][]*events.Message),
		revokes:   make(map[string]*events.Message),
	}
	seenEdits := make(map[string]bool)

	for _, file := range files {
		scanner := bufio.NewScanner(bytes.NewReader(file))
		scanner.Buffer(make([]byte, 0, 64*1024), maxSocketLine)
		for scanner.Scan() {
			var line logLine
			if err := json.Unmarshal(scanner.Bytes(), &line); err != nil {
				return nil, fmt.Errorf("event log line: %w", err)
			}
			if cl.oldest.IsZero() || line.Time.Before(cl.oldest) {
				cl.oldest = line.Time
			}

			switch line.Kind {
			case "verdict":
				var v messageVerdict
				if err := json.Unmarshal(line.Data, &v); err != nil {
					return nil, fmt.Errorf("verdict: %w", err)
				}
				// The sender key copy shares the content copy's id; its verdict
				// says nothing about what became of the message.
				if v.Decision != "dropped: sender key distribution copy" {
					cl.decisions[v.MessageID] = v.Decision
				}
			case "event":
				if line.Event != "*events.Message" || len(line.Data) == 0 {
					continue
				}
				msg, err := decodeLoggedMessage(line.Data)
				if err != nil {
					return nil, err
				}
				cl.add(msg, seenEdits)
			default:
				return nil, fmt.Errorf("unexpected event log kind: %q", line.Kind)
			}
		}
		if err := scanner.Err(); err != nil {
			return nil, err
		}
	}
	return cl, nil
}

func decodeLoggedMessage(data json.RawMessage) (*events.Message, error) {
	var logged loggedMessage
	if err := json.Unmarshal(data, &logged); err != nil {
		return nil, fmt.Errorf("logged message: %w", err)
	}
	payload := &waE2E.Message{}
	if len(logged.Message) > 0 && string(logged.Message) != "null" {
		if err := (protojson.UnmarshalOptions{DiscardUnknown: true}).Unmarshal(logged.Message, payload); err != nil {
			return nil, fmt.Errorf("logged message %s payload: %w", logged.Info.ID, err)
		}
	}
	return &events.Message{Info: logged.Info, Message: payload}, nil
}

func (cl *changeLog) add(msg *events.Message, seenEdits map[string]bool) {
	if msg.Info.Chat.Server == types.BroadcastServer {
		return
	}

	if protoMsg := msg.Message.GetProtocolMessage(); protoMsg != nil {
		targetID := protoMsg.GetKey().GetID()
		if targetID == "" {
			return
		}
		switch protoMsg.GetType() {
		case waE2E.ProtocolMessage_MESSAGE_EDIT:
			if protoMsg.GetEditedMessage() == nil || seenEdits[msg.Info.ID] {
				return
			}
			seenEdits[msg.Info.ID] = true
			cl.edits[targetID] = append(cl.edits[targetID], msg)
		case waE2E.ProtocolMessage_REVOKE:
			if _, ok := cl.revokes[targetID]; !ok {
				cl.revokes[targetID] = msg
			}
		}
		return
	}

	if isSenderKeyDistribution(msg.Message) {
		return
	}
	if _, ok := cl.originals[msg.Info.ID]; !ok {
		cl.originals[msg.Info.ID] = msg
	}
}

// Messages with an edit or deletion at or after since, oldest change first.
func (a *App) changes(since time.Time) (*ChangesData, error) {
	files, err := a.eventLog.snapshot()
	if err != nil {
		return nil, err
	}
	cl, err := parseChangeLog(files)
	if err != nil {
		return nil, err
	}

	targets := make(map[string]bool)
	for id, edits := range cl.edits {
		for _, edit := range edits {
			if !edit.Info.Timestamp.Before(since) {
				targets[id] = true
			}
		}
	}
	for id, revoke := range cl.revokes {
		if !revoke.Info.Timestamp.Before(since) {
			targets[id] = true
		}
	}

	chatNames := make(map[types.JID]string)
	changes := make([]Change, 0, len(targets))
	firstChange := make(map[string]int64, len(targets))
	for id := range targets {
		change, err := a.buildChange(id, cl, chatNames)
		if err != nil {
			return nil, err
		}
		firstChange[id] = earliestChange(change)
		changes = append(changes, change)
	}
	sort.Slice(changes, func(i, j int) bool {
		return firstChange[changes[i].MessageID] < firstChange[changes[j].MessageID]
	})

	data := &ChangesData{Changes: changes}
	if !cl.oldest.IsZero() {
		data.LogStarts = cl.oldest.Unix()
	}
	return data, nil
}

func earliestChange(c Change) int64 {
	earliest := int64(0)
	for _, edit := range c.Edits {
		if earliest == 0 || edit.Timestamp < earliest {
			earliest = edit.Timestamp
		}
	}
	if c.Deletion != nil && (earliest == 0 || c.Deletion.Timestamp < earliest) {
		earliest = c.Deletion.Timestamp
	}
	return earliest
}

func (a *App) buildChange(id string, cl *changeLog, chatNames map[types.JID]string) (Change, error) {
	change := Change{MessageID: id, Decision: cl.decisions[id], Edits: []Edit{}}

	if original, ok := cl.originals[id]; ok {
		change.Source = "log"
		change.Timestamp = original.Info.Timestamp.Unix()
		change.ChatJID = original.Info.Chat.String()
		change.ChatName = a.cachedChatName(original.Info.Chat, chatNames)
		change.IsGroup = original.Info.IsGroup
		change.SenderJID = original.Info.Sender.String()
		change.SenderName = a.getSenderName(original)
		change.IsFromMe = original.Info.IsFromMe
		change.MessageType, change.Text = extractMessage(original.Message)
		change.Text = a.resolveMentions(change.Text, original.Message)
	} else {
		found, err := a.fillFromStore(&change)
		if err != nil {
			return change, err
		}
		if !found {
			a.fillFromChangeEvent(&change, cl, chatNames)
		}
	}

	edits := cl.edits[id]
	sort.Slice(edits, func(i, j int) bool { return edits[i].Info.Timestamp.Before(edits[j].Info.Timestamp) })
	for _, edit := range edits {
		edited := edit.Message.GetProtocolMessage().GetEditedMessage()
		_, text := extractMessage(edited)
		change.Edits = append(change.Edits, Edit{
			Timestamp: edit.Info.Timestamp.Unix(),
			Text:      a.resolveMentions(text, edited),
		})
	}

	if revoke, ok := cl.revokes[id]; ok {
		change.Deletion = &Deletion{
			Timestamp: revoke.Info.Timestamp.Unix(),
			ByJID:     revoke.Info.Sender.String(),
			ByName:    a.getSenderName(revoke),
			ByMe:      revoke.Info.IsFromMe,
		}
	}
	return change, nil
}

// The original predates the log; the store may still hold it, with the text
// as it was before any edit.
func (a *App) fillFromStore(change *Change) (bool, error) {
	var isGroup, isFromMe int
	err := a.msgDB.QueryRow(
		`SELECT timestamp, chat_jid, chat_name, is_group, sender_jid, sender_name, is_from_me, message_type, COALESCE(original_text, text)
		 FROM messages WHERE message_id = ?`,
		change.MessageID,
	).Scan(&change.Timestamp, &change.ChatJID, &change.ChatName, &isGroup, &change.SenderJID, &change.SenderName, &isFromMe, &change.MessageType, &change.Text)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	change.Source = "store"
	change.IsGroup = isGroup != 0
	change.IsFromMe = isFromMe != 0
	return true, nil
}

// Nothing of the original survives, so the chat and the author come from the
// edit or revoke that names it. Only the author can edit; a revoke names the
// author in its key when an admin deleted someone else's message.
func (a *App) fillFromChangeEvent(change *Change, cl *changeLog, chatNames map[types.JID]string) {
	change.Source = "missing"

	var evt *events.Message
	if edits := cl.edits[change.MessageID]; len(edits) > 0 {
		evt = edits[0]
	} else {
		evt = cl.revokes[change.MessageID]
	}
	change.ChatJID = evt.Info.Chat.String()
	change.ChatName = a.cachedChatName(evt.Info.Chat, chatNames)
	change.IsGroup = evt.Info.IsGroup

	if participant := evt.Message.GetProtocolMessage().GetKey().GetParticipant(); participant != "" && participant != evt.Info.Sender.ToNonAD().String() {
		change.SenderJID = participant
		change.SenderName = a.resolveName(participant)
		return
	}
	change.SenderJID = evt.Info.Sender.String()
	change.SenderName = a.getSenderName(evt)
	change.IsFromMe = evt.Info.IsFromMe
}

// A group's name is a network round trip, and one chat usually carries several
// changes.
func (a *App) cachedChatName(chat types.JID, names map[types.JID]string) string {
	if name, ok := names[chat]; ok {
		return name
	}
	name := a.resolveChatName(chat)
	names[chat] = name
	return name
}

func (a *App) sendChanges(state *connState, since int64) error {
	data, err := a.changes(time.Unix(since, 0))
	if err != nil {
		data = &ChangesData{Error: err.Error()}
	}

	event, marshalErr := json.Marshal(SocketEvent{Type: "changes", Data: data})
	if marshalErr != nil {
		return marshalErr
	}
	event = append(event, '\n')
	if writeErr := state.write(event); writeErr != nil {
		return writeErr
	}
	return err
}
