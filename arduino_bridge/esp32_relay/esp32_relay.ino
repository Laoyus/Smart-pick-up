// esp32_relay/esp32_relay.ino  —  Flash this to ESP32 #2 (the one wired to Arduino)
// ─────────────────────────────────────────────────────────────────────────────
// Smart Assistive Part Pick — Production WiFi Relay
// Team Decepticons@123 · Caterpillar Tech Challenge 2026
//
// Full data path:
//
//   Arduino Mega ──Serial0──► ESP32 #2 ──WiFi──► app.py (/ws_esp)
//   Arduino Mega ◄─Serial0── ESP32 #2 ◄─WiFi── app.py
//
//   ESP32 #1 (separate board) hosts the WiFi AP — this ESP32 joins it as a client.
//
// Frame format (same protocol app.py already handles):
//   Arduino → Laptop:  lines from Serial0 wrapped as  >t<line><
//   Laptop → Arduino:  frames from app.py in           >c<cmd><  stripped → Serial0
//
// ─── Wiring ──────────────────────────────────────────────────────────────────
//   ESP32 GPIO16 (RX2) ← Arduino Mega Pin 16 (TX2)  via voltage divider*
//   ESP32 GPIO17 (TX2) → Arduino Mega Pin 17 (RX2)  direct is fine
//   GND shared (already done via VCC/GND connection)
//
//   * Voltage divider on TX2→RX2 (Arduino 5V → ESP32 3.3V max):
//       Arduino TX2 ──[10kΩ]──┬──[20kΩ]── GND
//                              └── ESP32 GPIO16
//
// ─── Libraries needed (Arduino Library Manager) ──────────────────────────────
//   Board:   esp32 by Espressif Systems
//   Library: WebSockets by Markus Sattler
//
// ─── How to run ──────────────────────────────────────────────────────────────
//   1. Flash this sketch to ESP32 #2.
//   2. Wire ESP32 #2 to Arduino Mega (TX0/RX0 ↔ GPIO16/17).
//   3. Flash arduino_bridge.ino to Arduino Mega.
//   4. On your laptop (connected to ESP32 #1 WiFi), run:
//        SIMULATION_MODE=false python app.py
//   5. LED goes solid when the full link is up.
//
// ─── LED status (GPIO2) ──────────────────────────────────────────────────────
//   Slow blink  WiFi connecting to ESP32 #1 AP
//   Fast blink  WiFi connected, WebSocket connecting to app.py
//   Solid ON    Full link up — relaying data
//   Solid OFF   WebSocket lost, retrying
// ─────────────────────────────────────────────────────────────────────────────

#include <WiFi.h>
#include <WebSocketsClient.h>

// ── Config ────────────────────────────────────────────────────────────────────

// ESP32 #1 AP credentials (the board hosting the WiFi network)
#define WIFI_SSID "MyESPNetwork"
#define WIFI_PASS "12345678"

// app.py WebSocket endpoint
#define LAPTOP_PORT 5000
#define LAPTOP_PATH "/ws_esp"

// UART to Arduino Mega
#define MEGA_RX_PIN 16 // ESP32 RX2 ← Arduino TX0 (Pin 1)
#define MEGA_TX_PIN 17 // ESP32 TX2 → Arduino RX0 (Pin 0)
#define MEGA_BAUD 9600

// Laptop IP on the phone hotspot — update if your laptop's IP changes
#define LAPTOP_IP "192.168.4.3"

#define STATUS_LED 2 // onboard LED (GPIO2 on most ESP32 dev boards)

// ── State ─────────────────────────────────────────────────────────────────────

WebSocketsClient ws;
bool wsConnected = false;
String serialBuf = "";

unsigned long lastLedMs = 0;
bool ledOn = false;

// ── WebSocket event handler ───────────────────────────────────────────────────

void onWsEvent(WStype_t type, uint8_t *payload, size_t length)
{
    switch (type)
    {

    case WStype_CONNECTED:
        wsConnected = true;
        digitalWrite(STATUS_LED, HIGH);
        Serial.println("[WS] Connected to app.py — LINK UP");
        break;

    case WStype_DISCONNECTED:
        wsConnected = false;
        digitalWrite(STATUS_LED, LOW);
        Serial.println("[WS] Disconnected — retrying");
        Serial2.println("RESET"); // tell Arduino to clear all outputs immediately
        break;

    case WStype_TEXT:
    {
        // app.py sends commands wrapped as >c<cmd><
        // Strip wrapper and forward to Arduino Mega over Serial0
        String msg = String((char *)payload, length);
        if (msg.startsWith(">c") && msg.endsWith("<"))
        {
            String cmd = msg.substring(2, msg.length() - 1);
            Serial2.println(cmd);
            Serial.println("[→ Arduino] " + cmd);
        }
        break;
    }

    case WStype_ERROR:
        Serial.println("[WS] Error");
        break;

    default:
        break;
    }
}

// ── setup ─────────────────────────────────────────────────────────────────────

void setup()
{
    Serial.begin(115200);
    delay(400);
    Serial2.begin(MEGA_BAUD, SERIAL_8N1, MEGA_RX_PIN, MEGA_TX_PIN);
    pinMode(STATUS_LED, OUTPUT);
    digitalWrite(STATUS_LED, LOW);

    Serial.println("==============================================");
    Serial.println("  Smart Assistive Part Pick — ESP32 Relay");
    Serial.println("==============================================");

    // ── Join ESP32 #1's WiFi AP ──────────────────────────────────────────
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    Serial.print("[WiFi] Connecting to ");
    Serial.println(WIFI_SSID);

    while (WiFi.status() != WL_CONNECTED)
    {
        unsigned long now = millis();
        if (now - lastLedMs >= 500)
        {
            lastLedMs = now;
            ledOn = !ledOn;
            digitalWrite(STATUS_LED, ledOn);
        }
        delay(100);
    }
    digitalWrite(STATUS_LED, LOW);
    Serial.println("[WiFi] Connected!");
    Serial.print("[WiFi] This ESP IP: ");
    Serial.println(WiFi.localIP());
    Serial.print("[WiFi] RSSI: ");
    Serial.print(WiFi.RSSI());
    Serial.println(" dBm");

    // ── Connect to laptop ────────────────────────────────────────────────
    IPAddress laptopIP;
    laptopIP.fromString(LAPTOP_IP);
    Serial.print("[WS] Connecting to ws://");
    Serial.print(laptopIP);
    Serial.print(":");
    Serial.print(LAPTOP_PORT);
    Serial.println(LAPTOP_PATH);

    // ── Connect WebSocket ────────────────────────────────────────────────
    ws.begin(laptopIP.toString().c_str(), LAPTOP_PORT, LAPTOP_PATH);
    ws.onEvent(onWsEvent);
    ws.setReconnectInterval(3000);

    lastLedMs = millis();
}

// ── loop ──────────────────────────────────────────────────────────────────────

void loop()
{
    ws.loop();

    // Fast blink while connecting; solid once up (handled in event callbacks)
    if (!wsConnected)
    {
        unsigned long now = millis();
        if (now - lastLedMs >= 150)
        {
            lastLedMs = now;
            ledOn = !ledOn;
            digitalWrite(STATUS_LED, ledOn);
        }
    }

    // ── Arduino Mega → Laptop ─────────────────────────────────────────────
    // Read lines from Serial0 via Serial2, wrap in >t...< and send to app.py
    while (Serial2.available())
    {
        char c = (char)Serial2.read();
        if (c == '\n')
        {
            serialBuf.trim();
            if (serialBuf.length() > 0)
            {
                if (wsConnected)
                {
                    String frame = ">t" + serialBuf + "<";
                    ws.sendTXT(frame);
                    Serial.println("[→ Laptop] " + frame);
                }
                else
                {
                    Serial.println("[DROP, WS down] " + serialBuf);
                }
            }
            serialBuf = "";
        }
        else if (c != '\r')
        {
            serialBuf += c;
        }
    }
}
