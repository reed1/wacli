package main

import (
	"fmt"
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
