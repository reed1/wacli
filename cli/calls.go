package main

import (
	"fmt"
	"os"
	"strings"

	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
)

type Call struct {
	ID         int64  `json:"id"`
	Timestamp  int64  `json:"timestamp"`
	CallID     string `json:"call_id"`
	CallerJID  string `json:"caller_jid"`
	CallerName string `json:"caller_name"`
	IsGroup    bool   `json:"is_group"`
	GroupJID   string `json:"group_jid"`
	GroupName  string `json:"group_name"`
}

func (a *App) handleCallOffer(evt *events.CallOffer) {
	isGroup := !evt.BasicCallMeta.GroupJID.IsEmpty()
	groupName := ""
	if isGroup {
		groupInfo, err := a.client.GetGroupInfo(a.ctx, evt.BasicCallMeta.GroupJID)
		if err == nil {
			groupName = groupInfo.Name
		}
	}

	call := &Call{
		Timestamp:  evt.BasicCallMeta.Timestamp.Unix(),
		CallID:     evt.BasicCallMeta.CallID,
		CallerJID:  evt.BasicCallMeta.From.String(),
		CallerName: a.getCallerName(evt.BasicCallMeta.From),
		IsGroup:    isGroup,
		GroupJID:   evt.BasicCallMeta.GroupJID.String(),
		GroupName:  groupName,
	}

	if err := a.saveCall(call); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to save call: %v\n", err)
	}
}

func (a *App) handleCallOfferNotice(evt *events.CallOfferNotice) {
	isGroup := !evt.BasicCallMeta.GroupJID.IsEmpty()
	groupName := ""
	if isGroup {
		groupInfo, err := a.client.GetGroupInfo(a.ctx, evt.BasicCallMeta.GroupJID)
		if err == nil {
			groupName = groupInfo.Name
		}
	}

	call := &Call{
		Timestamp:  evt.BasicCallMeta.Timestamp.Unix(),
		CallID:     evt.BasicCallMeta.CallID,
		CallerJID:  evt.BasicCallMeta.From.String(),
		CallerName: a.getCallerName(evt.BasicCallMeta.From),
		IsGroup:    isGroup,
		GroupJID:   evt.BasicCallMeta.GroupJID.String(),
		GroupName:  groupName,
	}

	if err := a.saveCall(call); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to save call: %v\n", err)
	}
}

func (a *App) getCallerName(callerJID types.JID) string {
	contact, err := a.client.Store.Contacts.GetContact(a.ctx, callerJID)
	if err == nil && contact.Found {
		if contact.PushName != "" {
			return contact.PushName
		}
		if contact.FullName != "" {
			return contact.FullName
		}
	}
	return callerJID.User
}

func (a *App) saveCall(call *Call) error {
	columns, placeholders, values := buildInsertParams(call)
	query := fmt.Sprintf(
		"INSERT INTO calls (%s) VALUES (%s)",
		strings.Join(columns, ", "),
		strings.Join(placeholders, ", "),
	)

	result, err := a.msgDB.Exec(query, values...)
	if err != nil {
		return err
	}

	call.ID, _ = result.LastInsertId()

	var count int
	err = a.msgDB.QueryRow("SELECT COUNT(*) FROM calls").Scan(&count)
	if err != nil {
		return err
	}

	if count > maxMessages {
		_, err = a.msgDB.Exec(`
			DELETE FROM calls WHERE id NOT IN (
				SELECT id FROM calls ORDER BY timestamp DESC LIMIT ?
			)
		`, trimToCount)
		if err != nil {
			return err
		}
	}

	return nil
}
