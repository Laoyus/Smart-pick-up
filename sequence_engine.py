"""
sequence_engine.py
==================
Variant-agnostic pick sequence state machine.

This is the BRAIN of the Smart Assistive Part Pick System. It is entirely
hardware-independent: it calls an injected HardwareInterface for all I/O.

Core responsibilities:
  1. Load a variant config (JSON) and prime the hardware.
  2. Drive the operator through the pick sequence: light correct bin,
     update display, listen for IR + weight events.
  3. Classify each pick event as:
        - CORRECT       (IR fired on expected bin + weight drop matches unit weight)
        - WRONG_BIN     (IR fired on a non-target bin)
        - QTY_MISMATCH  (right bin, but picked wrong number of parts)
        - GHOST_IR      (IR fired but weight did not drop — false trigger)
  4. Emit events: step_started, step_completed, error, variant_complete.
  5. Log everything with timestamps for the supervisor dashboard.

The separation IR-first + load-cell-confirmed is the core accuracy trick:
  - IR alone => false positives (hand passed over)
  - Load cell alone => slow, might miss rapid picks
  - IR arms the window, load cell confirms within 1.5s => robust detection
"""

import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Callable, Dict, List, Optional
from threading import Timer, Lock


# ---------- event types ----------

class EventType(str, Enum):
    VARIANT_LOADED      = "variant_loaded"
    STEP_STARTED        = "step_started"
    STEP_COMPLETED      = "step_completed"
    PICK_CORRECT        = "pick_correct"
    PICK_WRONG_BIN      = "pick_wrong_bin"
    PICK_QTY_MISMATCH   = "pick_qty_mismatch"
    GHOST_IR            = "ghost_ir"
    VARIANT_COMPLETE    = "variant_complete"
    OPERATOR_STUCK      = "operator_stuck"      # no pick in N seconds


# ---------- engine state ----------

@dataclass
class PickEvent:
    ts: float
    kind: str
    step: int
    bin_id: int
    expected_bin: Optional[int] = None
    expected_qty: Optional[int] = None
    actual_qty: Optional[int] = None
    message: str = ""


@dataclass
class EngineState:
    variant_id: str = ""
    variant_name: str = ""
    current_step_idx: int = -1
    total_steps: int = 0
    step_started_at: Optional[float] = None
    variant_started_at: Optional[float] = None
    errors: int = 0
    correct_picks: int = 0
    events: List[PickEvent] = field(default_factory=list)
    bin_qty_remaining: Dict[int, int] = field(default_factory=dict)


# ---------- engine ----------

class SequenceEngine:
    """
    The state machine. One instance per trolley.

    Wiring:
        engine = SequenceEngine(hardware, on_event=broadcast_fn)
        engine.load_variant("configs/model_a.json")
        engine.start()

    The hardware layer calls engine._on_ir_break(bin_id) when an IR beam trips.
    The engine polls hardware.read_weight(bin_id) inside a short confirmation
    window after each IR event.
    """

    # How long after IR break do we wait for a weight change to confirm pick?
    PICK_CONFIRM_WINDOW_SEC = 1.5
    # How much weight change counts as "something was definitely picked"?
    MIN_WEIGHT_DELTA_G = 1.0
    # If operator hasn't picked for this long, flag as stuck
    STUCK_THRESHOLD_SEC = 30.0

    def __init__(self, hardware, on_event: Callable[[EventType, dict], None],
                 cart_id: str = ""):
        self.hw = hardware
        self._on_event = on_event
        self.cart_id = cart_id
        self.state = EngineState()
        self.config: Optional[dict] = None
        self._lock = Lock()
        self._pending_ir: Optional[Dict] = None   # armed IR event awaiting weight confirm
        self._stuck_timer: Optional[Timer] = None

    # ---------- public API ----------

    def load_variant(self, config_path_or_dict) -> None:
        """Load variant from file path or already-parsed dict. Primes hardware."""
        if isinstance(config_path_or_dict, str):
            with open(config_path_or_dict, "r") as f:
                cfg = json.load(f)
        else:
            cfg = config_path_or_dict

        self.config = cfg
        self.state = EngineState(
            variant_id=cfg["variant_id"],
            variant_name=cfg["variant_name"],
            total_steps=len(cfg["pick_sequence"]),
            bin_qty_remaining={b["bin_id"]: b["initial_qty"] for b in cfg["bins"]}
        )

        # prime hardware: reset LEDs, set displays, tare load cells
        for b in cfg["bins"]:
            self.hw.set_led(b["bin_id"], "off")
            self.hw.set_display(b["bin_id"], f"{b['part_name'][:14]}\nQty: {b['initial_qty']}")
            # mock-specific: preload load cell
            if hasattr(self.hw, "prime_bin"):
                self.hw.prime_bin(b["bin_id"], b["unit_weight_g"], b["initial_qty"])
            self.hw.register_ir_callback(b["bin_id"], self._on_ir_break)

        self._emit(EventType.VARIANT_LOADED, {
            "variant_id": cfg["variant_id"],
            "variant_name": cfg["variant_name"],
            "bins": cfg["bins"],
            "sequence": cfg["pick_sequence"]
        })

    def start(self) -> None:
        """Begin the pick sequence from step 0."""
        if self.config is None:
            raise RuntimeError("Load a variant before start()")
        self.state.variant_started_at = time.time()
        self.state.current_step_idx = -1
        self._advance_to_next_step()

    def reset(self) -> None:
        """Clear all state & LEDs. Used between demos."""
        self._cancel_stuck_timer()
        self._pending_ir = None
        if self.config:
            for b in self.config["bins"]:
                self.hw.set_led(b["bin_id"], "off")
        self.state = EngineState()
        self.config = None

    # ---------- internals ----------

    def _current_step(self) -> Optional[dict]:
        if 0 <= self.state.current_step_idx < self.state.total_steps:
            return self.config["pick_sequence"][self.state.current_step_idx]
        return None

    def _bin_meta(self, bin_id: int) -> Optional[dict]:
        for b in self.config["bins"]:
            if b["bin_id"] == bin_id:
                return b
        return None

    def _advance_to_next_step(self) -> None:
        # clear LED of just-completed step
        prev = self._current_step()
        if prev:
            self.hw.set_led(prev["bin_id"], "done")

        self.state.current_step_idx += 1
        step = self._current_step()

        if step is None:
            # sequence complete
            self._on_variant_complete()
            return

        # light the new target bin
        self.hw.set_led(step["bin_id"], "active")
        bin_meta = self._bin_meta(step["bin_id"])
        self.hw.set_display(
            step["bin_id"],
            f"PICK {step['qty']}x\n{bin_meta['part_name'][:14]}"
        )
        self.hw.play_audio("chime_next")
        self.state.step_started_at = time.time()

        self._emit(EventType.STEP_STARTED, {
            "step": step["step"],
            "bin_id": step["bin_id"],
            "part_name": bin_meta["part_name"],
            "part_number": bin_meta["part_number"],
            "qty": step["qty"],
            "instruction": step["instruction"]
        })

        self._reset_stuck_timer()

    def _on_ir_break(self, bin_id: int) -> None:
        """Called by hardware layer when any IR beam is broken."""
        with self._lock:
            step = self._current_step()
            if step is None:
                return

            # Arm the confirmation window: record weight snapshot now
            weight_before = self.hw.read_weight(bin_id)
            self._pending_ir = {
                "bin_id": bin_id,
                "ts": time.time(),
                "weight_before": weight_before
            }

            # Schedule weight-based confirmation
            Timer(self.PICK_CONFIRM_WINDOW_SEC, self._confirm_pick,
                  args=[bin_id, weight_before]).start()

    def _confirm_pick(self, bin_id: int, weight_before: float) -> None:
        """Runs PICK_CONFIRM_WINDOW_SEC after an IR break to check weight drop."""
        with self._lock:
            if self._pending_ir is None or self._pending_ir["bin_id"] != bin_id:
                return  # another event superseded

            weight_after = self.hw.read_weight(bin_id)
            delta = weight_before - weight_after

            if delta < self.MIN_WEIGHT_DELTA_G:
                # IR tripped but no pick happened — false positive (hand waved past)
                self._emit(EventType.GHOST_IR, {
                    "bin_id": bin_id,
                    "weight_delta_g": round(delta, 2),
                    "message": "IR tripped but no weight drop — ignored"
                })
                self._pending_ir = None
                return

            # Something was actually picked. Classify it.
            step = self._current_step()
            bin_meta = self._bin_meta(bin_id)
            unit_w = bin_meta["unit_weight_g"]
            tol = bin_meta["weight_tolerance_g"]
            qty_picked = round(delta / unit_w)
            # sanity: qty picked should be >=1
            qty_picked = max(1, qty_picked)

            self._pending_ir = None

            if bin_id != step["bin_id"]:
                # WRONG BIN
                self.state.errors += 1
                self.hw.set_led(bin_id, "error")
                self.hw.play_audio("buzz_error")
                self.hw.set_buzzer(True)
                Timer(0.6, lambda: self.hw.set_buzzer(False)).start()
                Timer(1.5, lambda: self.hw.set_led(bin_id, "off")).start()
                self._emit(EventType.PICK_WRONG_BIN, {
                    "step": step["step"],
                    "expected_bin": step["bin_id"],
                    "actual_bin": bin_id,
                    "qty_picked": qty_picked,
                    "message": f"Wrong bin. Expected bin {step['bin_id']}, "
                               f"operator picked from bin {bin_id}."
                })
                return

            if qty_picked != step["qty"]:
                # RIGHT BIN, WRONG QUANTITY
                self.state.errors += 1
                self.hw.set_led(bin_id, "error")
                self.hw.play_audio("buzz_error")
                self._emit(EventType.PICK_QTY_MISMATCH, {
                    "step": step["step"],
                    "bin_id": bin_id,
                    "expected_qty": step["qty"],
                    "actual_qty": qty_picked,
                    "message": f"Qty mismatch on bin {bin_id}: "
                               f"expected {step['qty']}, got {qty_picked}."
                })
                # Re-arm the same step (don't advance)
                Timer(1.5, lambda: self.hw.set_led(bin_id, "active")).start()
                return

            # CORRECT PICK
            self.state.correct_picks += 1
            self.state.bin_qty_remaining[bin_id] -= qty_picked
            remaining = self.state.bin_qty_remaining[bin_id]
            self.hw.set_display(
                bin_id,
                f"{bin_meta['part_name'][:14]}\nLeft: {remaining}"
            )
            self.hw.play_audio("chime_ok")
            self._emit(EventType.PICK_CORRECT, {
                "step": step["step"],
                "bin_id": bin_id,
                "qty_picked": qty_picked,
                "remaining": remaining,
                "step_duration_sec": round(time.time() - self.state.step_started_at, 2)
            })
            self._emit(EventType.STEP_COMPLETED, {"step": step["step"]})
            self._cancel_stuck_timer()
            Timer(0.3, self._advance_to_next_step).start()

    def _on_variant_complete(self) -> None:
        self._cancel_stuck_timer()
        total_time = time.time() - self.state.variant_started_at
        target = self.config.get("cycle_time_target_sec", 0)
        self.hw.play_audio("chime_done")
        # victory lap: flash all bins green briefly
        for b in self.config["bins"]:
            self.hw.set_led(b["bin_id"], "done")
        self._emit(EventType.VARIANT_COMPLETE, {
            "variant_id": self.state.variant_id,
            "total_time_sec": round(total_time, 2),
            "target_time_sec": target,
            "on_target": total_time <= target * 1.1,
            "errors": self.state.errors,
            "correct_picks": self.state.correct_picks
        })

    def _reset_stuck_timer(self) -> None:
        self._cancel_stuck_timer()
        self._stuck_timer = Timer(self.STUCK_THRESHOLD_SEC, self._on_stuck)
        self._stuck_timer.daemon = True
        self._stuck_timer.start()

    def _cancel_stuck_timer(self) -> None:
        if self._stuck_timer is not None:
            self._stuck_timer.cancel()
            self._stuck_timer = None

    def _on_stuck(self) -> None:
        step = self._current_step()
        if step is None:
            return
        self._emit(EventType.OPERATOR_STUCK, {
            "step": step["step"],
            "bin_id": step["bin_id"],
            "message": f"No pick in {self.STUCK_THRESHOLD_SEC}s — flashing bin"
        })
        # escalate: blink LED by alternating active/error
        self.hw.set_led(step["bin_id"], "error")
        Timer(0.5, lambda: self.hw.set_led(step["bin_id"], "active")).start()
        self._reset_stuck_timer()

    def _emit(self, event_type: EventType, payload: dict) -> None:
        payload_with_meta = {
            "type": event_type.value,
            "ts": time.time(),
            "cart_id": self.cart_id,
            **payload
        }
        # also log to event history
        self.state.events.append(PickEvent(
            ts=time.time(),
            kind=event_type.value,
            step=(self._current_step()["step"] if self._current_step() else 0),
            bin_id=payload.get("bin_id", 0),
            message=payload.get("message", "")
        ))
        self._on_event(event_type, payload_with_meta)

    # ---------- supervisor dashboard snapshot ----------

    def snapshot(self) -> dict:
        return {
            "variant_id": self.state.variant_id,
            "variant_name": self.state.variant_name,
            "current_step": self.state.current_step_idx + 1,
            "total_steps": self.state.total_steps,
            "correct_picks": self.state.correct_picks,
            "errors": self.state.errors,
            "bin_qty": self.state.bin_qty_remaining,
            "recent_events": [asdict(e) for e in self.state.events[-20:]]
        }
