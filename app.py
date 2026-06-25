"""
app.py
======
Flask + Flask-SocketIO server for the Smart Assistive Part Pick system.

Core features:
  • TrolleyCoordinator manages SMALL_A01 + LARGE_A01 simultaneously.
  • ReroutingEngine handles substitution-rule based adaptive path selection.
  • fleet_api Blueprint provides REST endpoints for wireless variant management.
  • /manager    →  Fleet control dashboard.
  • /operator   →  Dual-trolley operator HMI.
  • /supervisor →  Supervisor dashboard.

ESP32 WiFi Bridge:
  • /ws_esp  →  WebSocket endpoint for ESP32-B relay.
                Receives Mega serial events wrapped in >t...<  framing.
                Feeds them into MockHardware (same as sim buttons).
                No other code changes needed — engine/HMI unchanged.

Run:
    python app.py

Open:
    http://localhost:5000/manager    ← Fleet control
    http://localhost:5000/          ← Operator HMI   (dual-trolley)
    http://localhost:5000/supervisor ← Supervisor dashboard
"""

import os
import json
from flask import Flask, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit
from flask_sock import Sock

from hardware_abstraction import create_hardware
from trolley_coordinator import TrolleyCoordinator
from rerouting_engine import ReroutingEngine
from fleet_api import make_fleet_blueprint


app    = Flask(__name__, static_folder="static", static_url_path="/static")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
sock   = Sock(app)   # WebSocket support for ESP32 bridge

CONFIG_DIR = os.path.join(os.path.dirname(__file__), "configs")

# Holds the live ESP32-B WebSocket connection (one at a time)
esp_connection = None


# ======================================================================
# Hardware + coordinator setup
# ======================================================================

def broadcast_hw(event_name: str, payload: dict) -> None:
    """Raw hardware events (LED, display, audio, weight) → browser."""
    socketio.emit(event_name, payload)


def _socketio_emit(event_name: str, payload: dict) -> None:
    """Generic emitter passed to coordinator / rerouter."""
    socketio.emit(event_name, payload)


# Two independent hardware instances — one per physical trolley.
hw_small = create_hardware(emit_fn=broadcast_hw, cart_id="SMALL_A01")
hw_large = create_hardware(emit_fn=broadcast_hw, cart_id="LARGE_A01")

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

# Wire emergency stop button (physical button on SMALL_A01 trolley)
def _on_emergency_stop():
    coordinator.reset_all()
    socketio.emit("coordinator_event", {
        "type":    "emergency_stop",
        "message": "Hardware emergency stop button pressed — all trolleys reset",
    })
    print("[App] EMERGENCY STOP — all trolleys reset.")

if hasattr(hw_small, "register_emergency_callback"):
    hw_small.register_emergency_callback(_on_emergency_stop)

# Convenience aliases — always refer to SMALL_A01
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
# Config API (targets SMALL_A01)
# ======================================================================

@app.route("/api/variants")
def list_variants():
    """List standalone variant configs for the operator page."""
    files = sorted(f for f in os.listdir(CONFIG_DIR) if f.endswith(".json"))
    out = []
    for fn in files:
        try:
            with open(os.path.join(CONFIG_DIR, fn)) as f:
                cfg = json.load(f)
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
    """Load a variant onto SMALL_A01."""
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
    """Start SMALL_A01 engine."""
    if engine.config is None:
        return jsonify({"error": "no variant loaded"}), 400
    coordinator.activate("SMALL_A01")
    return jsonify({"ok": True})


@app.route("/api/variant/reset", methods=["POST"])
def reset_variant():
    """Reset SMALL_A01."""
    coordinator.reset_trolley("SMALL_A01")
    return jsonify({"ok": True})


# ======================================================================
# Simulation endpoints  (cart_id defaults to SMALL_A01)
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
# Snapshot
# ======================================================================

@app.route("/api/snapshot")
def snapshot():
    """Return coordinator snapshot (all trolleys) or just SMALL_A01."""
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
# ESP32 WebSocket bridge  —  real hardware events from Mega via ESP32-B
# ======================================================================
#
# Protocol (defined by teammate's ESP32-B sketch):
#
#   Mega → ESP32-B UART → WiFi → ws://192.168.4.3:5000/ws_esp
#
#   Incoming frame:   >tPICK:2:3<
#   After stripping:  PICK:2:3
#
# Supported Mega serial events:
#   IR:bin              hand detected entering bin (IR sensor tripped)
#   LC:bin:grams        raw load cell reading in grams
#   PICK:bin:remaining  confirmed pick (remaining count after pick)
#   WRONG:bin           wrong bin picked
#   ASSEMBLY_DONE       all steps in sequence completed
#
# These map directly into MockHardware.simulate_ir_only() and
# MockHardware.simulate_pick() — same methods the sim buttons use.
# The sequence engine and browser HMI are completely unaware of the
# difference between a simulated and a real hardware event.
# ======================================================================

# @sock.route("/ws_esp")
# def esp_ws(ws):
#     """
#     ESP32-B connects here on startup and holds the connection open.
#     Every Mega serial line arrives as a WebSocket text frame.
#     """
#     global esp_connection
#     esp_connection = ws
#     print("[ESP32] Connected — real hardware bridge active")

#     # Notify browser that physical hardware is online
#     socketio.emit("coordinator_event", {
#         "type":    "hardware_connected",
#         "message": "ESP32 bridge connected — Arduino Mega online",
#     })

#     eng = coordinator.get_engine("SMALL_A01")
#     hw  = coordinator.get_hardware("SMALL_A01")

#     while True:
#         data = ws.receive()

#         # None means ESP32-B disconnected
#         if data is None:
#             print("[ESP32] Disconnected")
#             esp_connection = None
#             socketio.emit("coordinator_event", {
#                 "type":    "hardware_disconnected",
#                 "message": "ESP32 bridge disconnected",
#             })
#             break

#         print(f"[ESP32] Raw: {repr(data)}")

#         # ── Unwrap >t...< framing added by ESP32-B sketch ────────────
#         if data.startswith(">t") and data.endswith("<"):
#             line = data[2:-1]          # e.g. "PICK:2:3"
#         else:
#             # Ignore control frames (>r, >s) — not sensor data
#             continue

#         print(f"[ESP32] Parsed: {line}")
#         parts = line.split(":")

#         try:

#             # ----------------------------------------------------------
#             # IR:bin
#             # Hand detected entering bin — fire IR callback only.
#             # Weight has not changed yet; sequence engine starts its
#             # confirmation timer waiting for the load cell drop.
#             # ----------------------------------------------------------
#             if parts[0] == "IR" and len(parts) == 2:
#                 bin_id = int(parts[1])
#                 print(f"[ESP32] IR trigger → bin {bin_id}")
#                 hw.simulate_ir_only(bin_id)

#             # ----------------------------------------------------------
#             # LC:bin:grams
#             # Raw load cell reading. Forward as a weight_change event
#             # so the supervisor dashboard can show live weight.
#             # Does NOT trigger a pick — just updates the display.
#             # ----------------------------------------------------------
#             elif parts[0] == "LC" and len(parts) == 3:
#                 bin_id = int(parts[1])
#                 grams  = float(parts[2])
#                 print(f"[ESP32] Load cell → bin {bin_id}: {grams}g")
#                 socketio.emit("weight_change", {
#                     "bin_id":   bin_id,
#                     "weight_g": grams,
#                     "cart_id":  "SMALL_A01",
#                 })

#             # ----------------------------------------------------------
#             # PICK:bin:remaining
#             # Confirmed pick event from Mega (IR + load cell both fired).
#             # remaining = how many parts are left in the bin after pick.
#             # We calculate qty_picked from bin metadata and call
#             # simulate_pick() which fires IR callback + weight drop
#             # inside MockHardware, advancing the sequence engine.
#             # ----------------------------------------------------------
#             elif parts[0] == "PICK" and len(parts) == 3:
#                 bin_id    = int(parts[1])
#                 remaining = int(parts[2])
#                 print(f"[ESP32] Confirmed pick → bin {bin_id}, {remaining} remaining")

#                 bin_meta = eng._bin_meta(bin_id) if eng.config else None
#                 if bin_meta:
#                     total_qty = bin_meta.get("qty", 1)
#                     picked    = total_qty - remaining
#                     unit_w    = bin_meta.get("unit_weight_g") or 100.0
#                     hw.simulate_pick(bin_id, max(picked, 1), unit_w)
#                 else:
#                     print(f"[ESP32] Warning: no bin_meta for bin {bin_id} — variant loaded?")

#             # ----------------------------------------------------------
#             # WRONG:bin
#             # Mega detected a pick from the wrong bin.
#             # Emit error event — browser shows red flash + buzzer.
#             # ----------------------------------------------------------
#             elif parts[0] == "WRONG" and len(parts) == 2:
#                 bin_id = int(parts[1])
#                 print(f"[ESP32] Wrong pick → bin {bin_id}")
#                 socketio.emit("engine_event", {
#                     "type":    "wrong_pick",
#                     "cart_id": "SMALL_A01",
#                     "bin_id":  bin_id,
#                 })

#             # ----------------------------------------------------------
#             # ASSEMBLY_DONE
#             # Mega reports full sequence complete.
#             # ----------------------------------------------------------
#             elif parts[0] == "ASSEMBLY_DONE":
#                 print("[ESP32] Assembly complete signal received")
#                 socketio.emit("engine_event", {
#                     "type":    "assembly_complete",
#                     "cart_id": "SMALL_A01",
#                 })

#             else:
#                 print(f"[ESP32] Unknown command: {line}")

#         except (ValueError, IndexError) as e:
#             print(f"[ESP32] Parse error on '{line}': {e}")

@sock.route("/ws_esp")
def esp_ws(ws):
    """
    ESP32-B connects here on startup and holds the connection open.
    Every Mega serial line arrives as a WebSocket text frame.
    Commands from laptop go back to Mega via ws.send().
    """
    global esp_connection
    esp_connection = ws
    print("[ESP32] Connected — real hardware bridge active")

    # Inject send function into ArduinoHardware so it can
    # send commands to Mega (LED, DISP, BUZZ etc.) over WiFi
    def send_to_mega(cmd: str):
        try:
            ws.send(f">c{cmd}<")  # ESP32 relay only forwards >c...< frames
        except Exception as e:
            print(f"[ESP32] Send error: {e}")

    if hasattr(hw_small, "set_send_fn"):
        hw_small.set_send_fn(send_to_mega)

    socketio.emit("coordinator_event", {
        "type":    "hardware_connected",
        "message": "ESP32 bridge connected — Arduino Mega online",
    })

    eng = coordinator.get_engine("SMALL_A01")

    while True:
        data = ws.receive()

        if data is None:
            print("[ESP32] Disconnected")
            esp_connection = None
            if hasattr(hw_small, "clear_send_fn"):
                hw_small.clear_send_fn()
            socketio.emit("coordinator_event", {
                "type":    "hardware_disconnected",
                "message": "ESP32 bridge disconnected",
            })
            break

        print(f"[ESP32] Raw: {repr(data)}")

        # Strip >t...< wrapper added by ESP32-B
        if data.startswith(">t") and data.endswith("<"):
            line = data[2:-1]
        else:
            continue

        print(f"[ESP32] Parsed: {line}")

        # ── Connectivity test sketch (wifi_link_test.ino) ─────────────────
        # Respond to PING:N with PONG:N immediately, before any other handler.
        if line.startswith("PING:"):
            seq = line[5:].strip()
            try:
                ws.send(f">cPONG:{seq}<")
                print(f"[ESP32] Link test PING:{seq} → PONG:{seq}")
            except Exception as e:
                print(f"[ESP32] PONG send error: {e}")
            socketio.emit("coordinator_event", {
                "type":    "link_test_ping",
                "seq":     seq,
                "message": f"WiFi link test PING:{seq} — PONG sent",
            })
            continue

        # TEST_HELLO — wifi_link_test.ino just booted, acknowledge test mode
        if line == "TEST_HELLO":
            try:
                ws.send(">cTEST_ACK<")
                print("[ESP32] wifi_link_test detected — TEST_ACK sent")
            except Exception as e:
                print(f"[ESP32] TEST_ACK send error: {e}")
            socketio.emit("coordinator_event", {
                "type":    "link_test_hello",
                "message": "WiFi link test sketch connected — responding to PINGs",
            })
            continue

        # Route to ArduinoHardware if available (real hardware mode)
        # Otherwise fall back to MockHardware simulation methods
        if hasattr(hw_small, "handle_serial_line"):
            # ArduinoHardware — handles IR, WEIGHT, READY etc. natively
            hw_small.handle_serial_line(line)

            # Also forward weight updates to browser
            if line.startswith("WEIGHT:"):
                parts = line.split(":")
                if len(parts) >= 3:
                    socketio.emit("weight_change", {
                        "bin_id":   int(parts[1]) + 1,
                        "weight_g": float(parts[2]),
                        "cart_id":  "SMALL_A01",
                    })

        else:
            # MockHardware fallback — parse and simulate
            parts = line.split(":")
            try:
                if parts[0] == "IR_TRIGGERED" and len(parts) == 2:
                    hw_small.simulate_ir_only(int(parts[1]) + 1)

                elif parts[0] == "WEIGHT" and len(parts) == 3:
                    socketio.emit("weight_change", {
                        "bin_id":   int(parts[1]) + 1,
                        "weight_g": float(parts[2]),
                        "cart_id":  "SMALL_A01",
                    })

                elif parts[0] == "WRONG" and len(parts) == 2:
                    socketio.emit("engine_event", {
                        "type":    "wrong_pick",
                        "cart_id": "SMALL_A01",
                        "bin_id":  int(parts[1]) + 1,
                    })

                elif parts[0] == "ASSEMBLY_DONE":
                    socketio.emit("engine_event", {
                        "type":    "assembly_complete",
                        "cart_id": "SMALL_A01",
                    })

            except (ValueError, IndexError) as e:
                print(f"[ESP32] Parse error on '{line}': {e}")

def send_to_esp(command: str):
    """Send a command to Arduino Mega via ESP32-B WebSocket."""
    global esp_connection
    if esp_connection:
        try:
            # Wrap in >c...< format — ESP32-B forwards this to Mega via Serial2
            esp_connection.send(f">c{command}<")
            print(f"[ESP32] Sent to Mega: {command}")
        except Exception as e:
            print(f"[ESP32] Send error: {e}")
    else:
        print("[ESP32] Cannot send — no ESP32 connected")


@socketio.on("esp_command")
def on_esp_command(data):
    """
    Browser emits 'esp_command' with a command string.
    We forward it to Arduino Mega via ESP32-B.
    
    Example from browser JS:
        socket.emit('esp_command', {command: 'LED:2:green'})
    """
    command = data.get("command", "")
    if command:
        send_to_esp(command)


# ======================================================================
# SocketIO lifecycle
# ======================================================================

@socketio.on("connect")
def on_connect():
    """Push full current state to the newly connected browser tab only."""
    emit("coordinator_event", {
        "type": "mode_changed",
        "mode": coordinator.mode,
    })

    for cart_id, inst in coordinator.trolleys.items():
        emit("coordinator_event", {
            "type":             "trolley_status",
            "cart_id":          cart_id,
            "status":           inst.status,
            "loaded_config_id": inst.loaded_config_id,
            "is_active":        cart_id == coordinator._active_cart_id,
            "mode":             coordinator.mode,
        })

        if inst.engine.config is not None:
            cfg = inst.engine.config
            emit("engine_event", {
                "type":         "variant_loaded",
                "cart_id":      cart_id,
                "variant_id":   cfg.get("variant_id", ""),
                "variant_name": cfg.get("variant_name", ""),
                "bins":         cfg.get("bins", []),
                "sequence":     cfg.get("pick_sequence", []),
            })
            step = inst.engine._current_step()
            if step is not None:
                bin_meta = inst.engine._bin_meta(step["bin_id"]) or {}
                emit("engine_event", {
                    "type":        "step_started",
                    "cart_id":     cart_id,
                    "step":        step["step"],
                    "bin_id":      step["bin_id"],
                    "part_name":   bin_meta.get("part_name", ""),
                    "part_number": bin_meta.get("part_number", ""),
                    "qty":         step["qty"],
                    "instruction": step.get("instruction", ""),
                })

    for cart_id, authority in authority_state.items():
        emit("authority_event", {
            "type":      "authority_changed",
            "cart_id":   cart_id,
            "authority": authority,
        })

    # Tell new browser tabs whether hardware is currently connected.
    # USB mode: serial port open means connected (no ESP32 involved).
    # WiFi mode: connected only when ESP32 has an active WebSocket session.
    _usb_mode = hasattr(hw_small, "_transport") and hw_small._transport == "usb"
    _usb_up   = _usb_mode and getattr(hw_small, "_serial", None) and hw_small._serial.is_open
    _hw_up    = _usb_up or (not _usb_mode and bool(esp_connection))
    _hw_msg   = (
        "Arduino Mega connected via USB serial" if _usb_up else
        "Arduino Mega online via ESP32"          if esp_connection else
        "Running in simulation mode"
    )
    emit("coordinator_event", {
        "type":    "hardware_connected" if _hw_up else "hardware_disconnected",
        "message": _hw_msg,
    })


if __name__ == "__main__":
    print("Smart Assistive Part Pick — Server")
    if hasattr(hw_small, "_transport"):
        _t = hw_small._transport.upper()
        _extra = "waiting for ESP32 on /ws_esp" if _t == "WIFI" else f"serial on {hw_small._port}"
        print(f"Hardware mode:   {_t} ({_extra})")
    print("Fleet Manager:   http://localhost:5000/manager")
    print("Operator HMI:    http://localhost:5000/")
    print("Supervisor:      http://localhost:5000/supervisor")
    print("ESP32 Bridge:    ws://0.0.0.0:5000/ws_esp")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False,
                 allow_unsafe_werkzeug=True)