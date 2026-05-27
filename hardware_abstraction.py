"""
hardware_abstraction.py
=======================
Defines the hardware interface used by sequence_engine.py.

This is a MOCK implementation that broadcasts
events over WebSocket so the browser frontend can visualize the trolley.

For Round 3 prototype (Raspberry Pi), ONLY this file changes:
  - set_led()        -> drives NeoPixel via rpi_ws281x
  - read_weight()    -> reads HX711 load cell ADC
  - on_ir_break()    -> hooks RPi.GPIO interrupt on IR sensor pin
  - play_audio()     -> plays WAV via pygame/aplay
  - set_buzzer()     -> GPIO pin high/low

The sequence engine above this layer is completely unaware which backend
is running. Same code ships to the prototype.
"""

from abc import ABC, abstractmethod
from typing import Callable, Dict
import time


class HardwareInterface(ABC):
    """Contract every hardware backend (mock or real) must implement."""

    @abstractmethod
    def set_led(self, bin_id: int, color: str) -> None:
        """Set LED for a bin. color in {off, idle, active, done, error}."""
        pass

    @abstractmethod
    def set_display(self, bin_id: int, text: str) -> None:
        """Update per-bin count display."""
        pass

    @abstractmethod
    def read_weight(self, bin_id: int) -> float:
        """Return current weight on bin's load cell in grams."""
        pass

    @abstractmethod
    def play_audio(self, cue: str) -> None:
        """Play audio cue. cue in {chime_ok, chime_next, buzz_error, chime_done}."""
        pass

    @abstractmethod
    def set_buzzer(self, on: bool) -> None:
        pass

    @abstractmethod
    def register_ir_callback(self, bin_id: int, callback: Callable[[int], None]) -> None:
        """Register a callback invoked when IR break-beam for a bin trips."""
        pass


class MockHardware(HardwareInterface):
    """
    Mock backend for the simulation.
    All actions are broadcast to connected browser clients via an event emitter,
    which app.py wires up to Flask-SocketIO.
    """

    def __init__(self, emit_fn: Callable[[str, dict], None], cart_id: str = "SMALL_A01"):
        self._emit = emit_fn
        self.cart_id = cart_id
        self._weights: Dict[int, float] = {}     # current simulated weight per bin
        self._tare: Dict[int, float] = {}        # starting weight (full bin)
        self._ir_callbacks: Dict[int, Callable[[int], None]] = {}

    # ---- state setup ----
    def prime_bin(self, bin_id: int, unit_weight_g: float, qty: int) -> None:
        total = unit_weight_g * qty
        self._weights[bin_id] = total
        self._tare[bin_id] = total

    # ---- interface impl ----
    def set_led(self, bin_id: int, color: str) -> None:
        self._emit("led_update", {"bin_id": bin_id, "color": color, "cart_id": self.cart_id, "ts": time.time()})

    def set_display(self, bin_id: int, text: str) -> None:
        self._emit("display_update", {"bin_id": bin_id, "text": text, "cart_id": self.cart_id, "ts": time.time()})

    def read_weight(self, bin_id: int) -> float:
        return self._weights.get(bin_id, 0.0)

    def play_audio(self, cue: str) -> None:
        self._emit("audio", {"cue": cue, "cart_id": self.cart_id, "ts": time.time()})

    def set_buzzer(self, on: bool) -> None:
        self._emit("buzzer", {"on": on, "cart_id": self.cart_id, "ts": time.time()})

    def register_ir_callback(self, bin_id: int, callback: Callable[[int], None]) -> None:
        self._ir_callbacks[bin_id] = callback

    # ---- mock-only API: simulate operator actions from the UI ----
    def simulate_pick(self, bin_id: int, qty_picked: int, unit_weight_g: float,
                      noise_g: float = 0.0) -> None:
        """Simulate an operator physically picking qty parts from a bin.
        This drops the weight and fires the IR callback (hand entered bin)."""
        # IR trips first
        cb = self._ir_callbacks.get(bin_id)
        if cb:
            self._emit("ir_break", {"bin_id": bin_id, "cart_id": self.cart_id, "ts": time.time()})
            cb(bin_id)
        # Then weight drops after a brief delay to mimic physics
        removed = unit_weight_g * qty_picked + noise_g
        self._weights[bin_id] = max(0.0, self._weights.get(bin_id, 0.0) - removed)
        self._emit("weight_change", {
            "bin_id": bin_id,
            "weight_g": round(self._weights[bin_id], 1),
            "cart_id": self.cart_id,
            "ts": time.time()
        })

    def simulate_ir_only(self, bin_id: int) -> None:
        """Hand entered bin but picked nothing — tests false-positive rejection."""
        cb = self._ir_callbacks.get(bin_id)
        if cb:
            self._emit("ir_break", {"bin_id": bin_id, "cart_id": self.cart_id, "ts": time.time()})
            cb(bin_id)

    def reset(self) -> None:
        self._weights.clear()
        self._tare.clear()
        self._ir_callbacks.clear()
