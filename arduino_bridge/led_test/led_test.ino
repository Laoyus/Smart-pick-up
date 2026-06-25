// LED Strip Test — WS2812B / NeoPixel
// Smart Assistive Part Pick System
//
// IR HIGH → strip glows WHITE
// IR LOW  → strip OFF
//
// Requires: Adafruit NeoPixel library
//   Arduino IDE → Sketch → Include Library → Manage Libraries
//   → search "Adafruit NeoPixel" → Install
//
// ── Change these to match your wiring ────────────────────────────────────────
#define NUM_LEDS  1    // LEDs per strip
#define BRIGHT   150   // brightness 0–255

#include <Adafruit_NeoPixel.h>

const int IR_PINS[6] = {22, 23, 24, 25, 26, 27};

// Declare each strip separately — array initialisation breaks NeoPixel objects
Adafruit_NeoPixel s0(NUM_LEDS, 44, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel s1(NUM_LEDS, 45, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel s2(NUM_LEDS, 46, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel s3(NUM_LEDS, 47, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel s4(NUM_LEDS, 48, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel s5(NUM_LEDS, 49, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel* strips[6] = {&s0, &s1, &s2, &s3, &s4, &s5};

bool lastState[6];

void setStrip(int bin, bool on)
{
    uint32_t c = on ? strips[bin]->Color(255, 255, 255) : 0;
    for (int i = 0; i < NUM_LEDS; i++)
        strips[bin]->setPixelColor(i, c);
    strips[bin]->show();
}

void setup()
{
    Serial.begin(9600);

    for (int i = 0; i < 6; i++)
    {
        pinMode(IR_PINS[i], INPUT);
        lastState[i] = LOW;

        strips[i]->begin();
        strips[i]->setBrightness(BRIGHT);
        strips[i]->clear();
        strips[i]->show();
    }

    // Startup sweep — confirm all 6 strips work
    Serial.println("Startup sweep...");
    for (int i = 0; i < 6; i++)
    {
        setStrip(i, true);
        delay(300);
        setStrip(i, false);
    }

    Serial.println("READY — IR HIGH = strip WHITE, IR LOW = strip OFF");
}

void loop()
{
    for (int i = 0; i < 6; i++)
    {
        bool cur = (digitalRead(IR_PINS[i]) == HIGH);

        if (cur != lastState[i])
        {
            setStrip(i, cur);
            Serial.print("Bin "); Serial.print(i);
            Serial.println(cur ? " ON" : " OFF");
            lastState[i] = cur;
        }
    }
}
