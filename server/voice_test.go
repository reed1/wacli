package main

import "testing"

func TestSaveTranscription(t *testing.T) {
	a := newTestApp(t)

	msg := &Message{
		MessageID:   "voice-1",
		Timestamp:   1700000000,
		ChatJID:     "111@s.whatsapp.net",
		ChatName:    "Alice",
		SenderJID:   "111@s.whatsapp.net",
		SenderName:  "Alice",
		MessageType: "Voice",
	}
	if err := a.saveMessage(msg); err != nil {
		t.Fatalf("saveMessage: %v", err)
	}

	a.saveTranscription("voice-1", "halo mas")

	messages, err := a.recentMessages(entriesLimit)
	if err != nil {
		t.Fatalf("recentMessages: %v", err)
	}
	if len(messages) != 1 {
		t.Fatalf("got %d messages, want 1", len(messages))
	}
	if messages[0].Transcription == nil || *messages[0].Transcription != "halo mas" {
		t.Errorf("transcription = %v, want %q", messages[0].Transcription, "halo mas")
	}
}

func TestSaveTranscriptionUnknownMessage(t *testing.T) {
	a := newTestApp(t)

	a.saveTranscription("missing", "halo mas")

	if got := countMessages(t, a); got != 0 {
		t.Errorf("message count = %d, want 0", got)
	}
}
