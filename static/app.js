/* =========================================================================
   Smart Assistive Part Pick — frontend logic
   Connects to Flask-SocketIO server, renders the trolley, responds to events.
   ========================================================================= */

const socket = io();

const state = {
  variants: [],
  activeFilename: null,
  config: null,           // loaded variant config
  currentStep: null,      // current step object
  currentStepIdx: 0,      // 0-indexed step
  totalSteps: 0,
  errors: 0,
  audioCtx: null,
};

/* ---------- DOM helpers ---------- */
const $ = (id) => document.getElementById(id);

function fmtTs(ms) {
  const d = new Date(ms);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  const ms3 = String(d.getMilliseconds()).padStart(3, "0");
  return `${hh}:${mm}:${ss}.${ms3}`;
}

/* ---------- audio via WebAudio ---------- */
function ensureAudio() {
  if (!state.audioCtx) {
    try { state.audioCtx = new (window.AudioContext || window.webkitAudioContext)(); }
    catch { state.audioCtx = null; }
  }
  return state.audioCtx;
}
function beep({ freq = 880, dur = 0.12, type = "sine", vol = 0.15 }) {
  const ctx = ensureAudio(); if (!ctx) return;
  const o = ctx.createOscillator(); const g = ctx.createGain();
  o.type = type; o.frequency.value = freq;
  g.gain.value = vol;
  o.connect(g).connect(ctx.destination);
  o.start();
  g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + dur);
  o.stop(ctx.currentTime + dur);
}
function playCue(cue) {
  switch (cue) {
    case "chime_ok":   beep({ freq: 880, dur: 0.1 }); break;
    case "chime_next": beep({ freq: 520, dur: 0.07, vol: 0.1 }); break;
    case "buzz_error":
      beep({ freq: 180, dur: 0.18, type: "square", vol: 0.22 });
      setTimeout(() => beep({ freq: 140, dur: 0.18, type: "square", vol: 0.22 }), 120);
      break;
    case "chime_done":
      beep({ freq: 660, dur: 0.1 });
      setTimeout(() => beep({ freq: 880, dur: 0.1 }), 100);
      setTimeout(() => beep({ freq: 1174, dur: 0.18 }), 200);
      break;
  }
}

/* ---------- variant picker ---------- */
async function loadVariants() {
  const r = await fetch("/api/variants"); state.variants = await r.json();
  const listEl = $("variant-list");
  listEl.innerHTML = "";
  for (const v of state.variants) {
    const row = document.createElement("div");
    row.className = "variant-row";
    row.dataset.filename = v.filename;
    row.innerHTML = `
      <div style="flex:1;">
        <div class="vid">${v.variant_id}</div>
        <div class="vname">${v.variant_name}</div>
        <div class="vdesc">${v.description}</div>
      </div>`;
    row.onclick = () => selectVariant(v.filename);
    listEl.appendChild(row);
  }
}

async function selectVariant(filename) {
  document.querySelectorAll(".variant-row").forEach(r =>
    r.classList.toggle("active", r.dataset.filename === filename));
  state.activeFilename = filename;
  await fetch("/api/variant/load", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename })
  });
  $("btn-start").disabled = false;
}

async function startSequence() {
  ensureAudio();
  await fetch("/api/variant/start", { method: "POST" });
  $("btn-start").disabled = true;
}

async function resetAll() {
  await fetch("/api/variant/reset", { method: "POST" });
  state.config = null;
  state.currentStep = null;
  state.currentStepIdx = 0;
  state.errors = 0;
  renderBins([]);
  $("step-card-wrap").innerHTML = "";
  $("variant-title").textContent = "No variant loaded";
  $("hdr-step").textContent = "—/—";
  $("hdr-errors").textContent = "0";
  $("hdr-state").textContent = "IDLE";
  $("event-log").innerHTML = "";
  $("event-count").textContent = "0";
  $("btn-start").disabled = state.activeFilename === null;
  if (state.activeFilename) {
    // re-load to restore bin display
    await fetch("/api/variant/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: state.activeFilename })
    });
  }
}

/* ---------- bin rendering ---------- */
function renderBins(bins) {
  const el = $("bins");
  el.innerHTML = "";
  for (const b of bins) {
    const binEl = document.createElement("div");
    binEl.className = "bin led-off";
    binEl.id = `bin-${b.bin_id}`;
    binEl.innerHTML = `
      <div class="bin-header">
        <div class="led"></div>
        <div class="bin-id">BIN ${String(b.bin_id).padStart(2, "0")}</div>
        <div class="part-no">${b.part_number}</div>
      </div>
      <div class="display" id="disp-${b.bin_id}">${b.part_name.slice(0,14)}\nQty: ${b.initial_qty}</div>
      <button class="pick-btn" data-bin="${b.bin_id}">Pick From This Bin</button>
    `;
    binEl.querySelector(".pick-btn").onclick = () => operatorPick(b.bin_id);
    el.appendChild(binEl);
  }
}

function setBinLed(binId, color) {
  const el = $(`bin-${binId}`);
  if (!el) return;
  el.classList.remove("led-off", "led-active", "led-done", "led-error", "led-idle");
  el.classList.add(`led-${color}`);
}

function setBinDisplay(binId, text) {
  const el = $(`disp-${binId}`);
  if (el) el.textContent = text;
}

/* ---------- step card ---------- */
function renderStepCard(payload) {
  $("step-card-wrap").innerHTML = `
    <div class="step-card armed">
      <div class="step-label">Current Step ${payload.step} of ${state.totalSteps}</div>
      <div class="step-action">
        <span class="qty-badge">${payload.qty}×</span>
        ${payload.part_name}
      </div>
      <div class="step-instruction">${payload.instruction}</div>
    </div>`;
  $("hdr-step").textContent = `${payload.step}/${state.totalSteps}`;
  $("hdr-state").textContent = "PICK";
}

function renderCompleteCard(payload) {
  const onTarget = payload.on_target;
  $("step-card-wrap").innerHTML = `
    <div class="step-card" style="border-color:${onTarget ? 'var(--status-ok)' : 'var(--status-warn)'};">
      <div class="step-label" style="color:${onTarget ? 'var(--status-ok)' : 'var(--status-warn)'};">
        ✓ Variant Complete
      </div>
      <div class="step-action">${payload.variant_id}</div>
      <div class="step-instruction">
        Completed in <b style="color:var(--text-0);">${payload.total_time_sec}s</b>
        (target ${payload.target_time_sec}s) &nbsp;·&nbsp;
        ${payload.correct_picks} correct picks &nbsp;·&nbsp;
        ${payload.errors} error${payload.errors === 1 ? '' : 's'}
      </div>
    </div>`;
  $("hdr-state").textContent = "DONE";
}

/* ---------- event log ---------- */
let eventCount = 0;
function logEvent(cls, kind, msg) {
  eventCount++;
  $("event-count").textContent = eventCount;
  const line = document.createElement("div");
  line.className = `entry ${cls}`;
  line.innerHTML = `
    <span class="ts">${fmtTs(Date.now())}</span>
    <span class="kind">${kind}</span>
    ${msg}`;
  const log = $("event-log");
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

/* ---------- socket handlers ---------- */
socket.on("led_update", ({ bin_id, color }) => setBinLed(bin_id, color));
socket.on("display_update", ({ bin_id, text }) => setBinDisplay(bin_id, text));
socket.on("audio", ({ cue }) => playCue(cue));
socket.on("buzzer", ({ on }) => {
  if (on) document.body.style.background = "rgba(255,64,64,0.08)";
  else document.body.style.background = "";
});

socket.on("engine_event", (ev) => {
  switch (ev.type) {
    case "variant_loaded":
      state.config = { bins: ev.bins, sequence: ev.sequence };
      state.totalSteps = ev.sequence.length;
      $("variant-title").textContent = ev.variant_name;
      renderBins(ev.bins);
      logEvent("info", "VARIANT", `${ev.variant_id} loaded · ${ev.bins.length} bins · ${ev.sequence.length} steps`);
      break;
    case "step_started":
      state.currentStep = ev;
      renderStepCard(ev);
      logEvent("info", "STEP", `Step ${ev.step}: pick ${ev.qty}× ${ev.part_name} from bin ${ev.bin_id}`);
      break;
    case "pick_correct":
      logEvent("ok", "PICK OK", `bin ${ev.bin_id} · qty ${ev.qty_picked} · ${ev.step_duration_sec}s · ${ev.remaining} left`);
      break;
    case "step_completed":
      // just a marker, no log
      break;
    case "pick_wrong_bin":
      state.errors++;
      $("hdr-errors").textContent = state.errors;
      logEvent("err", "WRONG BIN", ev.message);
      break;
    case "pick_qty_mismatch":
      state.errors++;
      $("hdr-errors").textContent = state.errors;
      logEvent("err", "QTY ERROR", ev.message);
      break;
    case "ghost_ir":
      logEvent("warn", "GHOST IR", ev.message);
      break;
    case "operator_stuck":
      logEvent("warn", "STUCK", ev.message);
      break;
    case "variant_complete":
      renderCompleteCard(ev);
      logEvent("ok", "COMPLETE",
        `${ev.variant_id} · ${ev.total_time_sec}s / ${ev.target_time_sec}s target · ${ev.errors} errors`);
      break;
    default:
      logEvent("info", ev.type.toUpperCase().replace(/_/g, " "), "");
  }
});

/* ---------- operator actions ---------- */
async function operatorPick(binId) {
  await fetch("/api/sim/pick", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bin_id: binId, qty: 0 })  // 0 = use step's expected qty
  });
}

async function faultIrOnly() {
  if (!state.currentStep) return;
  await fetch("/api/sim/ir_only", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bin_id: state.currentStep.bin_id })
  });
}

async function faultWrongBin() {
  if (!state.currentStep || !state.config) return;
  // find a bin that ISN'T the current target
  const targetBin = state.currentStep.bin_id;
  const wrongBin = state.config.bins.find(b => b.bin_id !== targetBin);
  if (!wrongBin) return;
  await fetch("/api/sim/pick", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bin_id: wrongBin.bin_id, qty: 1 })
  });
}

async function faultWrongQty() {
  if (!state.currentStep) return;
  const expectedQty = state.currentStep.qty;
  // pick one more or one less to force mismatch
  const badQty = expectedQty > 1 ? expectedQty - 1 : expectedQty + 1;
  await fetch("/api/sim/wrong_qty", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bin_id: state.currentStep.bin_id, qty: badQty })
  });
}

/* ---------- init ---------- */
$("btn-start").onclick = startSequence;
$("btn-reset").onclick = resetAll;
$("btn-ir-only").onclick = faultIrOnly;
$("btn-wrong-bin").onclick = faultWrongBin;
$("btn-wrong-qty").onclick = faultWrongQty;

loadVariants();
