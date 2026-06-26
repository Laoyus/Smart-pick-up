# Smart Assistive Part Pick System — Project Documentation

> A sensor-guided, variant-agnostic kit-trolley system that directs assembly-line operators to pick the right part, in the right quantity, in the right sequence — with zero added cognitive load.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture](#2-system-architecture)
3. [Hardware Layer](#3-hardware-layer)
4. [WiFi Bridge Architecture](#4-wifi-bridge-architecture)
5. [Software Stack](#5-software-stack)
   - 5.1 [sequence\_engine.py — Pick State Machine](#51-sequence_enginepy--pick-state-machine)
   - 5.2 [hardware\_abstraction.py — HAL](#52-hardware_abstractionpy--hardware-abstraction-layer)
   - 5.3 [arduino\_hardware.py — Real Hardware Backend](#53-arduino_hardwarepy--real-hardware-backend)
   - 5.4 [trolley\_coordinator.py — Multi-Trolley Orchestration](#54-trolley_coordinatorpy--multi-trolley-orchestration)
   - 5.5 [rerouting\_engine.py — Adaptive Path Recovery](#55-rerouting_enginepy--adaptive-path-recovery)
   - 5.6 [fleet\_api.py — REST Fleet Management](#56-fleet_apipy--rest-fleet-management)
   - 5.7 [app.py — Flask Application Server](#57-apppy--flask-application-server)
   - 5.8 [cad\_to\_json\_converter.py — CAD Import Tool](#58-cad_to_json_converterpy--cad-import-tool)
6. [Variant JSON Configuration](#6-variant-json-configuration)
7. [Data Flow: Complete Pick Cycle](#7-data-flow-complete-pick-cycle)
8. [Operating Modes](#8-operating-modes)
9. [Tech Stack Reference](#9-tech-stack-reference)
10. [Running the System](#10-running-the-system)

---

## 1. Project Overview

### Problem

Modern assembly lines use kit trolleys where all parts for a workstation are pre-staged. Operators must still refer to work instructions to answer three questions on every step:

1. **Which part do I pick next?**
2. **How many do I pick?**
3. **In what sequence?**

In multi-variant assemblies — where different product configurations pass through the same workstation — this causes lost time, cognitive overload, and assembly errors that grow worse as product complexity increases and operator experience varies.

### Solution

The Smart Assistive Part Pick System eliminates work instruction dependency by embedding guidance directly into the kit trolley:

- A **NeoPixel LED strip** on each bin lights up to indicate which bin to pick from.
- A **TM1637 display** shows the quantity remaining.
- An **audio buzzer** gives confirmation or error feedback.
- An **IR break-beam sensor** detects hand entry into a bin.
- An **HX711 load cell** confirms the pick by measuring weight loss.

The system advances to the next step automatically when the correct quantity is confirmed. The operator's only task is to pick the lit bin.

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

---

## 2. System Architecture

The system has four layers:

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

---

## 3. Hardware Layer

### Physical Shelf Layout

The shelf has 6 compartments in a row, **0-indexed on the Arduino** and **1-indexed in Python**. Each compartment contains:

- One WS2812B NeoPixel strip (8 LEDs)
- One IR break-beam sensor pair
- One bar-type load cell with a bare HX711 ADC module
- One TM1637 4-digit 7-segment display

All six compartments share one Arduino Mega 2560, one active buzzer, and one emergency stop button.

### Arduino Pin Assignment

| Component | Pins | Notes |
|---|---|---|
| IR sensors (×6) | 22, 23, 24, 25, 26, 27 | INPUT\_PULLUP; LOW = beam broken |
| Active buzzer | 40 | tone() at 2.5 kHz |
| Emergency stop | 41 | INPUT\_PULLUP; LOW = pressed |
| HX711 DOUT (×6) | 3, 5, 7, 9, 11, 13 | Odd pins |
| HX711 SCK (×6) | 2, 4, 6, 8, 10, 12 | Even pins |
| TM1637 CLK (×6) | 28, 30, 32, 34, 36, 38 | Even pins |
| TM1637 DIO (×6) | 29, 31, 33, 35, 37, 39 | Odd pins |
| NeoPixel data (×6) | 44, 45, 46, 47, 48, 49 | One strip per bin |
| ESP32-B RX (Serial2) | 17 | Receives commands from laptop |
| ESP32-B TX (Serial2) | 16 | Sends events to laptop |

### HX711 Load Cell Wiring

| Load cell wire | Connects to | Notes |
|---|---|---|
| Red (E+) | HX711 E+ | Excitation positive |
| Black (E-) | HX711 E- | Excitation negative |
| White (A-) | HX711 A- | Signal negative |
| Green (A+) | HX711 A+ | Signal positive |
| VCC / GND | Arduino 5V / GND | — |
| DOUT / SCK | Arduino data pins | See pin table above |

> **Critical constraint:** Never use HX711 display boards (blue modules with onboard 7-segment display). They share DOUT/SCK lines with their onboard chip, which corrupts readings when a bare HX711 module is connected to the same load cell. Use only bare green-PCB HX711 modules.

### Calibration Factors

Each load cell has a unique sensitivity. Calibrate with `weight_measure.ino` before deploying.

| Bin (Arduino 0-indexed) | Sign note |
|---|---|
| 0, 1, 2 | Positive (normal orientation) |
| 3, 4, 5 | Negative (load cells mounted inverted) |

Negative factors yield correct positive gram values — the HX711 library handles this correctly.

### LED State Vocabulary

| State | RGB | Meaning |
|---|---|---|
| `active` | (255, 255, 255) white | Pick this bin now |
| `green` | (0, 255, 0) green | Correct quantity confirmed |
| `error` | (255, 0, 0) red | Wrong bin or over-pick |
| `done` | off | Step complete, sequence moved on |
| `off` | off | Idle / not in use this variant |
| `idle` | dim white | Bin exists but not current step |

### Audio Cue Vocabulary

| Cue name | Hardware mapping | Trigger |
|---|---|---|
| `chime_next` | BUZZ:50 ms | Step advance |
| `chime_ok` | BUZZ:100 ms | Correct pick confirmed |
| `chime_done` | BUZZ:300 ms | Variant complete |
| `buzz_error` | BUZZ:600 ms via `set_buzzer` | Wrong pick / over-pick |

Duration encodes severity: short chimes are positive feedback; the long burst is an error signal audible over factory floor noise.

---

## 4. WiFi Bridge Architecture

### Two-ESP32 Design

| Board | Role | Key detail |
|---|---|---|
| ESP32-A | WiFi Access Point | SSID: `MyESPNetwork`, password: `12345678`, subnet: 192.168.4.x |
| ESP32-B | UART relay + WS client | Joins ESP32-A's network; connects to laptop at `ws://192.168.4.3:5000/ws_esp` |

Running SoftAP and WebSocket client simultaneously on one ESP32 causes radio instability. Splitting the roles across two boards gives each a single responsibility and a stable radio state.

### Voltage Divider on UART

The Arduino Mega's TX2 (pin 16) outputs 5V logic. The ESP32's GPIO16 (RX2) is 3.3V maximum. A voltage divider is used on the Arduino→ESP32 direction:

```
Arduino TX2 → [10kΩ] → junction → ESP32 GPIO16
                         junction → [20kΩ] → GND
```

Output: `5 × 20/(10+20) = 3.33V` — within ESP32 tolerance. The ESP32→Arduino direction is direct (3.3V logic reliably reads as HIGH on the 5V Arduino).

### Frame Protocol

| Direction | Frame | Example |
|---|---|---|
| Arduino → Laptop | `>t`*payload*`<` | `>tIR_TRIGGERED:2<` |
| Laptop → Arduino | `>c`*cmd*`<` | `>cLED:3:active<` |

`>t` = telemetry (data from hardware); `>c` = command (data to hardware). The ESP32-B relay passes bytes between Serial2 and WebSocket in both directions, adding or stripping the frame wrapper without parsing the content.

### ESP32 LED Status

| LED behaviour | Meaning |
|---|---|
| Slow blink (500 ms) | WiFi connecting to ESP32-A AP |
| Fast blink (150 ms) | WiFi connected, WebSocket connecting to laptop |
| Solid ON | Full link up, relaying data |
| Solid OFF | WebSocket lost, retrying |

### Self-Recovery on Disconnect

When ESP32-B reconnects after a drop, `app.py` automatically re-injects a fresh send function into `ArduinoHardware`. The sequence engine continues running on cached state and resumes sending commands the moment the link is restored. No manual restart is needed.

---

## 5. Software Stack

### 5.1 `sequence_engine.py` — Pick State Machine

The core state machine. It consumes a variant JSON config, drives per-bin hardware feedback, and deterministically advances the operator through the pick sequence using a weight-based dual-confirmation gate.

#### Position in the Stack

```
Layer above ← events / snapshot    Browser HMI, Supervisor
SequenceEngine                      (this file)
Layer below → LED / weight / IR     HardwareInterface
```

#### Data Structures

**`EventType` (Enum)**

All events inherit from `str` so values are directly JSON-serialisable.

```python
class EventType(str, Enum):
    VARIANT_LOADED     = "variant_loaded"
    STEP_STARTED       = "step_started"
    STEP_COMPLETED     = "step_completed"
    PICK_CORRECT       = "pick_correct"
    PICK_WRONG_BIN     = "pick_wrong_bin"
    PICK_QTY_MISMATCH  = "pick_qty_mismatch"
    PICK_OVERPICK      = "pick_overpick"
    GHOST_IR           = "ghost_ir"
    VARIANT_COMPLETE   = "variant_complete"
    OPERATOR_STUCK     = "operator_stuck"
    REROUTE_TRIGGERED  = "reroute_triggered"
```

**`EngineState` (dataclass)**

All mutable runtime state lives here, separated from the engine class so it can be reset atomically with `self.state = EngineState(...)`.

```python
@dataclass
class EngineState:
    variant_id:          str = ""
    variant_name:        str = ""
    current_step_idx:    int = -1          # -1 = not started
    total_steps:         int = 0
    step_started_at:     Optional[float] = None
    variant_started_at:  Optional[float] = None
    errors:              int = 0
    correct_picks:       int = 0
    events:              List[PickEvent] = field(default_factory=list)
    bin_qty_remaining:   Dict[int, int]  = field(default_factory=dict)
```

#### Public API

| Method | Description |
|---|---|
| `load_variant(config_path_or_dict)` | Accepts a file path string or a pre-parsed dict. Resets state, primes bins, registers IR callbacks, emits `VARIANT_LOADED`. |
| `start()` | Stamps `variant_started_at`, sets `current_step_idx = -1`, kicks off step 0. |
| `reset()` | Stops monitor thread, cancels stuck timer, turns all LEDs off, replaces state with blank `EngineState`. |

#### Confirmation Flow — The Weight Monitor

```
LED WHITE → Operator picks → Weight drops → Count reaches 0
→ LED GREEN → IR clears → Advance
```

The weight monitor polls `hw.read_weight()` every **300 ms** (the HX711 ADC samples at ~10 Hz; 300 ms gives three distinct readings per second with no redundant calls).

**Over-pick is recoverable.** When `remaining < 0`, the monitor emits `PICK_OVERPICK` and flashes red — but does **not** set `_step_green`. The step stays open so the operator can return parts and continue normally.

**One-shot gate.** Two threads race to complete a step: the weight monitor (count hits 0, IR already clear) and `_on_ir_clear()` (beam restores after count hit 0). A `threading.Lock()` ensures only one wins and the step advances exactly once.

#### Class-Level Constants

| Constant | Value | Meaning |
|---|---|---|
| `STUCK_THRESHOLD_SEC` | 30 s | No-pick warning threshold |
| `GREEN_IR_TIMEOUT_SEC` | 5 s | Force-advance after GREEN if hand never leaves bin |

#### Threading Model

| Thread | Created by | Lifetime |
|---|---|---|
| Weight monitor | `_start_weight_monitor()` | One per active step; exits when `_step_active = False` |
| Stuck timer | `threading.Timer` | Restarted each step; cancelled on correct pick or reset |
| Advance timer | `threading.Timer(0.3, ...)` | One-shot 300 ms delay before next step |
| LED clear timer | `threading.Timer(1.5, ...)` | Clears wrong-bin error LED after 1.5 s |

---

### 5.2 `hardware_abstraction.py` — Hardware Abstraction Layer

Defines the contract between the sequence engine and all physical or simulated hardware. Contains three things: the `HardwareInterface` ABC, the `MockHardware` simulation backend, and the `create_hardware()` factory.

#### `HardwareInterface` — The ABC

```python
class HardwareInterface(ABC):
    @abstractmethod
    def set_led(self, bin_id: int, color: str) -> None: ...

    @abstractmethod
    def set_display(self, bin_id: int, text: str) -> None: ...

    @abstractmethod
    def read_weight(self, bin_id: int) -> float: ...

    @abstractmethod
    def play_audio(self, cue: str) -> None: ...

    @abstractmethod
    def set_buzzer(self, on: bool) -> None: ...

    @abstractmethod
    def register_ir_callback(self, bin_id: int, callback: Callable[[int], None]) -> None: ...
```

Using `ABC` and `@abstractmethod` means any class that inherits `HardwareInterface` but does not implement all six methods raises a `TypeError` at instantiation time — not silently at the first method call.

#### Method Contracts

| Method | Contract |
|---|---|
| `set_led(bin_id, color)` | Set visual indicator. `color` is one of: `off`, `idle`, `active`, `done`, `error`, `green`. |
| `set_display(bin_id, text)` | Update per-bin count display. Backend extracts the integer if needed. |
| `read_weight(bin_id) → float` | Return current weight in grams. **Must be non-blocking** — called every 300 ms by the weight monitor thread. |
| `play_audio(cue)` | Play a named audio cue. Valid: `chime_ok`, `chime_next`, `chime_done`, `buzz_error`. |
| `set_buzzer(on)` | Fire (`True`) or silence (`False`) the error buzzer. |
| `register_ir_callback(bin_id, callback)` | Store callback invoked when IR break-beam on `bin_id` fires. |

#### Optional Methods (not in ABC)

| Method | Notes |
|---|---|
| `prime_bin(bin_id, unit_weight_g, qty)` | Set initial weight in cache. Engine calls via `hasattr`. |
| `register_ir_clear_callback(bin_id, cb)` | Register beam-restore callback for GREEN + hand-out gate. |
| `is_ir_triggered(bin_id) → bool` | Query current beam state. |
| `simulate_pick(bin_id, qty, unit_weight_g)` | Browser manual override. Both backends implement this. |
| `simulate_ir_only(bin_id)` | Fire IR without weight change. Tests ghost-IR rejection. |
| `reset()` | Clear all state. Called by coordinator on variant change or emergency stop. |

#### `MockHardware` — Simulation Backend

Has no GPIO, no serial, no audio. All actions are broadcast to connected browsers via a `Flask-SocketIO` emit function injected at construction time.

```python
class MockHardware(HardwareInterface):
    def __init__(self, emit_fn: Callable[[str, dict], None],
                 cart_id: str = "SMALL_A01"):
        self._emit = emit_fn
        self.cart_id = cart_id
        self._weights: Dict[int, float] = {}
        self._ir_callbacks: Dict[int, Callable[[int], None]] = {}
```

`emit_fn` is `socketio.emit` passed from `app.py`. The mock never imports Flask — it only calls the injected function.

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

Key design decisions:
- **Default is simulation** — running without the env var never accidentally activates hardware mode.
- **`LARGE_A01` always gets `MockHardware`** — regardless of `SIMULATION_MODE`, preventing `ArduinoHardware` from attempting to open a second serial port.
- **Lazy import of `ArduinoHardware`** — simulation mode never imports `arduino_hardware.py`, so missing `pyserial` or `websockets` dependencies do not cause import errors on machines without those packages.

---

### 5.3 `arduino_hardware.py` — Real Hardware Backend

Drop-in replacement for `MockHardware` that communicates with the Arduino Mega 2560 either over direct USB serial or wirelessly via the two-ESP32 WiFi bridge.

#### Serial Protocol

**Arduino → Laptop (events)**

| Line | Meaning |
|---|---|
| `READY` | Arduino boot complete |
| `IR_TRIGGERED:N` | IR break-beam on bin N (0-indexed) fired |
| `IR_CLEARED:N` | IR beam on bin N restored |
| `WEIGHT:N:grams` | HX711 reading for bin N in grams (one decimal place) |
| `TARED:N` | Confirmation that a TARE:N command was executed |
| `NOT_READY:N` | HX711 chip for bin N not yet ready |
| `EMERGENCY_STOP` | Physical e-stop button pressed |

**Laptop → Arduino (commands)**

| Command | Effect |
|---|---|
| `LED:N:state` | Set NeoPixel strip N to `active`/`error`/`green`/`done`/`off` |
| `DISP:N:count` | Show integer count on TM1637 display N (0-indexed) |
| `BUZZ:duration_ms` | Fire buzzer at 2.5 kHz for duration ms (non-blocking on Arduino) |
| `TARE:N` | Re-zero load cell N |
| `RAW:N` | Return raw ADC count for bin N (calibration) |
| `CAL:N:factor` | Set runtime calibration factor for bin N |
| `0`–`5` | Poll weight for bin N on demand |
| `RESET` | Clear all outputs immediately |

> **Index convention:** Arduino is 0-indexed (bin 0–5); Python is 1-indexed (bin\_id 1–6). `ArduinoHardware` performs all conversion: `bin_id - 1` outbound, `bin_idx + 1` inbound.

#### Dual-Transport Architecture

Transport is selected at startup via environment variable:

```python
override = os.getenv("TRANSPORT_MODE", "wifi").lower()
self._transport = override if override in ("wifi", "usb") else "wifi"
```

**WiFi transport path:**
```
Arduino Mega
  | UART Serial2 (pins 16/17, 9600 baud)
  v
ESP32-B (relay board)
  | WiFi → WebSocket to ws://192.168.4.3:5000/ws_esp
  v
app.py Flask server
  | calls hw.set_send_fn(ws.send)
  | calls hw.handle_serial_line()
  v
ArduinoHardware
```

**USB transport path:**
```
Arduino Mega
  | USB serial (COM6 / /dev/ttyUSB0, 9600 baud)
  v
pyserial Serial object → _read_loop() daemon thread
  v
handle_serial_line()  ← same parser as WiFi path
```

`handle_serial_line()` is the single parser for both paths — a bug fix in one path fixes both.

#### Weight Polling Thread

Polls all 6 bins in a round-robin loop with 200 ms between each request. One full cycle takes ~1.2 s. The Arduino also pushes weights automatically every 2 s, so polling is redundant during normal operation but ensures the cache stays fresh when the push stream misses a cycle.

#### Key Design Decisions

1. **Inject, don't import.** `app.py` injects `set_send_fn(ws.send)` rather than this class importing Flask. Keeps `ArduinoHardware` transport-agnostic and independently testable.
2. **Weight cache is always non-blocking.** `read_weight()` is an O(1) dict lookup. The HX711 stream updates `_weights` asynchronously — a blocking ADC read here would stall the 300 ms monitor thread.
3. **Dropped commands are non-fatal.** If ESP32 disconnects mid-operation, `_send()` logs a warning and returns. The engine continues running. When ESP32 reconnects, commands resume transparently.
4. **0-indexed Arduino, 1-indexed Python.** All conversion is centralised inside this class — no other file in the Python stack deals with 0-indexed bin numbers.

---

### 5.4 `trolley_coordinator.py` — Multi-Trolley Orchestration

The orchestration layer that manages multiple `SequenceEngine` instances simultaneously. Neither `app.py` nor the fleet API ever call `SequenceEngine` methods directly.

```
app.py / fleet API  →  TrolleyCoordinator
TrolleyCoordinator  →  SequenceEngine (per trolley)
TrolleyCoordinator  →  routes events to Browser + ReroutingEngine
```

#### Module Structure

| Class | Role |
|---|---|
| `TrolleyStatus` | Five string constants: `IDLE`, `LOADED`, `RUNNING`, `DONE`, `ERROR` |
| `TrolleyInstance` | Internal container: hardware ref, engine, status, config ID, event history (last 200) |
| `TrolleyCoordinator` | Main orchestrator: variant lifecycle, operating modes, event routing |

#### Trolley State Machine

```
IDLE → LOADED → RUNNING → DONE
  ↑__________________________|   (reset_trolley / reset_all from any state)
```

#### Variant Lifecycle (Standalone Mode)

- **`push_variant(cart_id, config)`** — Resets the engine, loads the new config. Moves trolley to `LOADED`. Does not start the sequence.
- **`activate(cart_id)`** — Guards against starting without a loaded config. Sets trolley to `RUNNING`.
- **`reset_trolley(cart_id)`** / **`reset_all()`** — Returns trolley(s) to `IDLE`. `reset_all()` also clears linked-mode state and tells the rerouting engine to discard loaded rules.

#### Linked Mode

Both trolleys execute phases of a single assembly sequence. `_build_phases()` groups consecutive steps with the same `trolley_id` into phases:

```
[S, S, L, L, S] → [(SMALL, steps 1-2), (LARGE, steps 3-4), (SMALL, step 5)]
```

Each phase is executed as a standalone **mini-config** — the engine is completely unaware it is running a phase. When a phase completes (`VARIANT_COMPLETE`), the coordinator intercepts the event (swallowing it from the browser), advances to the next phase, and the browser instead receives a `trolley_activated` coordinator event.

#### Event Routing: `_on_engine_event()`

Every event from every engine flows through this method in order:

1. **Log** to trolley event history (cap at 200)
2. **Sync hardware qty** on `STEP_STARTED`
3. **Rerouting check** — on `PICK_WRONG_BIN`, offer to rerouting engine
4. **Surface reroute events** to browser dashboards
5. **Linked phase gate** — in linked mode, `VARIANT_COMPLETE` means phase done, not sequence done; swallow and advance
6. **Broadcast** everything else to browser as `engine_event`
7. **Mark done** — in standalone mode, `VARIANT_COMPLETE` sets trolley to `DONE`

#### Threading

A single `Lock()` protects `_linked_phase_idx` inside `_advance_linked_phase()`. This prevents a race condition where two `VARIANT_COMPLETE` events from two engine threads both try to advance the phase index simultaneously.

---

### 5.5 `rerouting_engine.py` — Adaptive Path Recovery

Handles pick-path recovery when an operator picks from the wrong bin. Uses a deterministic rule lookup — not machine learning.

#### Design Rationale: Classical, Not ML

The rerouting problem is: given that the operator picked part X instead of expected part Y, is X a valid substitute? This is a **deterministic constraint-satisfaction problem**. The set of valid substitutes is defined by engineering specifications — it either complies or it does not. The correct tool is a lookup table (O(1) dict access), not a trained model.

ML would only be defensible in this system for sensor anomaly detection or operator error-rate prediction.

#### Substitution Rules Schema

Rules are defined inside the variant JSON under `substitution_rules`. The key is the expected part name:

```json
{
  "substitution_rules": {
    "Bolt M10x40 Grade 10.9": {
      "compatible_parts": [
        "Bolt M10x40 Grade 8.8",
        "Bolt M10x45 Grade 10.9"
      ],
      "alternate_variant": "linked_excavator_alt1.json",
      "display_message": "Grade 8.8 bolt approved — continuing with alternate spec"
    }
  }
}
```

| Field | Meaning |
|---|---|
| `compatible_parts` | List of valid substitute part names |
| `alternate_variant` | Filename of alternate variant JSON to hot-swap to (`null` = stay on current config) |
| `display_message` | Human-readable message shown on dashboards when accepted |

#### `handle_wrong_pick()` Decision Flow

```
PICK_WRONG_BIN received
  │
  ├── No rules loaded? → return (engine's error handling applies)
  │
  ├── No rule for expected part? → return (engine handles)
  │
  ├── Actual part NOT in compatible_parts?
  │     → emit substitute_rejected
  │
  └── Valid substitute found
        → emit reroute_triggered
        → alternate_variant set? → _load_alternate()
```

#### Two Hot-Swap Modes

| Alt config `operating_mode` | What happens |
|---|---|
| `standalone` | Only the affected trolley resets and restarts from step 1 of the alternate config |
| `linked` | All trolleys reset; coordinator switches to linked mode and starts the alternate linked sequence |

#### Events Emitted

All on the `rerouting_event` channel:

| type | Trigger |
|---|---|
| `reroute_triggered` | Valid substitute found; rerouting will proceed |
| `alternate_loaded` | Alternate config loaded and activated |
| `substitute_rejected` | Picked part is not a valid substitute |
| `reroute_error` | Alternate variant file not found on disk |

#### The Critical Flag (Design Decision)

The variant JSON schema includes a `critical` boolean field on steps, intended to prevent rerouting past safety-critical steps. The `can_defer()` method is intentionally **commented out rather than deleted**. It was designed but deferred because:

1. Defining what "blocked" looks like in the UI required supervisor override design decisions
2. Testing the blocked state in a live environment without accidentally stalling was non-trivial

The commented code remains as a visible design record.

#### Two Rerouting Paths

Two separate mechanisms coexist without conflict:

| Mechanism | Location | Trigger | Level |
|---|---|---|---|
| In-engine rerouting | `sequence_engine.py` | `operating_mode = "reroutable"` configs | Bin-index level; patches current step in-place |
| ReroutingEngine | `rerouting_engine.py` | Substitution rules loaded | Part-name level; can trigger coordinator-level config swaps |

---

### 5.6 `fleet_api.py` — REST Fleet Management

A Flask Blueprint providing wireless variant management. Mounted at the app root via a factory function:

```python
def make_fleet_blueprint(coordinator, rerouter, config_dir: str) -> Blueprint:
    bp = Blueprint("fleet", __name__)
    # ... route definitions close over coordinator, rerouter, config_dir
    return bp
```

Using a factory avoids circular imports: the Blueprint captures live objects in closures rather than importing module-level globals.

#### Endpoints

| URL | Method | Description |
|---|---|---|
| `/api/fleet/trolleys` | GET | All cart IDs with live status, mode, linked-phase info |
| `/api/fleet/mode` | POST | Set operating mode: `"standalone"` or `"linked"` |
| `/api/fleet/configs` | GET | List all `.json` files in `configs/` with metadata |
| `/api/fleet/variant/push` | POST | Push a variant to one cart (`cart_id`) or all (`"all"`). Accepts `filename` or inline `config` dict |
| `/api/fleet/variant/activate` | POST | Start the loaded variant on a specific cart |
| `/api/fleet/linked/start` | POST | Start a linked sequence coordinating both trolleys |
| `/api/fleet/emergency_stop` | POST | Call `coordinator.reset_all()` immediately |
| `/api/fleet/<cart_id>/status` | GET | Health check for one cart |
| `/api/fleet/<cart_id>/logs` | GET | Event history for one cart (`?n=50`) |

**Validation before mutation:** Required config fields are checked before any trolley state is modified. A partial push (failing halfway through multiple targets) is prevented by validating first, then iterating targets.

---

### 5.7 `app.py` — Flask Application Server

The process entry point. Owns the Flask + Flask-SocketIO server, instantiates all hardware backends and the coordinator, wires the ESP32 WebSocket bridge, and registers the fleet Blueprint.

#### Core Features

- Dual-trolley operator HMI at `/operator`
- Supervisor dashboard at `/supervisor`
- Fleet control dashboard at `/manager`
- ESP32 WebSocket bridge at `/ws_esp`
- Simulation API endpoints at `/api/sim/*`
- Fleet REST API Blueprint at `/api/fleet/*`

#### Flask and SocketIO Initialisation

```python
app = Flask(__name__, static_folder="static")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
sock = Sock(app)
```

`async_mode="threading"` is required because the weight monitor thread calls `socketio.emit()` from a background thread.

#### `flask_sock` vs `flask_socketio`

These are two different libraries serving different clients:
- **`flask_socketio`** speaks the Socket.IO protocol (heartbeats, rooms, namespaces, JSON envelopes) — used by browsers.
- **`flask_sock`** speaks raw RFC 6455 WebSocket — used by the ESP32-B, which cannot parse Socket.IO frames.

They coexist on the same Flask app without conflict.

#### Hardware and Coordinator Wiring

```python
hw_small = create_hardware(emit_fn=broadcast_hw, cart_id="SMALL_A01")
hw_large = create_hardware(emit_fn=broadcast_hw, cart_id="LARGE_A01")

coordinator = TrolleyCoordinator(emit_fn=_socketio_emit, config_dir=CONFIG_DIR)
coordinator.register_trolley("SMALL_A01", hw_small)
coordinator.register_trolley("LARGE_A01", hw_large)

rerouter = ReroutingEngine(coordinator=coordinator,
                           emit_fn=_socketio_emit,
                           config_dir=CONFIG_DIR)
coordinator.rerouting_engine = rerouter
```

`rerouting_engine` is injected after construction to avoid a circular constructor dependency.

#### The ESP32 WebSocket Bridge

```python
@sock.route("/ws_esp")
def ws_esp(ws):
    hw_small.set_send_fn(lambda cmd: ws.send(f">c{cmd}<"))
    try:
        while True:
            raw = ws.receive()
            if raw is None:
                break
            if raw.startswith(">t") and raw.endswith("<"):
                line = raw[2:-1].strip()
                hw_small.handle_serial_line(line)
    finally:
        hw_small.clear_send_fn()
```

The lambda closes over `ws`. When ESP32 reconnects, a new lambda with the new connection object is injected automatically — zero manual intervention needed.

#### Startup Sequence

1. `SIMULATION_MODE` evaluated from env at import time
2. `create_hardware()` returns `MockHardware` or `ArduinoHardware`
3. `ArduinoHardware` (if active) starts weight polling thread
4. `TrolleyCoordinator` created and trolleys registered
5. `ReroutingEngine` created and wired into coordinator
6. Emergency stop callback registered on `hw_small`
7. Fleet Blueprint registered on Flask app
8. `socketio.run(app, host="0.0.0.0", port=5000)` starts the server
9. (WiFi mode) ESP32-B connects to `/ws_esp`; send function injected
10. Browser clients connect and request snapshots via `request_snapshot` SocketIO event

---

### 5.8 `cad_to_json_converter.py` — CAD Import Tool

A standalone PyQt6 desktop application that converts STEP, IGES, and STL assembly files into variant JSON configs compatible with the sequence engine.

**This is a separate process** from the Flask server. It runs on an engineer's workstation and writes output directly to the `configs/` directory.

#### Workflow

1. Engineer drops a CAD file onto the import zone
2. Converter parses it and populates the step table automatically
3. Engineer reviews, reorders, and edits as needed
4. Engineer exports a `.json` file to `configs/`
5. Manager dashboard picks it up immediately (fleet API lists all `.json` files in `configs/`)

#### Module Structure

| Class / function | Role |
|---|---|
| `CADParser` | Parses STEP, IGES, and STL files. Pure logic, no UI. |
| `ParseThread` | `QThread` subclass that runs `CADParser.parse()` in background to keep UI responsive |
| `DropZone` | `QFrame` with drag-and-drop file events and click-to-browse |
| `PartsTable` | `QTableWidget` with drag-reorderable rows; renumbers steps after every row move |
| `build_variant_json()` | Converts current table state into the exact JSON dict expected by the sequence engine |
| `MainWindow` | `QMainWindow` that owns all panels and wires signals between components |

#### Supported Formats

| Extension | Standard | Notes |
|---|---|---|
| `.stp`, `.step` | STEP AP214 / AP242 | Used by SolidWorks, CATIA, Fusion 360 |
| `.igs`, `.iges` | IGES | Older exchange format; still used in aerospace |
| `.stl` | STL | ASCII STL only (binary STL has no part names) |

#### Sequence Heuristic

Parts are sorted by assembly priority using keyword matching — deterministic, interpretable, requires no training data:

| Priority | Category | Keywords |
|---|---|---|
| 0 | Structural | frame, housing, body, base, cover, block, mount |
| 1 | Brackets / subassemblies | bracket, plate, flange, panel, shield, arm |
| 2 | Moving parts | shaft, gear, bearing, sleeve, piston, rod |
| 3 | Fasteners | bolt, screw, stud, pin, rivet, key |
| 4 | Hardware | washer, nut, clip, ring, seal, gasket, spring |

#### Key Output Decisions

| Field | Value | Reason |
|---|---|---|
| `unit_weight_g` | `0.0` | Cannot determine from geometry alone; engineer must weigh a real part |
| `initial_qty` | `max(qty * 5, 20)` | Conservative default for shift planning; engineer adjusts |
| `cycle_time_target_sec` | `len(parts) * 30` | Assumes 30 s per step baseline for supervisor dashboard |

#### Running

```bash
pip install PyQt6
python cad_to_json_converter.py
```

No Flask server, no Arduino, no network required. Runs entirely offline.

---

## 6. Variant JSON Configuration

All product-specific knowledge lives in JSON files. Swapping the file changes the entire pick sequence with no hardware or code changes.

### Standalone Variant Schema

```json
{
  "variant_id":            "MODEL_A",
  "variant_name":          "Model A Sub-Assembly Kit",
  "description":           "6-step pick sequence for Model A",
  "operating_mode":        "standalone",
  "cycle_time_target_sec": 120,
  "bins": [
    {
      "bin_id":         1,
      "part_name":      "Bolt M10x40",
      "part_number":    "CAT-1A2B3C",
      "initial_qty":    10,
      "unit_weight_g":  15.2
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
  "operating_mode":  "linked",
  "trolley_group":   ["SMALL_A01", "LARGE_A01"],
  "trolleys": {
    "SMALL_A01": { "bins": [ ... ] },
    "LARGE_A01": { "bins": [ ... ] }
  },
  "pick_sequence": [
    { "step": 1, "trolley_id": "SMALL_A01", "bin_id": 1, "qty": 4 },
    { "step": 2, "trolley_id": "SMALL_A01", "bin_id": 2, "qty": 2 },
    { "step": 3, "trolley_id": "LARGE_A01", "bin_id": 1, "qty": 1 }
  ]
}
```

### Fast Changeover

To change variants at runtime, the manager dashboard:

1. Selects a JSON from the `/api/fleet/configs` list
2. POSTs it to `/api/fleet/variant/push`
3. POSTs `/api/fleet/variant/activate`

No hardware changes. No rewiring. No recalibration.

---

## 7. Data Flow: Complete Pick Cycle

### Step Start

1. `TrolleyCoordinator.activate("SMALL_A01")` calls `engine.start()`
2. `SequenceEngine._advance_to_next_step()` reads step 1 from JSON
3. Calls `hw.set_led(bin_id, "active")` and `hw.set_display(bin_id, str(qty))`
4. `ArduinoHardware.set_led()` sends `LED:N:active` over WebSocket → ESP32-B → Arduino UART
5. Arduino enables TM1637 blink, sets NeoPixel white
6. Engine emits `STEP_STARTED` via `socketio.emit()` → all browsers update

### Operator Picks

1. Operator reaches into lit bin. IR beam breaks.
2. Arduino sends `IR_TRIGGERED:N` over Serial2
3. ESP32-B wraps it: `>tIR_TRIGGERED:N<` → WebSocket → laptop
4. `app.py /ws_esp` strips wrapper, calls `hw.handle_serial_line("IR_TRIGGERED:N")`
5. `ArduinoHardware` converts to 1-indexed, fires `_ir_callbacks[bin_id]`
6. `SequenceEngine._on_ir_break(bin_id)`: correct bin → no action (weight monitor owns confirmation)
7. Operator removes parts. Weight decreases.
8. Arduino streams `WEIGHT:N:grams`; `_weights[bin_id]` cache updated
9. Weight monitor reads cache every 300 ms; `remaining = target - picked` counts down
10. TM1637 display updates via `DISP:N:count` commands

### Pick Confirmation

1. `remaining` reaches 0. Monitor sets `_step_green = True`.
2. `hw.set_led(bin_id, "green")` → `LED:N:green` → Arduino → NeoPixel green
3. `hw.play_audio("chime_ok")` → `BUZZ:100` → 100 ms chime
4. Engine emits `PICK_CORRECT` → browsers update step counter
5. IR beam restores (hand leaves bin). Arduino sends `IR_CLEARED:N`.
6. `_on_ir_clear()` calls `_try_complete_step()`
7. Lock acquired: `_step_active = False` (stops monitor thread)
8. `STEP_COMPLETED` emitted. 300 ms timer fires, next step starts.

### Wrong-Bin Pick

1. IR fires on a bin that is not the current step's bin
2. `_on_ir_break(wrong_bin_id)` detects mismatch
3. If `operating_mode = "reroutable"`: checks substitution rules
4. Otherwise: `hw.set_led(wrong_bin_id, "error")` → NeoPixel red; 600 ms buzz
5. `PICK_WRONG_BIN` emitted → browsers flash error indicator
6. After 1.5 s: LED cleared. Operator must pick correct bin.

---

## 8. Operating Modes

### Standalone Mode

Each trolley runs its own independent variant sequence. The manager can push different variants to `SMALL_A01` and `LARGE_A01` simultaneously.

### Linked Mode

Both trolleys run phases of a single assembly sequence. `SMALL_A01` completes Phase 1 (small fasteners), then `LARGE_A01` activates for Phase 2 (large structural parts).

The `TrolleyCoordinator` manages the phase transition: when the active trolley emits `VARIANT_COMPLETE`, the coordinator intercepts it, advances to the next phase, and activates the next trolley. The browser only sees `trolley_activated` events during the sequence and `linked_complete` at the true end.

**Key design:** Each phase is presented to the engine as a complete standalone mini-config. The engine is entirely unaware of phases — it just runs a sequence to completion. This required zero changes to `sequence_engine.py` to support linked mode.

---

## 9. Tech Stack Reference

### Python Backend

| Package | Version | Used for |
|---|---|---|
| Python | 3.11+ | Entire backend + GUI |
| Flask | 3.x | HTTP server and routing |
| flask-socketio | 5.x | Browser bidirectional WebSocket |
| flask-sock | 0.7+ | Raw WebSocket for ESP32 bridge |
| PyQt6 | 6.x | CAD-to-JSON converter GUI |

### Python Standard Library Modules

| Module | Used for |
|---|---|
| `abc` | `HardwareInterface` ABC |
| `threading` | Weight monitor thread, stuck timer, advance timer |
| `dataclasses` | `EngineState`, `PickEvent` |
| `json` | Variant configs, API payloads |
| `re` | CAD file parsing, display text parser |
| `collections` | Part deduplication in CAD converter |

### Embedded (Arduino)

| Library | Version | Used for |
|---|---|---|
| HX711 (Arduino) | 0.7.5 | Load cell ADC interface |
| TM1637Display | 1.x | 7-segment display driver |
| Adafruit NeoPixel | 1.x | WS2812B LED strip driver |

### Embedded (ESP32)

| Library | Version | Used for |
|---|---|---|
| WebSocketsClient | 2.x | ESP32-B WebSocket client |
| WiFi (ESP32 core) | built-in | AP and station modes |

### Communication Protocols

| Link | Protocol | Speed | Content |
|---|---|---|---|
| Arduino ↔ ESP32-B | UART Serial2 | 9600 baud | ASCII line-delimited events + commands |
| ESP32-B ↔ Laptop | WebSocket RFC 6455 | WiFi 2.4 GHz | `>t...<` and `>c...<` frames |
| Laptop ↔ Browser | Socket.IO over WebSocket | Local network | JSON event envelopes |
| Browser → Laptop | HTTP REST | Local network | `/api/fleet/*` endpoints |
| CAD Converter → Configs | File I/O | Local disk | JSON variant config files |

---

## 10. Running the System

### Simulation Mode (default)

```bash
python app.py
```

```
http://localhost:5000/          ← Operator HMI
http://localhost:5000/operator  ← Dual-trolley operator HMI
http://localhost:5000/supervisor ← Supervisor dashboard
http://localhost:5000/manager   ← Fleet control dashboard
```

### Hardware Mode (via WiFi bridge)

```bash
SIMULATION_MODE=false python app.py
```

### Hardware Mode (USB serial fallback)

```bash
SIMULATION_MODE=false TRANSPORT_MODE=usb ARDUINO_PORT=COM6 python app.py
```

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SIMULATION_MODE` | `true` | Set to `false` to activate `ArduinoHardware` for `SMALL_A01` |
| `TRANSPORT_MODE` | `wifi` | `wifi` or `usb` — selects serial transport for `ArduinoHardware` |
| `ARDUINO_PORT` | `COM3` | Serial port (Windows: `COM6`; Linux: `/dev/ttyUSB0`) |

> **Safe default:** The default is simulation. Running without any env vars never accidentally activates hardware mode or attempts to open a serial port.

### Hardware Build Order

When setting up a physical shelf, follow this order to de-risk integration:

1. Calibrate each load cell using `weight_measure.ino`
2. Test each IR sensor (verify INPUT\_PULLUP logic and debounce)
3. Test each TM1637 display (verify brightness, blink, segment codes)
4. Test each NeoPixel strip (verify data pin and colour mapping)
5. Wire and test the buzzer
6. Build and test one full compartment end-to-end with `arduino_bridge.ino`
7. Scale to 6 compartments and load a full variant JSON

Validate each component in isolation before integration to minimise multi-component debugging.

### CAD Converter

```bash
pip install PyQt6
python cad_to_json_converter.py
```

Drop a `.stp`, `.step`, `.igs`, `.iges`, or `.stl` file onto the import zone. Review and reorder the auto-generated step table, fill in `unit_weight_g` for each part after weighing a physical sample, then export to `configs/`.

---

## Architecture Principles

Seven principles governed every design decision in this project:

1. **Hardware abstraction as the central pattern.** The `HardwareInterface` ABC is the reason the same codebase runs in simulation and on hardware with zero engine-level changes. The HAL is the only thing that changes between environments.

2. **JSON as the changeover mechanism.** All product-specific knowledge lives in JSON files. A new product variant is a new JSON file, not a code change.

3. **Dual-sensor confirmation.** IR detects intent; weight confirms outcome. Neither alone is sufficient. Together they eliminate false positives (ghost IR) and wrong-quantity picks.

4. **Triple feedback.** LED + audio + display gives redundant operator feedback. The system works even if the operator misses any one channel.

5. **Zero added cognitive load.** The operator has exactly one task: pick the lit bin. All sequencing, counting, verification, and error detection are handled by the system.

6. **Classical algorithms for deterministic problems.** Rerouting uses a substitution rule lookup and config hot-swap — not ML. The rerouting problem is a deterministic constraint; ML cannot improve on a dict access and would make it harder to audit.

7. **Fail-safe defaults everywhere.** Simulation mode is the default. Dropped WiFi commands are non-fatal. The weight monitor has a 5-second force-advance timeout. The Arduino watchdog clears all outputs on disconnect. Every failure mode has a defined safe fallback.
