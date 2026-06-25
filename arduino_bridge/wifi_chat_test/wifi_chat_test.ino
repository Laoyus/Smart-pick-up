// wifi_chat_test/wifi_chat_test.ino  —  Flash this to ESP32 #2
// ─────────────────────────────────────────────────────────────────────────────
// Two-way WiFi chat test between this ESP32 and the laptop running chat_server.py
//
// What happens:
//   You type in THIS Serial Monitor  → message goes over WiFi → laptop terminal
//   Laptop types in their terminal   → message comes over WiFi → shows here
//
// Setup:
//   1. Flash this to ESP32 #2.
//   2. On the OTHER laptop, open Serial Monitor at 115200 baud.
//   3. On YOUR laptop, run:  python arduino_bridge/wifi_chat_test/chat_server.py
//   4. Both sides can now type and see each other's messages.
//
// Libraries needed (Arduino Library Manager):
//   • Board:   esp32 by Espressif Systems
//   • Library: WebSockets by Markus Sattler
// ─────────────────────────────────────────────────────────────────────────────

#include <WiFi.h>
#include <WebSocketsClient.h>

// ── Config ────────────────────────────────────────────────────────────────────
#define WIFI_SSID  "MyESPNetwork"
#define WIFI_PASS  "12345678"

#define SERVER_PORT  5000
#define SERVER_PATH  "/ws_chat"

#define LED_PIN 2   // onboard LED (GPIO2 on most ESP32 dev boards)

// IP scan range on the AP subnet to find the laptop automatically
#define SCAN_START 2
#define SCAN_END   10
// ─────────────────────────────────────────────────────────────────────────────

WebSocketsClient ws;
bool connected = false;
String serialBuf = "";

// ── WebSocket events ──────────────────────────────────────────────────────────

void onWsEvent(WStype_t type, uint8_t* payload, size_t length) {
    if (type == WStype_CONNECTED) {
        connected = true;
        digitalWrite(LED_PIN, HIGH);
        Serial.println();
        Serial.println("══════════════════════════════════════");
        Serial.println("  LINK UP — connected to laptop");
        Serial.println("  Type here and press Enter to send.");
        Serial.println("══════════════════════════════════════");
    }
    else if (type == WStype_DISCONNECTED) {
        connected = false;
        digitalWrite(LED_PIN, LOW);
        Serial.println("[WiFi] Disconnected from laptop — retrying...");
    }
    else if (type == WStype_TEXT) {
        // Message received from laptop — print it
        String msg = String((char*)payload, length);
        Serial.println();
        Serial.print("[LAPTOP] ");
        Serial.println(msg);
    }
}

// ── Find laptop: probe each IP on port 5000 ───────────────────────────────────

IPAddress findLaptop() {
    Serial.println("[Scan] Looking for laptop on 192.168.4.x ...");
    for (int i = SCAN_START; i <= SCAN_END; i++) {
        IPAddress ip(192, 168, 4, i);
        WiFiClient probe;
        probe.setTimeout(400);
        if (probe.connect(ip, SERVER_PORT)) {
            probe.stop();
            Serial.print("[Scan] Found at 192.168.4.");
            Serial.println(i);
            return ip;
        }
    }
    return IPAddress(0, 0, 0, 0);
}

// ── setup ─────────────────────────────────────────────────────────────────────

void setup() {
    Serial.begin(115200);
    delay(400);
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);

    Serial.println("\n══════════════════════════════════════");
    Serial.println("  WiFi Chat Test — ESP32 #2");
    Serial.println("══════════════════════════════════════");

    // Connect to WiFi AP
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    Serial.print("[WiFi] Connecting");
    while (WiFi.status() != WL_CONNECTED) {
        delay(300);
        Serial.print(".");
    }
    Serial.println(" OK");
    Serial.print("[WiFi] IP: "); Serial.println(WiFi.localIP());
    Serial.print("[WiFi] RSSI: "); Serial.print(WiFi.RSSI()); Serial.println(" dBm");

    // Find laptop
    IPAddress laptopIP = findLaptop();
    if (laptopIP == IPAddress(0, 0, 0, 0)) {
        Serial.println("[ERROR] Laptop not found. Is chat_server.py running?");
        Serial.println("Rebooting in 5 s...");
        delay(5000);
        ESP.restart();
    }

    // Connect WebSocket
    Serial.print("[WS] Connecting to ws://");
    Serial.print(laptopIP); Serial.print(":"); Serial.print(SERVER_PORT);
    Serial.println(SERVER_PATH);

    ws.begin(laptopIP.toString().c_str(), SERVER_PORT, SERVER_PATH);
    ws.onEvent(onWsEvent);
    ws.setReconnectInterval(3000);
}

// ── loop ──────────────────────────────────────────────────────────────────────

void loop() {
    ws.loop();

    // Read from Serial Monitor — send to laptop when Enter pressed
    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\n') {
            serialBuf.trim();
            if (serialBuf.length() > 0) {
                if (connected) {
                    ws.sendTXT(serialBuf);
                    Serial.print("[YOU] ");
                    Serial.println(serialBuf);
                } else {
                    Serial.println("[not connected — message dropped]");
                }
            }
            serialBuf = "";
        } else if (c != '\r') {
            serialBuf += c;
        }
    }
}
