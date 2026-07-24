package main

import (
	"bufio"
	"context"
	"database/sql"
	"net"
	"testing"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/types"
)

type fakeContactStore struct {
	store.ContactStore
	contacts map[types.JID]types.ContactInfo
}

func (f *fakeContactStore) GetContact(ctx context.Context, user types.JID) (types.ContactInfo, error) {
	return f.contacts[user], nil
}

type fakeChatSettingsStore struct {
	store.ChatSettingsStore
	settings map[types.JID]types.LocalChatSettings
}

func (f *fakeChatSettingsStore) GetChatSettings(ctx context.Context, chat types.JID) (types.LocalChatSettings, error) {
	return f.settings[chat], nil
}

type testApp struct {
	*App
	contacts     *fakeContactStore
	chatSettings *fakeChatSettingsStore
	myJID        types.JID
	myLID        types.JID
}

func newTestApp(t *testing.T) *testApp {
	t.Helper()

	msgDB, err := initMessageDB("file:" + t.TempDir() + "/messages.db")
	if err != nil {
		t.Fatalf("initMessageDB: %v", err)
	}
	t.Cleanup(func() { msgDB.Close() })

	waDB, err := sql.Open("sqlite3", "file:"+t.TempDir()+"/wacli.db")
	if err != nil {
		t.Fatalf("open waDB: %v", err)
	}
	t.Cleanup(func() { waDB.Close() })
	if _, err := waDB.Exec(`CREATE TABLE whatsmeow_lid_map (lid TEXT PRIMARY KEY, pn TEXT)`); err != nil {
		t.Fatalf("create lid map: %v", err)
	}

	contacts := &fakeContactStore{contacts: make(map[types.JID]types.ContactInfo)}
	chatSettings := &fakeChatSettingsStore{settings: make(map[types.JID]types.LocalChatSettings)}

	myJID := types.JID{User: "15550001111", Server: types.DefaultUserServer, Device: 7}
	myLID := types.JID{User: "111222333444555", Server: types.HiddenUserServer, Device: 7}
	device := &store.Device{
		ID:           &myJID,
		LID:          myLID,
		PushName:     "Test Me",
		Contacts:     contacts,
		ChatSettings: chatSettings,
	}

	app := &App{
		client:      whatsmeow.NewClient(device, nil),
		ctx:         context.Background(),
		msgDB:       msgDB,
		waDB:        waDB,
		config:      Config{ListenAddr: "127.0.0.1:0"},
		socketConns: make(map[net.Conn]*connState),
	}

	return &testApp{App: app, contacts: contacts, chatSettings: chatSettings, myJID: myJID, myLID: myLID}
}

// attachConn registers a socket client on the app and returns its connState
// plus a channel of lines written to it. net.Pipe writes block until read, so
// lines are drained in a goroutine.
func (a *testApp) attachConn(t *testing.T) (*connState, <-chan string) {
	t.Helper()
	server, client := net.Pipe()
	state := &connState{conn: server}
	a.connMu.Lock()
	a.socketConns[server] = state
	a.connMu.Unlock()
	t.Cleanup(func() {
		a.connMu.Lock()
		delete(a.socketConns, server)
		a.connMu.Unlock()
		server.Close()
		client.Close()
	})

	lines := make(chan string, 16)
	go func() {
		scanner := bufio.NewScanner(client)
		scanner.Buffer(make([]byte, 0, 64*1024), maxSocketLine)
		for scanner.Scan() {
			lines <- scanner.Text()
		}
		close(lines)
	}()
	return state, lines
}

func recvLine(t *testing.T, lines <-chan string) string {
	t.Helper()
	select {
	case line, ok := <-lines:
		if !ok {
			t.Fatal("broadcast channel closed")
		}
		return line
	case <-time.After(2 * time.Second):
		t.Fatal("timed out waiting for broadcast line")
		return ""
	}
}

func expectNoLine(t *testing.T, lines <-chan string) {
	t.Helper()
	select {
	case line := <-lines:
		t.Fatalf("unexpected broadcast line: %s", line)
	case <-time.After(50 * time.Millisecond):
	}
}

func countMessages(t *testing.T, a *testApp) int {
	t.Helper()
	var count int
	if err := a.msgDB.QueryRow("SELECT COUNT(*) FROM messages").Scan(&count); err != nil {
		t.Fatalf("count messages: %v", err)
	}
	return count
}
