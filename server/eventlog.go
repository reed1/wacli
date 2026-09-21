package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"time"
)

// Every event whatsmeow hands the server is appended as one JSON object to a
// file named after the day of the week. Seven names cycle, and the first write
// of a day truncates the file it lands in, so the log carries the past week and
// never grows past it.
type EventLog struct {
	dir  string
	mu   sync.Mutex
	file *os.File
	day  string
}

func newEventLog(dir string) (*EventLog, error) {
	if err := os.MkdirAll(dir, 0755); err != nil {
		return nil, err
	}
	return &EventLog{dir: dir}, nil
}

type logRecord struct {
	Time         string      `json:"time"`
	Kind         string      `json:"kind"`
	Event        string      `json:"event,omitempty"`
	Data         interface{} `json:"data"`
	MarshalError string      `json:"marshal_error,omitempty"`
	Dump         string      `json:"dump,omitempty"`
}

// The raw event, exactly as whatsmeow delivered it.
func (e *EventLog) event(evt interface{}) {
	e.log("event", reflect.TypeOf(evt).String(), evt)
}

// What handleMessage made of a message event: the inputs the mention, mute and
// archive rules read, and the call they led to.
func (e *EventLog) verdict(v *messageVerdict) {
	e.log("verdict", "", v)
}

func (e *EventLog) log(kind, event string, data interface{}) {
	if e == nil {
		return
	}

	rec := logRecord{Time: time.Now().Format(time.RFC3339Nano), Kind: kind, Event: event}
	if raw, err := json.Marshal(data); err != nil {
		rec.MarshalError = err.Error()
		rec.Dump = fmt.Sprintf("%+v", data)
	} else {
		rec.Data = json.RawMessage(raw)
	}

	line, err := json.Marshal(rec)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to encode event log record: %v\n", err)
		return
	}
	line = append(line, '\n')

	e.mu.Lock()
	defer e.mu.Unlock()

	file, err := e.fileFor(time.Now())
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to open event log: %v\n", err)
		return
	}
	if _, err := file.Write(line); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to write event log: %v\n", err)
	}
}

// The open file is reused within a day. Which day the file on disk belongs to
// is read from its mtime rather than remembered, so a restart part-way through
// a day appends to what is already there instead of wiping it.
func (e *EventLog) fileFor(now time.Time) (*os.File, error) {
	day := now.Format(time.DateOnly)
	if e.file != nil && e.day == day {
		return e.file, nil
	}
	if e.file != nil {
		e.file.Close()
		e.file = nil
		e.day = ""
	}

	path := filepath.Join(e.dir, strings.ToLower(now.Format("Mon"))+".jsonl")
	flags := os.O_CREATE | os.O_WRONLY | os.O_APPEND
	if info, err := os.Stat(path); err != nil || info.ModTime().Format(time.DateOnly) != day {
		flags |= os.O_TRUNC
	}

	file, err := os.OpenFile(path, flags, 0644)
	if err != nil {
		return nil, err
	}
	e.file = file
	e.day = day
	return file, nil
}

func (e *EventLog) Close() error {
	if e == nil {
		return nil
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.file == nil {
		return nil
	}
	err := e.file.Close()
	e.file = nil
	e.day = ""
	return err
}
