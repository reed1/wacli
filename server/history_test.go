package main

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"
)

func editAt(eventID, targetID, newText string, at int64) *events.Message {
	evt := editEvent(targetID, newText)
	evt.Info.ID = eventID
	evt.Info.Timestamp = time.Unix(at, 0)
	return evt
}

func revokeAt(eventID, targetID string, at int64) *events.Message {
	evt := revokeEvent(targetID)
	evt.Info.ID = eventID
	evt.Info.Timestamp = time.Unix(at, 0)
	evt.Info.PushName = "Sender 111"
	return evt
}

func changesSince(t *testing.T, a *testApp, since int64) *ChangesData {
	t.Helper()
	data, err := a.changes(time.Unix(since, 0))
	if err != nil {
		t.Fatalf("changes: %v", err)
	}
	return data
}

func TestChangesJoinsEditsAndDeletionToTheLoggedOriginal(t *testing.T) {
	a := newTestApp(t)
	a.attachEventLog(t)

	a.handleEvent(incomingText("edited", "111", "first version"))
	a.handleEvent(incomingText("deleted", "111", "regret this"))
	a.handleEvent(incomingText("untouched", "111", "nothing happens to me"))
	a.handleEvent(editAt("e2", "edited", "third version", 1700000300))
	a.handleEvent(editAt("e1", "edited", "second version", 1700000200))
	a.handleEvent(revokeAt("r1", "deleted", 1700000100))

	data := changesSince(t, a, 0)
	if len(data.Changes) != 2 {
		t.Fatalf("changes = %+v, want the edited and the deleted message", data.Changes)
	}

	deleted, edited := data.Changes[0], data.Changes[1]
	if deleted.MessageID != "deleted" || edited.MessageID != "edited" {
		t.Fatalf("order = %s, %s; want oldest change first", deleted.MessageID, edited.MessageID)
	}

	if deleted.Source != "log" || deleted.Text != "regret this" || deleted.SenderName != "Sender 111" {
		t.Errorf("deleted original = %+v", deleted)
	}
	if deleted.Deletion == nil || deleted.Deletion.Timestamp != 1700000100 || deleted.Deletion.ByName != "Sender 111" {
		t.Errorf("deletion = %+v", deleted.Deletion)
	}
	if deleted.Decision != "stored" {
		t.Errorf("decision = %q, want the original's verdict", deleted.Decision)
	}

	if edited.Text != "first version" || edited.Deletion != nil {
		t.Errorf("edited original = %+v", edited)
	}
	if len(edited.Edits) != 2 || edited.Edits[0].Text != "second version" || edited.Edits[1].Text != "third version" {
		t.Errorf("edits = %+v, want both in time order", edited.Edits)
	}
	if data.LogStarts == 0 {
		t.Error("log_starts should name the oldest record")
	}
}

func TestChangesOnlyIncludesChangesInsideTheWindow(t *testing.T) {
	a := newTestApp(t)
	a.attachEventLog(t)

	a.handleEvent(incomingText("old-change", "111", "a"))
	a.handleEvent(incomingText("new-change", "111", "b"))
	a.handleEvent(revokeAt("r1", "old-change", 1700000100))
	a.handleEvent(editAt("e1", "new-change", "b2", 1700000900))

	data := changesSince(t, a, 1700000500)
	if len(data.Changes) != 1 || data.Changes[0].MessageID != "new-change" {
		t.Fatalf("changes = %+v, want only the change after since", data.Changes)
	}
	if data.Changes[0].Timestamp != 1700000000 {
		t.Errorf("an original older than the window still comes with its change")
	}
}

func TestChangesFallsBackToTheStoreForAnOriginalOlderThanTheLog(t *testing.T) {
	a := newTestApp(t)
	// Stored before the log existed: only the edit is in the log.
	a.handleMessage(incomingText("aged", "111", "as first sent"))
	a.attachEventLog(t)

	a.handleEvent(editAt("e1", "aged", "as corrected", 1700000200))

	data := changesSince(t, a, 0)
	if len(data.Changes) != 1 {
		t.Fatalf("changes = %+v", data.Changes)
	}
	change := data.Changes[0]
	if change.Source != "store" || change.Text != "as first sent" || change.ChatName != "111" {
		t.Errorf("change = %+v, want the pre-edit text from the store", change)
	}
}

func TestChangesNamesTheAuthorOfAMessageNothingRemembers(t *testing.T) {
	a := newTestApp(t)
	a.attachEventLog(t)

	group := types.JID{User: "120363000000000001", Server: types.GroupServer}
	admin := types.JID{User: "222", Server: types.HiddenUserServer, Device: 3}
	author := "333@lid"
	a.contacts.contacts[types.JID{User: "333", Server: types.HiddenUserServer}] = types.ContactInfo{Found: true, PushName: "The Author"}

	revoke := revokeAt("r1", "forgotten", 1700000100)
	revoke.Info.Chat = group
	revoke.Info.Sender = admin
	revoke.Info.IsGroup = true
	revoke.Info.PushName = "The Admin"
	revoke.Message.ProtocolMessage.Key.Participant = proto.String(author)
	a.handleEvent(revoke)

	data := changesSince(t, a, 0)
	if len(data.Changes) != 1 {
		t.Fatalf("changes = %+v", data.Changes)
	}
	change := data.Changes[0]
	if change.Source != "missing" || change.ChatJID != group.String() || !change.IsGroup {
		t.Errorf("change = %+v, want the chat from the revoke", change)
	}
	if change.SenderJID != author || change.SenderName != "The Author" {
		t.Errorf("author = %s %q, want the participant the revoke names", change.SenderJID, change.SenderName)
	}
	if change.Deletion.ByName != "The Admin" {
		t.Errorf("deleted by = %q, want the admin", change.Deletion.ByName)
	}
}

func TestChangesSkipsStatusDeletions(t *testing.T) {
	a := newTestApp(t)
	a.attachEventLog(t)

	revoke := revokeAt("r1", "my-status", 1700000100)
	revoke.Info.Chat = types.StatusBroadcastJID
	a.handleEvent(revoke)

	if data := changesSince(t, a, 0); len(data.Changes) != 0 {
		t.Errorf("changes = %+v, want status deletions left out", data.Changes)
	}
}

func TestChangesKeepsTheContentVerdictOverTheSenderKeyCopy(t *testing.T) {
	a := newTestApp(t)
	a.attachEventLog(t)

	a.handleEvent(incomingText("dual", "111", "real content"))
	keyCopy := incomingText("dual", "111", "")
	keyCopy.Message = &waE2E.Message{
		SenderKeyDistributionMessage: &waE2E.SenderKeyDistributionMessage{GroupID: proto.String("g")},
	}
	a.handleEvent(keyCopy)
	a.handleEvent(revokeAt("r1", "dual", 1700000100))

	change := changesSince(t, a, 0).Changes[0]
	if change.Decision != "stored" || change.Text != "real content" {
		t.Errorf("change = %+v, want the content copy and its verdict", change)
	}
}

func TestParseChangeLogReadsPayloadsWithOneofFields(t *testing.T) {
	// encoding/json writes a oneof as a nested Go field name, which it cannot
	// read back; this shape came from a status reshare in the live log.
	line := `{"time":"2026-09-25T00:10:17Z","kind":"event","event":"*events.Message","data":{"Info":{"Chat":"111@s.whatsapp.net","Sender":"111@s.whatsapp.net","ID":"vid","Timestamp":"2026-09-25T00:10:17Z"},"Message":{"videoMessage":{"caption":"look","contextInfo":{"statusAttributions":[{"AttributionData":{"ExternalShare":{"source":1}}}]}}}}}`

	cl, err := parseChangeLog([][]byte{[]byte(line + "\n")})
	if err != nil {
		t.Fatalf("parseChangeLog: %v", err)
	}
	original := cl.originals["vid"]
	if original == nil || original.Message.GetVideoMessage().GetCaption() != "look" {
		t.Errorf("original = %+v, want the video and its caption", original)
	}
}

func TestParseChangeLogRejectsAnUnknownKind(t *testing.T) {
	line := `{"time":"2026-09-25T00:10:17Z","kind":"mystery","data":{}}`
	if _, err := parseChangeLog([][]byte{[]byte(line)}); err == nil || !strings.Contains(err.Error(), "mystery") {
		t.Errorf("err = %v, want the unknown kind named", err)
	}
}

func TestSendChangesWritesOneChangesEvent(t *testing.T) {
	a := newTestApp(t)
	a.attachEventLog(t)
	state, lines := a.attachConn(t)

	a.handleEvent(incomingText("m1", "111", "x"))
	a.handleEvent(revokeAt("r1", "m1", 1700000100))

	if err := a.sendChanges(state, 0); err != nil {
		t.Fatalf("sendChanges: %v", err)
	}

	var event struct {
		Type string      `json:"type"`
		Data ChangesData `json:"data"`
	}
	for {
		line := recvLine(t, lines)
		if err := json.Unmarshal([]byte(line), &event); err != nil {
			t.Fatalf("unmarshal %q: %v", line, err)
		}
		// handleEvent broadcasts the message itself to every client first.
		if event.Type == "changes" {
			break
		}
	}
	if len(event.Data.Changes) != 1 || event.Data.Changes[0].MessageID != "m1" {
		t.Errorf("changes event = %+v", event.Data)
	}
}
