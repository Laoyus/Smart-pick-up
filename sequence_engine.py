"""
sequence_engine.py
==================
Variant-agnostic pick sequence state machine.

Confirmation flow (weight-based):
  1. Step starts → active bin lights WHITE, display shows qty to pick.
  2. Weight monitor thread polls load cell every 300ms.
     - Display counts down in real-time as each piece is picked.
  3. When remaining reaches 0 → LED turns GREEN, PICK_CORRECT emitted.
  4. After GREEN:
     - IR already clear (hand out) → advance to next bin after 0.3s.
     - IR still triggered (hand in bin) → keep GREEN, wait for beam restore.
     - Safety timeout (5s) → advance anyway.
  5. Wrong-bin detection: IR on non-active bin → error LED + buzzer.
"""

import json
import os
import time
import threading
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
    PICK_OVERPICK       = "pick_overpick"
    GHOST_IR            = "ghost_ir"
    VARIANT_COMPLETE    = "variant_complete"
    OPERATOR_STUCK      = "operator_stuck"
    REROUTE_TRIGGERED   = "reroute_triggered"


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
    Weight-based pick sequence state machine. One instance per trolley.

    Wiring:
        engine = SequenceEngine(hardware, on_event=broadcast_fn)
        engine.load_variant("configs/model_a.json")
        engine.start()
    """

    STUCK_THRESHOLD_SEC  = 30.0  # no-pick warning threshold
    GREEN_IR_TIMEOUT_SEC = 5.0   # max wait for IR clear after GREEN before forcing advance

    def __init__(self, hardware, on_event: Callable[[EventType, dict], None],
                 cart_id: str = ""):
        self.hw = hardware
        self._on_event = on_event
        self.cart_id = cart_id
        self.state = EngineState()
        self.config: Optional[dict] = None
        self._lock = Lock()
        self._stuck_timer: Optional[Timer] = None
        self._config_dir: str = ""

        # Per-step monitor state (reset at each step)
        self._step_active         = False   # monitor loop runs while True
        self._step_green          = False   # True once count hit 0 and LED went GREEN
        self._step_weight_start   = 0.0
        self._step_last_remaining = 0
        self._monitor_thread: Optional[threading.Thread] = None

    # ---------- public API ----------

    def load_variant(self, config_path_or_dict) -> None:
        """Load variant from file path or already-parsed dict. Primes hardware."""
        if isinstance(config_path_or_dict, str):
            self._config_dir = os.path.dirname(os.path.abspath(config_path_or_dict))
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

        for b in cfg["bins"]:
            self.hw.set_led(b["bin_id"], "off")
            self.hw.set_display(b["bin_id"], str(b["initial_qty"]))
            if hasattr(self.hw, "prime_bin"):
                self.hw.prime_bin(b["bin_id"], b["unit_weight_g"], b["initial_qty"])
            self.hw.register_ir_callback(b["bin_id"], self._on_ir_break)
            if hasattr(self.hw, "register_ir_clear_callback"):
                self.hw.register_ir_clear_callback(b["bin_id"], self._on_ir_clear)

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
        """Clear all state and LEDs."""
        self._step_active = False
        self._cancel_stuck_timer()
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
        # Stop the previous step's monitor thread
        self._step_active = False

        # Turn off just-completed step's LED
        prev = self._current_step()
        if prev:
            self.hw.set_led(prev["bin_id"], "done")

        self.state.current_step_idx += 1
        step = self._current_step()

        if step is None:
            self._on_variant_complete()
            return

        # Reset per-step flags
        self._step_green = False

        # Light active bin WHITE, show qty on display
        self.hw.set_led(step["bin_id"], "active")
        self.hw.set_display(step["bin_id"], str(step["qty"]))
        self.hw.play_audio("chime_next")
        self.state.step_started_at = time.time()

        bin_meta = self._bin_meta(step["bin_id"])
        self._emit(EventType.STEP_STARTED, {
            "step": step["step"],
            "bin_id": step["bin_id"],
            "part_name": bin_meta["part_name"],
            "part_number": bin_meta["part_number"],
            "qty": step["qty"],
            "instruction": step["instruction"]
        })

        self._reset_stuck_timer()
        self._start_weight_monitor(step)

    def _start_weight_monitor(self, step: dict) -> None:
        """
        Background thread: polls weight every 300ms, counts down display,
        goes GREEN at 0, then waits for IR clear before advancing.
        """
        bin_id  = step["bin_id"]
        target  = step["qty"]
        unit_w  = self._bin_meta(bin_id)["unit_weight_g"]

        # Snapshot starting weight from cache (instant — no Arduino round-trip)
        self._step_weight_start   = self.hw.read_weight(bin_id)
        self._step_last_remaining = target
        self._step_active         = True

        def _monitor():
            green_since: Optional[float] = None
            over_picked = False   # True while operator has taken too many

            while self._step_active:
                current   = self.hw.read_weight(bin_id)
                removed   = self._step_weight_start - current
                picked    = max(0, round(removed / unit_w)) if unit_w > 0 else 0
                remaining = target - picked   # can be negative if over-picked

                # Display clamps at 0 — negative means over-pick, shown via red LED
                display_remaining = max(0, remaining)
                if display_remaining != self._step_last_remaining:
                    self._step_last_remaining = display_remaining
                    self.hw.set_display(bin_id, str(display_remaining))

                # Over-pick: operator took more than required
                if remaining < 0 and not over_picked and not self._step_green:
                    over_picked = True
                    self.state.errors += 1
                    self.hw.set_led(bin_id, "error")
                    self.hw.set_buzzer(True)
                    Timer(0.6, lambda: self.hw.set_buzzer(False)).start()
                    self._emit(EventType.PICK_OVERPICK, {
                        "step": step["step"],
                        "bin_id": bin_id,
                        "expected_qty": target,
                        "actual_qty": picked,
                        "over_by": picked - target,
                        "message": f"Over-picked bin {bin_id}: expected {target}, "
                                   f"picked {picked} (+{picked - target} extra) — "
                                   f"please return {picked - target} part(s)"
                    })

                # Operator put parts back — recover from over-pick
                if over_picked and remaining >= 0 and not self._step_green:
                    over_picked = False
                    self.hw.set_led(bin_id, "active")

                # When count reaches 0 and no over-pick → GREEN
                if remaining == 0 and not self._step_green and not over_picked:
                    self._step_green = True
                    green_since = time.time()
                    self.hw.set_led(bin_id, "green")
                    self.hw.play_audio("chime_ok")

                    self.state.correct_picks += 1
                    self.state.bin_qty_remaining[bin_id] = max(
                        0, self.state.bin_qty_remaining.get(bin_id, 0) - target
                    )
                    self._emit(EventType.PICK_CORRECT, {
                        "step": step["step"],
                        "bin_id": bin_id,
                        "qty_picked": target,
                        "remaining": self.state.bin_qty_remaining[bin_id],
                        "step_duration_sec": round(
                            time.time() - self.state.step_started_at, 2)
                    })

                    # If IR already clear, advance now
                    ir_now = (self.hw.is_ir_triggered(bin_id)
                              if hasattr(self.hw, "is_ir_triggered") else False)
                    if not ir_now:
                        self._try_complete_step(step)
                        return
                    # else: _on_ir_clear will call _try_complete_step when beam restores

                # Safety timeout: if GREEN but hand never left, force advance
                if (self._step_green and green_since is not None
                        and (time.time() - green_since) > self.GREEN_IR_TIMEOUT_SEC):
                    self._try_complete_step(step)
                    return

                time.sleep(0.3)

        self._monitor_thread = threading.Thread(target=_monitor, daemon=True)
        self._monitor_thread.start()

    def _try_complete_step(self, step: dict) -> None:
        """
        One-shot gate: first caller (monitor thread or _on_ir_clear) wins.
        Sets _step_active=False to stop the monitor, then schedules advance.
        """
        with self._lock:
            if not self._step_active:
                return   # already completing
            self._step_active = False

        self._emit(EventType.STEP_COMPLETED, {"step": step["step"]})
        self._cancel_stuck_timer()
        Timer(0.3, self._advance_to_next_step).start()

    def _on_ir_break(self, bin_id: int) -> None:
        """Called when any IR beam is broken."""
        step = self._current_step()
        if step is None:
            return

        if bin_id != step["bin_id"]:
            # For reroutable configs (model_b): wrong-bin touch triggers reroute
            if self._try_reroute_on_wrong_bin(bin_id, step):
                return

            # Wrong bin accessed
            self.state.errors += 1
            self.hw.set_led(bin_id, "error")
            self.hw.set_buzzer(True)
            Timer(0.6, lambda: self.hw.set_buzzer(False)).start()

            def _clear_wrong_bin(bid: int) -> None:
                # Only reset to "off" if that bin hasn't since become the active step
                cur = self._current_step()
                if cur is None or cur["bin_id"] != bid:
                    self.hw.set_led(bid, "off")
                else:
                    # Bin is now active — re-apply active state so display is correct
                    self.hw.set_led(bid, "active")
                    self.hw.set_display(bid, str(cur["qty"]))

            Timer(1.5, lambda: _clear_wrong_bin(bin_id)).start()
            self._emit(EventType.PICK_WRONG_BIN, {
                "step": step["step"],
                "expected_bin": step["bin_id"],
                "actual_bin": bin_id,
                "message": f"Wrong bin. Expected bin {step['bin_id']}, "
                           f"operator accessed bin {bin_id}."
            })
        # Correct bin: weight monitor handles confirmation — nothing to do here

    def _on_ir_clear(self, bin_id: int) -> None:
        """Called when IR beam restores (hand removed from bin)."""
        step = self._current_step()
        if step and step["bin_id"] == bin_id and self._step_green:
            self._try_complete_step(step)

    def _on_variant_complete(self) -> None:
        self._cancel_stuck_timer()
        total_time = time.time() - self.state.variant_started_at
        target = self.config.get("cycle_time_target_sec", 0)
        self.hw.play_audio("chime_done")
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
        self.hw.set_led(step["bin_id"], "error")
        Timer(0.5, lambda: self.hw.set_led(step["bin_id"], "active")).start()
        self._reset_stuck_timer()

    def _try_reroute_on_wrong_bin(self, wrong_bin_id: int, step: dict) -> bool:
        """Only active for model_b (operating_mode=reroutable). Returns True if reroute was applied."""
        if self.config.get("operating_mode") != "reroutable":
            return False

        rules = self.config.get("substitution_rules", {})
        rule = rules.get(f"bin_{step['bin_id']}")
        if rule is None:
            return False

        self._emit(EventType.REROUTE_TRIGGERED, {
            "bin_id": step["bin_id"],
            "wrong_bin_id": wrong_bin_id,
            "message": rule["display_message"]
        })
        self.hw.set_led(step["bin_id"], "off")

        if "alternate_bin_id" in rule:
            # Redirect this step to the alternate bin in-place
            self.config["pick_sequence"][self.state.current_step_idx]["bin_id"] = rule["alternate_bin_id"]
            self._restart_current_step()
            return True

        if "alternate_variant" in rule:
            # Hot-swap to alternate config, restart the same step
            alt_path = os.path.join(self._config_dir, rule["alternate_variant"])
            with open(alt_path) as f:
                alt_cfg = json.load(f)
            self._reload_config(alt_cfg)
            self._restart_current_step()
            return True

        return False

    def _restart_current_step(self) -> None:
        """Stop current monitor and re-run the current step from scratch."""
        self._step_active = False
        self._cancel_stuck_timer()
        self.state.current_step_idx -= 1
        Timer(0.1, self._advance_to_next_step).start()

    def _reload_config(self, cfg: dict) -> None:
        """Hot-swap to alternate config during reroute. Preserves current step index."""
        self.config = cfg
        self.state.variant_id = cfg["variant_id"]
        self.state.variant_name = cfg["variant_name"]
        self.state.total_steps = len(cfg["pick_sequence"])
        self.state.bin_qty_remaining = {b["bin_id"]: b["initial_qty"] for b in cfg["bins"]}
        for b in cfg["bins"]:
            self.hw.set_led(b["bin_id"], "off")
            self.hw.set_display(b["bin_id"], str(b["initial_qty"]))
            if hasattr(self.hw, "prime_bin"):
                self.hw.prime_bin(b["bin_id"], b["unit_weight_g"], b["initial_qty"])

    def _emit(self, event_type: EventType, payload: dict) -> None:
        payload_with_meta = {
            "type": event_type.value,
            "ts": time.time(),
            "cart_id": self.cart_id,
            **payload
        }
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
