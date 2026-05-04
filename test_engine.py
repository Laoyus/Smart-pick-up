"""Headless smoke test — runs a full pick sequence and prints the event stream."""

import time
from hardware_abstraction import MockHardware
from sequence_engine import SequenceEngine, EventType


events_captured = []

def hw_emit(ev, payload):
    events_captured.append(("HW:" + ev, payload))

def engine_emit(evt, payload):
    events_captured.append(("EN:" + evt.value, payload))


hw = MockHardware(emit_fn=hw_emit)
eng = SequenceEngine(hw, on_event=engine_emit)

print("=" * 60)
print("TEST 1: Load Model A, run correct sequence")
print("=" * 60)

eng.load_variant("configs/model_a.json")
eng.start()

# Execute each step correctly by simulating picks
for step in eng.config["pick_sequence"]:
    bm = eng._bin_meta(step["bin_id"])
    time.sleep(0.1)
    hw.simulate_pick(step["bin_id"], step["qty"], bm["unit_weight_g"])
    time.sleep(1.8)  # wait for confirmation window

time.sleep(1)

print(f"\nTotal events captured: {len(events_captured)}")
print(f"Engine errors: {eng.state.errors}")
print(f"Correct picks: {eng.state.correct_picks}")
print(f"Expected steps: {eng.state.total_steps}")

print("\nEngine-level events:")
for tag, p in events_captured:
    if tag.startswith("EN:"):
        msg = p.get("message") or p.get("variant_name") or p.get("part_name") or ""
        extra = ""
        if "step" in p:
            extra = f" step={p['step']}"
        if "bin_id" in p:
            extra += f" bin={p['bin_id']}"
        if "qty_picked" in p:
            extra += f" qty={p['qty_picked']}"
        if "total_time_sec" in p:
            extra += f" total={p['total_time_sec']}s"
        print(f"  {tag:30s}{extra}  {msg}")


print("\n" + "=" * 60)
print("TEST 2: Wrong bin picked")
print("=" * 60)

events_captured.clear()
eng.reset()
eng.load_variant("configs/model_a.json")
eng.start()
time.sleep(0.1)

# Sequence says step 1 = bin 5. Operator picks from bin 3 (wrong).
wrong_bin_meta = eng._bin_meta(3)
hw.simulate_pick(3, 1, wrong_bin_meta["unit_weight_g"])
time.sleep(1.8)

# Check we got the error
wrong_bin_events = [p for tag, p in events_captured if tag == "EN:pick_wrong_bin"]
assert len(wrong_bin_events) == 1, f"Expected 1 wrong_bin event, got {len(wrong_bin_events)}"
print(f"  [PASS] Detected wrong bin: {wrong_bin_events[0]['message']}")


print("\n" + "=" * 60)
print("TEST 3: Ghost IR (hand waved past, no pick)")
print("=" * 60)

events_captured.clear()
eng.reset()
eng.load_variant("configs/model_a.json")
eng.start()
time.sleep(0.1)

hw.simulate_ir_only(5)  # IR on target bin, but no weight drop
time.sleep(1.8)

ghost_events = [p for tag, p in events_captured if tag == "EN:ghost_ir"]
assert len(ghost_events) == 1, f"Expected 1 ghost_ir event, got {len(ghost_events)}"
print(f"  [PASS] Ignored false IR trigger: {ghost_events[0]['message']}")


print("\n" + "=" * 60)
print("TEST 4: Qty mismatch (right bin, wrong count)")
print("=" * 60)

events_captured.clear()
eng.reset()
eng.load_variant("configs/model_a.json")
eng.start()
time.sleep(0.1)

# Step 1 needs qty 1 from bin 5. Operator grabs 2.
bm = eng._bin_meta(5)
hw.simulate_pick(5, 2, bm["unit_weight_g"])
time.sleep(1.8)

qty_events = [p for tag, p in events_captured if tag == "EN:pick_qty_mismatch"]
assert len(qty_events) == 1, f"Expected 1 qty_mismatch event, got {len(qty_events)}"
print(f"  [PASS] Detected qty mismatch: {qty_events[0]['message']}")


print("\n" + "=" * 60)
print("TEST 5: Variant changeover Model A -> Model C (different sequence)")
print("=" * 60)

events_captured.clear()
eng.reset()
eng.load_variant("configs/model_c.json")
first_step = eng.config["pick_sequence"][0]
assert first_step["bin_id"] == 2, f"Model C step 1 should be bin 2 (isolator bush), got {first_step}"
print(f"  [PASS] Model C loaded; first step is bin {first_step['bin_id']} "
      f"({eng._bin_meta(first_step['bin_id'])['part_name']})")


print("\nALL TESTS PASSED")
