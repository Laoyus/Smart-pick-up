/* =========================================================================
   Smart Assistive Part Pick — Operator HMI frontend (Round 3)

   Dual-trolley aware: all events carry cart_id and are routed to the
   correct trolley visualization. Handles:
     • engine_event      — pick-sequence state from SequenceEngine
     • coordinator_event — mode changes, trolley activation (linked mode)
     • rerouting_event   — adaptive path rerouting notifications
     • led_update / display_update / audio / buzzer — hardware simulation
   ========================================================================= */

const socket = io();
const $ = id => document.getElementById(id);

/* ---- application state ---- */
const state = {
  variants:       [],
  activeFilename: null,
  trolleyTarget:  "SMALL_A01",    // which trolley the load/start buttons target
  configs: {
    "SMALL_A01": null,
    "LARGE_A01": null,
  },
  currentStep: {
    "SMALL_A01": null,
    "LARGE_A01": null,
  },
  totalSteps:  { "SMALL_A01": 0, "LARGE_A01": 0 },
  errors:      { "SMALL_A01": 0, "LARGE_A01": 0 },
  mode:        "standalone",
  activeTrolley: null,
  audioCtx:    null,
  authority:   { "SMALL_A01": "manager", "LARGE_A01": "manager" },
};

/* ======================================================================
   Audio
   ====================================================================== */
function ensureAudio() {
  if (!state.audioCtx) {
    try { state.audioCtx = new (window.AudioContext || window.webkitAudioContext)(); }
    catch { state.audioCtx = null; }
  }
  return state.audioCtx;
}

function beep({ freq = 880, dur = 0.12, type = "sine", vol = 0.15 }) {
  const ctx = ensureAudio(); if (!ctx) return;
  const o = ctx.createOscillator();
  const g = ctx.createGain();
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
      setTimeout(() => beep({ freq: 880,  dur: 0.1  }), 100);
      setTimeout(() => beep({ freq: 1174, dur: 0.18 }), 200);
      break;
  }
}

/* ======================================================================
   Authority control
   ====================================================================== */
function applyAuthorityUI(cartId, authority) {
  state.authority[cartId] = authority;
  const currentTarget = $("trolley-target")?.value || "SMALL_A01";
  if (cartId !== currentTarget) return; // active target unchanged, skip control update

  const isLocked  = authority === "manager";
  const badge     = $("authority-status");
  if (badge) {
    badge.textContent = isLocked ? "🔒 MANAGER-CONTROLLED" : "🔓 STANDALONE OPERATION";
    badge.className   = `authority-badge ${isLocked ? "authority-locked" : "authority-unlocked"}`;
  }

  const variantList = $("variant-list");
  if (variantList) {
    variantList.style.pointerEvents = isLocked ? "none" : "";
    variantList.style.opacity       = isLocked ? "0.35" : "";
  }

  const btnStart = $("btn-start");
  if (btnStart) {
    if (isLocked) {
      btnStart.disabled = true;
    } else {
      btnStart.disabled = state.activeFilename === null;
    }
  }
}

/* ======================================================================
   Variant selector (left panel)
   ====================================================================== */
async function loadVariants() {
  const r = await fetch("/api/variants");
  state.variants = await r.json();
  const listEl = $("variant-list");
  listEl.innerHTML = "";
  for (const v of state.variants) {
    const isLinked = v.operating_mode === "linked";
    const row = document.createElement("div");
    row.className = "variant-row";
    row.dataset.filename = v.filename;
    row.innerHTML = `
      <div style="flex:1;">
        <div style="display:flex;gap:6px;align-items:center;">
          <div class="vid">${v.variant_id}</div>
          ${isLinked ? `<span class="tag" style="color:var(--status-warn);background:rgba(255,174,0,.12);">LINKED</span>` : ""}
        </div>
        <div class="vname">${v.variant_name}</div>
        <div class="vdesc">${v.description}</div>
      </div>`;
    row.onclick = () => selectVariant(v.filename);
    listEl.appendChild(row);
  }
}

async function selectVariant(filename) {
  const target = $("trolley-target")?.value || "SMALL_A01";
  if (state.authority[target] === "manager") return; // locked by manager

  document.querySelectorAll(".variant-row").forEach(r =>
    r.classList.toggle("active", r.dataset.filename === filename));
  state.activeFilename = filename;
  await fetch("/api/fleet/variant/push", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename, cart_id: target })
  });
  $("btn-start").disabled = false;
}

async function startSequence() {
  ensureAudio();
  const target = $("trolley-target")?.value || "SMALL_A01";
  await fetch("/api/fleet/variant/activate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cart_id: target })
  });
  $("btn-start").disabled = true;
}

async function resetAll() {
  const target = $("trolley-target")?.value || "SMALL_A01";
  await fetch("/api/variant/reset", { method: "POST" });

  state.errors[target]      = 0;
  state.currentStep[target] = null;
  $("hdr-errors").textContent = "0";
  $("hdr-state").textContent  = "IDLE";
  $("hdr-step").textContent   = "—/—";
  $("step-card-wrap").innerHTML = "";
  $("variant-title").textContent = "No variant loaded";
  clearBins(target);
  $("event-log").innerHTML = "";
  $("event-count").textContent = "0";
  $("btn-start").disabled = state.activeFilename === null;
  hideBanner();

  // Reload to restore bin display
  if (state.activeFilename) {
    await fetch("/api/fleet/variant/push", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: state.activeFilename, cart_id: target })
    });
  }
}

/* ======================================================================
   Bin rendering (per-trolley)
   ====================================================================== */
function getBinsEl(cartId) {
  return $(`bins-${cartId}`);
}

function renderBins(cartId, bins) {
  const el = getBinsEl(cartId);
  if (!el) return;
  el.innerHTML = "";
  // Adjust grid columns based on bin count
  const cols = bins.length <= 4 ? Math.min(bins.length, 2) : 3;
  el.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;

  for (const b of bins) {
    const binEl = document.createElement("div");
    binEl.className = "bin led-off";
    binEl.id = `bin-${cartId}-${b.bin_id}`;
    const wt = b.unit_weight_g ? `${b.unit_weight_g}g` : "IR only";
    binEl.innerHTML = `
      <div class="bin-header">
        <div class="led"></div>
        <div class="bin-id">BIN ${String(b.bin_id).padStart(2,"0")}</div>
        <div class="part-no">${b.part_number}</div>
      </div>
      <div class="display" id="disp-${cartId}-${b.bin_id}">${b.part_name.slice(0,14)}\nQty: ${b.initial_qty} · ${wt}</div>
      <button class="pick-btn" data-bin="${b.bin_id}" data-cart="${cartId}">Pick From This Bin</button>`;
    binEl.querySelector(".pick-btn").onclick = () => operatorPick(cartId, b.bin_id);
    el.appendChild(binEl);
  }
}

function clearBins(cartId) {
  const el = getBinsEl(cartId);
  if (!el) return;
  el.innerHTML = `
    <div class="bin-placeholder" style="grid-column:1/-1;text-align:center;
         color:var(--text-2);font-size:12px;padding:20px;">
      Load a variant to see bins
    </div>`;
}

function setBinLed(cartId, binId, color) {
  const el = $(`bin-${cartId}-${binId}`);
  if (!el) return;
  el.classList.remove("led-off","led-active","led-done","led-error","led-idle");
  el.classList.add(`led-${color}`);
}

function setBinDisplay(cartId, binId, text) {
  const el = $(`disp-${cartId}-${binId}`);
  if (el) el.textContent = text;
}

/* ======================================================================
   Trolley active indicator
   ====================================================================== */
function setActiveTrolley(cartId) {
  state.activeTrolley = cartId;

  // Update section borders
  ["SMALL_A01","LARGE_A01"].forEach(cid => {
    const sec = $(`section-${cid}`);
    const trl = $(`trolley-${cid}`);
    const pil = $(`pill-${cid}`);
    if (!sec) return;
    const isActive = cid === cartId;
    sec.classList.toggle("trolley-section-active", isActive);
    if (trl) trl.classList.toggle("trolley-active", isActive);
    if (pil) pil.style.display = isActive ? "inline-block" : "none";
  });

  // Update left-panel indicator
  const badge = $("active-trolley-badge");
  if (badge) {
    badge.textContent = cartId || "—";
    badge.style.color = cartId ? "var(--status-ok)" : "var(--status-idle)";
  }
}

/* ======================================================================
   Step card
   ====================================================================== */
function renderStepCard(ev) {
  const cartTag = ev.cart_id
    ? `<span style="font-family:var(--font-mono);font-size:10px;
         color:${ev.cart_id === "SMALL_A01" ? "var(--cat-yellow)" : "var(--status-ok)"};
         letter-spacing:.05em;margin-right:6px;">${ev.cart_id}</span>`
    : "";
  const totalSteps = state.totalSteps[ev.cart_id] || ev.step;
  $("step-card-wrap").innerHTML = `
    <div class="step-card armed">
      <div class="step-label">${cartTag}Step ${ev.step} of ${state.totalSteps[ev.cart_id] || "?"}</div>
      <div class="step-action">
        <span class="qty-badge">${ev.qty}×</span>${ev.part_name}
      </div>
      <div class="step-instruction">${ev.instruction}</div>
    </div>`;
  $("hdr-step").textContent =
    `${ev.step}/${state.totalSteps[ev.cart_id] || "?"}`;
  $("hdr-state").textContent = "PICK";
  $("variant-title").textContent =
    state.configs[ev.cart_id]?.variant_name || ev.part_name;
}

function renderCompleteCard(ev) {
  $("step-card-wrap").innerHTML = `
    <div class="step-card" style="border-color:${ev.on_target ? "var(--status-ok)" : "var(--status-warn)"};">
      <div class="step-label" style="color:${ev.on_target ? "var(--status-ok)" : "var(--status-warn)"};">
        ✓ Sequence Complete
      </div>
      <div class="step-action">${ev.variant_id}</div>
      <div class="step-instruction">
        Completed in <b style="color:var(--text-0);">${ev.total_time_sec}s</b>
        (target ${ev.target_time_sec}s) &nbsp;·&nbsp;
        ${ev.correct_picks} correct &nbsp;·&nbsp; ${ev.errors} error${ev.errors===1?"":"s"}
      </div>
    </div>`;
  $("hdr-state").textContent = "DONE";
}

/* ======================================================================
   Event log
   ====================================================================== */
let eventCount = 0;
function logEvent(cls, kind, msg) {
  eventCount++;
  $("event-count").textContent = eventCount;
  const line = document.createElement("div");
  line.className = `entry ${cls}`;
  line.innerHTML = `
    <span class="ts">${fmtTs(Date.now())}</span>
    <span class="kind">${kind}</span>${msg}`;
  const log = $("event-log");
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

function fmtTs(ms) {
  const d = new Date(ms);
  const hh = String(d.getHours()).padStart(2,"0");
  const mm = String(d.getMinutes()).padStart(2,"0");
  const ss = String(d.getSeconds()).padStart(2,"0");
  const ms3 = String(d.getMilliseconds()).padStart(3,"0");
  return `${hh}:${mm}:${ss}.${ms3}`;
}

/* ======================================================================
   Rerouting banner
   ====================================================================== */
function showBanner(msg) {
  const b = $("reroute-banner");
  if (!b) return;
  $("reroute-msg").textContent = msg;
  b.style.display = "block";
}
function hideBanner() {
  const b = $("reroute-banner");
  if (b) b.style.display = "none";
}

/* ======================================================================
   Socket event handlers
   ====================================================================== */

/* --- hardware layer --- */
socket.on("led_update",     ({ bin_id, color, cart_id }) =>
  setBinLed(cart_id || "SMALL_A01", bin_id, color));

socket.on("display_update", ({ bin_id, text, cart_id }) =>
  setBinDisplay(cart_id || "SMALL_A01", bin_id, text));

socket.on("audio",   ({ cue })  => playCue(cue));
socket.on("buzzer",  ({ on })   => {
  document.body.style.background = on ? "rgba(255,64,64,0.08)" : "";
});

/* --- coordinator events --- */
socket.on("coordinator_event", ev => {
  switch (ev.type) {
    case "mode_changed": {
      state.mode = ev.mode;
      const badge = $("mode-badge");
      if (badge) badge.textContent = ev.mode.toUpperCase();
      logEvent("info", "MODE", ev.mode.toUpperCase());
      break;
    }
    case "trolley_activated": {
      setActiveTrolley(ev.cart_id);
      const lbl = $("active-phase-label");
      if (lbl) lbl.textContent =
        `Phase ${ev.phase} / ${ev.total_phases} · Steps ${ev.first_step}–${ev.last_step}`;
      logEvent("info", "PHASE",
        `${ev.cart_id} active — Phase ${ev.phase}/${ev.total_phases}`);
      break;
    }
    case "trolley_status": {
      if (ev.is_active) setActiveTrolley(ev.cart_id);
      break;
    }
    case "linked_started": {
      hideBanner();
      logEvent("ok", "LINKED", `${ev.variant_id} — ${ev.total_steps} steps, ${ev.total_phases} phases`);
      // Render bins for each trolley from linked config
      if (ev.trolleys) {
        for (const [cid, tcfg] of Object.entries(ev.trolleys)) {
          if (tcfg.bins) renderBins(cid, tcfg.bins);
        }
      }
      $("variant-title").textContent = ev.variant_name;
      $("hdr-step").textContent = `—/${ev.total_steps}`;
      $("hdr-errors").textContent = "0";
      $("hdr-state").textContent = "LINKED";
      // Update total step counts
      if (ev.sequence) {
        for (const cid of Object.keys(state.totalSteps)) {
          state.totalSteps[cid] = ev.sequence.filter(s => s.trolley_id === cid).length || ev.sequence.length;
        }
      }
      break;
    }
    case "linked_complete": {
      logEvent("ok", "COMPLETE",
        `${ev.variant_id} · ${ev.total_time_sec}s / ${ev.target_time_sec}s`);
      $("hdr-state").textContent = "DONE";
      break;
    }
    case "emergency_stop": {
      logEvent("err", "EMERGENCY", ev.message || "Emergency stop — all trolleys reset");
      showBanner("⚠ EMERGENCY STOP — all trolleys have been reset");
      if ($("hdr-state")) $("hdr-state").textContent = "STOPPED";
      break;
    }
  }
});

/* --- engine events --- */
socket.on("engine_event", ev => {
  const cid = ev.cart_id || "SMALL_A01";

  switch (ev.type) {
    case "variant_loaded": {
      state.configs[cid] = { variant_id: ev.variant_id, variant_name: ev.variant_name };
      state.totalSteps[cid]  = (ev.sequence || []).length;
      state.errors[cid]      = 0;
      hideBanner();
      if (ev.bins) renderBins(cid, ev.bins);
      $("variant-title").textContent = ev.variant_name;
      $("hdr-step").textContent  = `—/${ev.sequence?.length || 0}`;
      $("hdr-errors").textContent = "0";
      $("hdr-state").textContent  = "LOADED";
      $("step-card-wrap").innerHTML = "";
      logEvent("info", "LOAD",
        `[${cid}] ${ev.variant_id} · ${ev.bins?.length} bins · ${ev.sequence?.length} steps`);
      break;
    }
    case "step_started": {
      state.currentStep[cid] = ev;
      setActiveTrolley(cid);
      renderStepCard(ev);
      logEvent("info", "STEP",
        `[${cid}] Step ${ev.step}: pick ${ev.qty}× ${ev.part_name} from bin ${ev.bin_id}`);
      break;
    }
    case "pick_correct": {
      logEvent("ok", "PICK OK",
        `[${cid}] bin ${ev.bin_id} · qty ${ev.qty_picked} · ${ev.step_duration_sec}s · ${ev.remaining} left`);
      break;
    }
    case "step_completed": {
      break; // just a marker; no UI action needed
    }
    case "pick_wrong_bin": {
      state.errors[cid] = (state.errors[cid] || 0) + 1;
      $("hdr-errors").textContent =
        Object.values(state.errors).reduce((a,b) => a+b, 0);
      logEvent("err", "WRONG PART", `[${cid}] ${ev.message}`);
      break;
    }
    case "pick_qty_mismatch": {
      state.errors[cid] = (state.errors[cid] || 0) + 1;
      $("hdr-errors").textContent =
        Object.values(state.errors).reduce((a,b) => a+b, 0);
      logEvent("err", "QTY ERROR", `[${cid}] ${ev.message}`);
      break;
    }
    case "ghost_ir": {
      logEvent("warn", "GHOST IR", `[${cid}] ${ev.message}`);
      break;
    }
    case "operator_stuck": {
      logEvent("warn", "STUCK", `[${cid}] ${ev.message}`);
      break;
    }
    case "variant_complete": {
      if (state.mode !== "linked") {
        renderCompleteCard(ev);
        logEvent("ok", "COMPLETE",
          `[${cid}] ${ev.variant_id} · ${ev.total_time_sec}s · ${ev.errors} errors`);
      }
      break;
    }
  }
});

/* --- rerouting events --- */
socket.on("rerouting_event", ev => {
  switch (ev.type) {
    case "reroute_triggered":
      logEvent("ok", "REROUTE →", `[${ev.cart_id}] ${ev.message}`);
      showBanner(`Rerouting: ${ev.message}`);
      break;
    case "alternate_loaded":
      logEvent("ok", "ALT LOADED", `[${ev.cart_id}] ${ev.variant_name} — ${ev.message}`);
      showBanner(ev.message);
      break;
    case "substitute_rejected":
      logEvent("err", "REJECTED", `[${ev.cart_id}] ${ev.message}`);
      break;
    case "reroute_error":
      logEvent("err", "REROUTE ERR", `[${ev.cart_id}] ${ev.message}`);
      break;
  }
});

/* --- authority events --- */
socket.on("authority_event", ev => {
  applyAuthorityUI(ev.cart_id, ev.authority);
  logEvent(
    ev.authority === "manager" ? "warn" : "ok",
    "AUTHORITY",
    `[${ev.cart_id}] → ${ev.authority.toUpperCase()}`
  );
});

/* ======================================================================
   Operator actions
   ====================================================================== */
function activeCartForFault() {
  // Use the active trolley for fault injection; fallback to SMALL_A01
  return state.activeTrolley || "SMALL_A01";
}

async function operatorPick(cartId, binId) {
  await fetch("/api/sim/pick", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cart_id: cartId, bin_id: binId, qty: 0 })
  });
}

async function faultIrOnly() {
  const cid  = activeCartForFault();
  const step = state.currentStep[cid];
  if (!step) return;
  await fetch("/api/sim/ir_only", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cart_id: cid, bin_id: step.bin_id })
  });
}

async function faultWrongBin() {
  const cid  = activeCartForFault();
  const step = state.currentStep[cid];
  const cfg  = state.configs[cid];
  if (!step) return;

  // Try to find a bin on the SAME trolley that is different
  const binsEl = getBinsEl(cid);
  const pickBtns = binsEl ? [...binsEl.querySelectorAll(".pick-btn")] : [];
  const wrongBin = pickBtns.find(b => +b.dataset.bin !== step.bin_id);
  if (!wrongBin) return;

  await fetch("/api/sim/pick", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cart_id: cid, bin_id: +wrongBin.dataset.bin, qty: 1 })
  });
}

async function faultWrongQty() {
  const cid  = activeCartForFault();
  const step = state.currentStep[cid];
  if (!step) return;
  const badQty = step.qty > 1 ? step.qty - 1 : step.qty + 1;
  await fetch("/api/sim/wrong_qty", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cart_id: cid, bin_id: step.bin_id, qty: badQty })
  });
}

/* ======================================================================
   Button wiring + init
   ====================================================================== */
if ($("btn-start"))     $("btn-start").onclick = startSequence;
if ($("btn-reset"))     $("btn-reset").onclick = resetAll;
if ($("btn-ir-only"))   $("btn-ir-only").onclick   = faultIrOnly;
if ($("btn-wrong-bin")) $("btn-wrong-bin").onclick  = faultWrongBin;
if ($("btn-wrong-qty")) $("btn-wrong-qty").onclick  = faultWrongQty;

if ($("trolley-target")) {
  $("trolley-target").onchange = () => {
    state.trolleyTarget = $("trolley-target").value;
    applyAuthorityUI(state.trolleyTarget, state.authority[state.trolleyTarget]);
  };
}

loadVariants();
