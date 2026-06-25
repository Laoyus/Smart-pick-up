"""
chat_server.py  —  Run this on YOUR laptop
==========================================
Two-way chat over WebSocket with the ESP32.

  YOU type here  →  appears on the other laptop's Serial Monitor
  They type there →  appears here

Run:
    python arduino_bridge/wifi_chat_test/chat_server.py

Then flash wifi_chat_test.ino to ESP32 #2, open Serial Monitor at 115200
on the other laptop, and both sides can start typing.
"""

import threading
import sys
from flask import Flask
from flask_sock import Sock

app  = Flask(__name__)
sock = Sock(app)

_ws  = None          # the live ESP32 WebSocket connection
_lock = threading.Lock()


def _stdin_loop():
    """Background thread: reads your keyboard input and sends to ESP32."""
    # Small delay so Flask startup messages finish printing first
    import time; time.sleep(1.5)
    print()
    print("══════════════════════════════════════════════")
    print("  Type a message and press Enter to send.")
    print("  Waiting for ESP32 to connect...")
    print("══════════════════════════════════════════════")
    print()

    while True:
        try:
            msg = input()          # blocks until you press Enter
        except (EOFError, KeyboardInterrupt):
            break
        if not msg.strip():
            continue
        with _lock:
            if _ws is not None:
                try:
                    _ws.send(msg)
                    print(f"  [YOU → ESP32] {msg}")
                except Exception as e:
                    print(f"  [send error] {e}")
            else:
                print("  [ESP32 not connected yet — message dropped]")


# Start stdin reader before Flask blocks the main thread
threading.Thread(target=_stdin_loop, daemon=True).start()


@sock.route("/ws_chat")
def ws_chat(ws):
    global _ws
    with _lock:
        _ws = ws

    print("\n[ESP32 connected] ✓  Link is up.\n")

    while True:
        msg = ws.receive()
        if msg is None:
            break
        print(f"  [ESP32 → YOU] {msg}")

    with _lock:
        _ws = None
    print("\n[ESP32 disconnected]\n")


if __name__ == "__main__":
    print("══════════════════════════════════════════════")
    print("  WiFi Chat Test — Laptop side")
    print("  WebSocket server: ws://0.0.0.0:5000/ws_chat")
    print("══════════════════════════════════════════════")
    # use_reloader=False is critical — reloader forks the process and
    # breaks the stdin thread and the global _ws variable
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
