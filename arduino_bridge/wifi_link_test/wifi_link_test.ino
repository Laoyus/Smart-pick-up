// wifi_link_test/wifi_link_test.ino
// ─────────────────────────────────────────────────────────────────────────────
// WiFi Bridge Round-Trip Connectivity Test
// Smart Assistive Part Pick System
//
// Tests the FULL path in both directions:
//
//   Arduino ─Serial─► ESP32 ─WiFi─► Laptop  (Arduino→Laptop)
//   Arduino ◄─Serial─ ESP32 ◄─WiFi─ Laptop  (Laptop→Arduino)
//
// SETUP (do this before flashing):
//   1. Flash this sketch to the Arduino Mega.
//   2. Power the ESP32 relay — it starts its WiFi AP (192.168.4.1).
//   3. Connect laptop to the ESP32's WiFi network.
//   4. Run:  python app.py
//      app.py auto-responds to PING:N with PONG:N.
//      Or run the standalone:  python arduino_bridge/wifi_link_test/laptop_test.py
//
// PROTOCOL (lines sent over Serial/WiFi, ESP32 strips the >t...< / >c...< framing):
//   Arduino → Laptop:  PING:<seq>        (every 1 s, seq wraps 0–999)
//   Laptop → Arduino:  PONG:<seq>        (laptop echoes seq immediately)
//   Arduino → Laptop:  TEST_HELLO        (once on boot)
//   Laptop → Arduino:  TEST_ACK          (server confirms test mode; optional)
//
// VISUAL FEEDBACK (uses the same hardware as the main trolley sketch):
//
//   NeoPixel 0  — Link status
//                   Slow WHITE blink  →  Waiting (no PONG yet)
//                   Solid GREEN       →  Link OK  (PONG within 3 s)
//                   Solid RED         →  Timeout  (no PONG for > 5 s)
//   NeoPixel 1  — Last PONG result
//                   GREEN pulse       →  Sequence matched (clean round-trip)
//                   ORANGE pulse      →  Sequence mismatch (packets reordered)
//                   RED dim           →  No PONG received for last PING
//   NeoPixels 2–5 — Heartbeat wave (cyan, cycles continuously = Arduino alive)
//
//   TM1637 Display 0 — TX  (PINGs sent, 0-9999)
//   TM1637 Display 1 — RX  (PONGs received, 0-9999)
//   TM1637 Display 2 — MSS (missed PINGs — PONG timeout, 0-9999)
//   TM1637 Display 3 — RTT (last round-trip time in ms, 0-9999)
//
//   Buzzer — 2 × short beep on first successful PONG
//           3 × long  buzz when connection is lost after being established
//
// ─────────────────────────────────────────────────────────────────────────────

#include <TM1637Display.h>
#include <Adafruit_NeoPixel.h>

// ── Pins — identical to arduino_bridge.ino ───────────────────────────────────
const int BUZZER_PIN = 40;

const int TM_CLK[6] = {30, 32, 34, 36, 38, 42};
const int TM_DIO[6] = {31, 33, 35, 37, 39, 43};

#define NEO_LEDS   8
#define NEO_BRIGHT 100

Adafruit_NeoPixel ns0(NEO_LEDS, 44, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel ns1(NEO_LEDS, 45, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel ns2(NEO_LEDS, 46, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel ns3(NEO_LEDS, 47, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel ns4(NEO_LEDS, 48, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel ns5(NEO_LEDS, 49, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel *neo[6] = {&ns0, &ns1, &ns2, &ns3, &ns4, &ns5};

TM1637Display disp[6] = {
    TM1637Display(30, 31),
    TM1637Display(32, 33),
    TM1637Display(34, 35),
    TM1637Display(36, 37),
    TM1637Display(38, 39),
    TM1637Display(42, 43),
};

// ── Test state ────────────────────────────────────────────────────────────────

// How often to send a PING
const unsigned long PING_INTERVAL_MS = 1000;

// How long to wait for a PONG before counting a miss
const unsigned long PONG_TIMEOUT_MS = 3000;

// How long with no PONG before declaring link lost
const unsigned long LINK_LOST_MS = 5000;

int  txCount   = 0;    // PINGs sent
int  rxCount   = 0;    // PONGs received (any)
int  missCount = 0;    // PINGs that timed out with no matching PONG
int  lastRTT   = 0;    // last measured round-trip time in ms

int  txSeq  = 0;       // sequence number of next PING to send
int  rxSeq  = -1;      // sequence number of most recent PONG received

// Timing
unsigned long lastPingMs      = 0;   // when the last PING was sent
unsigned long lastPongMs      = 0;   // when the last PONG arrived
bool          waitingForPong  = false;
bool          firstPongGot    = false;
bool          linkWasUp       = false;

// 7-segment for splash "- - - -"
const uint8_t SEG_DASHES[4] = {SEG_G, SEG_G, SEG_G, SEG_G};

// Serial input buffer
String serialBuf = "";

// ── Heartbeat wave (bins 2–5, cyan) ──────────────────────────────────────────
int  wavePos      = 0;
unsigned long lastWaveMs = 0;
const unsigned long WAVE_MS = 120;

// ── Neo0 blink for WAITING state ─────────────────────────────────────────────
bool          blinkOn   = false;
unsigned long lastBlinkMs = 0;
const unsigned long BLINK_MS = 400;

// ── Pulse state for Neo1 (result indicator) ───────────────────────────────────
unsigned long pulseClearMs = 0;   // when to clear the neo1 pulse (0 = already clear)

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

void setAllNeo(uint32_t color) {
    for (int i = 0; i < 6; i++) {
        neo[i]->fill(color);
        neo[i]->show();
    }
}

void buzzerBeep(int count, int durationMs, int pauseMs) {
    for (int i = 0; i < count; i++) {
        tone(BUZZER_PIN, 2500, durationMs);
        delay(durationMs + pauseMs);
    }
}

// Flash all NeoPixels a colour N times (blocking splash — only called at boot)
void splashFlash(uint32_t color, int times, int onMs, int offMs) {
    for (int t = 0; t < times; t++) {
        setAllNeo(color);
        delay(onMs);
        setAllNeo(0);
        delay(offMs);
    }
}

void updateDisplays() {
    disp[0].showNumberDec(txCount   % 10000, false);
    disp[1].showNumberDec(rxCount   % 10000, false);
    disp[2].showNumberDec(missCount % 10000, false);
    disp[3].showNumberDec(lastRTT   % 10000, false);
}

// ─────────────────────────────────────────────────────────────────────────────
// setup
// ─────────────────────────────────────────────────────────────────────────────

void setup() {
    Serial.begin(9600);

    pinMode(BUZZER_PIN, OUTPUT);

    for (int i = 0; i < 6; i++) {
        disp[i].setBrightness(7);
        disp[i].setSegments(SEG_DASHES);
        neo[i]->begin();
        neo[i]->setBrightness(NEO_BRIGHT);
        neo[i]->clear();
        neo[i]->show();
    }

    // Splash: white sweep across all bins to confirm NeoPixels work
    for (int i = 0; i < 6; i++) {
        neo[i]->fill(neo[i]->Color(255, 255, 255));
        neo[i]->show();
        delay(120);
        neo[i]->clear();
        neo[i]->show();
    }

    // Label displays with startup values
    updateDisplays();

    // Tell the laptop the test sketch is live
    Serial.println("TEST_HELLO");

    lastPingMs = millis();
}

// ─────────────────────────────────────────────────────────────────────────────
// loop
// ─────────────────────────────────────────────────────────────────────────────

void loop() {
    unsigned long now = millis();

    // ── 1. Send PING ─────────────────────────────────────────────────────────
    if (now - lastPingMs >= PING_INTERVAL_MS) {
        lastPingMs = now;

        // If the previous PING got no response, count it as a miss
        if (waitingForPong && (now - lastPingMs + PING_INTERVAL_MS > PONG_TIMEOUT_MS)) {
            missCount++;
            updateDisplays();
        }

        Serial.print("PING:");
        Serial.println(txSeq);

        waitingForPong = true;
        lastPingMs     = now;    // record actual send time for RTT
        txCount++;
        txSeq = (txSeq + 1) % 1000;

        updateDisplays();
    }

    // ── 2. Check PONG timeout (miss detection) ────────────────────────────────
    if (waitingForPong && (now - lastPingMs >= PONG_TIMEOUT_MS)) {
        waitingForPong = false;
        missCount++;
        updateDisplays();

        // Red dim on Neo1: missed PONG
        neo[1]->fill(neo[1]->Color(80, 0, 0));
        neo[1]->show();
        pulseClearMs = now + 800;
    }

    // ── 3. Clear Neo1 pulse after delay ──────────────────────────────────────
    if (pulseClearMs > 0 && now >= pulseClearMs) {
        neo[1]->clear();
        neo[1]->show();
        pulseClearMs = 0;
    }

    // ── 4. Read incoming serial (PONG:N or TEST_ACK) ─────────────────────────
    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\n') {
            serialBuf.trim();
            if (serialBuf.length() > 0) {
                handleIncoming(serialBuf, now);
            }
            serialBuf = "";
        } else if (c != '\r') {
            serialBuf += c;
        }
    }

    // ── 5. Neo0 — link status indicator ──────────────────────────────────────
    bool linkOK = (lastPongMs > 0) && (now - lastPongMs < LINK_LOST_MS);

    if (!firstPongGot) {
        // WAITING: slow white blink
        if (now - lastBlinkMs >= BLINK_MS) {
            blinkOn = !blinkOn;
            lastBlinkMs = now;
            if (blinkOn) {
                neo[0]->fill(neo[0]->Color(200, 200, 200));
            } else {
                neo[0]->clear();
            }
            neo[0]->show();
        }
    } else if (linkOK) {
        // CONNECTED: solid green
        neo[0]->fill(neo[0]->Color(0, 220, 0));
        neo[0]->show();

        // Alert if link was previously lost
        if (!linkWasUp) {
            linkWasUp = true;
        }
    } else {
        // TIMEOUT: solid red
        neo[0]->fill(neo[0]->Color(220, 0, 0));
        neo[0]->show();

        // Buzzer alert once when link drops
        if (linkWasUp) {
            linkWasUp = false;
            buzzerBeep(3, 300, 150);
        }
    }

    // ── 6. Heartbeat wave on bins 2–5 (cyan) ─────────────────────────────────
    if (now - lastWaveMs >= WAVE_MS) {
        lastWaveMs = now;
        for (int i = 2; i <= 5; i++) {
            neo[i]->clear();
        }
        // Light the active wave position
        int waveStrip = 2 + (wavePos % 4);
        neo[waveStrip]->fill(neo[waveStrip]->Color(0, 180, 180));
        for (int i = 2; i <= 5; i++) {
            neo[i]->show();
        }
        wavePos = (wavePos + 1) % 4;
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// handleIncoming — called for each complete line received from the ESP32 relay
// ─────────────────────────────────────────────────────────────────────────────

void handleIncoming(const String &line, unsigned long now) {

    // PONG:<seq> — response to our PING
    if (line.startsWith("PONG:")) {
        int pongSeq = line.substring(5).toInt();

        rxCount++;
        lastPongMs = now;

        // RTT = time since the PING for this sequence was sent.
        // lastPingMs holds when the most recent PING was sent.
        // This is accurate when PONG arrives before the next PING fires.
        lastRTT = (int)(now - lastPingMs);

        // Check if the sequence number matches the last ping we sent
        int expectedSeq = (txSeq - 1 + 1000) % 1000;   // seq of most recent PING
        bool matched = (pongSeq == expectedSeq);

        waitingForPong = false;
        updateDisplays();

        rxSeq = pongSeq;

        // Neo1 pulse: green = match, orange = mismatch
        if (matched) {
            neo[1]->fill(neo[1]->Color(0, 255, 0));
        } else {
            neo[1]->fill(neo[1]->Color(255, 140, 0));
        }
        neo[1]->show();
        pulseClearMs = now + 600;

        // First-ever PONG: buzzer celebration + update state
        if (!firstPongGot) {
            firstPongGot = true;
            linkWasUp    = true;
            buzzerBeep(2, 80, 80);
        }

        return;
    }

    // TEST_ACK — laptop confirmed test mode
    if (line == "TEST_ACK") {
        Serial.println("TEST_ACK_RECEIVED");
        return;
    }

    // Ignore anything else (stray lines from previous sketch run, etc.)
}
