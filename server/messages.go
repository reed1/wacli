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

func (a *App) resolveName(jidStr string) string {
	jid, err := types.ParseJID(jidStr)
	if err != nil {
		return "Unknown Contact"
	}

	contact, err := a.client.Store.Contacts.GetContact(a.ctx, jid)
	if err == nil && contact.Found {
		if contact.PushName != "" {
			return contact.PushName
		}
		if contact.FullName != "" {
			return contact.FullName
		}
	}

	if jid.Server == "lid" {
		var pn string
		err := a.waDB.QueryRow("SELECT pn FROM whatsmeow_lid_map WHERE lid = ?", jidStr).Scan(&pn)
		if err == nil && pn != "" {
			phoneJID, err := types.ParseJID(pn)
			if err == nil {
				contact, err := a.client.Store.Contacts.GetContact(a.ctx, phoneJID)
				if err == nil && contact.Found {
					if contact.PushName != "" {
						return contact.PushName
					}
					if contact.FullName != "" {
						return contact.FullName
					}
				}
			}
		}
	}

	return "Unknown Contact"
}

func (a *App) resolveMentions(text string, msg *waE2E.Message) (result string) {
	defer func() {
		if r := recover(); r != nil {
			result = text
		}
	}()

	ctx := getContextInfo(msg)
	if ctx == nil {
		return text
	}

	mentionedJIDs := ctx.GetMentionedJID()
	if len(mentionedJIDs) == 0 {
		return text
	}

	result = text
	for _, jidStr := range mentionedJIDs {
		jid, err := types.ParseJID(jidStr)
		if err != nil {
			continue
		}
		name := a.resolveName(jidStr)
		result = strings.ReplaceAll(result, "@"+jid.User, fmt.Sprintf(`<mention jid="%s" name="%s"/>`, jidStr, name))
	}
	return result
}

type Message struct {
	ID           int64   `json:"id"`
	MessageID    string  `json:"message_id"`
	Timestamp    int64   `json:"timestamp"`
	ChatJID      string  `json:"chat_jid"`
	ChatName     string  `json:"chat_name"`
	SenderJID    string  `json:"sender_jid"`
	SenderName   string  `json:"sender_name"`
	IsGroup      bool    `json:"is_group"`
	IsMuted      bool    `json:"is_muted"`
	IsReplyToMe  bool    `json:"is_reply_to_me"`
	IsFromMe     bool    `json:"is_from_me"`
	MessageType  string  `json:"message_type"`
	Text         string  `json:"text"`
	MediaFile    *string `json:"media_file"`
	OriginalText *string `json:"original_text"`
	IsDeleted    bool    `json:"is_deleted"`
}

func (a *App) handleMessage(msg *events.Message) {
	vlogf("handleMessage id=%s chat=%s sender=%s pushname=%q isGroup=%v isFromMe=%v isEdit=%v isEphemeral=%v isViewOnce=%v retry=%d",
		msg.Info.ID, msg.Info.Chat.String(), msg.Info.Sender.String(), msg.Info.PushName,
		msg.Info.IsGroup, msg.Info.IsFromMe, msg.IsEdit, msg.IsEphemeral, msg.IsViewOnce, msg.RetryCount)
	vlogf("  message payload: %s", protoDump(msg.Message))
	vlogf("  raw payload:     %s", protoDump(msg.RawMessage))
	if protoMsg := msg.Message.GetProtocolMessage(); protoMsg != nil {
		vlogf("  protocolMessage type=%v key=%s", protoMsg.GetType(), protoDump(protoMsg.GetKey()))
		if edited := protoMsg.GetEditedMessage(); edited != nil {
			vlogf("  protocolMessage.editedMessage: %s", protoDump(edited))
		}
		switch protoMsg.GetType() {
		case waE2E.ProtocolMessage_MESSAGE_EDIT:
			a.handleEdit(msg, protoMsg)
		case waE2E.ProtocolMessage_REVOKE:
			a.handleRevoke(msg, protoMsg)
		default:
			vlogf("  protocolMessage type %v not handled; skipping save", protoMsg.GetType())
		}
		return
	}

	chatJID := msg.Info.Chat

	if chatJID.Server == "broadcast" && !a.config.IncludeStatusMessages {
		vlogf("  dropped: broadcast/status message")
		return
	}

	isFromMe := msg.Info.IsFromMe
	isMuted := a.isMuted(chatJID)
	isArchived := a.isArchived(chatJID)
	isMentioned := a.isMentioned(msg)
	isReplyToMe := a.isReplyToMe(msg)

	if isMuted && !isMentioned && !isReplyToMe && !isFromMe && !a.config.IncludeMutedMessages {
		vlogf("  dropped: muted chat, not mentioned/reply-to-me")
		return
	}

	if isArchived && !isMentioned && !isReplyToMe && !isFromMe {
		vlogf("  dropped: archived chat, not mentioned/reply-to-me")
		return
	}

	msgType, text := extractMessage(msg.Message)
	text = a.resolveMentions(text, msg.Message)

	if audio := msg.Message.GetAudioMessage(); audio != nil && audio.GetPTT() {
		go a.handleVoiceMessage(msg, audio)
	}

	var mediaFile *string
	if img := msg.Message.GetImageMessage(); img != nil {
		filename, err := a.downloadMedia(img, img.GetMimetype())
		if err != nil {
			fmt.Fprintf(os.Stderr, "Failed to download image: %v\n", err)
		} else {
			mediaFile = &filename
		}
	} else if vid := msg.Message.GetVideoMessage(); vid != nil {
		filename, err := a.downloadMedia(vid, vid.GetMimetype())
		if err != nil {
			fmt.Fprintf(os.Stderr, "Failed to download video: %v\n", err)
		} else {
			mediaFile = &filename
		}
	}

	senderName := a.getSenderName(msg)
	chatName := a.getChatName(msg)

	message := &Message{
		MessageID:   msg.Info.ID,
		Timestamp:   msg.Info.Timestamp.Unix(),
		ChatJID:     chatJID.String(),
		ChatName:    chatName,
		SenderJID:   msg.Info.Sender.String(),
		SenderName:  senderName,
		IsGroup:     msg.Info.IsGroup,
		IsMuted:     isMuted,
		IsReplyToMe: isReplyToMe,
		IsFromMe:    isFromMe,
		MessageType: msgType,
		Text:        text,
		MediaFile:   mediaFile,
	}

	if err := a.saveMessage(message); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to save message: %v\n", err)
		os.Exit(1)
	}

	a.broadcastMessage(message)
}

func (a *App) handleEdit(msg *events.Message, protoMsg *waE2E.ProtocolMessage) {
	targetID := protoMsg.GetKey().GetID()
	edited := protoMsg.GetEditedMessage()
	if targetID == "" || edited == nil {
		vlogf("  edit missing key.ID or editedMessage; skipping")
		return
	}

	_, newText := extractMessage(edited)
	newText = a.resolveMentions(newText, edited)

	res, err := a.msgDB.Exec(
		`UPDATE messages
		 SET original_text = COALESCE(original_text, text), text = ?
		 WHERE message_id = ?`,
		newText, targetID,
	)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to apply edit for %s: %v\n", targetID, err)
		return
	}
	n, _ := res.RowsAffected()
	vlogf("  applied edit: target=%s newText=%q rows=%d", targetID, newText, n)
	if n == 0 {
		return
	}
	a.broadcastMessageUpdate(targetID)
}

func (a *App) handleRevoke(msg *events.Message, protoMsg *waE2E.ProtocolMessage) {
	targetID := protoMsg.GetKey().GetID()
	if targetID == "" {
		vlogf("  revoke missing key.ID; skipping")
		return
	}

	res, err := a.msgDB.Exec(
		`UPDATE messages SET is_deleted = 1 WHERE message_id = ?`,
		targetID,
	)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to apply revoke for %s: %v\n", targetID, err)
		return
	}
	n, _ := res.RowsAffected()
	vlogf("  applied revoke: target=%s rows=%d", targetID, n)
	if n == 0 {
		return
	}
	a.broadcastMessageUpdate(targetID)
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

	if count > maxEntries {
		a.cleanupMediaForOldMessages()
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

	if settings.MutedUntil.Equal(store.MutedForever) {
		return true
	}
	if settings.MutedUntil.After(time.Now()) {
		return true
	}
	return false
}

func (a *App) isArchived(chatJID types.JID) bool {
	settings, err := a.client.Store.ChatSettings.GetChatSettings(a.ctx, chatJID)
	if err != nil || !settings.Found {
		return false
	}
	return settings.Archived
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
	return a.resolveChatName(msg.Info.Chat)
}

func (a *App) resolveChatName(chatJID types.JID) string {
	if chatJID.Server == types.GroupServer {
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

func (a *App) recordOutgoing(chatJID types.JID, messageID string, timestamp time.Time, msgType, text string) {
	myJID := a.client.Store.ID
	if myJID == nil {
		return
	}
	if timestamp.IsZero() {
		timestamp = time.Now()
	}

	senderName := a.client.Store.PushName
	if senderName == "" {
		senderName = myJID.User
	}

	message := &Message{
		MessageID:   messageID,
		Timestamp:   timestamp.Unix(),
		ChatJID:     chatJID.String(),
		ChatName:    a.resolveChatName(chatJID),
		SenderJID:   myJID.ToNonAD().String(),
		SenderName:  senderName,
		IsGroup:     chatJID.Server == types.GroupServer,
		IsMuted:     a.isMuted(chatJID),
		IsFromMe:    true,
		MessageType: msgType,
		Text:        text,
	}

	if err := a.saveMessage(message); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to save outgoing message: %v\n", err)
		return
	}
	a.broadcastMessage(message)
}
