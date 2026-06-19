// Smart Assistive Part Pick System — Part Weight Measurement Utility
// Team Decepticons@123 · Caterpillar Tech Challenge 2026
//
// Flash this sketch (separate from the main bridge) to measure part unit weights.
// Use the measured values to update unit_weight_g in your JSON config files.
//
// ── Workflow ──────────────────────────────────────────────────────────────────
//   1. Flash this sketch to the Arduino Mega (same wiring as main bridge)
//   2. Open Serial Monitor at 9600 baud, set line ending to "Newline"
//   3. Wait for "READY" — all 6 bins auto-tare on boot
//   4. Empty a bin, send TARE:N to zero it
//   5. Place a known quantity of identical parts on the bin
//   6. Send  UNIT:N:qty  (e.g.  UNIT:2:10  for 10 parts on bin 2)
//      → Arduino prints  UNIT_WEIGHT:2:42.30 g/part
//   7. Copy that value into unit_weight_g for the matching bin in your JSON file
//
// ── Commands ──────────────────────────────────────────────────────────────────
//   W           — print weight of all 6 bins
//   W:N         — print weight of bin N  (N = 0–5)
//   TARE        — re-zero all bins  (empty them first)
//   TARE:N      — re-zero bin N only
//   UNIT:N:qty  — print unit weight = total_weight / qty  (parts already on bin)
//
// ── Wiring (same as main bridge sketch) ──────────────────────────────────────
//   HX711:  Bin  DOUT  SCK
//            0     3    2
//            1     5    4
//            2     7    6
//            3     9    8
//            4    11   10
//            5    13   12
// ─────────────────────────────────────────────────────────────────────────────

#include <HX711.h>

// ── Pin definitions ───────────────────────────────────────────────────────────
const int HX_DOUT[6] = {3, 5, 7, 9, 11, 13};
const int HX_SCK[6] = {2, 4, 6, 8, 10, 12};

// ── Calibration — keep in sync with arduino_bridge.ino ───────────────────────
float CALIB[6] = {425.28, 456.175, 439.58, -394.01, -420.84, -443.8};

// ── Hardware objects ──────────────────────────────────────────────────────────
HX711 scale[6];

// ── Serial buffer ─────────────────────────────────────────────────────────────
String serialBuf = "";

// ─────────────────────────────────────────────────────────────────────────────

void setup()
{
    Serial.begin(9600);
    Serial.println("Initializing load cells — please wait...");

    for (int i = 0; i < 6; i++)
    {
        scale[i].begin(HX_DOUT[i], HX_SCK[i]);
        scale[i].set_scale(CALIB[i]);
        scale[i].tare(20);
        for (int j = 0; j < 5; j++)
            scale[i].get_units(1); // flush filter after tare
    }

    Serial.println("READY — all bins tared.");
    Serial.println("Commands: W  W:N  TARE  TARE:N  UNIT:N:qty");
    Serial.println("─────────────────────────────────────────");
    printAllWeights();
}

void loop()
{
    // Serial command reader
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
        {
            serialBuf += c;
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────

void printAllWeights()
{
    Serial.println("── Current weights ──────────────────────");
    for (int i = 0; i < 6; i++)
    {
        Serial.print("  Bin ");
        Serial.print(i);
        Serial.print(": ");
        if (scale[i].is_ready())
        {
            float g = scale[i].get_units(5);
            Serial.print(g, 2);
            Serial.println(" g");
        }
        else
        {
            Serial.println("NOT READY");
        }
    }
}

void printWeight(int binIdx)
{
    if (!scale[binIdx].is_ready())
    {
        Serial.print("Bin ");
        Serial.print(binIdx);
        Serial.println(": NOT READY");
        return;
    }
    float g = scale[binIdx].get_units(10);
    Serial.print("Bin ");
    Serial.print(binIdx);
    Serial.print(": ");
    Serial.print(g, 2);
    Serial.println(" g");
}

void handleCommand(const String &cmd)
{
    // W — print all weights
    if (cmd == "W")
    {
        printAllWeights();

        // W:N — print single bin weight
    }
    else if (cmd.startsWith("W:"))
    {
        int binIdx = cmd.substring(2).toInt();
        if (binIdx >= 0 && binIdx < 6)
            printWeight(binIdx);
        else
            Serial.println("ERROR: bin index must be 0–5");

        // TARE — re-zero all bins
    }
    else if (cmd == "TARE")
    {
        Serial.println("Taring all bins — make sure they are EMPTY...");
        for (int i = 0; i < 6; i++)
        {
            scale[i].tare(20);
            for (int j = 0; j < 5; j++)
                scale[i].get_units(1);
            Serial.print("  Bin ");
            Serial.print(i);
            Serial.println(" tared.");
        }
        Serial.println("All bins tared.");

        // TARE:N — re-zero single bin
    }
    else if (cmd.startsWith("TARE:"))
    {
        int binIdx = cmd.substring(5).toInt();
        if (binIdx >= 0 && binIdx < 6)
        {
            Serial.print("Taring bin ");
            Serial.print(binIdx);
            Serial.println(" — make sure it is EMPTY...");
            scale[binIdx].tare(20);
            for (int j = 0; j < 5; j++)
                scale[binIdx].get_units(1);
            Serial.print("Bin ");
            Serial.print(binIdx);
            Serial.println(" tared.");
        }
        else
        {
            Serial.println("ERROR: bin index must be 0–5");
        }

        // UNIT:N:qty — calculate unit weight = total_weight / qty
    }
    else if (cmd.startsWith("UNIT:"))
    {
        int colon2 = cmd.indexOf(':', 5);
        if (colon2 < 0)
        {
            Serial.println("ERROR: use  UNIT:N:qty  e.g.  UNIT:2:10");
            return;
        }
        int binIdx = cmd.substring(5, colon2).toInt();
        int qty = cmd.substring(colon2 + 1).toInt();

        if (binIdx < 0 || binIdx >= 6)
        {
            Serial.println("ERROR: bin index must be 0–5");
            return;
        }
        if (qty <= 0)
        {
            Serial.println("ERROR: qty must be > 0");
            return;
        }
        if (!scale[binIdx].is_ready())
        {
            Serial.print("ERROR: bin ");
            Serial.print(binIdx);
            Serial.println(" not ready");
            return;
        }

        Serial.print("Measuring bin ");
        Serial.print(binIdx);
        Serial.print(" with ");
        Serial.print(qty);
        Serial.println(" parts — please hold still...");

        float totalGrams = scale[binIdx].get_units(20); // 20-sample avg for accuracy
        float unitGrams = totalGrams / (float)qty;

        Serial.print("  Total weight : ");
        Serial.print(totalGrams, 2);
        Serial.println(" g");
        Serial.print("  Unit weight  : ");
        Serial.print(unitGrams, 3);
        Serial.println(" g/part");
        Serial.print("  → Set  unit_weight_g: ");
        Serial.print(unitGrams, 3);
        Serial.print("  for bin ");
        Serial.print(binIdx + 1); // 1-indexed as used in JSON
        Serial.println("  in your JSON config");
    }
    else
    {
        Serial.print("Unknown command: ");
        Serial.println(cmd);
        Serial.println("Commands: W  W:N  TARE  TARE:N  UNIT:N:qty");
    }
}
