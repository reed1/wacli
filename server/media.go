package main

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"

	"github.com/google/uuid"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types/events"
)

const mediaDir = "media"

var mimeExtensions = map[string]string{
	"image/jpeg": ".jpg",
	"image/png":  ".png",
	"image/webp": ".webp",
	"image/gif":  ".gif",
}

func (a *App) downloadMedia(msg *events.Message, img *waE2E.ImageMessage) (string, error) {
	data, err := a.client.Download(a.ctx, img)
	if err != nil {
		return "", fmt.Errorf("download: %w", err)
	}

	if err := os.MkdirAll(mediaDir, 0755); err != nil {
		return "", fmt.Errorf("mkdir: %w", err)
	}

	ext := mimeExtensions[img.GetMimetype()]
	if ext == "" {
		ext = ".bin"
	}
	filename := uuid.NewString() + ext

	if err := os.WriteFile(filepath.Join(mediaDir, filename), data, 0644); err != nil {
		return "", fmt.Errorf("write: %w", err)
	}

	return filename, nil
}

type MediaResponse struct {
	Type      string `json:"type"`
	RequestID string `json:"request_id"`
	Data      string `json:"data,omitempty"`
	Error     string `json:"error,omitempty"`
}

func (a *App) sendMedia(conn net.Conn, requestID string, filename string) {
	resp := MediaResponse{Type: "media", RequestID: requestID}

	if filename == "" || strings.Contains(filename, "/") || strings.Contains(filename, "..") {
		resp.Error = "invalid filename"
		data, _ := json.Marshal(resp)
		conn.Write(append(data, '\n'))
		return
	}

	fileData, err := os.ReadFile(filepath.Join(mediaDir, filename))
	if err != nil {
		resp.Error = "file not found"
		data, _ := json.Marshal(resp)
		conn.Write(append(data, '\n'))
		return
	}

	resp.Data = base64.StdEncoding.EncodeToString(fileData)
	data, _ := json.Marshal(resp)
	conn.Write(append(data, '\n'))
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
