"""
arduino_hardware.py
===================
Real-hardware backend for the Smart Assistive Part Pick System.
Drop-in replacement for MockHardware when SIMULATION_MODE=False.

Hardware connected to Arduino Mega 2560:
  • 6 × IR break-beam sensors   → report IR_TRIGGERED:N (0-indexed)
  • 6 × HX711 load cell ADCs    → stream WEIGHT:N:grams continuously
  • 6 × TM1637 4-digit displays → receive DISP:N:count to show qty
  • 1 × Passive buzzer          → receive BUZZ:duration_ms

Serial protocol: see arduino_bridge.ino for full spec.

IMPORTANT — calibration:
  HX711 readings must be in grams for qty detection to work.
  Use the Serial Monitor to send "RAW:N" with an empty bin, then again
  with a known weight W placed on the load cell. Set:
      CALIB[N] = raw_value_with_weight / W
  Update CALIB[] in arduino_bridge.ino and re-flash.

  For initial IR + display testing, calibration does NOT matter —
  the display update and IR trigger work regardless of CALIB values.
  Only qty detection (right bin / wrong qty) needs accurate gram readings.
"""

import re
import time
import threading
from typing import Callable, Dict, Optional

try:
    import serial as _pyserial
    _SERIAL_AVAILABLE = True
except ImportError:
    _SERIAL_AVAILABLE = False
    print("[ArduinoHardware] pyserial not installed — run: pip install pyserial")

from hardware_abstraction import HardwareInterface


class ArduinoHardware(HardwareInterface):
    """
    Real-hardware backend. Implements the same HardwareInterface as MockHardware
    so the sequence engine is unaware which backend is running.

    Weight flow:
      Arduino streams WEIGHT:N:grams ~every 100ms per bin.
      Python caches the latest value per bin.
      SequenceEngine reads cache at IR-break time (weight_before) and
      again 1.5s later (weight_after). Delta confirms the pick.
    """

    def __init__(self, port: str = "COM3", baud: int = 9600) -> None:
        self._port  = port
        self._baud  = baud
        self._serial: Optional[object] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # IR callbacks registered by the sequence engine (1-indexed bin_id keys)
        self._ir_callbacks: Dict[int, Callable[[int], None]] = {}
        self._ir_clear_callbacks: Dict[int, Callable[[int], None]] = {}

        # Current IR beam state per bin (True = beam broken / hand in bin)
        self._ir_state: Dict[int, bool] = {}

        # Called when the physical emergency stop button is pressed
        self._emergency_callback: Optional[Callable] = None

        # Weight cache: bin_id (1-indexed) → latest grams from HX711 stream.
        # prime_bin() sets an initial simulated value that gets overwritten by
        # the real Arduino stream within ~500ms of startup.
        self._weights:      Dict[int, float] = {}
        self._unit_weights: Dict[int, float] = {}   # grams/part — from variant config
        self._step_qtys:    Dict[int, int]   = {}   # current step's expected qty

        self._connect()
        self._poll_thread: Optional[threading.Thread] = None
        self._start_weight_polling()

    # ------------------------------------------------------------------ #
    # Connection + background reader
    # ------------------------------------------------------------------ #

    def _connect(self) -> None:
        if not _SERIAL_AVAILABLE:
            print("[ArduinoHardware] pyserial unavailable — IR events will be silent.")
            return
        try:
            self._serial  = _pyserial.Serial(self._port, self._baud, timeout=1)
            time.sleep(2)   # wait for Arduino auto-reset after DTR toggle
            self._running = True
            self._thread  = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()
            print(f"[ArduinoHardware] Connected to {self._port} @ {self._baud} baud")
        except Exception as exc:
            print(f"[ArduinoHardware] Warning: could not open {self._port}: {exc}")
            print("[ArduinoHardware] Running without hardware — check port and wiring.")

    def _read_loop(self) -> None:
        while self._running:
            try:
                raw  = self._serial.readline()
                line = raw.decode("ascii", errors="ignore").strip()

                if line.startswith("IR_TRIGGERED:"):
                    # Arduino sends 0-indexed; engine uses 1-indexed bin_id
                    bin_idx = int(line.split(":")[1])
                    bin_id  = bin_idx + 1
                    self._ir_state[bin_id] = True
                    self._fire_ir(bin_id)

                elif line.startswith("IR_CLEARED:"):
                    bin_idx = int(line.split(":")[1])
                    bin_id  = bin_idx + 1
                    self._ir_state[bin_id] = False
                    cb = self._ir_clear_callbacks.get(bin_id)
                    if cb:
                        cb(bin_id)

                elif line.startswith("WEIGHT:"):
                    # WEIGHT:N:grams — N is 0-indexed on Arduino side
                    parts = line.split(":")
                    if len(parts) >= 3:
                        bin_idx = int(parts[1])
                        grams   = float(parts[2])
                        self._weights[bin_idx + 1] = grams   # store as 1-indexed

                elif line.startswith("TARED:"):
                    bin_idx = int(line.split(":")[1])
                    print(f"[ArduinoHardware] Bin {bin_idx} tared.")

                elif line == "EMERGENCY_STOP":
                    print("[ArduinoHardware] EMERGENCY STOP button pressed!")
                    if self._emergency_callback:
                        self._emergency_callback()

                elif line == "READY":
                    print("[ArduinoHardware] Arduino READY.")

            except (ValueError, IndexError):
                pass
            except Exception as exc:
                if self._running:
                    print(f"[ArduinoHardware] Read error: {exc}")
                    time.sleep(0.5)

    def _start_weight_polling(self) -> None:
        """Background thread: request each bin's weight every 200ms.
        Full cycle (all 6 bins) completes in ~1.2s — within the 1.5s confirm window."""
        def _poll():
            time.sleep(3)   # wait for Arduino READY before polling
            last_log = 0.0
            while self._running:
                for i in range(6):
                    if not self._running:
                        break
                    self._send(str(i))
                    time.sleep(0.2)
                # Print live weight table every 10 seconds
                now = time.time()
                if now - last_log >= 10.0:
                    last_log = now
                    rows = "  ".join(
                        f"B{b}:{self._weights.get(b, 0.0):>7.1f}g"
                        for b in range(1, 7)
                    )
                    print(f"[weights] {rows}")

        self._poll_thread = threading.Thread(target=_poll, daemon=True)
        self._poll_thread.start()

    def _fire_ir(self, bin_id: int) -> None:
        """
        Called when Arduino reports IR_TRIGGERED.
        bin_id is already 1-indexed (converted in _read_loop).
        Invokes the engine callback — engine will read weight_before immediately.
        Real load cells handle the weight drop; no simulation needed here.
        """
        cb = self._ir_callbacks.get(bin_id)
        if cb:
            cb(bin_id)

    # ------------------------------------------------------------------ #
    # Serial write helper
    # ------------------------------------------------------------------ #

    def _send(self, cmd: str) -> None:
        try:
            if self._serial and self._serial.is_open:
                self._serial.write((cmd + "\n").encode())
        except Exception as exc:
            print(f"[ArduinoHardware] Send error: {exc}")

    # ------------------------------------------------------------------ #
    # HardwareInterface — required methods
    # ------------------------------------------------------------------ #

    def set_led(self, bin_id: int, color: str) -> None:
        # TM1637 display acts as LED substitute:
        #   active → blink the qty number
        #   error  → show "Err"
        #   done   → show "----"
        #   off    → blank
        self._send(f"LED:{bin_id - 1}:{color}")

    def set_display(self, bin_id: int, text: str) -> None:
        """Extract the quantity number from display text and send to TM1637."""
        count = self._parse_count(text)
        self._send(f"DISP:{bin_id - 1}:{count}")   # Arduino is 0-indexed

    def read_weight(self, bin_id: int) -> float:
        """Return latest cached weight in grams from HX711 stream."""
        return self._weights.get(bin_id, 0.0)

    def play_audio(self, cue: str) -> None:
        if cue == "chime_ok":
            self._send("BUZZ:100")
        elif cue == "chime_next":
            self._send("BUZZ:50")
        elif cue == "chime_done":
            self._send("BUZZ:300")
        # buzz_error: set_buzzer(True) handles it — avoids double BUZZ command

    def set_buzzer(self, on: bool) -> None:
        if on:
            self._send("BUZZ:600")
        # set_buzzer(False) is a no-op — tone() auto-stops after its duration

    def register_ir_callback(self, bin_id: int,
                             callback: Callable[[int], None]) -> None:
        self._ir_callbacks[bin_id] = callback

    def register_ir_clear_callback(self, bin_id: int,
                                   callback: Optional[Callable[[int], None]]) -> None:
        if callback is None:
            self._ir_clear_callbacks.pop(bin_id, None)
        else:
            self._ir_clear_callbacks[bin_id] = callback

    def is_ir_triggered(self, bin_id: int) -> bool:
        """True if the IR beam for this bin is currently broken (hand in bin)."""
        return self._ir_state.get(bin_id, False)

    # ------------------------------------------------------------------ #
    # MockHardware-compatible helpers (called by coordinator / engine)
    # ------------------------------------------------------------------ #

    def prime_bin(self, bin_id: int, unit_weight_g: float, qty: int) -> None:
        """
        Sets initial simulated weight until the real HX711 stream arrives.
        The Arduino stream overwrites this within ~500ms of startup.
        """
        self._weights[bin_id]      = unit_weight_g * qty
        self._unit_weights[bin_id] = unit_weight_g

    def simulate_pick(self, bin_id: int, qty: int, unit_weight_g: float) -> None:
        """Browser manual override: drop weight in cache then fire IR callback."""
        current = self._weights.get(bin_id, unit_weight_g * qty)
        self._weights[bin_id] = max(0.0, current - qty * unit_weight_g)
        self._fire_ir(bin_id)

    def simulate_ir_only(self, bin_id: int) -> None:
        """Browser manual override: fire IR without changing weight cache."""
        self._fire_ir(bin_id)

    def update_expected_qty(self, bin_id: int, qty: int) -> None:
        """Called by TrolleyCoordinator on STEP_STARTED. Stored for reference."""
        self._step_qtys[bin_id] = qty

    def reset(self) -> None:
        self._ir_callbacks.clear()
        self._ir_clear_callbacks.clear()
        self._ir_state.clear()
        self._weights.clear()
        self._unit_weights.clear()
        self._step_qtys.clear()

    # ------------------------------------------------------------------ #
    # Calibration helpers (callable from app.py or REPL for field tuning)
    # ------------------------------------------------------------------ #

    def register_emergency_callback(self, callback: Callable) -> None:
        """Register a zero-argument function called when the emergency stop button fires."""
        self._emergency_callback = callback

    def tare_bin(self, bin_id: int) -> None:
        """Re-zero a load cell (bin_id 1-indexed). Use when bin is empty."""
        self._send(f"TARE:{bin_id - 1}")

    def set_calibration(self, bin_id: int, factor: float) -> None:
        """
        Send a runtime calibration factor to the Arduino.
        NOT persistent — re-flash with updated CALIB[] to make it permanent.
        """
        self._send(f"CAL:{bin_id - 1}:{factor:.4f}")

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _parse_count(self, text: str) -> int:
        """
        Extract the relevant quantity number from display text strings.

        Text formats the engine sends:
          "PICK 4x\\nBolt M10x40"   → qty to pick  → 4
          "Bolt M10x40\\nQty: 10"   → initial qty   → 10
          "Bolt M10x40\\nLeft: 6"   → remaining qty → 6
        """
        m = re.search(r'PICK\s+(\d+)', text, re.IGNORECASE)
        if m:
            return int(m.group(1))
        m = re.search(r'(?:Qty|Left):\s*(\d+)', text, re.IGNORECASE)
        if m:
            return int(m.group(1))
        m = re.search(r'\d+', text)
        return int(m.group(0)) if m else 0

    # ------------------------------------------------------------------ #
    # Stubs for future hardware
    # ------------------------------------------------------------------ #

    def set_neopixel(self, bin_index: int, color: str) -> None:
        pass  # STUB: NeoPixel per bin (future)

    def set_led_strip(self, zone_index: int, color: str) -> None:
        pass  # STUB: RGB strip for large-part shelf zones (future)

    def set_lcd(self, bin_index: int, line1: str, line2: str) -> None:
        pass  # STUB: replaced by TM1637 set_display() above

    def read_load_cell(self, bin_index: int) -> float:
        return self._weights.get(bin_index + 1, 0.0)
