// Load Cell Calibration Utility
// Team Decepticons@123 · Caterpillar Tech Challenge 2026
//
// Commands (Serial Monitor, 9600 baud, Newline ending):
//   TARE:N        — zero bin N (empty it first)
//   W:N           — show weight of bin N in grams
//   W             — show weight of all bins in grams
//   RAW:N         — show raw ADC count of bin N (no scale factor)
//   SET:N:factor  — set calibration factor for bin N
//
// Workflow:
//   1. Flash, open Serial Monitor, wait for READY
//   2. Empty bin N → send TARE:N
//   3. Send RAW:N → note value (should be near 0)
//   4. Place a known weight (e.g. 500 g) on bin N
//   5. Send RAW:N again → note the new value R
//   6. Calculate:  factor = R / 500.0   (use a calculator)
//   7. Send SET:N:factor  (e.g. SET:0:370.25)
//   8. Send W:N → should read close to 500 g
//   9. Copy factor into CALIB[] in arduino_bridge.ino and weight_measure.ino
//
// Wiring:  Bin  DOUT  SCK
//           0     3    2
//           1     5    4
//           2     7    6
//           3     9    8
//           4    11   10
//           5    13   12

#include <HX711.h>

const int HX_DOUT[6] = {3, 5, 7, 9, 11, 13};
const int HX_SCK[6] = {2, 4, 6, 8, 10, 12};

// Current best-known calibration — keeps W:N readable from the start
float CALIB[6] = {425.28, 456.175, 439.58, -394.01, -420.84, -443.8};

HX711 scale[6];
String serialBuf = "";

void handleCommand(const String &cmd);

void setup()
{
    Serial.begin(9600);
    Serial.println("Initializing...");
    for (int i = 0; i < 6; i++)
    {
        scale[i].begin(HX_DOUT[i], HX_SCK[i]);
        scale[i].set_scale(CALIB[i]);
        scale[i].tare(20);
        for (int j = 0; j < 5; j++)
            scale[i].get_units(1);
    }
    Serial.println("READY — all bins tared.");
    Serial.println("Commands: TARE:N  W:N  W  RAW:N  SET:N:factor");
}

void loop()
{
    while (Serial.available())
    {
        char c = (char)Serial.read();
        if (c == '\n')
        {
            serialBuf.trim();
            if (serialBuf.length() > 0)
                handleCommand(serialBuf);
            serialBuf = "";
        }
        else if (c != '\r')
            serialBuf += c;
    }
}

void handleCommand(const String &cmd)
{
    // TARE:N
    if (cmd.startsWith("TARE:"))
    {
        int n = cmd.substring(5).toInt();
        if (n < 0 || n >= 6)
        {
            Serial.println("ERROR: bin 0-5");
            return;
        }
        Serial.print("Taring bin ");
        Serial.print(n);
        Serial.println("...");
        scale[n].tare(20);
        for (int j = 0; j < 5; j++)
            scale[n].get_units(1);
        Serial.print("Bin ");
        Serial.print(n);
        Serial.println(" tared.");
    }

    // W:N — weight in grams
    else if (cmd.startsWith("W:"))
    {
        int n = cmd.substring(2).toInt();
        if (n < 0 || n >= 6)
        {
            Serial.println("ERROR: bin 0-5");
            return;
        }
        if (!scale[n].is_ready())
        {
            Serial.println("NOT READY");
            return;
        }
        float g = scale[n].get_units(10);
        Serial.print("Bin ");
        Serial.print(n);
        Serial.print(": ");
        Serial.print(g, 2);
        Serial.println(" g");
    }

    // W — all bins in grams
    else if (cmd == "W")
    {
        for (int i = 0; i < 6; i++)
        {
            Serial.print("Bin ");
            Serial.print(i);
            Serial.print(": ");
            if (!scale[i].is_ready())
            {
                Serial.println("NOT READY");
                continue;
            }
            float g = scale[i].get_units(5);
            Serial.print(g, 2);
            Serial.println(" g");
        }
    }

    // RAW:N — raw ADC counts (tare offset applied, no scale factor)
    else if (cmd.startsWith("RAW:"))
    {
        int n = cmd.substring(4).toInt();
        if (n < 0 || n >= 6)
        {
            Serial.println("ERROR: bin 0-5");
            return;
        }
        if (!scale[n].is_ready())
        {
            Serial.println("NOT READY");
            return;
        }
        scale[n].set_scale(1.0);
        long raw = (long)scale[n].get_units(10);
        scale[n].set_scale(CALIB[n]);
        Serial.print("RAW Bin ");
        Serial.print(n);
        Serial.print(": ");
        Serial.println(raw);
        Serial.println("  → factor = RAW / known_weight_grams");
    }

    // SET:N:factor — apply your calculated calibration factor
    else if (cmd.startsWith("SET:"))
    {
        int colon2 = cmd.indexOf(':', 4);
        if (colon2 < 0)
        {
            Serial.println("ERROR: use SET:N:factor  e.g. SET:0:370.25");
            return;
        }
        int n = cmd.substring(4, colon2).toInt();
        float factor = cmd.substring(colon2 + 1).toFloat();
        if (n < 0 || n >= 6)
        {
            Serial.println("ERROR: bin 0-5");
            return;
        }
        if (factor == 0.0)
        {
            Serial.println("ERROR: factor must not be 0");
            return;
        }

        CALIB[n] = factor;
        scale[n].set_scale(factor);

        Serial.print("CALIB[");
        Serial.print(n);
        Serial.print("] set to ");
        Serial.println(factor, 5);

        if (!scale[n].is_ready())
        {
            Serial.println("  (NOT READY — cannot verify yet)");
            return;
        }
        float g = scale[n].get_units(10);
        Serial.print("  W:");
        Serial.print(n);
        Serial.print(" = ");
        Serial.print(g, 2);
        Serial.println(" g");
        Serial.println("  Copy this factor into CALIB[] in arduino_bridge.ino and weight_measure.ino");
    }

    else
    {
        Serial.print("Unknown: ");
        Serial.println(cmd);
        Serial.println("Commands: TARE:N  W:N  W  RAW:N  SET:N:factor");
    }
}
