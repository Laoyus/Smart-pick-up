// wifi_network_test/wifi_network_test.ino
// ─────────────────────────────────────────────────────────────────────────────
// WiFi Network Connectivity Test — ESP32 #2 side
// Team Decepticons@123 · Caterpillar Tech Challenge 2026
//
// PURPOSE:
//   Prove that ESP32 #2 (the Arduino-side relay) can reach this laptop over
//   the WiFi network hosted by ESP32 #1.  NO Arduino involved — pure network test.
//
// WHAT IT DOES:
//   1. ESP32 joins ESP32 #1's WiFi AP as a station.
//   2. Scans 192.168.4.2–.10 on port 5000 to auto-find the laptop running app.py.
//   3. Opens a WebSocket to ws://[laptop-ip]:5000/ws_esp.
//   4. Sends  PING:<seq>  every second.
//   5. Expects PONG:<seq> back (app.py / laptop_test.py handles this already).
//   6. Prints RTT, TX/RX counts, and miss count to Serial Monitor (115200).
//
// LAPTOP SIDE (no changes needed):
//   Just run:  python app.py
//   The PING → PONG handler was already added to /ws_esp.
//   Or run the minimal:  python arduino_bridge/wifi_link_test/laptop_test.py
//
// SETUP:
//   - Flash this to ESP32 #2 (the one near the Arduino / other laptop).
//   - Open Serial Monitor at 115200 baud on that ESP32's USB port.
//   - Watch for "LINK UP" and the RTT numbers streaming in.
//
// WiFi credentials — must match ESP32 #1's AP config
#define WIFI_SSID  "MyESPNetwork"
#define WIFI_PASS  "12345678"

// Flask server port (must match app.py)
#define LAPTOP_PORT  5000
#define LAPTOP_PATH  "/ws_esp"

// How often to send a PING (ms)
#define PING_INTERVAL_MS  1000

// PONG timeout — counts as a miss if no reply in this window (ms)
#define PONG_TIMEOUT_MS   3000

// IP scan range: tries 192.168.4.START to 192.168.4.END to find the laptop
#define SCAN_START  2
#define SCAN_END   10
// ─────────────────────────────────────────────────────────────────────────────

#include <WiFi.h>
#include <WebSocketsClient.h>   // arduinoWebSockets by Markus Sattler

WebSocketsClient ws;

// ── State ─────────────────────────────────────────────────────────────────────
bool    wsConnected  = false;
int     txCount      = 0;
int     rxCount      = 0;
int     missCount    = 0;
int     txSeq        = 0;
int     rxSeq        = -1;

unsigned long lastPingMs     = 0;
unsigned long pingSentMs     = 0;   // millis() when last PING was sent (for RTT)
bool          waitingForPong = false;
int           lastRTT        = -1;  // ms, -1 = not measured yet

// Onboard LED
#define LED_PIN 2

// ─────────────────────────────────────────────────────────────────────────────
// WebSocket event handler
// ─────────────────────────────────────────────────────────────────────────────

void onWsEvent(WStype_t type, uint8_t* payload, size_t length) {
    switch (type) {

        case WStype_CONNECTED:
            wsConnected = true;
            digitalWrite(LED_PIN, HIGH);
            Serial.println("\n[WS] ✓ Connected to laptop Flask server — LINK UP");
            Serial.println("[WS] Sending PINGs every second. Watch for PONGs...\n");
            printHeader();
            break;

        case WStype_DISCONNECTED:
            wsConnected = false;
            digitalWrite(LED_PIN, LOW);
            Serial.println("\n[WS] Disconnected — retrying...");
            break;

        case WStype_TEXT: {
            String msg = String((char*)payload, length);

            // app.py wraps its response in >c...< when sending through the relay.
            // In a direct WebSocket test (laptop_test.py), it sends plain PONG:N.
            // Handle both formats.
            if (msg.startsWith(">c") && msg.endsWith("<")) {
                msg = msg.substring(2, msg.length() - 1);
            }

            if (msg.startsWith("PONG:")) {
                int pongSeq = msg.substring(5).toInt();
                unsigned long now = millis();

                rxCount++;
                rxSeq = pongSeq;
                lastRTT = (int)(now - pingSentMs);
                waitingForPong = false;

                bool matched = (pongSeq == ((txSeq - 1 + 10000) % 10000));
                printStats(matched);
            }

            // TEST_ACK from laptop — just acknowledge
            if (msg == "TEST_ACK") {
                Serial.println("[WS] Laptop is in test mode (TEST_ACK received).");
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

// ─────────────────────────────────────────────────────────────────────────────
// Find laptop: TCP probe each candidate IP until port 5000 responds
// ─────────────────────────────────────────────────────────────────────────────

IPAddress findLaptop() {
    Serial.println("[Scan] Searching for laptop on 192.168.4.x:5000 ...");
    for (int i = SCAN_START; i <= SCAN_END; i++) {
        IPAddress candidate(192, 168, 4, i);
        WiFiClient probe;
        probe.setTimeout(400);
        Serial.print("[Scan]   192.168.4.");
        Serial.print(i);
        Serial.print(" ... ");
        if (probe.connect(candidate, LAPTOP_PORT)) {
            probe.stop();
            Serial.println("FOUND ✓");
            return candidate;
        }
        Serial.println("no response");
    }
    return IPAddress(0, 0, 0, 0);   // not found
}

// ─────────────────────────────────────────────────────────────────────────────
// Print helpers
// ─────────────────────────────────────────────────────────────────────────────

void printHeader() {
    Serial.println("──────────────────────────────────────────────────────────");
    Serial.println("  TX(sent)  RX(recv)  MISS  RTT(ms)  SEQ match?");
    Serial.println("──────────────────────────────────────────────────────────");
}

void printStats(bool seqMatched) {
    char buf[80];
    snprintf(buf, sizeof(buf),
        "  %-9d %-9d %-5d %-8d %s",
        txCount, rxCount, missCount,
        lastRTT >= 0 ? lastRTT : 0,
        seqMatched ? "OK" : "MISMATCH");
    Serial.println(buf);
}

// ─────────────────────────────────────────────────────────────────────────────
// setup
// ─────────────────────────────────────────────────────────────────────────────

void setup() {
    Serial.begin(115200);
    delay(500);
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);

    Serial.println("=======================================================");
    Serial.println("  WiFi Network Test — ESP32 #2");
    Serial.println("=======================================================");

    // ── Connect to ESP32 #1's AP ──────────────────────────────────────────
    Serial.print("[WiFi] Connecting to AP: ");
    Serial.println(WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);

    // Blink while connecting
    int dots = 0;
    while (WiFi.status() != WL_CONNECTED) {
        digitalWrite(LED_PIN, dots % 2);
        delay(300);
        Serial.print(".");
        dots++;
        if (dots > 40) {
            Serial.println("\n[WiFi] Still waiting — is ESP32 #1 AP running?");
            dots = 0;
        }
    }
    digitalWrite(LED_PIN, LOW);

    Serial.println("\n[WiFi] Connected!");
    Serial.print("[WiFi] This ESP32's IP: ");
    Serial.println(WiFi.localIP());
    Serial.print("[WiFi] RSSI: ");
    Serial.print(WiFi.RSSI());
    Serial.println(" dBm");
    Serial.println();

    // ── Find the laptop running app.py ────────────────────────────────────
    IPAddress laptopIP = findLaptop();
    if (laptopIP == IPAddress(0, 0, 0, 0)) {
        Serial.println("[ERROR] Laptop not found! Is app.py running?");
        Serial.println("        Retrying in 5 seconds...");
        delay(5000);
        ESP.restart();
    }

    Serial.print("[Laptop] Flask server found at: ");
    Serial.println(laptopIP);
    Serial.println();

    // ── Open WebSocket to laptop ──────────────────────────────────────────
    ws.begin(laptopIP.toString().c_str(), LAPTOP_PORT, LAPTOP_PATH);
    ws.onEvent(onWsEvent);
    ws.setReconnectInterval(3000);

    lastPingMs = millis();
}

// ─────────────────────────────────────────────────────────────────────────────
// loop
// ─────────────────────────────────────────────────────────────────────────────

void loop() {
    ws.loop();

    unsigned long now = millis();

    if (!wsConnected) return;

    // ── Send PING ─────────────────────────────────────────────────────────
    if (now - lastPingMs >= PING_INTERVAL_MS) {
        lastPingMs = now;

        // Previous PING timed out?
        if (waitingForPong) {
            missCount++;
            waitingForPong = false;
            Serial.println("  [MISS] No PONG received in time");
        }

        // Send new PING as plain text (app.py strips >t...< framing itself,
        // and the relay framing is only for the Arduino→Laptop path.
        // From the ESP32 side in test mode we send plain text directly.)
        String ping = "PING:" + String(txSeq);
        ws.sendTXT(ping);

        pingSentMs    = now;
        waitingForPong = true;
        txCount++;
        txSeq = (txSeq + 1) % 10000;
    }

    // ── PONG timeout check ────────────────────────────────────────────────
    if (waitingForPong && (now - pingSentMs >= PONG_TIMEOUT_MS)) {
        missCount++;
        waitingForPong = false;
        Serial.println("  [TIMEOUT] PONG not received — miss counted");
    }
}
