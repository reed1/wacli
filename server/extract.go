package main

import (
	"sort"
	"strings"

	"go.mau.fi/whatsmeow/proto/waE2E"
	"google.golang.org/protobuf/reflect/protoreflect"
)

// Fields WhatsApp attaches as transport plumbing rather than as something a
// human sent. A payload carrying only these is not worth showing.
var transportOnlyFields = map[string]bool{
	"senderKeyDistributionMessage": true,
	"messageContextInfo":           true,
}

// A group message is encrypted twice and whatsmeow dispatches each copy as its
// own event under the same message ID: one copy holds the content, the other
// only the sender key that lets future messages be decrypted. Without this the
// second copy is stored as a duplicate "Unhandled" entry beside the real one.
func isSenderKeyDistribution(msg *waE2E.Message) bool {
	if msg.GetSenderKeyDistributionMessage() == nil {
		return false
	}
	for _, name := range populatedFields(msg) {
		if !transportOnlyFields[name] {
			return false
		}
	}
	return true
}

func populatedFields(msg *waE2E.Message) []string {
	if msg == nil {
		return nil
	}
	var names []string
	msg.ProtoReflect().Range(func(fd protoreflect.FieldDescriptor, _ protoreflect.Value) bool {
		names = append(names, fd.JSONName())
		return true
	})
	sort.Strings(names)
	return names
}

// Names the fields that were actually set, so an entry rendered as "Unhandled"
// says what it was carrying instead of leaving it to be guessed at.
func unhandledType(msg *waE2E.Message) string {
	var named []string
	for _, name := range populatedFields(msg) {
		if !transportOnlyFields[name] {
			named = append(named, name)
		}
	}
	if len(named) == 0 {
		return "Unhandled"
	}
	return "Unhandled: " + strings.Join(named, ", ")
}

func extractMessage(msg *waE2E.Message) (msgType, text string) {
	if msg == nil {
		return "Unhandled", ""
	}
	if text := msg.GetConversation(); text != "" {
		return "", text
	}
	if ext := msg.GetExtendedTextMessage(); ext != nil {
		return "", ext.GetText()
	}
	if img := msg.GetImageMessage(); img != nil {
		return "Image", img.GetCaption()
	}
	if vid := msg.GetVideoMessage(); vid != nil {
		return "Video", vid.GetCaption()
	}
	if doc := msg.GetDocumentMessage(); doc != nil {
		return "Document", doc.GetFileName()
	}
	if audio := msg.GetAudioMessage(); audio != nil {
		if audio.GetPTT() {
			return "Voice", ""
		}
		return "Audio", ""
	}
	if sticker := msg.GetStickerMessage(); sticker != nil {
		return "Sticker", ""
	}
	if contact := msg.GetContactMessage(); contact != nil {
		return "Contact", contact.GetDisplayName()
	}
	if loc := msg.GetLocationMessage(); loc != nil {
		return "Location", ""
	}
	if btn := msg.GetButtonsMessage(); btn != nil {
		return "Buttons", btn.GetContentText()
	}
	if list := msg.GetListMessage(); list != nil {
		return "List", list.GetTitle()
	}
	if poll := msg.GetPollCreationMessage(); poll != nil {
		return "Poll", poll.GetName()
	}
	if tmpl := msg.GetTemplateMessage(); tmpl != nil {
		if hydratedTmpl := tmpl.GetHydratedTemplate(); hydratedTmpl != nil {
			return "Template", hydratedTmpl.GetHydratedContentText()
		}
		return "Template", ""
	}
	if interactive := msg.GetInteractiveMessage(); interactive != nil {
		if body := interactive.GetBody(); body != nil {
			return "Interactive", body.GetText()
		}
		return "Interactive", ""
	}
	if groupInvite := msg.GetGroupInviteMessage(); groupInvite != nil {
		return "Group Invite", groupInvite.GetGroupName()
	}
	if order := msg.GetOrderMessage(); order != nil {
		return "Order", order.GetOrderTitle()
	}
	if product := msg.GetProductMessage(); product != nil {
		if productInfo := product.GetProduct(); productInfo != nil {
			if productImage := productInfo.GetProductImage(); productImage != nil {
				return "Product", productImage.GetCaption()
			}
		}
		return "Product", ""
	}
	if reaction := msg.GetReactionMessage(); reaction != nil {
		return "Reaction", reaction.GetText()
	}
	if event := msg.GetEventMessage(); event != nil {
		return "Event", event.GetName()
	}
	return unhandledType(msg), ""
}
