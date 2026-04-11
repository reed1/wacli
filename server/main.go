package main

import (
	"bufio"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"os/signal"
	"reflect"
	"sync"
	"syscall"

	"github.com/joho/godotenv"
	_ "github.com/mattn/go-sqlite3"
	"github.com/mdp/qrterminal/v3"
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
)

const (
	maxEntries               = 200
	trimToCount              = 150
	permanentFailureExitCode = 2
)

type Config struct {
	ListenAddr            string
	IncludeStatusMessages bool
	IncludeMutedMessages  bool
	VoiceMessageDir       string
	TranscriptionScript   string
}

type App struct {
	client       *whatsmeow.Client
	ctx          context.Context
	msgDB        *sql.DB
	waDB         *sql.DB
	config       Config
	socketConns  map[net.Conn]*connState
	connMu       sync.RWMutex
	waConnected  bool
	waReason     string
	stateMu      sync.RWMutex
}

type connState struct {
	conn    net.Conn
	writeMu sync.Mutex
}

func (s *connState) write(data []byte) error {
	s.writeMu.Lock()
	defer s.writeMu.Unlock()
	_, err := s.conn.Write(data)
	return err
}

func loadConfig() Config {
	godotenv.Load()

	listenAddr := os.Getenv("LISTEN_ADDR")
	if listenAddr == "" {
		fmt.Fprintln(os.Stderr, "LISTEN_ADDR is required in .env")
		os.Exit(1)
	}

	return Config{
		ListenAddr:            listenAddr,
		IncludeStatusMessages: os.Getenv("INCLUDE_STATUS_MESSAGES") == "true",
		IncludeMutedMessages:  os.Getenv("INCLUDE_MUTED_MESSAGES") == "true",
		VoiceMessageDir:       os.Getenv("VOICE_MESSAGE_DIR"),
		TranscriptionScript:   os.Getenv("TRANSCRIPTION_SCRIPT"),
	}
}

func main() {
	command := "daemon"
	if len(os.Args) > 1 {
		command = os.Args[1]
	}

	config := loadConfig()
	ctx := context.Background()

	msgDB, err := initMessageDB()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to init message database: %v\n", err)
		os.Exit(1)
	}
	defer msgDB.Close()

	waDB, err := sql.Open("sqlite3", "file:wacli.db?_foreign_keys=on&mode=ro")
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to open wacli.db: %v\n", err)
		os.Exit(1)
	}
	defer waDB.Close()

	dbLog := waLog.Stdout("Database", "ERROR", true)
	container, err := sqlstore.New(ctx, "sqlite3", "file:wacli.db?_foreign_keys=on", dbLog)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to create database: %v\n", err)
		os.Exit(1)
	}

	deviceStore, err := container.GetFirstDevice(ctx)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to get device store: %v\n", err)
		os.Exit(1)
	}

	clientLog := waLog.Stdout("Client", "ERROR", true)
	client := whatsmeow.NewClient(deviceStore, clientLog)
	client.EnableAutoReconnect = true

	app := &App{
		client:      client,
		ctx:         ctx,
		msgDB:       msgDB,
		waDB:        waDB,
		config:      config,
		socketConns: make(map[net.Conn]*connState),
	}

	client.AddEventHandler(app.handleEvent)

	switch command {
	case "daemon":
		runDaemon(app)
	case "login":
		runLogin(app)
	default:
		fmt.Fprintf(os.Stderr, "Unknown command: %s\n", command)
		fmt.Fprintf(os.Stderr, "Usage: wacli [daemon|login]\n")
		os.Exit(1)
	}
}

func runDaemon(app *App) {
	if app.client.Store.ID == nil {
		fmt.Fprintf(os.Stderr, "Device not logged in. Run 'wacli login' first.\n")
		os.Exit(1)
	}

	listener, err := app.startSocketServer()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to start socket server: %v\n", err)
		os.Exit(1)
	}
	defer listener.Close()

	if err := app.client.Connect(); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to connect: %v\n", err)
		os.Exit(1)
	}

	fmt.Println("Connected. Watching for messages...")
	fmt.Printf("TCP server listening on %s\n", app.config.ListenAddr)

	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)
	<-sigChan

	app.client.Disconnect()
	fmt.Println("\nDisconnected.")
}

func runLogin(app *App) {
	if app.client.Store.ID != nil {
		fmt.Println("Device already logged in.")
		os.Exit(0)
	}

	if err := app.loginWithQR(); err != nil {
		fmt.Fprintf(os.Stderr, "Login failed: %v\n", err)
		os.Exit(1)
	}

	fmt.Println("Login complete. You can now run 'wacli daemon' or start the systemd service.")
}

func initMessageDB() (*sql.DB, error) {
	db, err := sql.Open("sqlite3", "file:messages.db?_foreign_keys=on")
	if err != nil {
		return nil, err
	}

	_, err = db.Exec(`
		CREATE TABLE IF NOT EXISTS messages (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			message_id TEXT NOT NULL DEFAULT '',
			timestamp INTEGER NOT NULL,
			chat_jid TEXT NOT NULL,
			chat_name TEXT NOT NULL,
			sender_jid TEXT NOT NULL,
			sender_name TEXT NOT NULL,
			is_group INTEGER NOT NULL,
			is_muted INTEGER NOT NULL,
			is_reply_to_me INTEGER NOT NULL,
			message_type TEXT NOT NULL DEFAULT '',
			text TEXT NOT NULL,
			media_file TEXT
		);
		CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);

		CREATE TABLE IF NOT EXISTS calls (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			timestamp INTEGER NOT NULL,
			call_id TEXT NOT NULL,
			caller_jid TEXT NOT NULL,
			caller_name TEXT NOT NULL,
			is_group INTEGER NOT NULL,
			group_jid TEXT NOT NULL,
			group_name TEXT NOT NULL
		);
		CREATE INDEX IF NOT EXISTS idx_calls_timestamp ON calls(timestamp);
	`)
	if err != nil {
		return nil, err
	}

	db.Exec("ALTER TABLE messages ADD COLUMN media_file TEXT")

	_, err = db.Exec("VACUUM")
	if err != nil {
		return nil, err
	}

	return db, nil
}

func (a *App) startSocketServer() (net.Listener, error) {
	listener, err := net.Listen("tcp", a.config.ListenAddr)
	if err != nil {
		return nil, err
	}

	go func() {
		for {
			conn, err := listener.Accept()
			if err != nil {
				return
			}
			go a.handleSocketConn(conn)
		}
	}()

	return listener, nil
}

type SocketCommand struct {
	Action    string `json:"action"`
	RequestID string `json:"request_id"`
	ChatJID   string `json:"chat_jid"`
	MessageID string `json:"message_id"`
	SenderJID string `json:"sender_jid"`
	Text      string `json:"text"`
	Filename  string `json:"filename"`
}

type SocketResponse struct {
	Type      string `json:"type"`
	RequestID string `json:"request_id"`
	Success   bool   `json:"success"`
	Error     string `json:"error,omitempty"`
}

func (a *App) handleSocketConn(conn net.Conn) {
	state := &connState{conn: conn}

	a.connMu.Lock()
	a.socketConns[conn] = state
	a.connMu.Unlock()

	defer func() {
		a.connMu.Lock()
		delete(a.socketConns, conn)
		a.connMu.Unlock()
		conn.Close()
	}()

	a.sendConnectionState(state)

	scanner := bufio.NewScanner(conn)
	for scanner.Scan() {
		line := scanner.Bytes()
		var cmd SocketCommand
		if err := json.Unmarshal(line, &cmd); err != nil {
			fmt.Fprintf(os.Stderr, "Failed to parse socket command: %v\n", err)
			continue
		}

		switch cmd.Action {
		case "send":
			err := a.sendMessage(cmd.ChatJID, cmd.Text)
			if err != nil {
				fmt.Fprintf(os.Stderr, "Failed to send message: %v\n", err)
			}
			a.sendResponse(state, cmd.RequestID, err)
		case "reply":
			err := a.replyToMessage(cmd.ChatJID, cmd.MessageID, cmd.SenderJID, cmd.Text)
			if err != nil {
				fmt.Fprintf(os.Stderr, "Failed to reply to message: %v\n", err)
			}
			a.sendResponse(state, cmd.RequestID, err)
		case "get_entries":
			if err := a.sendEntries(state); err != nil {
				fmt.Fprintf(os.Stderr, "Failed to send entries: %v\n", err)
			}
		case "get_media":
			a.sendMedia(state, cmd.RequestID, cmd.Filename)
		default:
			fmt.Fprintf(os.Stderr, "Unknown socket command: %s\n", cmd.Action)
		}
	}
}

func (a *App) sendResponse(state *connState, requestID string, err error) {
	resp := SocketResponse{
		Type:      "response",
		RequestID: requestID,
		Success:   err == nil,
	}
	if err != nil {
		resp.Error = err.Error()
	}
	data, marshalErr := json.Marshal(resp)
	if marshalErr != nil {
		return
	}
	data = append(data, '\n')
	state.write(data)
}

type SocketEvent struct {
	Type string      `json:"type"`
	Data interface{} `json:"data"`
}

type ConnectionState struct {
	Connected bool   `json:"connected"`
	Reason    string `json:"reason,omitempty"`
}

func (a *App) setWAState(connected bool, reason string) {
	a.stateMu.Lock()
	a.waConnected = connected
	a.waReason = reason
	a.stateMu.Unlock()

	event := SocketEvent{Type: "connection_state", Data: ConnectionState{Connected: connected, Reason: reason}}
	data, err := json.Marshal(event)
	if err != nil {
		return
	}
	data = append(data, '\n')

	a.connMu.RLock()
	defer a.connMu.RUnlock()
	for _, state := range a.socketConns {
		state.write(data)
	}
}

func (a *App) sendConnectionState(state *connState) {
	a.stateMu.RLock()
	connected, reason := a.waConnected, a.waReason
	a.stateMu.RUnlock()

	event := SocketEvent{Type: "connection_state", Data: ConnectionState{Connected: connected, Reason: reason}}
	data, err := json.Marshal(event)
	if err != nil {
		return
	}
	data = append(data, '\n')
	state.write(data)
}

func (a *App) broadcastMessage(msg *Message) {
	event := SocketEvent{Type: "message", Data: msg}
	data, err := json.Marshal(event)
	if err != nil {
		return
	}
	data = append(data, '\n')

	a.connMu.RLock()
	defer a.connMu.RUnlock()

	for _, state := range a.socketConns {
		state.write(data)
	}
}

func (a *App) broadcastCall(call *Call) {
	event := SocketEvent{Type: "call", Data: call}
	data, err := json.Marshal(event)
	if err != nil {
		return
	}
	data = append(data, '\n')

	a.connMu.RLock()
	defer a.connMu.RUnlock()

	for _, state := range a.socketConns {
		state.write(data)
	}
}

type EntriesData struct {
	Messages []Message `json:"messages"`
	Calls    []Call    `json:"calls"`
}

func (a *App) sendEntries(state *connState) error {
	rows, err := a.msgDB.Query("SELECT id, message_id, timestamp, chat_jid, chat_name, sender_jid, sender_name, is_group, is_muted, is_reply_to_me, message_type, text, media_file FROM messages ORDER BY timestamp")
	if err != nil {
		return err
	}
	defer rows.Close()

	var messages []Message
	for rows.Next() {
		var msg Message
		var isGroup, isMuted, isReplyToMe int
		if err := rows.Scan(&msg.ID, &msg.MessageID, &msg.Timestamp, &msg.ChatJID, &msg.ChatName, &msg.SenderJID, &msg.SenderName, &isGroup, &isMuted, &isReplyToMe, &msg.MessageType, &msg.Text, &msg.MediaFile); err != nil {
			return err
		}
		msg.IsGroup = isGroup != 0
		msg.IsMuted = isMuted != 0
		msg.IsReplyToMe = isReplyToMe != 0
		messages = append(messages, msg)
	}

	callRows, err := a.msgDB.Query("SELECT id, timestamp, call_id, caller_jid, caller_name, is_group, group_jid, group_name FROM calls ORDER BY timestamp")
	if err != nil {
		return err
	}
	defer callRows.Close()

	var calls []Call
	for callRows.Next() {
		var call Call
		var isGroup int
		if err := callRows.Scan(&call.ID, &call.Timestamp, &call.CallID, &call.CallerJID, &call.CallerName, &isGroup, &call.GroupJID, &call.GroupName); err != nil {
			return err
		}
		call.IsGroup = isGroup != 0
		calls = append(calls, call)
	}

	event := SocketEvent{Type: "entries", Data: EntriesData{Messages: messages, Calls: calls}}
	data, err := json.Marshal(event)
	if err != nil {
		return err
	}
	data = append(data, '\n')
	return state.write(data)
}

func (a *App) sendMessage(chatJID string, text string) error {
	jid, err := types.ParseJID(chatJID)
	if err != nil {
		return fmt.Errorf("invalid JID: %w", err)
	}

	msg := &waE2E.Message{
		Conversation: proto.String(text),
	}

	_, err = a.client.SendMessage(a.ctx, jid, msg)
	if err != nil {
		return fmt.Errorf("send failed: %w", err)
	}

	fmt.Printf("Sent message to %s\n", chatJID)
	return nil
}

func (a *App) replyToMessage(chatJID string, messageID string, senderJID string, text string) error {
	jid, err := types.ParseJID(chatJID)
	if err != nil {
		return fmt.Errorf("invalid chat JID: %w", err)
	}

	msg := &waE2E.Message{
		ExtendedTextMessage: &waE2E.ExtendedTextMessage{
			Text: proto.String(text),
			ContextInfo: &waE2E.ContextInfo{
				StanzaID:    proto.String(messageID),
				Participant: proto.String(senderJID),
			},
		},
	}

	_, err = a.client.SendMessage(a.ctx, jid, msg)
	if err != nil {
		return fmt.Errorf("reply failed: %w", err)
	}

	fmt.Printf("Replied to message %s in %s\n", messageID, chatJID)
	return nil
}

func (a *App) loginWithQR() error {
	qrChan, _ := a.client.GetQRChannel(a.ctx)
	if err := a.client.Connect(); err != nil {
		return err
	}

	for evt := range qrChan {
		if evt.Event == "code" {
			fmt.Println("Scan this QR code to login:")
			qrterminal.GenerateHalfBlock(evt.Code, qrterminal.L, os.Stdout)
		} else if evt.Event == "success" {
			fmt.Println("Login successful")
		} else {
			panic(fmt.Sprintf("Login failed: %s", evt.Event))
		}
	}
	return nil
}

func (a *App) handleEvent(evt interface{}) {
	if pd, ok := evt.(events.PermanentDisconnect); ok {
		eventType := reflect.TypeOf(evt).String()
		desc := pd.PermanentDisconnectDescription()
		fmt.Fprintf(os.Stderr, "Permanent WhatsApp failure (%s): %s\n", eventType, desc)
		a.setWAState(false, desc)
		os.Exit(permanentFailureExitCode)
	}

	switch v := evt.(type) {
	case *events.Message:
		a.handleMessage(v)
	case *events.CallOffer:
		a.handleCallOffer(v)
	case *events.CallOfferNotice:
		a.handleCallOfferNotice(v)
	case *events.Connected:
		fmt.Println("Connected to WhatsApp")
		a.setWAState(true, "")
	case *events.Disconnected:
		fmt.Println("Disconnected from WhatsApp")
		a.setWAState(false, "disconnected")
	case *events.StreamError:
		fmt.Fprintf(os.Stderr, "WhatsApp stream error: %s\n", v.Code)
		a.setWAState(false, fmt.Sprintf("stream error: %s", v.Code))
	}
}

func buildInsertParams(record interface{}) (columns []string, placeholders []string, values []interface{}) {
	v := reflect.ValueOf(record)
	if v.Kind() == reflect.Ptr {
		v = v.Elem()
	}
	t := v.Type()

	for i := 0; i < t.NumField(); i++ {
		field := t.Field(i)
		jsonTag := field.Tag.Get("json")
		if jsonTag == "" || jsonTag == "id" {
			continue
		}
		columns = append(columns, jsonTag)
		placeholders = append(placeholders, "?")
		values = append(values, v.Field(i).Interface())
	}
	return
}
