// Smart Assistive Part Pick System — Arduino Full Bridge
// Industrial Assembly Aid Platform
//
// Hardware on this sketch:
//   6 × IR sensors              → Pins 22–27  (bin 2 skipped — hardware fault)
//   6 × HX711 load cell ADCs    → DOUT/SCK pairs below
//   6 × TM1637 4-digit displays → CLK/DIO pairs below  (bin 0 display fault — ignored)
//   6 × WS2812B NeoPixel strips → Data pins 44–49
//   1 × Active buzzer module    → Pin 40  (I/O pin of module)
//   1 × Emergency stop button   → Pin 41  (other terminal to GND)
//
// !! IMPORTANT !!
//   Buzzer moved from pin 8 to pin 40.
//   Pin 8 is now HX711 Bin 3 SCK — rewire buzzer to pin 40.
//
// Serial protocol (9600 baud):
//   Arduino → Laptop:
//     "READY\n"              on boot
//     "IR_TRIGGERED:N\n"     N = 0–5 (0-indexed), beam broken
//     "WEIGHT:N:grams\n"     N = 0–5, streamed continuously ~every 100ms per bin
//     "TARED:N\n"            confirmation after TARE command
//     "RAW_VAL:N:value\n"    raw ADC reading for calibration (after tare offset)
//
//   Laptop → Arduino:
//     "BUZZ:duration_ms\n"   play 1 kHz tone for duration_ms
//     "DISP:N:count\n"       show count on TM1637 display N (0-indexed)
//     "TARE:N\n"             re-zero load cell N
//     "RAW:N\n"              print raw ADC value (for calibration, see below)
//     "CAL:N:factor\n"       set calibration factor at runtime (temporary)
//
// ─── Wiring ──────────────────────────────────────────────────────────────────
//   IR Sensors  → 22(Bin0) 23(Bin1) 24(Bin2) 25(Bin3) 26(Bin4) 27(Bin5)
//   Buzzer      → Pin 40   (NOTE: NOT pin 8 — see above)
//
//   HX711:  Bin  DOUT  SCK
//            0     3    2
//            1     5    4
//            2     7    6
//            3     9    8
//            4    11   10
//            5    13   12
//
//   TM1637: Bin  CLK  DIO
//            0    30   31
//            1    32   33
//            2    34   35
//            3    36   37
//            4    38   39
//            5    42   43
//
// ─── Calibration ─────────────────────────────────────────────────────────────
//   Each load cell has its own sensitivity. CALIB[N] = raw_ADC_units / gram.
//   Default 420.0 is a rough guess for typical 5 kg load cells.
//   To calibrate bin N:
//     1. Power on (bins auto-tare on boot).
//     2. Send "RAW:N" via Serial Monitor — note the value printed (should be ~0).
//     3. Place a known weight (e.g. 500 g) on the load cell.
//     4. Send "RAW:N" again — note the new value R.
//     5. CALIB[N] = R / 500.0  → update the array below and re-flash.
//     6. Verify with "WEIGHT:N" — it should now read ~500.0 g.
// ─────────────────────────────────────────────────────────────────────────────

#include <HX711.h>
#include <TM1637Display.h>
#include <Adafruit_NeoPixel.h>

// ── Pin definitions ───────────────────────────────────────────────────────────
const int IR_PINS[6] = {22, 23, 24, 25, 26, 27};
const int BUZZER_PIN = 40; // active buzzer module I/O pin
const int BUTTON_PIN = 41; // emergency stop button (other terminal → GND)

const int HX_DOUT[6] = {3, 5, 7, 9, 11, 13};
const int HX_SCK[6] = {2, 4, 6, 8, 10, 12};

const int TM_CLK[6] = {28, 30, 32, 34, 36, 38};
const int TM_DIO[6] = {29, 31, 33, 35, 37, 39};

// ── Calibration (raw ADC units per gram) ─────────────────────────────────────
float CALIB[6] = {425.28, 456.175, 439.58, -394.01, -420.84, -443.8};

// ── Hardware objects ──────────────────────────────────────────────────────────
HX711 scale[6];

// TM1637 display objects — CLK/DIO must match TM_CLK/TM_DIO arrays above
TM1637Display disp[6] = {
    TM1637Display(28, 29),
    TM1637Display(30, 31),
    TM1637Display(32, 33),
    TM1637Display(34, 35),
    TM1637Display(36, 37),
    TM1637Display(38, 39),
};

// ── NeoPixel LED strips (one per bin, data pins 44–49) ───────────────────────
#define NEO_LEDS 8 // LEDs per strip
#define NEO_BRIGHT 120
Adafruit_NeoPixel ns0(NEO_LEDS, 44, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel ns1(NEO_LEDS, 45, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel ns2(NEO_LEDS, 46, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel ns3(NEO_LEDS, 47, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel ns4(NEO_LEDS, 48, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel ns5(NEO_LEDS, 49, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel *neo[6] = {&ns0, &ns1, &ns2, &ns3, &ns4, &ns5};

// ── IR state ──────────────────────────────────────────────────────────────────
bool lastState[6];
unsigned long lastTriggerMs[6];
const int DEBOUNCE_MS = 150;

// ── Emergency button state ────────────────────────────────────────────────────
bool lastBtnState = HIGH;
unsigned long lastBtnMs = 0;
const int BTN_DEBOUNCE = 300;

// ── Buzzer (non-blocking) ─────────────────────────────────────────────────────
// tone(pin, freq, duration) drives passive buzzers at full volume; also works
// on active buzzers (drives them at 2.5 kHz duty cycle instead of DC).
// tone() auto-stops after duration — no manual timer needed.

// ── Auto weight stream ────────────────────────────────────────────────────────
// Toggle with STREAM:1 / STREAM:0 from Serial Monitor.
// Prints all 6 bin weights every 2s — useful for standalone testing.
// Python polling works independently; these extra WEIGHT: lines are harmless.
bool streamOn = false;
unsigned long lastStreamMs = 0;
const unsigned long STREAM_INTERVAL = 2000;

// ── Display blink state (used as LED substitute) ──────────────────────────────
// When a bin is "active", its TM1637 blinks to draw the operator's attention.
bool blinkEnabled[6] = {false, false, false, false, false, false};
bool blinkVisible[6] = {true, true, true, true, true, true};
unsigned long lastBlinkMs[6] = {0, 0, 0, 0, 0, 0};
const unsigned long BLINK_MS = 400; // toggle every 400 ms

// 7-segment encodings for "Err " (SEG_x constants from TM1637Display.h)
// A=bit0 B=bit1 C=bit2 D=bit3 E=bit4 F=bit5 G=bit6
const uint8_t SEG_ERR[4] = {0x79, 0x50, 0x50, 0x00};        // E r r (blank)
const uint8_t SEG_DASHES[4] = {SEG_G, SEG_G, SEG_G, SEG_G}; // ----

// ── Serial buffer ─────────────────────────────────────────────────────────────
String serialBuf = "";

// ── Watchdog — detect Python disconnect ───────────────────────────────────────
// Python's weight polling sends a command every ~200ms.
// If nothing arrives for 5s, assume Python closed → clear all outputs.
unsigned long lastCmdMs = 0;
const unsigned long WATCHDOG_MS = 5000;

// ─────────────────────────────────────────────────────────────────────────────

void handleCommand(const String &cmd); // forward declaration

void setup()
{
    Serial.begin(115200); // USB — debug monitor only
    Serial2.begin(9600);  // pins 16(TX2)/17(RX2) → ESP32 relay

    // IR sensors — INPUT_PULLUP keeps line HIGH when beam is intact
    for (int i = 0; i < 6; i++)
    {
        pinMode(IR_PINS[i], INPUT_PULLUP);
        lastState[i] = HIGH;
        lastTriggerMs[i] = 0;
    }

    // Buzzer
    pinMode(BUZZER_PIN, OUTPUT);

    // Emergency stop button — INPUT_PULLUP: resting HIGH, pressed LOW
    pinMode(BUTTON_PIN, INPUT_PULLUP);

    // HX711 load cells: begin, set calibration, tare, then flush filter
    for (int i = 0; i < 6; i++)
    {
        scale[i].begin(HX_DOUT[i], HX_SCK[i]);
        scale[i].set_scale(CALIB[i]);
        scale[i].tare(20);
        for (int j = 0; j < 5; j++)
            scale[i].get_units(1);
    }

    // TM1637 displays: max brightness, show "----" on all until a variant loads
    for (int i = 0; i < 6; i++)
    {
        disp[i].setBrightness(7);
        uint8_t dashes[] = {SEG_G, SEG_G, SEG_G, SEG_G};
        disp[i].setSegments(dashes);
    }

    // NeoPixel strips: init then brief white flash to confirm all 6 work
    for (int i = 0; i < 6; i++)
    {
        neo[i]->begin();
        neo[i]->setBrightness(NEO_BRIGHT);
        neo[i]->clear();
        neo[i]->show();
    }
    for (int i = 0; i < 6; i++)
    {
        neo[i]->fill(neo[i]->Color(255, 255, 255));
        neo[i]->show();
        delay(150);
        neo[i]->clear();
        neo[i]->show();
    }

    Serial2.println("READY");
}

int loopcount = 0;
void loop()
{
    unsigned long now = millis();

    // ── IR polling (falling edge = beam broken) ───────────────────────────────
    for (int i = 0; i < 6; i++)
    {
        bool cur = digitalRead(IR_PINS[i]);
        if (cur == LOW && lastState[i] == HIGH)
        {
            if (now - lastTriggerMs[i] >= (unsigned long)DEBOUNCE_MS)
            {
                Serial.println("ir trig");
                lastTriggerMs[i] = now;
                Serial2.print("IR_TRIGGERED:");
                Serial2.println(i);
            }
        }
        else if (cur == HIGH && lastState[i] == LOW)
        {
            // Beam restored — hand removed from bin
            Serial2.print("IR_CLEARED:");
            Serial2.println(i);
        }
        lastState[i] = cur;
    }

    // ── Emergency stop button (falling edge, debounced) ───────────────────────
    bool curBtn = digitalRead(BUTTON_PIN);
    if (curBtn == LOW && lastBtnState == HIGH)
    {
        if (now - lastBtnMs >= (unsigned long)BTN_DEBOUNCE)
        {
            lastBtnMs = now;
            tone(BUZZER_PIN, 2500, 2000);
            for (int k = 0; k < 6; k++)
            {
                blinkEnabled[k] = false;
                disp[k].setBrightness(7);
                disp[k].setSegments(SEG_ERR);
                neo[k]->fill(neo[k]->Color(255, 0, 0));
                neo[k]->show();
            }
            Serial2.println("EMERGENCY_STOP");
        }
    }
    lastBtnState = curBtn;

    // ── Watchdog — clear outputs if Python has disconnected ──────────────────
    if (lastCmdMs > 0 && (now - lastCmdMs) > WATCHDOG_MS)
    {
        lastCmdMs = 0; // prevent repeated clearing
        for (int k = 0; k < 6; k++)
        {
            blinkEnabled[k] = false;
            disp[k].setBrightness(0);
            disp[k].clear();
            neo[k]->clear();
            neo[k]->show();
        }
    }

    loopcount++;
    if (loopcount % 200 == 0)
    {
        // ── Auto weight stream ────────────────────────────────────────────────────
        Serial.print("stream on: ");
        Serial.println(streamOn);
    }
    // Serial.print(".");
    // if (streamOn && (now - lastStreamMs) >= STREAM_INTERVAL)
    if ((now - lastStreamMs) >= STREAM_INTERVAL)
    {
        // Serial.println("enter wights loop");
        lastStreamMs = now;
        for (int k = 0; k < 6; k++)
        {
            if (scale[k].is_ready())
            {
                if (k == 2)
                {
                    Serial.println("entered");
                }
                float g = scale[k].get_units(3);
                Serial2.print("WEIGHT:");
                Serial2.print(k);
                Serial2.print(":");
                Serial2.println(g, 1);
            }
        }
    }

    // ── Blink active displays ─────────────────────────────────────────────────
    for (int i = 0; i < 6; i++)
    {
        if (blinkEnabled[i] && (now - lastBlinkMs[i] >= BLINK_MS))
        {
            blinkVisible[i] = !blinkVisible[i];
            disp[i].setBrightness(blinkVisible[i] ? 7 : 0);
            lastBlinkMs[i] = now;
        }
    }

    // ── Serial command reader ─────────────────────────────────────────────────
    while (Serial2.available())
    {
        char c = (char)Serial2.read();
        if (c == '\n')
        {
            serialBuf.trim();
            Serial.print("input: ");
            Serial.println(serialBuf);
            handleCommand(serialBuf);
            serialBuf = "";
        }
        else if (c != '\r')
        {
            serialBuf += c;
        }
    }

    delay(5);
}

// ─────────────────────────────────────────────────────────────────────────────

void handleCommand(const String &cmd)
{
    lastCmdMs = millis(); // reset watchdog on every command from Python

    // BUZZ:duration_ms — tone at 2.5 kHz for dur ms (auto-stops, non-blocking)
    if (cmd.startsWith("BUZZ:"))
    {
        int dur = cmd.substring(5).toInt();
        if (dur > 0)
            tone(BUZZER_PIN, 2500, (unsigned long)dur);

        // DISP:N:count  — update TM1637 display N with pick count
    }
    else if (cmd.startsWith("DISP:"))
    {
        int colon2 = cmd.indexOf(':', 5);
        if (colon2 < 0)
            return;
        int binIdx = cmd.substring(5, colon2).toInt();
        int count = cmd.substring(colon2 + 1).toInt();
        if (binIdx >= 0 && binIdx < 6)
        {
            disp[binIdx].setBrightness(7);            // always restore before drawing
            disp[binIdx].showNumberDec(count, false); // no leading zeros
        }

        // TARE:N  — re-zero load cell N (use when bin is empty)
    }
    else if (cmd.startsWith("TARE:"))
    {
        int binIdx = cmd.substring(5).toInt();
        if (binIdx >= 0 && binIdx < 6)
        {
            scale[binIdx].tare(20);     // avg 20 readings for a stable zero
            for (int j = 0; j < 5; j++) // flush filter — discard first 5 readings
                scale[binIdx].get_units(1);
            Serial2.print("TARED:");
            Serial2.println(binIdx);
        }

        // RAW:N  — print tare-corrected raw ADC value (for calibration)
    }
    else if (cmd.startsWith("RAW:"))
    {
        int binIdx = cmd.substring(4).toInt();
        if (binIdx >= 0 && binIdx < 6)
        {
            long raw = scale[binIdx].get_value(10); // 10-sample average, tare offset applied
            Serial2.print("RAW_VAL:");
            Serial2.print(binIdx);
            Serial2.print(":");
            Serial2.println(raw);
        }

        // CAL:N:factor  — update calibration factor at runtime (not persistent)
    }
    else if (cmd.startsWith("CAL:"))
    {
        int colon2 = cmd.indexOf(':', 4);
        if (colon2 < 0)
            return;
        int binIdx = cmd.substring(4, colon2).toInt();
        float factor = cmd.substring(colon2 + 1).toFloat();
        if (binIdx >= 0 && binIdx < 6 && factor > 0.0)
        {
            CALIB[binIdx] = factor;
            scale[binIdx].set_scale(factor);
            Serial2.print("CAL_SET:");
            Serial2.print(binIdx);
            Serial2.print(":");
            Serial2.println(factor);
        }

        // LED:N:state  — visual feedback via TM1637 (replaces physical LEDs)
        //   active → blink the displayed number
        //   error  → show "Err"
        //   done   → show "----"
        //   off    → blank + stop blinking
    }
    else if (cmd.startsWith("LED:"))
    {
        int colon2 = cmd.indexOf(':', 4);
        if (colon2 < 0)
            return;
        int binIdx = cmd.substring(4, colon2).toInt();
        String st = cmd.substring(colon2 + 1);
        if (binIdx < 0 || binIdx >= 6)
            return;

        blinkEnabled[binIdx] = false;  // stop any existing blink
        disp[binIdx].setBrightness(7); // restore full brightness

        if (st == "active")
        {
            blinkEnabled[binIdx] = true;
            blinkVisible[binIdx] = true;
            lastBlinkMs[binIdx] = millis();
            neo[binIdx]->fill(neo[binIdx]->Color(255, 255, 255)); // WHITE — pick this bin
        }
        else if (st == "error")
        {
            disp[binIdx].setSegments(SEG_ERR);
            neo[binIdx]->fill(neo[binIdx]->Color(255, 0, 0)); // RED — wrong bin / bad qty
        }
        else if (st == "green")
        {
            neo[binIdx]->fill(neo[binIdx]->Color(0, 255, 0)); // GREEN — correct qty picked
        }
        else if (st == "done")
        {
            disp[binIdx].setSegments(SEG_DASHES);
            neo[binIdx]->clear(); // OFF — step complete
        }
        else if (st == "off")
        {
            disp[binIdx].setSegments(SEG_DASHES);
            neo[binIdx]->clear(); // OFF — idle
        }
        neo[binIdx]->show();

        // 0–5  — print weight for that bin on demand (manual check or Python poll)
    }
    else if (cmd.length() == 1 && cmd[0] >= '0' && cmd[0] <= '5')
    {
        int binIdx = cmd[0] - '0';
        if (scale[binIdx].is_ready())
        {
            float grams = scale[binIdx].get_units(3); // 3-sample avg keeps latency ~300ms for Python polling
            Serial2.print("WEIGHT:");
            Serial2.print(binIdx);
            Serial2.print(":");
            Serial2.println(grams, 1);
        }
        else
        {
            Serial2.print("NOT_READY:");
            Serial2.println(binIdx);
        }

        // STREAM:1 / STREAM:0  — toggle auto weight broadcast every 2s
    }
    else if (cmd.startsWith("STREAM:"))
    {
        streamOn = (cmd.charAt(7) == '1');
        Serial2.println(streamOn ? "STREAM ON" : "STREAM OFF");
        lastStreamMs = millis();
    }
}
