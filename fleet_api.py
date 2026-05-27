"""
fleet_api.py
============
Flask Blueprint providing the wireless fleet-management REST API for Round 3.

Mount in app.py:
    from fleet_api import make_fleet_blueprint
    fleet_bp = make_fleet_blueprint(coordinator, rerouter, CONFIG_DIR)
    app.register_blueprint(fleet_bp)

Endpoints
---------
GET  /api/fleet/trolleys            list all carts with live status
POST /api/fleet/mode                set operating mode  {mode: "standalone"|"linked"}
GET  /api/fleet/configs             list all available variant JSON files
POST /api/fleet/variant/push        push a variant to one or all carts
POST /api/fleet/variant/activate    start the loaded variant on a cart
POST /api/fleet/linked/start        start a linked sequence from a config file
POST /api/fleet/emergency_stop      reset all carts immediately
GET  /api/fleet/<cart_id>/status    health check for one cart
GET  /api/fleet/<cart_id>/logs      event history for one cart  (?n=50)
"""

import json
import os

from flask import Blueprint, request, jsonify


def make_fleet_blueprint(coordinator, rerouter, config_dir: str) -> Blueprint:
    """
    Factory so the Blueprint captures coordinator and rerouter without globals.
    """
    bp = Blueprint("fleet", __name__)

    # ------------------------------------------------------------------ #
    # Fleet overview
    # ------------------------------------------------------------------ #

    @bp.route("/api/fleet/trolleys")
    def get_trolleys():
        snap = coordinator.snapshot()
        return jsonify({
            "mode":           snap["mode"],
            "active_cart_id": snap["active_cart_id"],
            "linked_phase":   snap["linked_phase"],
            "total_phases":   snap["total_phases"],
            "trolleys":       list(snap["trolleys"].values()),
        })

    # ------------------------------------------------------------------ #
    # Mode control
    # ------------------------------------------------------------------ #

    @bp.route("/api/fleet/mode", methods=["POST"])
    def set_mode():
        data = request.json or {}
        mode = data.get("mode")
        if mode not in ("standalone", "linked"):
            return jsonify({"error": "mode must be 'standalone' or 'linked'"}), 400
        coordinator.set_mode(mode)
        return jsonify({"ok": True, "mode": mode})

    # ------------------------------------------------------------------ #
    # Available configs
    # ------------------------------------------------------------------ #

    @bp.route("/api/fleet/configs")
    def list_configs():
        files = sorted(f for f in os.listdir(config_dir) if f.endswith(".json"))
        out = []
        for fn in files:
            try:
                with open(os.path.join(config_dir, fn)) as f:
                    cfg = json.load(f)
                out.append({
                    "filename":       fn,
                    "variant_id":     cfg.get("variant_id", ""),
                    "variant_name":   cfg.get("variant_name", ""),
                    "operating_mode": cfg.get("operating_mode", "standalone"),
                    "description":    cfg.get("description", ""),
                    "trolley_group":  cfg.get("trolley_group", []),
                })
            except Exception:
                pass
        return jsonify(out)

    # ------------------------------------------------------------------ #
    # Push variant to one or all carts
    # ------------------------------------------------------------------ #

    @bp.route("/api/fleet/variant/push", methods=["POST"])
    def push_variant():
        """
        Body (JSON):
          { "cart_id": "SMALL_A01" | "all",
            "filename": "standalone_small_motor.json" }
          OR
          { "cart_id": "SMALL_A01" | "all",
            "config": { ...raw variant object... } }
        """
        data    = request.json or {}
        cart_id = data.get("cart_id", "all")

        # Resolve config
        if "config" in data:
            config = data["config"]
        elif "filename" in data:
            fn = data["filename"]
            if not fn.endswith(".json"):
                return jsonify({"error": "filename must end with .json"}), 400
            path = os.path.join(config_dir, fn)
            if not os.path.exists(path):
                return jsonify({"error": f"file not found: {fn}"}), 404
            with open(path) as f:
                config = json.load(f)
        else:
            return jsonify({"error": "provide 'filename' or 'config'"}), 400

        # Load substitution rules
        if rerouter:
            rerouter.load_rules_from_config(config)

        targets = (
            list(coordinator.trolleys.keys())
            if cart_id == "all"
            else [cart_id]
        )
        for tid in targets:
            if tid not in coordinator.trolleys:
                return jsonify({"error": f"unknown cart_id: {tid}"}), 404
            coordinator.push_variant(tid, config)

        return jsonify({
            "ok":         True,
            "pushed_to":  targets,
            "variant_id": config.get("variant_id"),
        })

    # ------------------------------------------------------------------ #
    # Activate (start) a loaded variant
    # ------------------------------------------------------------------ #

    @bp.route("/api/fleet/variant/activate", methods=["POST"])
    def activate_variant():
        """Body: { "cart_id": "SMALL_A01" }"""
        data    = request.json or {}
        cart_id = data.get("cart_id")
        if not cart_id:
            return jsonify({"error": "cart_id required"}), 400
        if cart_id not in coordinator.trolleys:
            return jsonify({"error": f"unknown cart_id: {cart_id}"}), 404
        try:
            coordinator.activate(cart_id)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"ok": True, "cart_id": cart_id})

    # ------------------------------------------------------------------ #
    # Start a linked sequence
    # ------------------------------------------------------------------ #

    @bp.route("/api/fleet/linked/start", methods=["POST"])
    def start_linked():
        """Body: { "filename": "linked_excavator.json" } or { "config": {...} }"""
        data = request.json or {}

        if "config" in data:
            config = data["config"]
        elif "filename" in data:
            fn   = data["filename"]
            path = os.path.join(config_dir, fn)
            if not os.path.exists(path):
                return jsonify({"error": f"file not found: {fn}"}), 404
            with open(path) as f:
                config = json.load(f)
        else:
            return jsonify({"error": "provide 'filename' or 'config'"}), 400

        if rerouter:
            rerouter.load_rules_from_config(config)

        try:
            coordinator.start_linked(config)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 400

        return jsonify({"ok": True, "variant_id": config.get("variant_id")})

    # ------------------------------------------------------------------ #
    # Emergency stop
    # ------------------------------------------------------------------ #

    @bp.route("/api/fleet/emergency_stop", methods=["POST"])
    def emergency_stop():
        coordinator.reset_all()
        return jsonify({"ok": True, "message": "All trolleys stopped and reset"})

    # ------------------------------------------------------------------ #
    # Per-cart endpoints
    # ------------------------------------------------------------------ #

    @bp.route("/api/fleet/<cart_id>/status")
    def cart_status(cart_id):
        if cart_id not in coordinator.trolleys:
            return jsonify({"error": "unknown cart_id"}), 404
        inst   = coordinator.trolleys[cart_id]
        engine = inst.engine
        return jsonify({
            "cart_id":          cart_id,
            "status":           inst.status,
            "loaded_config_id": inst.loaded_config_id,
            "is_active":        cart_id == coordinator._active_cart_id,
            "mode":             coordinator.mode,
            **engine.snapshot(),
        })

    @bp.route("/api/fleet/<cart_id>/logs")
    def cart_logs(cart_id):
        if cart_id not in coordinator.trolleys:
            return jsonify({"error": "unknown cart_id"}), 404
        inst = coordinator.trolleys[cart_id]
        n    = int(request.args.get("n", 50))
        return jsonify({
            "cart_id": cart_id,
            "count":   len(inst.events),
            "events":  inst.events[-n:],
        })

    return bp
