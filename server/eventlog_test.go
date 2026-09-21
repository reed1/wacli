package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func newTestEventLog(t *testing.T) *EventLog {
	t.Helper()
	log, err := newEventLog(t.TempDir())
	if err != nil {
		t.Fatalf("newEventLog: %v", err)
	}
	t.Cleanup(func() { log.Close() })
	return log
}

func writeAt(t *testing.T, log *EventLog, now time.Time, line string) {
	t.Helper()
	log.mu.Lock()
	defer log.mu.Unlock()
	file, err := log.fileFor(now)
	if err != nil {
		t.Fatalf("fileFor: %v", err)
	}
	if _, err := file.Write([]byte(line + "\n")); err != nil {
		t.Fatalf("write: %v", err)
	}
	if err := os.Chtimes(file.Name(), now, now); err != nil {
		t.Fatalf("chtimes: %v", err)
	}
}

func readLog(t *testing.T, log *EventLog, name string) []string {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(log.dir, name))
	if err != nil {
		t.Fatalf("read %s: %v", name, err)
	}
	return strings.Split(strings.TrimSuffix(string(data), "\n"), "\n")
}

func TestEventLogNamesFileAfterDayOfWeek(t *testing.T) {
	log := newTestEventLog(t)
	writeAt(t, log, time.Date(2026, 9, 16, 9, 0, 0, 0, time.Local), "a")

	if _, err := os.Stat(filepath.Join(log.dir, "wed.jsonl")); err != nil {
		t.Fatalf("expected wed.jsonl: %v", err)
	}
}

func TestEventLogAppendsWithinTheSameDay(t *testing.T) {
	log := newTestEventLog(t)
	writeAt(t, log, time.Date(2026, 9, 16, 9, 0, 0, 0, time.Local), "a")
	writeAt(t, log, time.Date(2026, 9, 16, 23, 0, 0, 0, time.Local), "b")

	if lines := readLog(t, log, "wed.jsonl"); len(lines) != 2 {
		t.Fatalf("expected 2 lines, got %v", lines)
	}
}

// A restart mid-day reopens the file from scratch; the day it belongs to comes
// from its mtime, so what is already in it survives.
func TestEventLogAppendsAfterRestartOnTheSameDay(t *testing.T) {
	log := newTestEventLog(t)
	now := time.Date(2026, 9, 16, 9, 0, 0, 0, time.Local)
	writeAt(t, log, now, "a")

	reopened, err := newEventLog(log.dir)
	if err != nil {
		t.Fatalf("newEventLog: %v", err)
	}
	t.Cleanup(func() { reopened.Close() })
	writeAt(t, reopened, now.Add(time.Hour), "b")

	if lines := readLog(t, reopened, "wed.jsonl"); len(lines) != 2 {
		t.Fatalf("expected 2 lines, got %v", lines)
	}
}

func TestEventLogTruncatesWhenTheNameComesRoundAgain(t *testing.T) {
	log := newTestEventLog(t)
	writeAt(t, log, time.Date(2026, 9, 16, 9, 0, 0, 0, time.Local), "last week")
	writeAt(t, log, time.Date(2026, 9, 23, 9, 0, 0, 0, time.Local), "this week")

	lines := readLog(t, log, "wed.jsonl")
	if len(lines) != 1 || lines[0] != "this week" {
		t.Fatalf("expected only this week's line, got %v", lines)
	}
}

func TestEventLogWritesOneJSONObjectPerEvent(t *testing.T) {
	log := newTestEventLog(t)
	log.event(&struct {
		Code string
	}{Code: "515"})

	lines := readLog(t, log, strings.ToLower(time.Now().Format("Mon"))+".jsonl")
	if len(lines) != 1 {
		t.Fatalf("expected 1 line, got %v", lines)
	}

	var rec struct {
		Time  string          `json:"time"`
		Kind  string          `json:"kind"`
		Event string          `json:"event"`
		Data  json.RawMessage `json:"data"`
	}
	if err := json.Unmarshal([]byte(lines[0]), &rec); err != nil {
		t.Fatalf("unmarshal record: %v", err)
	}
	if rec.Kind != "event" {
		t.Errorf("kind = %q, want event", rec.Kind)
	}
	if rec.Time == "" {
		t.Error("time is empty")
	}
	if string(rec.Data) != `{"Code":"515"}` {
		t.Errorf("data = %s", rec.Data)
	}
}

// json.Marshal cannot encode a channel; the record still has to land, carrying
// the reason and a readable dump instead of the payload.
func TestEventLogRecordsUnmarshalablePayloads(t *testing.T) {
	log := newTestEventLog(t)
	log.event(&struct {
		Ch chan int
	}{Ch: make(chan int)})

	lines := readLog(t, log, strings.ToLower(time.Now().Format("Mon"))+".jsonl")
	if len(lines) != 1 {
		t.Fatalf("expected 1 line, got %v", lines)
	}

	var rec struct {
		MarshalError string `json:"marshal_error"`
		Dump         string `json:"dump"`
	}
	if err := json.Unmarshal([]byte(lines[0]), &rec); err != nil {
		t.Fatalf("unmarshal record: %v", err)
	}
	if rec.MarshalError == "" {
		t.Error("marshal_error is empty")
	}
	if rec.Dump == "" {
		t.Error("dump is empty")
	}
}
