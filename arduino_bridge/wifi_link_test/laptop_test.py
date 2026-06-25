"""
laptop_test.py
==============
Standalone WiFi link test — run this on the laptop instead of app.py
when you just want to verify the full Arduino ↔ ESP32 ↔ Laptop chain.

Usage:
    python arduino_bridge/wifi_link_test/laptop_test.py

What it does:
  1. Starts a minimal WebSocket server on ws://0.0.0.0:5000/ws_esp
  2. Waits for the ESP32 relay to connect.
  3. Forwards every PING:N from the Arduino as PONG:N back.
  4. Prints a live stats table: TX, RX, miss-rate, RTT estimate.

The Arduino side must be running wifi_link_test.ino.
The ESP32 relay firmware is unchanged from production.

Dependencies:
    pip install flask flask-sock
"""

import time
from flask import Flask
from flask_sock import Sock

app  = Flask(__name__)
sock = Sock(app)

# ── Stats ─────────────────────────────────────────────────────────────────────
stats = {
    "rx":       0,      # PINGs received from Arduino
    "tx":       0,      # PONGs sent back
    "misorder": 0,      # sequence number jumps (reordering / drops)
    "last_seq": -1,
    "last_rtt_note": "n/a",   # RTT is measured on the Arduino side
    "start": time.time(),
}


def _print_stats():
    elapsed = int(time.time() - stats["start"])
    miss = max(0, stats["rx"] - stats["tx"])
    print(
        f"\r[{elapsed:>5}s]  "
        f"PING rx: {stats['rx']:>4}  "
        f"PONG tx: {stats['tx']:>4}  "
        f"seq err: {stats['misorder']:>3}  "
        f"last seq: {stats['last_seq']:>4}",
        end="", flush=True
    )


@sock.route("/ws_esp")
def ws_esp(ws):
    print("[Laptop] ESP32 relay connected.")
    print("[Laptop] Waiting for PING messages from Arduino…")
    print()

    # Acknowledge the test sketch
    try:
        ws.send(">cTEST_ACK<")
    except Exception:
        pass

    while True:
        data = ws.receive()
        if data is None:
            print("\n[Laptop] ESP32 relay disconnected.")
            break

        # Strip >t...< frame added by ESP32 relay
        if data.startswith(">t") and data.endswith("<"):
            line = data[2:-1]
        else:
            continue

        if line.startswith("PING:"):
            seq_str = line[5:].strip()
            try:
                seq = int(seq_str)
            except ValueError:
                continue

            # Sequence continuity check
            expected = (stats["last_seq"] + 1) % 1000
            if stats["last_seq"] >= 0 and seq != expected:
                stats["misorder"] += 1

            stats["last_seq"] = seq
            stats["rx"] += 1

            # Send PONG back (ESP32 unwraps >c...< and sends to Arduino via UART)
            try:
                ws.send(f">cPONG:{seq}<")
                stats["tx"] += 1
            except Exception as e:
                print(f"\n[Laptop] PONG send error: {e}")

            _print_stats()

        elif line == "TEST_HELLO":
            print("[Laptop] Arduino says TEST_HELLO — test sketch is live.\n")
            try:
                ws.send(">cTEST_ACK<")
            except Exception:
                pass

        elif line == "TEST_ACK_RECEIVED":
            print("[Laptop] Arduino acknowledged TEST_ACK.\n")

        else:
            # Print any other messages (READY, etc.)
            print(f"\n[Laptop] Arduino says: {line}")


if __name__ == "__main__":
    print("=" * 60)
    print("  WiFi Bridge Connectivity Test — Laptop Side")
    print("=" * 60)
    print("  Listening for ESP32 on ws://0.0.0.0:5000/ws_esp")
    print("  Make sure:")
    print("    1. Laptop is connected to the ESP32's WiFi AP")
    print("    2. Arduino is running wifi_link_test.ino")
    print("    3. ESP32 relay firmware is running")
    print("=" * 60)
    print()
    app.run(host="0.0.0.0", port=5000, debug=False)
