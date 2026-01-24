package main

import "go.mau.fi/whatsmeow/proto/waE2E"

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
	return "Unhandled", ""
}
