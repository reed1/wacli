package main

import (
	"encoding/json"
	"fmt"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waCommon"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"
)

func incomingText(id, chatUser, text string) *events.Message {
	return &events.Message{
		Info: types.MessageInfo{
			MessageSource: types.MessageSource{
				Chat:   types.JID{User: chatUser, Server: types.DefaultUserServer},
				Sender: types.JID{User: chatUser, Server: types.DefaultUserServer},
			},
			ID:        id,
			PushName:  "Sender " + chatUser,
			Timestamp: time.Unix(1700000000, 0),
		},
		Message: &waE2E.Message{Conversation: proto.String(text)},
	}
}

func decodeEvent(t *testing.T, line string) (string, Message) {
	t.Helper()
	var event struct {
		Type string  `json:"type"`
		Data Message `json:"data"`
	}
	if err := json.Unmarshal([]byte(line), &event); err != nil {
		t.Fatalf("unmarshal broadcast %q: %v", line, err)
	}
	return event.Type, event.Data
}

func TestHandleMessageSavesAndBroadcasts(t *testing.T) {
	a := newTestApp(t)
	_, lines := a.attachConn(t)

	a.handleMessage(incomingText("msg1", "111", "hello there"))

	eventType, data := decodeEvent(t, recvLine(t, lines))
	if eventType != "message" {
		t.Errorf("event type = %q, want message", eventType)
	}
	if data.Text != "hello there" || data.MessageID != "msg1" {
		t.Errorf("broadcast payload = %+v", data)
	}
	if data.SenderName != "Sender 111" {
		t.Errorf("sender name = %q, want push name fallback", data.SenderName)
	}
	if data.ChatName != "111" {
		t.Errorf("chat name = %q, want JID user fallback", data.ChatName)
	}
	if data.IsFromMe || data.IsMuted || data.IsGroup || data.IsDeleted {
		t.Errorf("unexpected flags in %+v", data)
	}

	var text string
	if err := a.msgDB.QueryRow("SELECT text FROM messages WHERE message_id = ?", "msg1").Scan(&text); err != nil {
		t.Fatalf("saved row: %v", err)
	}
	if text != "hello there" {
		t.Errorf("saved text = %q", text)
	}
}

func TestHandleMessageDropsMutedChat(t *testing.T) {
	a := newTestApp(t)
	_, lines := a.attachConn(t)

	chat := types.JID{User: "111", Server: types.DefaultUserServer}
	a.chatSettings.settings[chat] = types.LocalChatSettings{Found: true, MutedUntil: store.MutedForever}

	a.handleMessage(incomingText("muted1", "111", "you should not see this"))

	expectNoLine(t, lines)
	if got := countMessages(t, a); got != 0 {
		t.Errorf("muted message was saved, count = %d", got)
	}
}

func TestHandleMessageKeepsMutedWhenMentioned(t *testing.T) {
	a := newTestApp(t)
	_, lines := a.attachConn(t)

	chat := types.JID{User: "111", Server: types.DefaultUserServer}
	a.chatSettings.settings[chat] = types.LocalChatSettings{Found: true, MutedUntil: store.MutedForever}

	evt := incomingText("mention1", "111", "hey @15550001111")
	evt.Message = textWithMentions("hey @15550001111", a.myJID.ToNonAD().String())
	a.handleMessage(evt)

	eventType, data := decodeEvent(t, recvLine(t, lines))
	if eventType != "message" {
		t.Fatalf("event type = %q, want message", eventType)
	}
	if !data.IsMuted {
		t.Error("is_muted flag should be set")
	}
}

func TestHandleMessageDropsBroadcastStatus(t *testing.T) {
	a := newTestApp(t)
	_, lines := a.attachConn(t)

	evt := incomingText("status1", "111", "status update")
	evt.Info.Chat = types.JID{User: "status", Server: types.BroadcastServer}
	a.handleMessage(evt)

	expectNoLine(t, lines)
	if got := countMessages(t, a); got != 0 {
		t.Errorf("status message was saved, count = %d", got)
	}
}

func TestHandleMessageDropsSenderKeyDistributionCopy(t *testing.T) {
	a := newTestApp(t)
	_, lines := a.attachConn(t)

	evt := incomingText("grp1", "111", "")
	evt.Message = &waE2E.Message{
		SenderKeyDistributionMessage: &waE2E.SenderKeyDistributionMessage{
			GroupID: proto.String("120@g.us"),
		},
		MessageContextInfo: &waE2E.MessageContextInfo{},
	}
	a.handleMessage(evt)

	expectNoLine(t, lines)
	if got := countMessages(t, a); got != 0 {
		t.Errorf("key distribution copy was saved, count = %d", got)
	}

	evt.Message.Conversation = proto.String("real content")
	a.handleMessage(evt)

	_, data := decodeEvent(t, recvLine(t, lines))
	if data.Text != "real content" {
		t.Errorf("text = %q, want the content copy to still be saved", data.Text)
	}
}

func editEvent(targetID, newText string) *events.Message {
	return &events.Message{
		Info: types.MessageInfo{
			MessageSource: types.MessageSource{
				Chat:   types.JID{User: "111", Server: types.DefaultUserServer},
				Sender: types.JID{User: "111", Server: types.DefaultUserServer},
			},
			ID:        "edit-evt",
			Timestamp: time.Unix(1700000100, 0),
		},
		Message: &waE2E.Message{
			ProtocolMessage: &waE2E.ProtocolMessage{
				Type:          waE2E.ProtocolMessage_MESSAGE_EDIT.Enum(),
				Key:           &waCommon.MessageKey{ID: proto.String(targetID)},
				EditedMessage: &waE2E.Message{Conversation: proto.String(newText)},
			},
		},
	}
}

func revokeEvent(targetID string) *events.Message {
	evt := editEvent(targetID, "")
	evt.Message.ProtocolMessage.Type = waE2E.ProtocolMessage_REVOKE.Enum()
	evt.Message.ProtocolMessage.EditedMessage = nil
	return evt
}

func TestHandleEditUpdatesAndBroadcasts(t *testing.T) {
	a := newTestApp(t)

	a.handleMessage(incomingText("orig1", "111", "first version"))
	_, lines := a.attachConn(t)

	a.handleMessage(editEvent("orig1", "second version"))

	eventType, data := decodeEvent(t, recvLine(t, lines))
	if eventType != "message_updated" {
		t.Fatalf("event type = %q, want message_updated", eventType)
	}
	if data.Text != "second version" {
		t.Errorf("text = %q, want edited text", data.Text)
	}
	if data.OriginalText == nil || *data.OriginalText != "first version" {
		t.Errorf("original_text = %v, want first version", data.OriginalText)
	}

	a.handleMessage(editEvent("orig1", "third version"))
	_, data = decodeEvent(t, recvLine(t, lines))
	if data.OriginalText == nil || *data.OriginalText != "first version" {
		t.Errorf("original_text after second edit = %v, must keep first version", data.OriginalText)
	}
	if data.Text != "third version" {
		t.Errorf("text after second edit = %q", data.Text)
	}
}

func TestHandleEditUnknownTargetIsSilent(t *testing.T) {
	a := newTestApp(t)
	_, lines := a.attachConn(t)

	a.handleMessage(editEvent("does-not-exist", "new text"))

	expectNoLine(t, lines)
}

func TestHandleRevokeMarksDeleted(t *testing.T) {
	a := newTestApp(t)

	a.handleMessage(incomingText("gone1", "111", "delete me"))
	_, lines := a.attachConn(t)

	a.handleMessage(revokeEvent("gone1"))

	eventType, data := decodeEvent(t, recvLine(t, lines))
	if eventType != "message_updated" {
		t.Fatalf("event type = %q, want message_updated", eventType)
	}
	if !data.IsDeleted {
		t.Error("is_deleted flag should be set")
	}
	if data.Text != "delete me" {
		t.Errorf("text = %q, original text should be preserved", data.Text)
	}
}

func TestSendEntries(t *testing.T) {
	a := newTestApp(t)

	a.handleMessage(incomingText("e1", "111", "first"))
	a.handleMessage(incomingText("e2", "222", "second"))

	state, lines := a.attachConn(t)
	if err := a.sendEntries(state); err != nil {
		t.Fatalf("sendEntries: %v", err)
	}

	var event struct {
		Type string      `json:"type"`
		Data EntriesData `json:"data"`
	}
	if err := json.Unmarshal([]byte(recvLine(t, lines)), &event); err != nil {
		t.Fatal(err)
	}
	if event.Type != "entries" {
		t.Errorf("event type = %q, want entries", event.Type)
	}
	if len(event.Data.Entries) != 2 {
		t.Fatalf("got %d entries, want 2", len(event.Data.Entries))
	}
	if event.Data.Entries[0].Message.Text != "first" || event.Data.Entries[1].Message.Text != "second" {
		t.Errorf("entries out of order: %+v", event.Data.Entries)
	}
	if event.Data.Entries[0].Kind != "message" {
		t.Errorf("kind = %q, want message", event.Data.Entries[0].Kind)
	}
}

func TestMergeEntriesKeepsNewestAcrossSources(t *testing.T) {
	var messages []Message
	for i := range entriesLimit {
		messages = append(messages, Message{ID: int64(i), Timestamp: int64(1000 + i)})
	}
	calls := []Call{{ID: 1, Timestamp: 10}, {ID: 2, Timestamp: 1000 + entriesLimit}}

	entries := mergeEntries(messages, calls, entriesLimit)

	if len(entries) != entriesLimit {
		t.Fatalf("got %d entries, want %d", len(entries), entriesLimit)
	}
	if entries[len(entries)-1].Kind != "call" || entries[len(entries)-1].Call.ID != 2 {
		t.Errorf("newest entry = %+v, want the recent call", entries[len(entries)-1])
	}
	for _, entry := range entries {
		if entry.Kind == "call" && entry.Call.ID == 1 {
			t.Error("stale call survived the trim")
		}
	}
	for i := 1; i < len(entries); i++ {
		if entries[i-1].Timestamp > entries[i].Timestamp {
			t.Fatalf("entries not sorted ascending at %d", i)
		}
	}
}

func TestSendEntriesTrimsStaleCalls(t *testing.T) {
	a := newTestApp(t)

	for i := range entriesLimit {
		a.handleMessage(incomingText(fmt.Sprintf("m%d", i), "111", "recent"))
	}
	if err := a.saveCall(&Call{Timestamp: 1, CallID: "old", CallerJID: "222@s.whatsapp.net", CallerName: "Bob"}); err != nil {
		t.Fatal(err)
	}

	state, lines := a.attachConn(t)
	if err := a.sendEntries(state); err != nil {
		t.Fatalf("sendEntries: %v", err)
	}

	var event struct {
		Data EntriesData `json:"data"`
	}
	if err := json.Unmarshal([]byte(recvLine(t, lines)), &event); err != nil {
		t.Fatal(err)
	}
	if len(event.Data.Entries) != entriesLimit {
		t.Fatalf("got %d entries, want %d", len(event.Data.Entries), entriesLimit)
	}
	for _, entry := range event.Data.Entries {
		if entry.Kind != "message" {
			t.Fatalf("stale call included in entries: %+v", entry)
		}
	}
}
