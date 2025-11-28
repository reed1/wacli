package main

import (
	"fmt"
	"os"
	"strings"
	"time"

	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
)

type Message struct {
	Type        string `json:"type"`
	ID          int64  `json:"id"`
	Timestamp   int64  `json:"timestamp"`
	ChatJID     string `json:"chat_jid"`
	ChatName    string `json:"chat_name"`
	SenderJID   string `json:"sender_jid"`
	SenderName  string `json:"sender_name"`
	IsGroup     bool   `json:"is_group"`
	IsMuted     bool   `json:"is_muted"`
	IsReplyToMe bool   `json:"is_reply_to_me"`
	Text        string `json:"text"`
}

func (a *App) handleMessage(msg *events.Message) {
	if msg.Info.IsFromMe {
		return
	}

	chatJID := msg.Info.Chat

	if chatJID.Server == "broadcast" && !a.config.IncludeStatusMessages {
		return
	}

	isMuted := a.isMuted(chatJID)
	isMentioned := a.isMentioned(msg)
	isReplyToMe := a.isReplyToMe(msg)

	if isMuted && !isMentioned && !isReplyToMe && !a.config.IncludeMutedMessages {
		return
	}

	text := extractText(msg.Message)
	if text == "" {
		text = "[Media/Other]"
	}

	senderName := a.getSenderName(msg)
	chatName := a.getChatName(msg)

	message := &Message{
		Type:        "message",
		Timestamp:   msg.Info.Timestamp.Unix(),
		ChatJID:     chatJID.String(),
		ChatName:    chatName,
		SenderJID:   msg.Info.Sender.String(),
		SenderName:  senderName,
		IsGroup:     msg.Info.IsGroup,
		IsMuted:     isMuted,
		IsReplyToMe: isReplyToMe,
		Text:        text,
	}

	if err := a.saveMessage(message); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to save message: %v\n", err)
	}

	a.broadcastMessage(message)
}

func (a *App) saveMessage(msg *Message) error {
	columns, placeholders, values := buildInsertParams(msg)
	query := fmt.Sprintf(
		"INSERT INTO messages (%s) VALUES (%s)",
		strings.Join(columns, ", "),
		strings.Join(placeholders, ", "),
	)

	result, err := a.msgDB.Exec(query, values...)
	if err != nil {
		return err
	}

	msg.ID, _ = result.LastInsertId()

	var count int
	err = a.msgDB.QueryRow("SELECT COUNT(*) FROM messages").Scan(&count)
	if err != nil {
		return err
	}

	if count > maxMessages {
		_, err = a.msgDB.Exec(`
			DELETE FROM messages WHERE id NOT IN (
				SELECT id FROM messages ORDER BY timestamp DESC LIMIT ?
			)
		`, trimToCount)
		if err != nil {
			return err
		}
	}

	return nil
}

func (a *App) isMuted(chatJID types.JID) bool {
	settings, err := a.client.Store.ChatSettings.GetChatSettings(a.ctx, chatJID)
	if err != nil || !settings.Found {
		return false
	}

	if settings.MutedUntil == store.MutedForever {
		return true
	}
	if settings.MutedUntil.After(time.Now()) {
		return true
	}
	return false
}

func (a *App) isMentioned(msg *events.Message) bool {
	myJID := a.client.Store.ID
	myLID := a.client.Store.LID
	if myJID == nil {
		return false
	}

	ctx := getContextInfo(msg.Message)
	if ctx == nil {
		return false
	}

	for _, jid := range ctx.GetMentionedJID() {
		if jid == myJID.ToNonAD().String() || jid == myJID.String() {
			return true
		}
		if !myLID.IsEmpty() && jid == myLID.ToNonAD().String() {
			return true
		}
	}
	return false
}

func (a *App) isReplyToMe(msg *events.Message) bool {
	myJID := a.client.Store.ID
	myLID := a.client.Store.LID
	if myJID == nil {
		return false
	}

	ctx := getContextInfo(msg.Message)
	if ctx == nil {
		return false
	}

	participant := ctx.GetParticipant()
	if participant == "" {
		return false
	}

	if participant == myJID.ToNonAD().String() || participant == myJID.String() {
		return true
	}
	if !myLID.IsEmpty() && participant == myLID.ToNonAD().String() {
		return true
	}
	return false
}

func getContextInfo(msg *waE2E.Message) *waE2E.ContextInfo {
	if msg == nil {
		return nil
	}
	if ext := msg.GetExtendedTextMessage(); ext != nil {
		return ext.GetContextInfo()
	}
	if img := msg.GetImageMessage(); img != nil {
		return img.GetContextInfo()
	}
	if vid := msg.GetVideoMessage(); vid != nil {
		return vid.GetContextInfo()
	}
	if doc := msg.GetDocumentMessage(); doc != nil {
		return doc.GetContextInfo()
	}
	if audio := msg.GetAudioMessage(); audio != nil {
		return audio.GetContextInfo()
	}
	if sticker := msg.GetStickerMessage(); sticker != nil {
		return sticker.GetContextInfo()
	}
	return nil
}

func extractText(msg *waE2E.Message) string {
	if msg == nil {
		return ""
	}
	if text := msg.GetConversation(); text != "" {
		return text
	}
	if ext := msg.GetExtendedTextMessage(); ext != nil {
		return ext.GetText()
	}
	if img := msg.GetImageMessage(); img != nil {
		if cap := img.GetCaption(); cap != "" {
			return "[Image] " + cap
		}
		return "[Image]"
	}
	if vid := msg.GetVideoMessage(); vid != nil {
		if cap := vid.GetCaption(); cap != "" {
			return "[Video] " + cap
		}
		return "[Video]"
	}
	if doc := msg.GetDocumentMessage(); doc != nil {
		return "[Document] " + doc.GetFileName()
	}
	if audio := msg.GetAudioMessage(); audio != nil {
		if audio.GetPTT() {
			return "[Voice Message]"
		}
		return "[Audio]"
	}
	if sticker := msg.GetStickerMessage(); sticker != nil {
		return "[Sticker]"
	}
	if contact := msg.GetContactMessage(); contact != nil {
		return "[Contact] " + contact.GetDisplayName()
	}
	if loc := msg.GetLocationMessage(); loc != nil {
		return "[Location]"
	}
	return ""
}

func (a *App) getSenderName(msg *events.Message) string {
	senderJID := msg.Info.Sender
	if msg.Info.IsGroup {
		contact, err := a.client.Store.Contacts.GetContact(a.ctx, senderJID)
		if err == nil && contact.Found {
			if contact.PushName != "" {
				return contact.PushName
			}
			if contact.FullName != "" {
				return contact.FullName
			}
		}
	}
	if msg.Info.PushName != "" {
		return msg.Info.PushName
	}
	return senderJID.User
}

func (a *App) getChatName(msg *events.Message) string {
	chatJID := msg.Info.Chat
	if msg.Info.IsGroup {
		groupInfo, err := a.client.GetGroupInfo(a.ctx, chatJID)
		if err == nil {
			return groupInfo.Name
		}
	}
	contact, err := a.client.Store.Contacts.GetContact(a.ctx, chatJID)
	if err == nil && contact.Found {
		if contact.PushName != "" {
			return contact.PushName
		}
		if contact.FullName != "" {
			return contact.FullName
		}
	}
	return chatJID.User
}
