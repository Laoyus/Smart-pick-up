"""
app.py  —  Round 3
==================
Flask + Flask-SocketIO server for the Smart Assistive Part Pick simulation.

Round 3 additions over Round 2:
  • TrolleyCoordinator manages SMALL_A01 + LARGE_A01 simultaneously.
  • ReroutingEngine handles substitution-rule based adaptive path selection.
  • fleet_api Blueprint provides REST endpoints for wireless variant management.
  • /manager  →  Fleet control dashboard (new page).
  • /operator →  Dual-trolley operator HMI (replaces /   for R3 operator page).
  • /         →  still serves trolley.html for Round 2 backwards compatibility.
  • /supervisor remains unchanged in URL; content is enhanced.

Round 2 API endpoints remain fully functional (they target SMALL_A01 by default).

Run:
    python app.py

Open:
    http://localhost:5000/manager    ← Fleet control (R3)
    http://localhost:5000/          ← Operator HMI   (dual-trolley, R3)
    http://localhost:5000/supervisor ← Supervisor dashboard
"""

import os
import json
from flask import Flask, request, jsonify, send_from_directory
from flask_socketio import SocketIO

from hardware_abstraction import MockHardware
from trolley_coordinator import TrolleyCoordinator
from rerouting_engine import ReroutingEngine
from fleet_api import make_fleet_blueprint


app = Flask(__name__, static_folder="static", static_url_path="/static")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

CONFIG_DIR = os.path.join(os.path.dirname(__file__), "configs")


# ======================================================================
# Hardware + coordinator setup
# ======================================================================

def broadcast_hw(event_name: str, payload: dict) -> None:
    """Raw hardware events (LED, display, audio, weight) → browser."""
    socketio.emit(event_name, payload)


def _socketio_emit(event_name: str, payload: dict) -> None:
    """Generic emitter passed to coordinator / rerouter."""
    socketio.emit(event_name, payload)


# Two independent hardware instances — one per physical trolley
hw_small = MockHardware(emit_fn=broadcast_hw, cart_id="SMALL_A01")
hw_large = MockHardware(emit_fn=broadcast_hw, cart_id="LARGE_A01")

# Coordinator owns both engines
coordinator = TrolleyCoordinator(emit_fn=_socketio_emit, config_dir=CONFIG_DIR)
coordinator.register_trolley("SMALL_A01", hw_small)
coordinator.register_trolley("LARGE_A01", hw_large)

# Rerouting engine — wired back into coordinator
rerouter = ReroutingEngine(
    coordinator=coordinator,
    emit_fn=_socketio_emit,
    config_dir=CONFIG_DIR,
)
coordinator.rerouting_engine = rerouter

# Round 2 backwards-compat aliases (always refer to SMALL_A01)
hardware = hw_small
engine   = coordinator.get_engine("SMALL_A01")

# Fleet management Blueprint
fleet_bp = make_fleet_blueprint(coordinator, rerouter, CONFIG_DIR)
app.register_blueprint(fleet_bp)


# ======================================================================
# Static pages
# ======================================================================

@app.route("/")
def operator_view():
    return send_from_directory("static", "trolley.html")


@app.route("/supervisor")
def supervisor_view():
    return send_from_directory("static", "supervisor.html")


@app.route("/manager")
def manager_view():
    return send_from_directory("static", "manager.html")


# ======================================================================
# Round 2 config API  (backwards compatible — targets SMALL_A01)
# ======================================================================

@app.route("/api/variants")
def list_variants():
    """List standalone variant configs for the Round 2 operator page."""
    files = sorted(f for f in os.listdir(CONFIG_DIR) if f.endswith(".json"))
    out = []
    for fn in files:
        try:
            with open(os.path.join(CONFIG_DIR, fn)) as f:
                cfg = json.load(f)
            # Only expose configs that have a flat bins list (standalone-compatible)
            if "bins" in cfg or "trolleys" not in cfg:
                out.append({
                    "filename":       fn,
                    "variant_id":     cfg.get("variant_id", ""),
                    "variant_name":   cfg.get("variant_name", ""),
                    "operating_mode": cfg.get("operating_mode", "standalone"),
                    "description":    cfg.get("description", ""),
                })
        except Exception:
            pass
    return jsonify(out)


@app.route("/api/variant/load", methods=["POST"])
def load_variant():
    """Round 2: load a variant onto SMALL_A01."""
    fn = (request.json or {}).get("filename")
    if not fn or not fn.endswith(".json"):
        return jsonify({"error": "bad filename"}), 400
    path = os.path.join(CONFIG_DIR, fn)
    if not os.path.exists(path):
        return jsonify({"error": "not found"}), 404

    with open(path) as f:
        config = json.load(f)

    rerouter.load_rules_from_config(config)
    coordinator.push_variant("SMALL_A01", config)
    return jsonify({"ok": True, "variant_id": engine.state.variant_id})


@app.route("/api/variant/start", methods=["POST"])
def start_variant():
    """Round 2: start SMALL_A01 engine."""
    if engine.config is None:
        return jsonify({"error": "no variant loaded"}), 400
    coordinator.activate("SMALL_A01")
    return jsonify({"ok": True})


@app.route("/api/variant/reset", methods=["POST"])
def reset_variant():
    """Round 2: reset SMALL_A01."""
    coordinator.reset_trolley("SMALL_A01")
    return jsonify({"ok": True})


# ======================================================================
# Simulation endpoints  (cart_id defaults to SMALL_A01 for R2 compat)
# ======================================================================

def _resolve_hw_engine(data: dict):
    cart_id = data.get("cart_id", "SMALL_A01")
    eng = coordinator.get_engine(cart_id)
    hw  = coordinator.get_hardware(cart_id)
    if eng is None or hw is None:
        return None, None, cart_id
    return eng, hw, cart_id


@app.route("/api/sim/pick", methods=["POST"])
def sim_pick():
    """Simulate operator picking qty parts from bin_id on a trolley."""
    data = request.json or {}
    eng, hw, cart_id = _resolve_hw_engine(data)
    if eng is None:
        return jsonify({"error": f"unknown cart_id: {cart_id}"}), 400
    if eng.config is None:
        return jsonify({"error": "no variant loaded"}), 400

    bin_id   = int(data["bin_id"])
    qty      = int(data.get("qty", 0))
    bin_meta = eng._bin_meta(bin_id)
    if not bin_meta:
        return jsonify({"error": "bad bin_id"}), 400

    if qty == 0:
        step = eng._current_step()
        qty  = step["qty"] if (step and step["bin_id"] == bin_id) else 1

    unit_w = bin_meta.get("unit_weight_g") or 100.0
    hw.simulate_pick(bin_id, qty, unit_w)
    return jsonify({"ok": True})


@app.route("/api/sim/ir_only", methods=["POST"])
def sim_ir_only():
    """Simulate IR break with no weight change (ghost trigger)."""
    data = request.json or {}
    _, hw, cart_id = _resolve_hw_engine(data)
    if hw is None:
        return jsonify({"error": f"unknown cart_id: {cart_id}"}), 400
    bin_id = int(data["bin_id"])
    hw.simulate_ir_only(bin_id)
    return jsonify({"ok": True})


@app.route("/api/sim/wrong_qty", methods=["POST"])
def sim_wrong_qty():
    """Simulate picking the wrong quantity from the target bin."""
    data = request.json or {}
    eng, hw, cart_id = _resolve_hw_engine(data)
    if eng is None:
        return jsonify({"error": f"unknown cart_id: {cart_id}"}), 400
    if eng.config is None:
        return jsonify({"error": "no variant loaded"}), 400

    bin_id   = int(data["bin_id"])
    qty      = int(data["qty"])
    bin_meta = eng._bin_meta(bin_id)
    if not bin_meta:
        return jsonify({"error": "bad bin_id"}), 400

    unit_w = bin_meta.get("unit_weight_g") or 100.0
    hw.simulate_pick(bin_id, qty, unit_w)
    return jsonify({"ok": True})


# ======================================================================
# Snapshot (supports both R2 and R3)
# ======================================================================

@app.route("/api/snapshot")
def snapshot():
    """Return coordinator snapshot (all trolleys) or just SMALL_A01 for R2 compat."""
    return jsonify(coordinator.snapshot())


# ======================================================================
# Authority control  (which role owns each trolley's variant selector)
# ======================================================================

authority_state = {"SMALL_A01": "manager", "LARGE_A01": "manager"}


@app.route("/api/fleet/authority", methods=["GET"])
def get_authority():
    return jsonify(authority_state)


@app.route("/api/fleet/authority", methods=["POST"])
def set_authority():
    data      = request.json or {}
    cart_id   = data.get("cart_id")
    authority = data.get("authority")
    if cart_id not in authority_state:
        return jsonify({"error": f"unknown cart_id: {cart_id}"}), 400
    if authority not in ("manager", "operator"):
        return jsonify({"error": "authority must be 'manager' or 'operator'"}), 400
    authority_state[cart_id] = authority
    socketio.emit("authority_event", {
        "type":      "authority_changed",
        "cart_id":   cart_id,
        "authority": authority,
    })
    return jsonify({"ok": True, "cart_id": cart_id, "authority": authority})


# ======================================================================
# SocketIO lifecycle
# ======================================================================

@socketio.on("connect")
def on_connect():
    """Push full current state to a newly connected browser tab."""
    # Emit mode
    socketio.emit("coordinator_event", {
        "type": "mode_changed",
        "mode": coordinator.mode,
    })

    # Emit status for each trolley
    for cart_id, inst in coordinator.trolleys.items():
        socketio.emit("coordinator_event", {
            "type":             "trolley_status",
            "cart_id":          cart_id,
            "status":           inst.status,
            "loaded_config_id": inst.loaded_config_id,
            "is_active":        cart_id == coordinator._active_cart_id,
            "mode":             coordinator.mode,
        })
        # If a variant is loaded, replay the variant_loaded event so the UI renders bins
        if inst.engine.config is not None:
            cfg = inst.engine.config
            socketio.emit("engine_event", {
                "type":         "variant_loaded",
                "cart_id":      cart_id,
                "variant_id":   cfg.get("variant_id", ""),
                "variant_name": cfg.get("variant_name", ""),
                "bins":         cfg.get("bins", []),
                "sequence":     cfg.get("pick_sequence", []),
            })

    # Emit authority state for each trolley
    for cart_id, authority in authority_state.items():
        socketio.emit("authority_event", {
            "type":      "authority_changed",
            "cart_id":   cart_id,
            "authority": authority,
        })


if __name__ == "__main__":
    print("Smart Assistive Part Pick — Round 3 Simulation Server")
    print("Fleet Manager:   http://localhost:5000/manager")
    print("Operator HMI:    http://localhost:5000/")
    print("Supervisor:      http://localhost:5000/supervisor")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False,
                 allow_unsafe_werkzeug=True)
