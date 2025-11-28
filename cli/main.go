package main

import (
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
	"github.com/mdp/qrterminal/v3"
	_ "github.com/mattn/go-sqlite3"
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
)

const (
	socketPath  = "/tmp/wacli.sock"
	maxMessages = 200
	trimToCount = 150
)

type Config struct {
	IncludeStatusMessages bool
	IncludeMutedMessages  bool
}

type App struct {
	client      *whatsmeow.Client
	ctx         context.Context
	msgDB       *sql.DB
	config      Config
	socketConns map[net.Conn]struct{}
	connMu      sync.RWMutex
}

func loadConfig() Config {
	godotenv.Load()

	return Config{
		IncludeStatusMessages: os.Getenv("INCLUDE_STATUS_MESSAGES") == "true",
		IncludeMutedMessages:  os.Getenv("INCLUDE_MUTED_MESSAGES") == "true",
	}
}

func main() {
	config := loadConfig()
	ctx := context.Background()

	msgDB, err := initMessageDB()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to init message database: %v\n", err)
		os.Exit(1)
	}
	defer msgDB.Close()

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
		config:      config,
		socketConns: make(map[net.Conn]struct{}),
	}

	client.AddEventHandler(app.handleEvent)

	listener, err := app.startSocketServer()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to start socket server: %v\n", err)
		os.Exit(1)
	}
	defer listener.Close()
	defer os.Remove(socketPath)

	if client.Store.ID == nil {
		if err := app.loginWithQR(); err != nil {
			fmt.Fprintf(os.Stderr, "Login failed: %v\n", err)
			os.Exit(1)
		}
	} else {
		if err := client.Connect(); err != nil {
			fmt.Fprintf(os.Stderr, "Failed to connect: %v\n", err)
			os.Exit(1)
		}
	}

	fmt.Println("Connected. Watching for messages...")
	fmt.Printf("Socket server listening on %s\n", socketPath)

	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)
	<-sigChan

	client.Disconnect()
	fmt.Println("\nDisconnected.")
}

func initMessageDB() (*sql.DB, error) {
	db, err := sql.Open("sqlite3", "file:messages.db?_foreign_keys=on")
	if err != nil {
		return nil, err
	}

	_, err = db.Exec(`
		CREATE TABLE IF NOT EXISTS messages (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			timestamp INTEGER NOT NULL,
			chat_jid TEXT NOT NULL,
			chat_name TEXT NOT NULL,
			sender_jid TEXT NOT NULL,
			sender_name TEXT NOT NULL,
			is_group INTEGER NOT NULL,
			is_muted INTEGER NOT NULL,
			is_reply_to_me INTEGER NOT NULL,
			text TEXT NOT NULL
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

	return db, nil
}

func (a *App) startSocketServer() (net.Listener, error) {
	os.Remove(socketPath)
	listener, err := net.Listen("unix", socketPath)
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

func (a *App) handleSocketConn(conn net.Conn) {
	a.connMu.Lock()
	a.socketConns[conn] = struct{}{}
	a.connMu.Unlock()

	defer func() {
		a.connMu.Lock()
		delete(a.socketConns, conn)
		a.connMu.Unlock()
		conn.Close()
	}()

	buf := make([]byte, 1024)
	for {
		_, err := conn.Read(buf)
		if err != nil {
			return
		}
	}
}

func (a *App) broadcastMessage(msg *Message) {
	data, err := json.Marshal(msg)
	if err != nil {
		return
	}
	data = append(data, '\n')

	a.connMu.RLock()
	defer a.connMu.RUnlock()

	for conn := range a.socketConns {
		conn.Write(data)
	}
}

func (a *App) broadcastCall(call *Call) {
	data, err := json.Marshal(call)
	if err != nil {
		return
	}
	data = append(data, '\n')

	a.connMu.RLock()
	defer a.connMu.RUnlock()

	for conn := range a.socketConns {
		conn.Write(data)
	}
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
		} else {
			fmt.Printf("Login event: %s\n", evt.Event)
		}
	}
	return nil
}

func (a *App) handleEvent(evt interface{}) {
	switch v := evt.(type) {
	case *events.Message:
		a.handleMessage(v)
	case *events.CallOffer:
		a.handleCallOffer(v)
	case *events.CallOfferNotice:
		a.handleCallOfferNotice(v)
	case *events.Connected:
		fmt.Println("Connected to WhatsApp")
	case *events.Disconnected:
		fmt.Println("Disconnected from WhatsApp")
	case *events.LoggedOut:
		fmt.Println("Logged out from WhatsApp")
		os.Exit(0)
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
