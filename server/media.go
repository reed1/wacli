package main

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"github.com/google/uuid"
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"google.golang.org/protobuf/proto"
)

const (
	mediaDir       = "media"
	mediaChunkSize = 256 * 1024
)

var mimeExtensions = map[string]string{
	"image/jpeg":      ".jpg",
	"image/png":       ".png",
	"image/webp":      ".webp",
	"image/gif":       ".gif",
	"video/mp4":       ".mp4",
	"video/3gpp":      ".3gp",
	"video/quicktime": ".mov",
	"video/webm":      ".webm",
}

func (a *App) downloadMedia(dl whatsmeow.DownloadableMessage, mimetype string) (string, error) {
	data, err := a.client.Download(a.ctx, dl)
	if err != nil {
		return "", fmt.Errorf("download: %w", err)
	}

	if err := os.MkdirAll(mediaDir, 0755); err != nil {
		return "", fmt.Errorf("mkdir: %w", err)
	}

	ext := mimeExtensions[mimetype]
	if ext == "" {
		ext = ".bin"
	}
	filename := uuid.NewString() + ext

	if err := os.WriteFile(filepath.Join(mediaDir, filename), data, 0644); err != nil {
		return "", fmt.Errorf("write: %w", err)
	}

	return filename, nil
}

func (a *App) sendImage(chatJID string, imageDataB64 string) error {
	jid, err := types.ParseJID(chatJID)
	if err != nil {
		return fmt.Errorf("invalid JID: %w", err)
	}

	data, err := base64.StdEncoding.DecodeString(imageDataB64)
	if err != nil {
		return fmt.Errorf("decode image data: %w", err)
	}
	if len(data) == 0 {
		return fmt.Errorf("empty image data")
	}

	mimetype := http.DetectContentType(data)

	uploaded, err := a.client.Upload(a.ctx, data, whatsmeow.MediaImage)
	if err != nil {
		return fmt.Errorf("upload: %w", err)
	}

	msg := &waE2E.Message{
		ImageMessage: &waE2E.ImageMessage{
			Mimetype:      proto.String(mimetype),
			URL:           proto.String(uploaded.URL),
			DirectPath:    proto.String(uploaded.DirectPath),
			MediaKey:      uploaded.MediaKey,
			FileEncSHA256: uploaded.FileEncSHA256,
			FileSHA256:    uploaded.FileSHA256,
			FileLength:    proto.Uint64(uint64(len(data))),
		},
	}

	resp, err := a.client.SendMessage(a.ctx, jid, msg)
	if err != nil {
		return fmt.Errorf("send failed: %w", err)
	}

	mediaFile, err := a.saveOutgoingMedia(data, mimetype)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to save outgoing image locally: %v\n", err)
	}

	fmt.Printf("Sent image to %s\n", chatJID)
	a.recordOutgoing(jid, resp.ID, resp.Timestamp, "image", "", mediaFile)
	return nil
}

func (a *App) saveOutgoingMedia(data []byte, mimetype string) (*string, error) {
	if err := os.MkdirAll(mediaDir, 0755); err != nil {
		return nil, fmt.Errorf("mkdir: %w", err)
	}

	ext := mimeExtensions[mimetype]
	if ext == "" {
		ext = ".bin"
	}
	filename := uuid.NewString() + ext

	if err := os.WriteFile(filepath.Join(mediaDir, filename), data, 0644); err != nil {
		return nil, fmt.Errorf("write: %w", err)
	}

	return &filename, nil
}

type MediaResponse struct {
	Type      string `json:"type"`
	RequestID string `json:"request_id"`
	Seq       int    `json:"seq"`
	Data      string `json:"data,omitempty"`
	Done      bool   `json:"done,omitempty"`
	Error     string `json:"error,omitempty"`
}

func (a *App) sendMedia(state *connState, requestID string, filename string) {
	sendErr := func(msg string) {
		resp := MediaResponse{Type: "media", RequestID: requestID, Error: msg}
		data, _ := json.Marshal(resp)
		state.write(append(data, '\n'))
	}

	if filename == "" || strings.Contains(filename, "/") || strings.Contains(filename, "..") {
		sendErr("invalid filename")
		return
	}

	fileData, err := os.ReadFile(filepath.Join(mediaDir, filename))
	if err != nil {
		sendErr("file not found")
		return
	}

	total := len(fileData)
	if total == 0 {
		resp := MediaResponse{Type: "media", RequestID: requestID, Seq: 0, Done: true}
		data, _ := json.Marshal(resp)
		state.write(append(data, '\n'))
		return
	}

	seq := 0
	for offset := 0; offset < total; offset += mediaChunkSize {
		end := offset + mediaChunkSize
		if end > total {
			end = total
		}
		resp := MediaResponse{
			Type:      "media",
			RequestID: requestID,
			Seq:       seq,
			Data:      base64.StdEncoding.EncodeToString(fileData[offset:end]),
			Done:      end == total,
		}
		data, _ := json.Marshal(resp)
		if err := state.write(append(data, '\n')); err != nil {
			return
		}
		seq++
	}
}

func (a *App) cleanupMediaForOldMessages() {
	rows, err := a.msgDB.Query(`
		SELECT media_file FROM messages
		WHERE media_file IS NOT NULL AND id NOT IN (
			SELECT id FROM messages ORDER BY timestamp DESC LIMIT ?
		)
	`, trimToCount)
	if err != nil {
		return
	}
	defer rows.Close()

	for rows.Next() {
		var filename string
		if err := rows.Scan(&filename); err != nil {
			continue
		}
		if filename != "" && !strings.Contains(filename, "/") {
			os.Remove(filepath.Join(mediaDir, filename))
		}
	}
}
