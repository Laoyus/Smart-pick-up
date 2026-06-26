# Smart Assistive Part Pick System

> A sensor-guided, variant-agnostic kit-trolley system that directs assembly-line operators to pick the right part, in the right quantity, in the right sequence — with zero added cognitive load.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Getting Started](#2-getting-started)
3. [System Architecture](#3-system-architecture)
4. [Hardware Layer](#4-hardware-layer)
5. [WiFi Bridge Architecture](#5-wifi-bridge-architecture)
6. [Software Stack](#6-software-stack)
   - 6.1 [sequence\_engine.py — Pick State Machine](#61-sequence_enginepy--pick-state-machine)
   - 6.2 [hardware\_abstraction.py — HAL](#62-hardware_abstractionpy--hardware-abstraction-layer)
   - 6.3 [arduino\_hardware.py — Real Hardware Backend](#63-arduino_hardwarepy--real-hardware-backend)
   - 6.4 [trolley\_coordinator.py — Multi-Trolley Orchestration](#64-trolley_coordinatorpy--multi-trolley-orchestration)
   - 6.5 [rerouting\_engine.py — Adaptive Path Recovery](#65-rerouting_enginepy--adaptive-path-recovery)
   - 6.6 [fleet\_api.py — REST Fleet Management](#66-fleet_apipy--rest-fleet-management)
   - 6.7 [app.py — Flask Application Server](#67-apppy--flask-application-server)
   - 6.8 [cad\_to\_json\_converter.py — CAD Import Tool](#68-cad_to_json_converterpy--cad-import-tool)
7. [Variant JSON Configuration](#7-variant-json-configuration)
8. [Data Flow: Complete Pick Cycle](#8-data-flow-complete-pick-cycle)
9. [Operating Modes](#9-operating-modes)
10. [Tech Stack Reference](#10-tech-stack-reference)
11. [Architecture Principles](#11-architecture-principles)

---

## 1. Project Overview

### Problem

Modern assembly lines use kit trolleys where all parts for a workstation are pre-staged. Operators must still refer to work instructions to answer three questions on every step:

1. **Which part do I pick next?**
2. **How many do I pick?**
3. **In what sequence?**

In multi-variant assemblies — where different product configurations pass through the same workstation — this causes lost time, cognitive overload, and assembly errors that grow worse as product complexity increases.

### Solution

The Smart Assistive Part Pick System eliminates work instruction dependency by embedding guidance directly into the kit trolley:

- A **NeoPixel LED strip** lights the correct bin.
- A **TM1637 display** shows the quantity remaining.
- An **audio buzzer** gives confirmation or error feedback.
- An **IR break-beam sensor** detects hand entry.
- An **HX711 load cell** confirms the pick by measuring weight loss.

The system advances to the next step automatically. The operator's only task is to pick the lit bin.

### Key Features

| Feature | Description |
|---|---|
| Variant-agnostic | Unlimited product variants via JSON config swap — no rewiring, no recalibration |
| Dual-sensor confirmation | IR detects intent; weight confirms outcome — eliminates false positives |
| Triple feedback | LED + audio + display — works even if any single channel is missed |
| Fast changeover | Config swap in seconds via the manager dashboard |
| CAD-to-JSON converter | Drop a STEP/IGES/STL file, get a deployment-ready config automatically |
| Wireless operation | Two-ESP32 WiFi bridge — no USB cable tethering the laptop to the shelf |
| Multi-trolley support | Standalone and linked modes for coordinating multiple kit trolleys |
| Adaptive rerouting | Substitution-rule engine redirects picks when a valid alternate part is used |

### Pick Confirmation Logic

Pick confirmation is a two-stage gate — neither sensor alone is sufficient:

| IR fires? | Weight drops? | Classification |
|---|---|---|
| Yes | Yes (matches qty) | `PICK_CORRECT` |
| Yes | Yes (wrong qty) | `PICK_QTY_MISMATCH` |
| Yes | No | `GHOST_IR` — ignored |
| Yes | Wrong bin | `PICK_WRONG_BIN` |

---

## 2. Getting Started

### Prerequisites

- **Python 3.11+**
- **pip**
- (For hardware mode) Arduino IDE 2.x, two ESP32 dev boards, Arduino Mega 2560

### Installation

```bash
git clone https://github.com/Laoyus/Smart-pick-up.git
cd Smart-pick-up
pip install -r requirements.txt
```

If you don't have a `requirements.txt`, install directly:

```bash
pip install flask flask-socketio flask-sock
```

For the CAD converter only:

```bash
pip install PyQt6
```

---

### Option A — Run in Simulation (no hardware needed)

This is the fastest path. Everything runs in a browser with manual pick buttons.

```bash
python app.py
```

Open the following in your browser:

| URL | Purpose |
|---|---|
| `http://localhost:5000/` | Operator HMI — the trolley view |
| `http://localhost:5000/supervisor` | Supervisor dashboard — live metrics |
| `http://localhost:5000/manager` | Fleet control — load variants, switch modes |

**Try a full pick cycle:**

1. Open the **Manager** tab → select a config from the dropdown (e.g. `model_a.json`) → click **Push** → click **Activate**.
2. Open the **Operator** tab — the first bin lights up white with a pick count.
3. Click **Pick From This Bin** — LED turns green, a chime plays, the next step lights up.
4. Walk through all steps to see the sequence complete.

**Try fault injection:**

- Click **Ghost IR** → hand-waves over a bin with no weight drop. Engine ignores it.
- Click **Wrong Bin** → red LED flashes, buzzer fires, error counter increments. Sequence does not advance.
- Click **Wrong Quantity** → engine rejects, re-arms the same step.

**Try variant changeover:**

- Click **Reset** → open the Manager tab → select `model_c.json` → Push → Activate.
- Different parts, different sequence, same shelf — zero hardware changes.

---

### Option B — Run with Real Hardware (Arduino + WiFi bridge)

#### Step 1 — Flash the Arduino

Open `arduino_bridge/arduino_bridge.ino` in Arduino IDE and flash it to the Arduino Mega 2560.

#### Step 2 — Flash the ESP32s

| Sketch | Board | Role |
|---|---|---|
| `arduino_bridge/esp32_relay/esp32_relay.ino` | ESP32-B (wired to Arduino) | UART relay + WebSocket client |

ESP32-A runs as a plain WiFi access point — flash it with any AP sketch that broadcasts:
- **SSID:** `MyESPNetwork`
- **Password:** `12345678`

ESP32-B connects to this AP and relays Arduino serial to the laptop at `ws://192.168.4.3:5000/ws_esp`.

#### Step 3 — Connect the laptop to the ESP32 AP

Join the `MyESPNetwork` WiFi network on the laptop. The laptop gets a static IP of `192.168.4.3` on that subnet.

> **Institutional WiFi workaround:** Guest and campus WiFi networks block device-to-device communication. The ESP32 AP bypasses this entirely by creating its own private subnet. A phone hotspot is a fallback if the ESP32 AP is unavailable.

#### Step 4 — Run the server in hardware mode

```bash
SIMULATION_MODE=false python app.py
```

On Windows:

```powershell
$env:SIMULATION_MODE="false"; python app.py
```

The server prints `[ArduinoHardware] ESP32 bridge connected — send channel ready.` when ESP32-B connects.

#### Step 5 — Calibrate the load cells (first time only)

Flash `arduino_bridge/calibrate/calibrate.ino` and use the Serial Monitor (9600 baud) to tare each bin and measure calibration factors. Enter them into `arduino_bridge/arduino_bridge.ino` as `CALIB[]` values, then re-flash the main sketch.

> **Critical:** Only use bare green-PCB HX711 modules. HX711 display boards (blue modules with onboard 7-segment display) share DOUT/SCK lines with their onboard chip and corrupt readings when connected to the same load cell.

#### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SIMULATION_MODE` | `true` | Set to `false` to activate `ArduinoHardware` for `SMALL_A01` |
| `TRANSPORT_MODE` | `wifi` | `wifi` or `usb` — selects the serial transport |
| `ARDUINO_PORT` | `COM3` | Serial port for USB fallback (`COM6` on Windows, `/dev/ttyUSB0` on Linux) |

USB fallback:

```bash
SIMULATION_MODE=false TRANSPORT_MODE=usb ARDUINO_PORT=COM6 python app.py
```

---

### Option C — Create a New Variant Config

#### Using the CAD Converter (recommended)

```bash
python cad_to_json_converter.py
```

1. Drop a `.stp`, `.step`, `.igs`, `.iges`, or `.stl` file onto the import zone.
2. Review the auto-generated step table — parts are sorted by assembly heuristic (structural → brackets → moving parts → fasteners → hardware).
3. Drag rows to reorder steps.
4. Fill in `unit_weight_g` for each part after physically weighing one sample unit.
5. Click **Export JSON** → save to the `configs/` directory.
6. The manager dashboard picks it up immediately — no server restart needed.

#### Writing a Config Manually

Create a `.json` file in `configs/` following the [Variant JSON schema](#7-variant-json-configuration). The minimum required fields are `variant_id`, `variant_name`, `bins`, and `pick_sequence`.

---

### Option D — Run Tests

```bash
python test_engine.py
```

Expected output: `ALL TESTS PASSED` — validates correct sequence, wrong-bin detection, ghost IR rejection, quantity mismatch, and variant changeover.

---

## 3. System Architecture

```
Layer 4 — Browser Clients
  Operator HMI (trolley.html)
  Supervisor Dashboard (supervisor.html)
  Manager Dashboard (manager.html)

Layer 3 — Python Backend (laptop)
  app.py  →  TrolleyCoordinator  →  SequenceEngine (×2)
                                →  ReroutingEngine
  fleet_api.py (REST Blueprint)

Layer 2 — Hardware Abstraction
  HardwareInterface (ABC)
    ├── MockHardware       (simulation)
    └── ArduinoHardware    (real hardware)

Layer 1.5 — WiFi Bridge
  ESP32-A (Access Point: MyESPNetwork)
  ESP32-B (UART relay → WebSocket client)

Layer 1 — Physical Hardware
  Arduino Mega 2560
    ├── IR break-beam sensors  (×6, pins 22–27)
    ├── HX711 load cell ADCs   (×6, pins 2–13)
    ├── WS2812B NeoPixel strips (×6, pins 44–49)
    ├── TM1637 displays         (×6, pins 28–39)
    ├── Active buzzer           (pin 40)
    └── Emergency stop button  (pin 41)
```

### Module Dependency Map

```
app.py
  ├── hardware_abstraction.py
  │     ├── MockHardware          (simulation backend)
  │     └── ArduinoHardware       (real hardware backend)
  ├── trolley_coordinator.py
  │     └── sequence_engine.py    (one instance per trolley)
  │           └── hardware_abstraction.py [HardwareInterface]
  ├── rerouting_engine.py
  │     └── trolley_coordinator.py (back-reference)
  ├── fleet_api.py
  │     └── trolley_coordinator.py (injected)
  └── configs/
        ├── model_a.json          (standalone)
        ├── model_b.json          (reroutable)
        └── model_c.json          (standalone)

cad_to_json_converter.py  (separate PyQt6 process)
  └── configs/              (writes output here)
```

### File Inventory

| File | Purpose |
|---|---|
| `app.py` | Flask + SocketIO server, ESP32 bridge, HTTP routes, event broadcast |
| `sequence_engine.py` | Core pick state machine — hardware-agnostic |
| `hardware_abstraction.py` | `HardwareInterface` ABC, `MockHardware`, `create_hardware()` factory |
| `arduino_hardware.py` | Real-hardware backend; dual-transport (WiFi / USB) |
| `trolley_coordinator.py` | Multi-trolley orchestration; standalone and linked modes |
| `rerouting_engine.py` | Rule-based substitution and config hot-swap |
| `fleet_api.py` | Flask Blueprint — 9 REST endpoints for wireless fleet management |
| `cad_to_json_converter.py` | Standalone PyQt6 desktop tool for CAD → JSON |
| `arduino_bridge/arduino_bridge.ino` | Arduino Mega sketch |
| `arduino_bridge/esp32_relay/esp32_relay.ino` | ESP32-B relay sketch |
| `arduino_bridge/calibrate/calibrate.ino` | Load cell calibration utility |
| `arduino_bridge/weight_measure/weight_measure.ino` | Part weight measurement utility |
| `configs/model_a.json` | Standalone variant A |
| `configs/model_b.json` | Reroutable variant with substitution rules |
| `configs/model_c.json` | Standalone variant C |
| `test_engine.py` | Headless test suite |

---

## 4. Hardware Layer

### Arduino Pin Assignment

| Component | Pins | Notes |
|---|---|---|
| IR sensors (×6) | 22, 23, 24, 25, 26, 27 | `INPUT_PULLUP`; `LOW` = beam broken |
| Active buzzer | 40 | `tone()` at 2.5 kHz |
| Emergency stop | 41 | `INPUT_PULLUP`; `LOW` = pressed |
| HX711 DOUT (×6) | 3, 5, 7, 9, 11, 13 | Odd pins |
| HX711 SCK (×6) | 2, 4, 6, 8, 10, 12 | Even pins |
| TM1637 CLK (×6) | 28, 30, 32, 34, 36, 38 | — |
| TM1637 DIO (×6) | 29, 31, 33, 35, 37, 39 | — |
| NeoPixel data (×6) | 44, 45, 46, 47, 48, 49 | One strip per bin |
| ESP32-B RX (Serial2) | 17 | Receives commands from laptop |
| ESP32-B TX (Serial2) | 16 | Sends events to laptop |

### HX711 Load Cell Wiring

| Load cell wire | Connects to |
|---|---|
| Red (E+) | HX711 E+ |
| Black (E-) | HX711 E- |
| White (A-) | HX711 A- |
| Green (A+) | HX711 A+ |

> **Critical:** Never use HX711 display boards (blue modules with an onboard 7-segment display). They share DOUT/SCK lines with their onboard chip, which corrupts readings when a bare HX711 module is connected to the same load cell. Use only bare green-PCB HX711 modules.

### LED State Vocabulary

| State | RGB | Meaning |
|---|---|---|
| `active` | (255, 255, 255) white | Pick this bin now |
| `green` | (0, 255, 0) green | Correct quantity confirmed |
| `error` | (255, 0, 0) red | Wrong bin or over-pick |
| `done` | off | Step complete |
| `off` | off | Idle / not in use this variant |

### Audio Cue Vocabulary

| Cue | Duration | Trigger |
|---|---|---|
| `chime_next` | 50 ms | Step advance |
| `chime_ok` | 100 ms | Correct pick confirmed |
| `chime_done` | 300 ms | Variant complete |
| `buzz_error` | 600 ms | Wrong pick / over-pick |

### Hardware Build Order

When setting up a physical shelf for the first time:

1. Calibrate each load cell using `weight_measure.ino`
2. Test each IR sensor (verify `INPUT_PULLUP` logic and 50 ms debounce)
3. Test each TM1637 display (brightness, blink, segment codes for `Err` and `----`)
4. Test each NeoPixel strip (verify data pin, brightness set to 120/255)
5. Wire and test the buzzer (pin 40)
6. Build and test one full compartment end-to-end with `arduino_bridge.ino`
7. Scale to all 6 compartments and load a full variant JSON

---

## 5. WiFi Bridge Architecture

### Two-ESP32 Design

| Board | Role | Key detail |
|---|---|---|
| ESP32-A | WiFi Access Point | SSID: `MyESPNetwork`, password: `12345678`, subnet: 192.168.4.x |
| ESP32-B | UART relay + WS client | Joins ESP32-A's network; connects to laptop at `ws://192.168.4.3:5000/ws_esp` |

### Voltage Divider on UART

The Arduino Mega's TX2 (pin 16) outputs 5V. The ESP32's GPIO16 is 3.3V maximum.

```
Arduino TX2 → [10kΩ] → junction → ESP32 GPIO16
                         junction → [20kΩ] → GND
```

Output: `5 × 20/(10+20) = 3.33V` — within ESP32 tolerance. The ESP32→Arduino direction is direct (3.3V reliably reads as HIGH on the 5V Arduino).

### Frame Protocol

| Direction | Frame | Example |
|---|---|---|
| Arduino → Laptop | `>t`*line*`<` | `>tIR_TRIGGERED:2<` |
| Laptop → Arduino | `>c`*cmd*`<` | `>cLED:3:active<` |

### Self-Recovery on Disconnect

When ESP32-B reconnects after a drop, `app.py` automatically re-injects a fresh send function into `ArduinoHardware`. The engine continues running on cached state and resumes sending commands the moment the link is restored — no manual restart needed.

---

## 6. Software Stack

### 6.1 `sequence_engine.py` — Pick State Machine

The core state machine. Hardware-agnostic — it calls `HardwareInterface` abstract methods and never touches GPIO, serial, or WebSocket code.

#### Confirmation Flow

```
LED WHITE → Operator picks → Weight drops → Count reaches 0
→ LED GREEN → IR clears → Advance to next step
```

The weight monitor polls `hw.read_weight()` every **300 ms**. Over-pick is recoverable — the step stays open so the operator can return parts.

#### Public API

| Method | Description |
|---|---|
| `load_variant(config_path_or_dict)` | Accepts a file path or pre-parsed dict. Resets state, primes bins, registers IR callbacks. |
| `start()` | Kicks off step 0. |
| `reset()` | Stops monitor thread, turns all LEDs off, replaces state with blank `EngineState`. |

#### Threading Model

| Thread | Lifetime |
|---|---|
| Weight monitor | One per active step; exits when `_step_active = False` |
| Stuck timer (30 s) | Restarted each step; cancelled on correct pick or reset |
| Advance timer (0.3 s) | One-shot delay before next step starts |
| LED clear timer (1.5 s) | Clears wrong-bin error LED |

#### Class Constants

| Constant | Value | Meaning |
|---|---|---|
| `STUCK_THRESHOLD_SEC` | 30 s | No-pick warning fires after this long |
| `GREEN_IR_TIMEOUT_SEC` | 5 s | Force-advance after GREEN if hand never leaves |

---

### 6.2 `hardware_abstraction.py` — Hardware Abstraction Layer

Contains three things: the `HardwareInterface` ABC, the `MockHardware` simulation backend, and the `create_hardware()` factory.

#### `HardwareInterface` — The ABC

```python
class HardwareInterface(ABC):
    @abstractmethod
    def set_led(self, bin_id: int, color: str) -> None: ...
    @abstractmethod
    def set_display(self, bin_id: int, text: str) -> None: ...
    @abstractmethod
    def read_weight(self, bin_id: int) -> float: ...     # must be non-blocking
    @abstractmethod
    def play_audio(self, cue: str) -> None: ...
    @abstractmethod
    def set_buzzer(self, on: bool) -> None: ...
    @abstractmethod
    def register_ir_callback(self, bin_id: int, callback) -> None: ...
```

#### `create_hardware()` — The Factory

```python
SIMULATION_MODE: bool = os.getenv("SIMULATION_MODE", "true").lower() != "false"

def create_hardware(emit_fn=None, cart_id: str = "SMALL_A01"):
    if SIMULATION_MODE or cart_id == "LARGE_A01":
        return MockHardware(emit_fn=emit_fn, cart_id=cart_id)
    from arduino_hardware import ArduinoHardware
    port = os.getenv("ARDUINO_PORT", "COM3")
    return ArduinoHardware(port=port)
```

Key decisions:
- **Default is simulation** — no env var never accidentally activates hardware mode.
- **`LARGE_A01` always gets `MockHardware`** — prevents opening a second serial port.
- **Lazy import of `ArduinoHardware`** — missing `pyserial` doesn't break simulation imports.

---

### 6.3 `arduino_hardware.py` — Real Hardware Backend

Drop-in replacement for `MockHardware`. Communicates with the Arduino Mega over WiFi (ESP32 bridge) or direct USB serial.

#### Serial Protocol — Arduino → Laptop

| Line | Meaning |
|---|---|
| `READY` | Arduino boot complete |
| `IR_TRIGGERED:N` | IR break-beam on bin N (0-indexed) fired |
| `IR_CLEARED:N` | IR beam on bin N restored |
| `WEIGHT:N:grams` | HX711 reading (one decimal place) |
| `EMERGENCY_STOP` | Physical e-stop button pressed |

#### Serial Protocol — Laptop → Arduino

| Command | Effect |
|---|---|
| `LED:N:state` | Set NeoPixel strip N (`active`/`error`/`green`/`done`/`off`) |
| `DISP:N:count` | Show integer on TM1637 display N |
| `BUZZ:duration_ms` | Fire buzzer at 2.5 kHz (non-blocking on Arduino) |
| `TARE:N` | Re-zero load cell N |
| `RESET` | Clear all outputs immediately |

> **Index convention:** Arduino is 0-indexed (bins 0–5); Python is 1-indexed (bin\_id 1–6). All conversion happens inside this file — no other Python file deals with 0-indexed bin numbers.

#### Weight Cache

`read_weight()` returns a cached value immediately (O(1) dict lookup). The HX711 stream updates the cache asynchronously via `handle_serial_line()`. This guarantees the weight monitor thread never blocks on hardware I/O.

---

### 6.4 `trolley_coordinator.py` — Multi-Trolley Orchestration

Manages two `SequenceEngine` instances simultaneously. Neither `app.py` nor the fleet API calls `SequenceEngine` methods directly — everything goes through the coordinator.

#### Trolley State Machine

```
IDLE → LOADED → RUNNING → DONE
  ↑__________________________|   (reset_trolley / reset_all from any state)
```

#### Variant Lifecycle

- **`push_variant(cart_id, config)`** — Resets engine, loads config. Trolley moves to `LOADED` but does not start. Allows the manager to prepare a variant in advance.
- **`activate(cart_id)`** — Starts the sequence. Trolley moves to `RUNNING`.
- **`reset_all()`** — Returns all trolleys to `IDLE`. Clears linked-mode state and tells the rerouting engine to discard loaded substitution rules.

#### Linked Mode — Phase Execution

`_build_phases()` groups consecutive steps with the same `trolley_id`:

```
[S, S, L, L, S] → [(SMALL, steps 1–2), (LARGE, steps 3–4), (SMALL, step 5)]
```

Each phase runs as a standalone mini-config. The engine is completely unaware it is running a phase. When a phase's `VARIANT_COMPLETE` fires, the coordinator intercepts and swallows it (to avoid confusing the browser HMI), then activates the next trolley.

---

### 6.5 `rerouting_engine.py` — Adaptive Path Recovery

Handles pick-path recovery when an operator picks from the wrong bin. Uses deterministic rule lookup — not machine learning.

The rerouting problem is a **deterministic constraint-satisfaction problem**. Valid substitutes are defined by engineering specifications — they either comply or they don't. The correct tool is a dict access (O(1)), not a trained model.

#### Substitution Rule Schema

```json
{
  "substitution_rules": {
    "Bolt M10x40 Grade 10.9": {
      "compatible_parts":  ["Bolt M10x40 Grade 8.8"],
      "alternate_variant": "model_b_alt1.json",
      "display_message":   "Grade 8.8 bolt approved — continuing with alternate spec"
    }
  }
}
```

If `alternate_variant` is `null`, the substitute is accepted in-place with no config change. If set, the entire variant config hot-swaps.

#### Events Emitted

| type | Trigger |
|---|---|
| `reroute_triggered` | Valid substitute found |
| `alternate_loaded` | Alternate config loaded and activated |
| `substitute_rejected` | Picked part is not a valid substitute |
| `reroute_error` | Alternate variant file not found on disk |

---

### 6.6 `fleet_api.py` — REST Fleet Management

Flask Blueprint mounted at the app root. All endpoints prefixed `/api/fleet/`.

| URL | Method | Description |
|---|---|---|
| `/api/fleet/trolleys` | GET | All carts with live status |
| `/api/fleet/mode` | POST | Set mode: `"standalone"` or `"linked"` |
| `/api/fleet/configs` | GET | List all `.json` files in `configs/` |
| `/api/fleet/variant/push` | POST | Push a variant (by filename or inline dict) to one or all carts |
| `/api/fleet/variant/activate` | POST | Start the loaded variant on a cart |
| `/api/fleet/linked/start` | POST | Start a linked sequence across both trolleys |
| `/api/fleet/emergency_stop` | POST | Reset all carts immediately |
| `/api/fleet/<cart_id>/status` | GET | Health check for one cart |
| `/api/fleet/<cart_id>/logs` | GET | Event history for one cart (`?n=50`) |

---

### 6.7 `app.py` — Flask Application Server

Process entry point. Creates hardware backends, wires the coordinator and rerouter, registers the fleet Blueprint, and hosts the ESP32 WebSocket bridge.

#### Browser URLs

| URL | Purpose |
|---|---|
| `http://localhost:5000/` | Operator HMI |
| `http://localhost:5000/operator` | Dual-trolley operator HMI |
| `http://localhost:5000/supervisor` | Supervisor dashboard |
| `http://localhost:5000/manager` | Fleet control dashboard |

#### ESP32 Bridge Endpoint

```python
@sock.route("/ws_esp")
def ws_esp(ws):
    hw_small.set_send_fn(lambda cmd: ws.send(f">c{cmd}<"))
    try:
        while True:
            raw = ws.receive()
            if raw and raw.startswith(">t") and raw.endswith("<"):
                hw_small.handle_serial_line(raw[2:-1].strip())
    finally:
        hw_small.clear_send_fn()
```

The lambda closes over `ws`. On ESP32 reconnect, a new lambda is injected automatically — zero manual intervention needed.

#### Startup Sequence

1. `SIMULATION_MODE` evaluated from env
2. `create_hardware()` returns appropriate backend
3. `ArduinoHardware` (if active) starts weight polling thread
4. `TrolleyCoordinator` and `ReroutingEngine` created and wired
5. Emergency stop callback registered on `hw_small`
6. Fleet Blueprint registered
7. `socketio.run(app, host="0.0.0.0", port=5000)` starts server
8. ESP32-B connects to `/ws_esp`; send function injected
9. Browser clients connect and request snapshots

---

### 6.8 `cad_to_json_converter.py` — CAD Import Tool

Standalone PyQt6 desktop application. Converts STEP, IGES, and STL assembly files into variant JSON configs. **Separate process from the Flask server.**

#### Supported Formats

| Extension | Standard |
|---|---|
| `.stp`, `.step` | STEP AP214 / AP242 |
| `.igs`, `.iges` | IGES |
| `.stl` | ASCII STL (binary STL has no part names) |

#### Sequence Heuristic

Parts auto-sorted by assembly priority via keyword matching:

| Priority | Category | Example keywords |
|---|---|---|
| 0 | Structural | frame, housing, body, base, chassis |
| 1 | Brackets | bracket, plate, flange, panel, arm |
| 2 | Moving parts | shaft, gear, bearing, piston, rod |
| 3 | Fasteners | bolt, screw, stud, pin, rivet |
| 4 | Hardware | washer, nut, clip, seal, gasket, spring |

The engineer can reorder steps by dragging rows in the table before exporting.

> **Note:** `unit_weight_g` is set to `0.0` in the exported config — the converter has no way to know part weights from geometry alone. Weigh one physical sample and fill in the value before deploying.

---

## 7. Variant JSON Configuration

All product-specific knowledge lives in JSON files. A new product variant is a new JSON file, not a code change.

### Standalone Variant

```json
{
  "variant_id":            "MODEL_A",
  "variant_name":          "Model A Sub-Assembly Kit",
  "description":           "6-step linear pick sequence",
  "operating_mode":        "standalone",
  "cycle_time_target_sec": 120,
  "bins": [
    {
      "bin_id":        1,
      "part_name":     "Bolt M10x40",
      "part_number":   "PN-001",
      "initial_qty":   20,
      "unit_weight_g": 15.2
    }
  ],
  "pick_sequence": [
    {
      "step":        1,
      "bin_id":      1,
      "qty":         4,
      "instruction": "Pick 4 bolts from bin 1"
    }
  ]
}
```

### Reroutable Variant (adds substitution rules)

```json
{
  "operating_mode": "reroutable",
  "substitution_rules": {
    "Bolt M10x40 Grade 10.9": {
      "compatible_parts":  ["Bolt M10x40 Grade 8.8"],
      "alternate_variant": "model_b_alt1.json",
      "display_message":   "Grade 8.8 approved — continuing with alternate spec"
    }
  }
}
```

### Linked Variant (multi-trolley)

```json
{
  "operating_mode": "linked",
  "trolley_group":  ["SMALL_A01", "LARGE_A01"],
  "trolleys": {
    "SMALL_A01": { "bins": [ ... ] },
    "LARGE_A01": { "bins": [ ... ] }
  },
  "pick_sequence": [
    { "step": 1, "trolley_id": "SMALL_A01", "bin_id": 1, "qty": 4 },
    { "step": 2, "trolley_id": "LARGE_A01", "bin_id": 1, "qty": 1 }
  ]
}
```

### Deploying a New Variant

1. Place the `.json` file in `configs/`
2. Open the Manager dashboard → select the config → Push → Activate

No server restart. No hardware changes. The new sequence begins immediately.

---

## 8. Data Flow: Complete Pick Cycle

### Step Start

```
coordinator.activate("SMALL_A01")
  → engine.start()
  → _advance_to_next_step()
  → hw.set_led(bin_id, "active")    → LED:N:active → Arduino → NeoPixel white
  → hw.set_display(bin_id, qty)     → DISP:N:count → Arduino → TM1637
  → socketio.emit("STEP_STARTED")   → all browsers update
```

### Pick + Confirmation

```
Operator reaches into bin
  → IR beam breaks
  → Arduino: IR_TRIGGERED:N → ESP32-B → >tIR_TRIGGERED:N< → /ws_esp
  → handle_serial_line() fires _ir_callbacks[bin_id]
  → on_ir_break(): correct bin → no action (weight monitor owns confirmation)

Parts removed → weight drops
  → Arduino streams WEIGHT:N:grams → _weights[bin_id] cache updated
  → weight monitor (300ms poll): remaining = 0
  → hw.set_led(bin_id, "green") + hw.play_audio("chime_ok")
  → PICK_CORRECT emitted → browsers update step counter

IR beam restores (hand leaves bin)
  → IR_CLEARED:N → _on_ir_clear() → _try_complete_step()
  → _step_active = False (stops monitor thread)
  → STEP_COMPLETED emitted
  → 300ms timer → next step starts
```

### Wrong-Bin Pick

```
IR fires on wrong bin
  → on_ir_break(): bin_id ≠ current step's bin_id
  → reroutable config? → check substitution rules → redirect or reject
  → otherwise: set_led("error") + set_buzzer(True) + 600ms buzz
  → PICK_WRONG_BIN emitted → browser flashes error
  → 1.5s timer → LED cleared
  → operator must pick correct bin (sequence does not advance)
```

---

## 9. Operating Modes

### Standalone Mode

Each trolley runs its own independent variant sequence. The manager can push different variants to `SMALL_A01` and `LARGE_A01` simultaneously.

### Linked Mode

Both trolleys run phases of a single assembly sequence. The coordinator manages handoffs between trolleys — when `SMALL_A01` finishes its phase, `LARGE_A01` activates automatically, and vice versa.

To start a linked sequence via the fleet API:

```
POST /api/fleet/mode         { "mode": "linked" }
POST /api/fleet/linked/start { "filename": "linked_excavator.json" }
```

---

## 10. Tech Stack Reference

### Python Backend

| Package | Used for |
|---|---|
| Flask 3.x | HTTP routing and static file serving |
| flask-socketio 5.x | Browser bidirectional WebSocket (Socket.IO protocol) |
| flask-sock 0.7+ | Raw RFC 6455 WebSocket for ESP32 bridge |
| PyQt6 6.x | CAD-to-JSON converter GUI |

### Python Standard Library

| Module | Used for |
|---|---|
| `abc` | `HardwareInterface` ABC |
| `threading` | Weight monitor, stuck timer, advance timer |
| `dataclasses` | `EngineState`, `PickEvent` |
| `json` | Variant configs and API payloads |
| `re` | CAD file parsing, display text parser |
| `collections` | Part deduplication in CAD converter |

### Embedded

| Library | Used for |
|---|---|
| HX711 (Arduino) 0.7.5 | Load cell ADC |
| TM1637Display 1.x | 7-segment display driver |
| Adafruit NeoPixel 1.x | WS2812B LED strip |
| WebSocketsClient (ESP32) 2.x | ESP32-B WebSocket client |

### Communication

| Link | Protocol | Speed |
|---|---|---|
| Arduino ↔ ESP32-B | UART Serial2 | 9600 baud |
| ESP32-B ↔ Laptop | WebSocket RFC 6455 | WiFi 2.4 GHz |
| Laptop ↔ Browser | Socket.IO over WebSocket | Local network |
| Browser → Laptop | HTTP REST | Local network |

---

## 11. Architecture Principles

1. **Hardware abstraction is the central design pattern.** The `HardwareInterface` ABC is the reason the same codebase runs identically in simulation and on hardware. The HAL is the only thing that changes between environments.

2. **JSON as the changeover mechanism.** All product-specific knowledge lives in JSON files. The code is entirely variant-agnostic. A new product model is a new JSON file, not a code change.

3. **Dual-sensor confirmation.** IR detects intent; weight confirms outcome. Neither alone is sufficient — IR alone cannot confirm pick quantity; weight alone cannot identify which bin was accessed.

4. **Triple feedback.** LED + audio + display gives redundant operator feedback. The system works even if the operator misses any one channel.

5. **Zero added cognitive load.** The operator has exactly one task: pick the lit bin. Every other decision is handled by the system.

6. **Classical algorithms for deterministic problems.** Rerouting uses a substitution rule lookup — not ML. The rerouting problem has a known structure; ML cannot improve on a dict access and would make it harder to audit by a quality engineer.

7. **Fail-safe defaults everywhere.** Simulation mode is the default. Dropped WiFi commands are non-fatal. The weight monitor has a 5-second force-advance timeout. The Arduino watchdog clears all outputs on disconnect. Every failure mode has a defined safe fallback.
