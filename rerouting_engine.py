"""
rerouting_engine.py
===================
Adaptive pick-path rerouting — the "Google Maps reroute" feature for Round 3.

When an operator picks the wrong part, this engine checks whether the
incorrectly-picked part is a valid substitute defined in the current config's
substitution_rules. If it is, it loads an alternate variant and the assembly
continues. If not, the system emits a hard error and holds the current step.

substitution_rules JSON format (inside a variant config):
  {
    "substitution_rules": {
      "Bolt M10x40 Grade 10.9": {
        "compatible_parts": ["Bolt M10x40 Grade 8.8", "Bolt M10x45 Grade 10.9"],
        "alternate_variant": "linked_excavator_alt1.json",
        "display_message": "Grade 8.8 bolt approved — continuing with alternate spec"
      }
    }
  }

Flow:
  PICK_WRONG_BIN fired
    → coordinator calls handle_wrong_pick(cart_id, payload)
    → look up expected part + actual part from engine config
    → check substitution_rules[expected_part]
    → if actual_part in compatible_parts  → reroute_triggered + load alternate
    → else                                → substitute_rejected  (stay on current step)

Events emitted on "rerouting_event" channel:
  reroute_triggered  — valid substitute found, alternate loading
  alternate_loaded   — alternate variant is live
  substitute_rejected — wrong part, not a valid substitute
  reroute_error      — alternate variant file not found
"""

import json
import os
import time
from typing import Callable, Dict, Optional


class ReroutingEngine:

    def __init__(self, coordinator, emit_fn: Callable, config_dir: str) -> None:
        """
        coordinator — TrolleyCoordinator instance (circular ref OK; set after init).
        emit_fn     — socketio.emit(event_name, payload).
        config_dir  — path to configs/ folder.
        """
        self.coordinator = coordinator
        self._emit_raw   = emit_fn
        self.config_dir  = config_dir
        self._rules: Dict[str, dict] = {}
        self._reroute_count: int     = 0

    # ------------------------------------------------------------------ #
    # Rule management
    # ------------------------------------------------------------------ #

    def load_rules_from_config(self, config: dict) -> None:
        self._rules = config.get("substitution_rules", {})

    def clear_rules(self) -> None:
        self._rules = {}

    @property
    def reroute_count(self) -> int:
        return self._reroute_count

    # ------------------------------------------------------------------ #
    # Wrong-pick handler (called by coordinator on PICK_WRONG_BIN)
    # ------------------------------------------------------------------ #

    def handle_wrong_pick(self, cart_id: str, event_payload: dict) -> None:
        """
        Check if the part that was actually picked is a valid substitute for
        the expected part. If yes, trigger rerouting. If no, emit a rejected
        event (the engine's own error handling already flashed the LED).
        """
        if not self._rules:
            return

        expected_bin_id = event_payload.get("expected_bin")
        actual_bin_id   = event_payload.get("actual_bin")

        config = self._current_config(cart_id)
        if not config:
            return

        expected_part = self._part_name(config, expected_bin_id)
        actual_part   = self._part_name(config, actual_bin_id)

        if not expected_part or not actual_part:
            return

        rule = self._rules.get(expected_part)
        if not rule:
            return   # no substitution rule for this part — engine handles normally

        if actual_part not in rule.get("compatible_parts", []):
            self._emit("rerouting_event", {
                "type":          "substitute_rejected",
                "ts":            time.time(),
                "cart_id":       cart_id,
                "expected_part": expected_part,
                "actual_part":   actual_part,
                "message": (
                    f"'{actual_part}' is NOT a valid substitute for "
                    f"'{expected_part}' — correct the pick."
                ),
            })
            return

        # ---- Valid substitute found ----
        self._reroute_count += 1
        alt_filename = rule.get("alternate_variant")
        display_msg  = rule.get("display_message",
                                f"Substituting '{actual_part}' for '{expected_part}'")

        self._emit("rerouting_event", {
            "type":              "reroute_triggered",
            "ts":                time.time(),
            "cart_id":           cart_id,
            "expected_part":     expected_part,
            "actual_part":       actual_part,
            "alternate_variant": alt_filename,
            "message":           display_msg,
        })

        if alt_filename:
            self._load_alternate(cart_id, alt_filename, display_msg)

    # ------------------------------------------------------------------ #
    # Alternate variant loading
    # ------------------------------------------------------------------ #

    def _load_alternate(self, cart_id: str, filename: str, message: str) -> None:
        path = os.path.join(self.config_dir, filename)
        if not os.path.exists(path):
            self._emit("rerouting_event", {
                "type":    "reroute_error",
                "ts":      time.time(),
                "cart_id": cart_id,
                "message": f"Alternate variant file not found: {filename}",
            })
            return

        with open(path) as f:
            alt_config = json.load(f)

        self.load_rules_from_config(alt_config)   # update rules for the new variant

        op_mode = alt_config.get("operating_mode", "standalone")

        if op_mode == "linked":
            self.coordinator.reset_all()
            self.coordinator.set_mode("linked")
            self.coordinator.start_linked(alt_config)
        else:
            # Standalone: reload on the same trolley, restart from step 1
            self.coordinator.reset_trolley(cart_id)
            self.coordinator.push_variant(cart_id, alt_config)
            self.coordinator.activate(cart_id)

        self._emit("rerouting_event", {
            "type":         "alternate_loaded",
            "ts":           time.time(),
            "cart_id":      cart_id,
            "variant_id":   alt_config.get("variant_id"),
            "variant_name": alt_config.get("variant_name"),
            "message": (
                f"Alternate loaded: {alt_config.get('variant_name')} — {message}"
            ),
        })

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _current_config(self, cart_id: str) -> Optional[dict]:
        engine = self.coordinator.get_engine(cart_id)
        return engine.config if engine else None

    def _part_name(self, config: dict, bin_id: Optional[int]) -> Optional[str]:
        if bin_id is None:
            return None
        for b in config.get("bins", []):
            if b["bin_id"] == bin_id:
                return b.get("part_name")
        return None

    def _emit(self, event_name: str, payload: dict) -> None:
        self._emit_raw(event_name, payload)
