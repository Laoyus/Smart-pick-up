# Smart Assistive Part Pick System — Round 2 Simulation

**Team:** Decepticons@123 · IIT (ISM) Dhanbad
**Event:** Caterpillar Tech Challenge 2026 — Problem Statement 8
**Presenting:** May 5, 2026

---

## What this is

A software simulation of the Smart Assistive Part Pick System (small-parts
trolley variant) running the exact same state machine that will run on the
Raspberry Pi prototype for Round 3. The only thing that changes between this
sim and the hardware prototype is the `hardware_abstraction.py` file —
everything above it (the sequence engine, variant configs, event model) is
identical.

## Quick start

```bash
# 1. install deps (Python 3.10+ recommended)
pip install -r requirements.txt

# 2. run the server
python app.py

# 3. open two browser tabs:
#    http://localhost:5000/            <- Operator HMI (the trolley)
#    http://localhost:5000/supervisor  <- Supervisor dashboard
```

## Demo flow for judges (5 min)

1. **Load Model A** from the left panel — trolley populates with 6 bins, part
   numbers and displays render.
2. **Click Start Sequence** — first LED lights up (bin 5, LH bracket),
   instruction card shows "PICK 1× Bracket Engine Mount LH".
3. **Click "Pick From This Bin"** on bin 5 — green LED, chime, display count
   decrements, next step lights up automatically.
4. **Walk through steps 2–6** correctly — emphasize that this is the *real
   engine* running, not a scripted animation.
5. **Fault injection demo (reset and reload Model A):**
   - Click **Ghost IR** — simulates hand waved over bin with no weight drop.
     Engine ignores it. Event log shows `GHOST IR · IR tripped but no weight drop — ignored`.
   - Click **Wrong Bin** — simulates picking from the wrong bin. Red flash,
     buzzer, error counter increments. Sequence does NOT advance.
   - Click **Wrong Quantity** — simulates picking one fewer/more than needed.
     Engine rejects, re-arms same step.
6. **Variant changeover** — click Reset, then load Model C. Same trolley
   hardware, different parts, different pick sequence. Zero hardware changes.
7. **Switch to Supervisor tab** — show judges the dashboard view: live metrics,
   sequence timeline with completed/active steps, bin inventory.

## Architecture

```
┌────────────────────────┐
│  Browser (operator)    │  <-- visualizes trolley, LEDs, displays
│  Browser (supervisor)  │  <-- dashboard view
└──────────┬─────────────┘
           │  WebSocket (Flask-SocketIO)
┌──────────▼─────────────┐
│  app.py (Flask)        │  <-- HTTP API + event broadcaster
├────────────────────────┤
│  sequence_engine.py    │  <-- STATE MACHINE (ships to Pi unchanged)
├────────────────────────┤
│  hardware_abstraction  │  <-- MOCK now, GPIO for Round 3
│     HardwareInterface  │        (abstract base class)
│     MockHardware       │        (sim)
│     PiHardware  ← TODO │        (prototype, Round 3)
└────────────────────────┘
```

### Why this structure matters for judges

When asked "how do you know the prototype will work?":

> "The sequence engine you're watching is the same Python code that will run
> on the Raspberry Pi. One file changes — the hardware layer. Mock sensors
> become real HX711 load cell reads, mock LEDs become NeoPixel calls. The
> logic that matters — sequence tracking, IR+weight pick confirmation,
> variant handling — is already proven working."

## Variant configs

Each `configs/model_*.json` defines a product variant:

- `bins[]` — physical bin contents: part number, name, unit weight, initial qty, tolerance
- `pick_sequence[]` — ordered steps: which bin, how many, what instruction
- `cycle_time_target_sec` — performance benchmark

**Changing a variant on a real line = copy a new JSON file to the Pi.**
No rewiring, no recalibration. This is the "20-30 min changeover" promise
from Round 1 made concrete.

## Key detection logic (in sequence_engine.py)

Pick confirmation is a two-stage gate:

1. **IR break-beam** arms a detection window (~1.5 sec).
2. **Load cell weight drop** within that window confirms a real pick.

| IR fires? | Weight drops? | Classification       |
|-----------|---------------|----------------------|
| Yes       | Yes (matches) | PICK_CORRECT         |
| Yes       | Yes (off qty) | PICK_QTY_MISMATCH    |
| Yes       | No            | GHOST_IR (ignored)   |
| No        | Yes           | (not possible in sim; physical anomaly in real life) |
| + wrong bin | -           | PICK_WRONG_BIN       |

This eliminates the two classic failure modes: IR false positives (hand
passed over), and load-cell noise (cannot detect pick by weight alone in
reasonable time on small parts).

## Files

| File | Purpose |
|------|---------|
| `app.py` | Flask + SocketIO server, HTTP endpoints, event broadcast |
| `sequence_engine.py` | State machine — **core IP, runs unchanged on Pi** |
| `hardware_abstraction.py` | HardwareInterface ABC + MockHardware (swap target) |
| `test_engine.py` | Headless test suite — 5 scenarios, run `python test_engine.py` |
| `configs/model_a.json` | Standard engine mount variant |
| `configs/model_b.json` | Heavy-duty engine mount variant (M12 hardware) |
| `configs/model_c.json` | Isolator mount variant (different part mix + sequence) |
| `static/trolley.html` | Operator HMI |
| `static/supervisor.html` | Supervisor dashboard |
| `static/app.js` | Operator frontend logic |
| `static/style.css` | Industrial HMI styling |

## Round 3 porting checklist

To take this from sim to Pi prototype, you write one new file: `pi_hardware.py`.

```python
class PiHardware(HardwareInterface):
    def __init__(self):
        import RPi.GPIO as GPIO
        import board, neopixel
        from hx711 import HX711
        # ... init pins, sensors, NeoPixel strip

    def set_led(self, bin_id, color):
        self.neopixels[bin_id] = COLOR_MAP[color]
        self.neopixels.show()

    def read_weight(self, bin_id):
        return self.load_cells[bin_id].get_weight()

    def register_ir_callback(self, bin_id, callback):
        GPIO.add_event_detect(IR_PINS[bin_id], GPIO.FALLING,
                              callback=lambda ch: callback(bin_id))
    # ... etc
```

Then in `app.py` change one line:
```python
hardware = MockHardware(emit_fn=broadcast_hw)
# becomes:
hardware = PiHardware()
```

That's it. The engine, variant configs, event model, and even the
supervisor dashboard all carry over.

## Running the tests

```bash
python test_engine.py
```

Expected output: `ALL TESTS PASSED` (validates correct sequence, wrong bin,
ghost IR, qty mismatch, and variant changeover).
