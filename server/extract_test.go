package main

import (
	"testing"

	"go.mau.fi/whatsmeow/proto/waE2E"
	"google.golang.org/protobuf/proto"
)

func TestExtractMessage(t *testing.T) {
	tests := []struct {
		name     string
		msg      *waE2E.Message
		wantType string
		wantText string
	}{
		{"nil", nil, "Unhandled", ""},
		{"empty", &waE2E.Message{}, "Unhandled", ""},
		{
			"conversation",
			&waE2E.Message{Conversation: proto.String("hello")},
			"", "hello",
		},
		{
			"extended text",
			&waE2E.Message{ExtendedTextMessage: &waE2E.ExtendedTextMessage{Text: proto.String("linked text")}},
			"", "linked text",
		},
		{
			"image with caption",
			&waE2E.Message{ImageMessage: &waE2E.ImageMessage{Caption: proto.String("look")}},
			"Image", "look",
		},
		{
			"video",
			&waE2E.Message{VideoMessage: &waE2E.VideoMessage{Caption: proto.String("clip")}},
			"Video", "clip",
		},
		{
			"document",
			&waE2E.Message{DocumentMessage: &waE2E.DocumentMessage{FileName: proto.String("report.pdf")}},
			"Document", "report.pdf",
		},
		{
			"voice note",
			&waE2E.Message{AudioMessage: &waE2E.AudioMessage{PTT: proto.Bool(true)}},
			"Voice", "",
		},
		{
			"audio",
			&waE2E.Message{AudioMessage: &waE2E.AudioMessage{}},
			"Audio", "",
		},
		{
			"sticker",
			&waE2E.Message{StickerMessage: &waE2E.StickerMessage{}},
			"Sticker", "",
		},
		{
			"contact",
			&waE2E.Message{ContactMessage: &waE2E.ContactMessage{DisplayName: proto.String("Alice")}},
			"Contact", "Alice",
		},
		{
			"location",
			&waE2E.Message{LocationMessage: &waE2E.LocationMessage{}},
			"Location", "",
		},
		{
			"poll",
			&waE2E.Message{PollCreationMessage: &waE2E.PollCreationMessage{Name: proto.String("lunch?")}},
			"Poll", "lunch?",
		},
		{
			"reaction",
			&waE2E.Message{ReactionMessage: &waE2E.ReactionMessage{Text: proto.String("👍")}},
			"Reaction", "👍",
		},
		{
			"event",
			&waE2E.Message{EventMessage: &waE2E.EventMessage{Name: proto.String("party")}},
			"Event", "party",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			gotType, gotText := extractMessage(tt.msg)
			if gotType != tt.wantType || gotText != tt.wantText {
				t.Errorf("extractMessage() = (%q, %q), want (%q, %q)", gotType, gotText, tt.wantType, tt.wantText)
			}
		})
	}
}

func TestGetContextInfo(t *testing.T) {
	ctx := &waE2E.ContextInfo{StanzaID: proto.String("abc")}

	if got := getContextInfo(nil); got != nil {
		t.Errorf("nil message: got %v, want nil", got)
	}
	if got := getContextInfo(&waE2E.Message{Conversation: proto.String("hi")}); got != nil {
		t.Errorf("conversation has no context info: got %v, want nil", got)
	}

	withCtx := []*waE2E.Message{
		{ExtendedTextMessage: &waE2E.ExtendedTextMessage{ContextInfo: ctx}},
		{ImageMessage: &waE2E.ImageMessage{ContextInfo: ctx}},
		{VideoMessage: &waE2E.VideoMessage{ContextInfo: ctx}},
		{DocumentMessage: &waE2E.DocumentMessage{ContextInfo: ctx}},
		{AudioMessage: &waE2E.AudioMessage{ContextInfo: ctx}},
		{StickerMessage: &waE2E.StickerMessage{ContextInfo: ctx}},
	}
	for i, msg := range withCtx {
		if got := getContextInfo(msg); got.GetStanzaID() != "abc" {
			t.Errorf("case %d: context info not extracted", i)
		}
	}
}
