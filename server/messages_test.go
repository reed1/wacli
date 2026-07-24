package main

import (
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"
)

func textWithMentions(text string, mentioned ...string) *waE2E.Message {
	return &waE2E.Message{
		ExtendedTextMessage: &waE2E.ExtendedTextMessage{
			Text:        proto.String(text),
			ContextInfo: &waE2E.ContextInfo{MentionedJID: mentioned},
		},
	}
}

func replyTo(text, participant string) *waE2E.Message {
	return &waE2E.Message{
		ExtendedTextMessage: &waE2E.ExtendedTextMessage{
			Text: proto.String(text),
			ContextInfo: &waE2E.ContextInfo{
				StanzaID:    proto.String("orig-id"),
				Participant: proto.String(participant),
			},
		},
	}
}

func TestIsMentioned(t *testing.T) {
	a := newTestApp(t)

	tests := []struct {
		name string
		msg  *waE2E.Message
		want bool
	}{
		{"no context info", &waE2E.Message{Conversation: proto.String("hi")}, false},
		{"other user mentioned", textWithMentions("@999", "999@s.whatsapp.net"), false},
		{"me mentioned by phone JID", textWithMentions("@me", a.myJID.ToNonAD().String()), true},
		{"me mentioned by full JID", textWithMentions("@me", a.myJID.String()), true},
		{"me mentioned by LID", textWithMentions("@me", a.myLID.ToNonAD().String()), true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			evt := &events.Message{Message: tt.msg}
			if got := a.isMentioned(evt); got != tt.want {
				t.Errorf("isMentioned() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestIsReplyToMe(t *testing.T) {
	a := newTestApp(t)

	tests := []struct {
		name string
		msg  *waE2E.Message
		want bool
	}{
		{"no context info", &waE2E.Message{Conversation: proto.String("hi")}, false},
		{"no participant", textWithMentions("hi"), false},
		{"reply to someone else", replyTo("hi", "999@s.whatsapp.net"), false},
		{"reply to my phone JID", replyTo("hi", a.myJID.ToNonAD().String()), true},
		{"reply to my LID", replyTo("hi", a.myLID.ToNonAD().String()), true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			evt := &events.Message{Message: tt.msg}
			if got := a.isReplyToMe(evt); got != tt.want {
				t.Errorf("isReplyToMe() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestIsMutedAndArchived(t *testing.T) {
	a := newTestApp(t)

	mutedForever := types.JID{User: "1", Server: types.DefaultUserServer}
	mutedFuture := types.JID{User: "2", Server: types.DefaultUserServer}
	mutedExpired := types.JID{User: "3", Server: types.DefaultUserServer}
	archived := types.JID{User: "4", Server: types.DefaultUserServer}
	unknown := types.JID{User: "5", Server: types.DefaultUserServer}

	a.chatSettings.settings[mutedForever] = types.LocalChatSettings{Found: true, MutedUntil: store.MutedForever}
	a.chatSettings.settings[mutedFuture] = types.LocalChatSettings{Found: true, MutedUntil: time.Now().Add(time.Hour)}
	a.chatSettings.settings[mutedExpired] = types.LocalChatSettings{Found: true, MutedUntil: time.Now().Add(-time.Hour)}
	a.chatSettings.settings[archived] = types.LocalChatSettings{Found: true, Archived: true}

	if !a.isMuted(mutedForever) {
		t.Error("muted forever should be muted")
	}
	if !a.isMuted(mutedFuture) {
		t.Error("muted until future should be muted")
	}
	if a.isMuted(mutedExpired) {
		t.Error("expired mute should not be muted")
	}
	if a.isMuted(unknown) {
		t.Error("unknown chat should not be muted")
	}
	if !a.isArchived(archived) {
		t.Error("archived chat should be archived")
	}
	if a.isArchived(unknown) {
		t.Error("unknown chat should not be archived")
	}
}

func TestResolveName(t *testing.T) {
	a := newTestApp(t)

	pushJID := types.JID{User: "111", Server: types.DefaultUserServer}
	fullJID := types.JID{User: "222", Server: types.DefaultUserServer}
	a.contacts.contacts[pushJID] = types.ContactInfo{Found: true, PushName: "Pushy"}
	a.contacts.contacts[fullJID] = types.ContactInfo{Found: true, FullName: "Full Name"}

	lidJID := types.JID{User: "333444555", Server: types.HiddenUserServer}
	phoneJID := types.JID{User: "666", Server: types.DefaultUserServer}
	a.contacts.contacts[phoneJID] = types.ContactInfo{Found: true, PushName: "Via LID Map"}
	if _, err := a.waDB.Exec("INSERT INTO whatsmeow_lid_map (lid, pn) VALUES (?, ?)", lidJID.String(), phoneJID.String()); err != nil {
		t.Fatal(err)
	}

	tests := []struct {
		jid  string
		want string
	}{
		{pushJID.String(), "Pushy"},
		{fullJID.String(), "Full Name"},
		{lidJID.String(), "Via LID Map"},
		{"777@s.whatsapp.net", "Unknown Contact"},
		{"not a jid at all !!", "Unknown Contact"},
	}
	for _, tt := range tests {
		if got := a.resolveName(tt.jid); got != tt.want {
			t.Errorf("resolveName(%q) = %q, want %q", tt.jid, got, tt.want)
		}
	}
}

func TestResolveMentions(t *testing.T) {
	a := newTestApp(t)

	aliceJID := types.JID{User: "111", Server: types.DefaultUserServer}
	a.contacts.contacts[aliceJID] = types.ContactInfo{Found: true, PushName: "Alice"}

	msg := textWithMentions("hey @111 and @222", aliceJID.String(), "222@s.whatsapp.net")
	got := a.resolveMentions("hey @111 and @222", msg)
	want := `hey <mention jid="111@s.whatsapp.net" name="Alice"/> and <mention jid="222@s.whatsapp.net" name="Unknown Contact"/>`
	if got != want {
		t.Errorf("resolveMentions() = %q, want %q", got, want)
	}

	plain := &waE2E.Message{Conversation: proto.String("no mentions")}
	if got := a.resolveMentions("no mentions", plain); got != "no mentions" {
		t.Errorf("text without mentions should pass through, got %q", got)
	}
}

func TestSaveMessageTrimsOldEntries(t *testing.T) {
	a := newTestApp(t)

	for i := 0; i <= maxEntries; i++ {
		msg := &Message{
			MessageID: "msg-" + string(rune('A'+i%26)) + "-" + string(rune('0'+i%10)),
			Timestamp: int64(1000 + i),
			ChatJID:   "1@s.whatsapp.net",
			ChatName:  "Chat",
			SenderJID: "1@s.whatsapp.net",
		}
		if err := a.saveMessage(msg); err != nil {
			t.Fatalf("saveMessage #%d: %v", i, err)
		}
	}

	if got := countMessages(t, a); got != trimToCount {
		t.Errorf("message count after trim = %d, want %d", got, trimToCount)
	}

	var minTS int64
	if err := a.msgDB.QueryRow("SELECT MIN(timestamp) FROM messages").Scan(&minTS); err != nil {
		t.Fatal(err)
	}
	wantMin := int64(1000 + maxEntries - trimToCount + 1)
	if minTS != wantMin {
		t.Errorf("oldest surviving timestamp = %d, want %d (newest %d kept)", minTS, wantMin, trimToCount)
	}
}

func TestBuildInsertParams(t *testing.T) {
	media := "pic.jpg"
	msg := &Message{ID: 99, MessageID: "m1", Timestamp: 123, Text: "hi", MediaFile: &media}
	columns, placeholders, values := buildInsertParams(msg)

	if len(columns) != len(placeholders) || len(columns) != len(values) {
		t.Fatalf("mismatched lengths: %d columns, %d placeholders, %d values", len(columns), len(placeholders), len(values))
	}
	for _, col := range columns {
		if col == "id" {
			t.Error("id column must be excluded from insert")
		}
	}
	byCol := make(map[string]interface{}, len(columns))
	for i, col := range columns {
		byCol[col] = values[i]
	}
	if byCol["message_id"] != "m1" || byCol["timestamp"] != int64(123) || byCol["text"] != "hi" {
		t.Errorf("unexpected values: %v", byCol)
	}
	if byCol["media_file"] != &media {
		t.Errorf("media_file pointer not passed through")
	}
}
