"""
app.py
======
Flask + Flask-SocketIO server that hosts the Smart Assistive Part Pick simulation.

Run:
    pip install -r requirements.txt
    python app.py

Then open:
    http://localhost:5000/          -> Operator view (the trolley)
    http://localhost:5000/supervisor -> Supervisor dashboard

Event flow:
    Browser click "simulate pick bin 3"
        -> HTTP POST /api/sim/pick
        -> MockHardware.simulate_pick() fires IR callback
        -> SequenceEngine._on_ir_break() -> _confirm_pick()
        -> engine emits EventType.PICK_CORRECT/WRONG/etc
        -> on_event() broadcasts over SocketIO
        -> Browser updates LED, display, log
"""

import os
import json
from flask import Flask, request, jsonify, send_from_directory
from flask_socketio import SocketIO

from hardware_abstraction import MockHardware
from sequence_engine import SequenceEngine, EventType


app = Flask(__name__, static_folder="static", static_url_path="/static")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

CONFIG_DIR = os.path.join(os.path.dirname(__file__), "configs")


# ---------- event broadcaster ----------

def broadcast_hw(event_name: str, payload: dict):
    """MockHardware calls this to push raw hardware events to browser."""
    socketio.emit(event_name, payload)


def broadcast_engine(event_type: EventType, payload: dict):
    """SequenceEngine calls this for semantic events."""
    socketio.emit("engine_event", payload)


# ---------- singleton instances ----------

hardware = MockHardware(emit_fn=broadcast_hw)
engine = SequenceEngine(hardware, on_event=broadcast_engine)


# ---------- static pages ----------

@app.route("/")
def operator_view():
    return send_from_directory("static", "trolley.html")


@app.route("/supervisor")
def supervisor_view():
    return send_from_directory("static", "supervisor.html")


# ---------- config API ----------

@app.route("/api/variants")
def list_variants():
    files = sorted(f for f in os.listdir(CONFIG_DIR) if f.endswith(".json"))
    out = []
    for fn in files:
        with open(os.path.join(CONFIG_DIR, fn)) as f:
            cfg = json.load(f)
        out.append({
            "filename": fn,
            "variant_id": cfg["variant_id"],
            "variant_name": cfg["variant_name"],
            "description": cfg.get("description", "")
        })
    return jsonify(out)


@app.route("/api/variant/load", methods=["POST"])
def load_variant():
    fn = request.json.get("filename")
    if not fn or not fn.endswith(".json"):
        return jsonify({"error": "bad filename"}), 400
    path = os.path.join(CONFIG_DIR, fn)
    if not os.path.exists(path):
        return jsonify({"error": "not found"}), 404
    engine.reset()
    engine.load_variant(path)
    return jsonify({"ok": True, "variant_id": engine.state.variant_id})


@app.route("/api/variant/start", methods=["POST"])
def start_variant():
    if engine.config is None:
        return jsonify({"error": "no variant loaded"}), 400
    engine.start()
    return jsonify({"ok": True})


@app.route("/api/variant/reset", methods=["POST"])
def reset_variant():
    engine.reset()
    return jsonify({"ok": True})


# ---------- simulate operator actions ----------

@app.route("/api/sim/pick", methods=["POST"])
def sim_pick():
    """Simulate operator picking qty parts from bin_id."""
    data = request.json or {}
    bin_id = int(data["bin_id"])
    qty = int(data.get("qty", 0))  # 0 means "pick whatever the current step says"

    if engine.config is None:
        return jsonify({"error": "no variant loaded"}), 400

    bin_meta = engine._bin_meta(bin_id)
    if not bin_meta:
        return jsonify({"error": "bad bin_id"}), 400

    # if qty is 0, infer from current step (if target bin) else default to 1
    if qty == 0:
        step = engine._current_step()
        qty = step["qty"] if (step and step["bin_id"] == bin_id) else 1

    hardware.simulate_pick(bin_id, qty, bin_meta["unit_weight_g"])
    return jsonify({"ok": True})


@app.route("/api/sim/ir_only", methods=["POST"])
def sim_ir_only():
    """Simulate IR break with no weight change (ghost trigger)."""
    bin_id = int((request.json or {})["bin_id"])
    hardware.simulate_ir_only(bin_id)
    return jsonify({"ok": True})


@app.route("/api/sim/wrong_qty", methods=["POST"])
def sim_wrong_qty():
    """Simulate picking wrong quantity from target bin."""
    data = request.json or {}
    bin_id = int(data["bin_id"])
    qty = int(data["qty"])
    bin_meta = engine._bin_meta(bin_id)
    if not bin_meta:
        return jsonify({"error": "bad bin_id"}), 400
    hardware.simulate_pick(bin_id, qty, bin_meta["unit_weight_g"])
    return jsonify({"ok": True})


# ---------- supervisor snapshot ----------

@app.route("/api/snapshot")
def snapshot():
    return jsonify(engine.snapshot())


# ---------- socketio lifecycle ----------

@socketio.on("connect")
def on_connect():
    # push current state to newly-connected client
    if engine.config is not None:
        socketio.emit("engine_event", {
            "type": "variant_loaded",
            "variant_id": engine.state.variant_id,
            "variant_name": engine.state.variant_name,
            "bins": engine.config["bins"],
            "sequence": engine.config["pick_sequence"]
        })


if __name__ == "__main__":
    print("Smart Assistive Part Pick — Simulation Server")
    print("Operator view:   http://localhost:5000/")
    print("Supervisor view: http://localhost:5000/supervisor")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
