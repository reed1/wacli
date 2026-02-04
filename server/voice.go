package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"
)

const voiceFileMaxAgeDays = 5

func (a *App) handleVoiceMessage(msg *events.Message, audio *waE2E.AudioMessage) {
	if a.config.TranscriptionScript == "" {
		return
	}

	data, err := a.client.Download(a.ctx, audio)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to download voice message: %v\n", err)
		a.sendVoiceReply(msg, "Transcription error, please see server logs")
		return
	}

	voiceDir := a.getVoiceDir()
	if err := os.MkdirAll(voiceDir, 0755); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to create voice directory: %v\n", err)
		a.sendVoiceReply(msg, "Transcription error, please see server logs")
		return
	}

	filename := fmt.Sprintf("%d_%s.ogg", msg.Info.Timestamp.Unix(), msg.Info.ID)
	filePath := filepath.Join(voiceDir, filename)

	if err := os.WriteFile(filePath, data, 0644); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to save voice message: %v\n", err)
		a.sendVoiceReply(msg, "Transcription error, please see server logs")
		return
	}

	result, err := a.runTranscription(filePath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Transcription failed: %v\n", err)
		a.sendVoiceReply(msg, "Transcription error, please see server logs")
		return
	}

	a.sendVoiceReply(msg, strings.TrimSpace(result))

	go a.cleanupOldVoiceFiles()
}

func (a *App) getVoiceDir() string {
	if a.config.VoiceMessageDir != "" {
		return a.config.VoiceMessageDir
	}
	return filepath.Join(os.TempDir(), "wacli-voice")
}

func (a *App) runTranscription(filePath string) (string, error) {
	cmd := exec.Command(a.config.TranscriptionScript, filePath)
	output, err := cmd.Output()
	if err != nil {
		return "", err
	}
	return string(output), nil
}

func (a *App) sendVoiceReply(msg *events.Message, text string) {
	replyMsg := &waE2E.Message{
		ExtendedTextMessage: &waE2E.ExtendedTextMessage{
			Text: proto.String(text),
			ContextInfo: &waE2E.ContextInfo{
				StanzaID:    proto.String(msg.Info.ID),
				Participant: proto.String(msg.Info.Sender.String()),
			},
		},
	}

	_, err := a.client.SendMessage(a.ctx, msg.Info.Chat, replyMsg)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to send voice reply: %v\n", err)
	}
}

func (a *App) cleanupOldVoiceFiles() {
	voiceDir := a.getVoiceDir()
	cutoff := time.Now().AddDate(0, 0, -voiceFileMaxAgeDays)

	entries, err := os.ReadDir(voiceDir)
	if err != nil {
		return
	}

	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			continue
		}
		if info.ModTime().Before(cutoff) {
			os.Remove(filepath.Join(voiceDir, entry.Name()))
		}
	}
}
