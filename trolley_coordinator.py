"""
trolley_coordinator.py
======================
Multi-trolley orchestration for Round 3 dual-trolley operation.

Manages N trolleys (each with its own SequenceEngine + MockHardware) and
coordinates between them in two operating modes:

  STANDALONE — each trolley runs its own variant independently. The manager
               can push and start different configs on each cart simultaneously.

  LINKED     — a single "linked" config drives a unified assembly sequence
               across both trolleys. Steps carry a trolley_id field; the
               coordinator groups consecutive same-trolley steps into phases
               and hands each phase to the appropriate engine as a mini-config.
               When a phase completes (VARIANT_COMPLETE), the coordinator
               automatically starts the next phase on whichever trolley owns it.

Events broadcast to the browser:
  coordinator_event  →  type in {mode_changed, trolley_status, trolley_activated,
                                  linked_started, linked_complete}
  engine_event       →  all SequenceEngine events (with cart_id field)
  rerouting_event    →  emitted by ReroutingEngine (wired in via .rerouting_engine)
"""

import time
from threading import Lock
from typing import Callable, Dict, List, Optional

from hardware_abstraction import MockHardware
from sequence_engine import SequenceEngine, EventType


# ---------- trolley status constants ----------

class TrolleyStatus:
    IDLE    = "idle"
    LOADED  = "loaded"
    RUNNING = "running"
    DONE    = "done"
    ERROR   = "error"


# ---------- internal per-trolley container ----------

class TrolleyInstance:
    def __init__(self, cart_id: str, hardware: MockHardware,
                 on_engine_event: Callable) -> None:
        self.cart_id   = cart_id
        self.hardware  = hardware
        self.engine    = SequenceEngine(
            hardware,
            on_event=lambda ev_type, payload: on_engine_event(cart_id, ev_type, payload),
            cart_id=cart_id,
        )
        self.status: str          = TrolleyStatus.IDLE
        self.loaded_config_id: Optional[str] = None
        self.events: List[dict]   = []   # last 200 engine events for this trolley


# ---------- coordinator ----------

class TrolleyCoordinator:
    STANDALONE = "standalone"
    LINKED     = "linked"

    def __init__(self, emit_fn: Callable, config_dir: str) -> None:
        """
        emit_fn   — socketio.emit(event_name, payload), called for every outbound event.
        config_dir — path to the configs/ folder (used by ReroutingEngine).
        """
        self._emit_raw  = emit_fn
        self.config_dir = config_dir
        self.mode       = self.STANDALONE

        self.trolleys: Dict[str, TrolleyInstance] = {}
        self._lock = Lock()

        # Linked-mode state
        self._linked_config: Optional[dict] = None
        self._linked_phases: List[dict]     = []   # [{cart_id, steps}]
        self._linked_phase_idx: int         = -1
        self._linked_start_time: Optional[float] = None
        self._active_cart_id: Optional[str] = None

        # Plugged in after construction by app.py
        self.rerouting_engine = None

    # ------------------------------------------------------------------ #
    # Trolley registration
    # ------------------------------------------------------------------ #

    def register_trolley(self, cart_id: str, hardware: MockHardware) -> None:
        inst = TrolleyInstance(cart_id, hardware,
                               on_engine_event=self._on_engine_event)
        self.trolleys[cart_id] = inst

    def get_engine(self, cart_id: str) -> Optional[SequenceEngine]:
        t = self.trolleys.get(cart_id)
        return t.engine if t else None

    def get_hardware(self, cart_id: str) -> Optional[MockHardware]:
        t = self.trolleys.get(cart_id)
        return t.hardware if t else None

    # ------------------------------------------------------------------ #
    # Mode control
    # ------------------------------------------------------------------ #

    def set_mode(self, mode: str) -> None:
        if mode not in (self.STANDALONE, self.LINKED):
            raise ValueError(f"Unknown mode: {mode}")
        self.mode = mode
        self._emit("coordinator_event", {"type": "mode_changed", "mode": mode})

    # ------------------------------------------------------------------ #
    # Variant management (standalone + pre-load for linked)
    # ------------------------------------------------------------------ #

    def push_variant(self, cart_id: str, config: dict) -> None:
        """Load a variant config onto a specific trolley without starting it."""
        inst = self._get_inst(cart_id)
        inst.engine.reset()
        inst.engine.load_variant(config)
        inst.status          = TrolleyStatus.LOADED
        inst.loaded_config_id = config.get("variant_id", "unknown")
        self._emit_status(cart_id)

    def activate(self, cart_id: str) -> None:
        """Start the already-loaded variant on a trolley (standalone mode)."""
        inst = self._get_inst(cart_id)
        if inst.engine.config is None:
            raise RuntimeError(f"No variant loaded on {cart_id}")
        inst.engine.start()
        inst.status          = TrolleyStatus.RUNNING
        self._active_cart_id = cart_id
        self._emit_status(cart_id)

    def reset_trolley(self, cart_id: str) -> None:
        inst = self._get_inst(cart_id)
        inst.engine.reset()
        inst.hardware.reset()
        inst.status           = TrolleyStatus.IDLE
        inst.loaded_config_id = None
        self._emit_status(cart_id)

    def reset_all(self) -> None:
        for cart_id in list(self.trolleys):
            self.reset_trolley(cart_id)
        self._linked_config    = None
        self._linked_phases    = []
        self._linked_phase_idx = -1
        self._active_cart_id   = None
        if self.rerouting_engine:
            self.rerouting_engine.clear_rules()

    # ------------------------------------------------------------------ #
    # Linked-mode sequence
    # ------------------------------------------------------------------ #

    def start_linked(self, config: dict) -> None:
        """
        Start a linked sequence. Resets all trolleys, primes their hardware
        from the per-trolley bin lists in the config, builds execution phases,
        then kicks off phase 0.

        Expected config shape:
          {
            "variant_id": "...",
            "variant_name": "...",
            "operating_mode": "linked",
            "trolley_group": ["SMALL_A01", "LARGE_A01"],
            "trolleys": {
              "SMALL_A01": {"bins": [...]},
              "LARGE_A01": {"bins": [...]}
            },
            "pick_sequence": [
              {"step": 1, "trolley_id": "SMALL_A01", "bin_id": 5, "qty": 1, ...},
              ...
            ],
            "substitution_rules": {...},
            "cycle_time_target_sec": 180
          }
        """
        if self.mode != self.LINKED:
            raise RuntimeError("Call set_mode('linked') before start_linked()")

        # Reset all trolleys cleanly
        for cart_id in list(self.trolleys):
            self.reset_trolley(cart_id)

        self._linked_config    = config
        self._linked_phases    = self._build_phases(config)
        self._linked_phase_idx = -1
        self._linked_start_time = time.time()

        # Prime hardware weights for each trolley's bins
        trolleys_cfg = config.get("trolleys", {})
        for cart_id, tcfg in trolleys_cfg.items():
            if cart_id not in self.trolleys:
                continue
            hw = self.trolleys[cart_id].hardware
            for b in tcfg.get("bins", []):
                if hasattr(hw, "prime_bin") and b.get("unit_weight_g"):
                    hw.prime_bin(b["bin_id"], b["unit_weight_g"], b["initial_qty"])

        total_steps = len(config.get("pick_sequence", []))
        self._emit("coordinator_event", {
            "type":         "linked_started",
            "variant_id":   config.get("variant_id"),
            "variant_name": config.get("variant_name"),
            "description":  config.get("description", ""),
            "total_steps":  total_steps,
            "total_phases": len(self._linked_phases),
            "trolley_group": config.get("trolley_group", []),
            "sequence":     config.get("pick_sequence", []),
            "trolleys":     {
                cid: {"bins": tcfg.get("bins", [])}
                for cid, tcfg in trolleys_cfg.items()
            },
        })

        self._advance_linked_phase()

    def _build_phases(self, config: dict) -> List[dict]:
        """
        Group consecutive steps with the same trolley_id into phases.
        E.g. [S, S, S, L, L, S, S] → [(SMALL, steps 1-3), (LARGE, steps 4-5), (SMALL, steps 6-7)]
        """
        sequence = config.get("pick_sequence", [])
        phases: List[dict] = []
        current_cart: Optional[str] = None
        current_steps: List[dict]   = []

        for step in sequence:
            tid = step.get("trolley_id")
            if tid != current_cart:
                if current_steps:
                    phases.append({"cart_id": current_cart, "steps": current_steps})
                current_cart  = tid
                current_steps = [step]
            else:
                current_steps.append(step)

        if current_steps:
            phases.append({"cart_id": current_cart, "steps": current_steps})

        return phases

    def _advance_linked_phase(self) -> None:
        with self._lock:
            self._linked_phase_idx += 1

        if self._linked_phase_idx >= len(self._linked_phases):
            self._on_linked_complete()
            return

        phase    = self._linked_phases[self._linked_phase_idx]
        cart_id  = phase["cart_id"]
        steps    = phase["steps"]

        self._active_cart_id = cart_id
        inst = self._get_inst(cart_id)

        # Build a mini-config containing just this trolley's bins and this phase's steps
        trolley_cfg  = self._linked_config.get("trolleys", {}).get(cart_id, {})
        mini_config  = {
            "variant_id":   (
                f"{self._linked_config.get('variant_id')}_P{self._linked_phase_idx + 1}"
            ),
            "variant_name": (
                f"{self._linked_config.get('variant_name')} · Phase {self._linked_phase_idx + 1}"
            ),
            "cart_id":      cart_id,
            "bins":         trolley_cfg.get("bins", []),
            "pick_sequence": steps,
            "cycle_time_target_sec": self._linked_config.get("cycle_time_target_sec", 999),
        }

        inst.engine.reset()
        inst.engine.load_variant(mini_config)
        inst.status = TrolleyStatus.RUNNING
        inst.engine.start()

        self._emit("coordinator_event", {
            "type":         "trolley_activated",
            "cart_id":      cart_id,
            "phase":        self._linked_phase_idx + 1,
            "total_phases": len(self._linked_phases),
            "step_count":   len(steps),
            "first_step":   steps[0]["step"] if steps else 0,
            "last_step":    steps[-1]["step"] if steps else 0,
        })
        self._emit_status(cart_id)

    def _on_linked_complete(self) -> None:
        total_time = time.time() - (self._linked_start_time or time.time())
        cfg    = self._linked_config or {}
        target = cfg.get("cycle_time_target_sec", 0)
        self._emit("coordinator_event", {
            "type":            "linked_complete",
            "variant_id":      cfg.get("variant_id", ""),
            "variant_name":    cfg.get("variant_name", ""),
            "total_time_sec":  round(total_time, 2),
            "target_time_sec": target,
            "on_target":       total_time <= target * 1.1 if target else True,
        })

    # ------------------------------------------------------------------ #
    # Engine event routing
    # ------------------------------------------------------------------ #

    def _on_engine_event(self, cart_id: str,
                         event_type: EventType, payload: dict) -> None:
        """
        Intercept every SequenceEngine event.
        • Log to the trolley's event history.
        • In linked mode: swallow VARIANT_COMPLETE (internal phase boundary)
          and advance to the next phase instead.
        • Offer wrong-bin events to the rerouting engine.
        • Broadcast everything else to the browser as engine_event.
        """
        inst = self.trolleys.get(cart_id)
        if inst:
            inst.events.append(payload)
            if len(inst.events) > 200:
                inst.events = inst.events[-200:]

        # Keep hardware in sync with current step so multi-part IR picks work
        if event_type == EventType.STEP_STARTED:
            hw = self.get_hardware(cart_id)
            if hasattr(hw, "update_expected_qty"):
                hw.update_expected_qty(payload.get("bin_id", 0),
                                       payload.get("qty", 1))

        # Rerouting check on wrong-bin events
        if (event_type == EventType.PICK_WRONG_BIN
                and self.rerouting_engine is not None):
            self.rerouting_engine.handle_wrong_pick(cart_id, payload)

        # In linked mode VARIANT_COMPLETE means phase done, not sequence done
        if self.mode == self.LINKED and event_type == EventType.VARIANT_COMPLETE:
            if cart_id == self._active_cart_id:
                inst.status = TrolleyStatus.IDLE
                self._emit_status(cart_id)
                self._advance_linked_phase()
                return   # don't broadcast the mini phase's VARIANT_COMPLETE

        # Broadcast to browser
        self._emit("engine_event", payload)

        # Mark trolley done when a standalone sequence finishes
        if event_type == EventType.VARIANT_COMPLETE and self.mode == self.STANDALONE:
            if inst:
                inst.status = TrolleyStatus.DONE
                self._emit_status(cart_id)

    # ------------------------------------------------------------------ #
    # Snapshot for REST + on-connect sync
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict:
        return {
            "mode":            self.mode,
            "active_cart_id":  self._active_cart_id,
            "linked_phase":    self._linked_phase_idx + 1 if self._linked_phases else 0,
            "total_phases":    len(self._linked_phases),
            "trolleys": {
                cid: {
                    "cart_id":          cid,
                    "status":           inst.status,
                    "loaded_config_id": inst.loaded_config_id,
                    "is_active":        cid == self._active_cart_id,
                    **inst.engine.snapshot(),
                }
                for cid, inst in self.trolleys.items()
            },
        }

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _get_inst(self, cart_id: str) -> TrolleyInstance:
        inst = self.trolleys.get(cart_id)
        if inst is None:
            raise KeyError(f"Unknown cart_id: {cart_id}")
        return inst

    def _emit(self, event_name: str, payload: dict) -> None:
        self._emit_raw(event_name, payload)

    def _emit_status(self, cart_id: str) -> None:
        inst = self.trolleys.get(cart_id)
        if inst:
            self._emit("coordinator_event", {
                "type":             "trolley_status",
                "cart_id":          cart_id,
                "status":           inst.status,
                "loaded_config_id": inst.loaded_config_id,
                "is_active":        cart_id == self._active_cart_id,
                "mode":             self.mode,
            })
