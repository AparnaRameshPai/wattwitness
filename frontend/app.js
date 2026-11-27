// Receipts frontend: plain JavaScript, no build step.
// Layout: a guided story (data -> question -> watch -> answer), an attack page, and a Vultr sidebar that shows,
// live, how the run uses the Vultr VM, Vultr Serverless Inference and the sandboxes.
"use strict";

// ---------- helpers ----------
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v) => (typeof v === "number" ? (Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(+v.toFixed(4))) : esc(v ?? "—"));
const usd = (v) => (!v ? "$0.00" : v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(3)}`);
const tok = (n) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n || 0));
const secs = (s) => `${(+s || 0).toFixed(1)}s`;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const lastLine = (s) => (String(s || "").trim().split("\n").filter(Boolean).pop() || "").slice(0, 140);
const BUSY = "The demo is busy, or you already have a run in progress. Please try again in a moment.";

const STATUS_CHIP = { verified: "chip-green", corrected: "chip-amber", unverified: "chip-red" };
const STATUS_WORD = { verified: "✓ Confirmed", corrected: "🛠 Corrected", unverified: "? Not confirmed" };
const STATUS_ICON = { verified: "✓", corrected: "🛠", unverified: "?" };
const humanize = (name) => String(name || "").replace(/_/g, " ");

// ---------- theme ----------
function setTheme(theme) {
  document.documentElement.classList.toggle("dark", theme === "dark");
  localStorage.setItem("theme", theme);
  $("themeIcon").textContent = theme === "dark" ? "☀️" : "🌙";
}
setTheme(localStorage.getItem("theme") === "dark" ? "dark" : "light");
$("themeBtn").addEventListener("click", () => setTheme(document.documentElement.classList.contains("dark") ? "light" : "dark"));

// ---------- tabs ----------
function showTab(name) {
  document.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("tab-on", b.dataset.tab === name));
  $("analyst").classList.toggle("hidden", name !== "analyst");
  $("lab").classList.toggle("hidden", name !== "lab");
}
document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
showTab("analyst");

// ---------- health ----------
async function refreshHealth() {
  try {
    const h = await (await fetch("/api/health")).json();
    const ok = h.docker && h.model_configured;
    $("health").className = `chip ${ok ? "chip-green" : "chip-red"} hidden sm:inline-flex`;
    $("health").innerHTML = `<span class="w-1.5 h-1.5 rounded-full ${ok ? "bg-emerald-500" : "bg-red-500"}"></span>` +
      (ok ? `Live on Vultr · ${h.runtime === "runsc" ? "gVisor" : esc(h.runtime)}` : "Not ready");
    $("hostStatus").className = `chip ${h.docker ? "chip-green" : "chip-red"} !text-sm !py-1`;
    $("hostStatus").textContent = h.docker ? "✓ Server healthy · sandbox engine running" : "⚠ Sandbox engine unreachable";
  } catch {
    $("health").textContent = "offline";
  }
}
refreshHealth();
setInterval(refreshHealth, 8000);

// =====================================================================================================
// VULTR SIDEBAR: live state fed by every event stream, plus /api/vultr for facts and server totals
// =====================================================================================================
const live = { inflight: new Map(), sandboxes: new Set(), busy: false, run: freshUsage(), recent: [] };
let vultrInfo = null;
let usageView = "run";

function freshUsage() {
  return { calls: 0, prompt: 0, completion: 0, cost: 0, byModel: {} };
}

function resetLive() {
  live.run = freshUsage();
  live.recent = [];
  live.inflight.clear();
  live.busy = true;
  scheduleSidebar();
}

function stopLive() {
  live.busy = false;
  live.inflight.clear();
  scheduleSidebar();
  refreshVultr();
  refreshHealth();
}

function trackLive(ev) {
  switch (ev.type) {
    case "llm_start":
      live.inflight.set(ev.call_id, ev);
      break;
    case "llm_call": {
      live.inflight.delete(ev.call_id);
      const u = live.run;
      u.calls += 1;
      u.prompt += ev.prompt_tokens;
      u.completion += ev.completion_tokens;
      u.cost += ev.cost_usd;
      const m = (u.byModel[ev.model] ||= { calls: 0, prompt_tokens: 0, completion_tokens: 0, cost_usd: 0, seconds: 0, roles: [] });
      m.calls += 1;
      m.prompt_tokens += ev.prompt_tokens;
      m.completion_tokens += ev.completion_tokens;
      m.cost_usd += ev.cost_usd;
      m.seconds += ev.seconds;
      if (!m.roles.includes(ev.role)) m.roles.push(ev.role);
      live.recent.unshift(ev);
      live.recent.length = Math.min(live.recent.length, 10);
      break;
    }
    case "sandbox_start":
      live.sandboxes.add(ev.sandbox_id);
      break;
    case "sandbox_end":
      live.sandboxes.delete(ev.sandbox_id);
      break;
    default:
      return;
  }
  scheduleSidebar();
}

function sidebarOpen() {
  return !$("vultr").classList.contains("translate-x-full");
}

function setSidebar(open) {
  $("vultr").classList.toggle("translate-x-full", !open);
  $("vultr").setAttribute("aria-hidden", String(!open));
  $("shell").classList.toggle("xl:mr-[420px]", open);
  localStorage.setItem("sidebar", open ? "1" : "0");
  if (open) refreshVultr();
}
$("vultrBtn").addEventListener("click", () => setSidebar(!sidebarOpen()));
$("vultrClose").addEventListener("click", () => setSidebar(false));
setSidebar(localStorage.getItem("sidebar") === "1");
setInterval(() => sidebarOpen() && refreshVultr(), 4000);

async function refreshVultr() {
  try {
    vultrInfo = await (await fetch("/api/vultr")).json();
    renderSidebar();
  } catch {
    /* keep the last known state */
  }
}
refreshVultr();

let sidebarQueued = false;
function scheduleSidebar() {
  if (sidebarQueued) return;
  sidebarQueued = true;
  requestAnimationFrame(() => {
    sidebarQueued = false;
    renderSidebar();
  });
}

function renderSidebar() {
  const active = live.inflight.size > 0 || live.sandboxes.size > 0;
  $("vultrDot").className = `w-2 h-2 rounded-full ${active ? "bg-emerald-300 pulse-dot" : "bg-white/60"}`;
  renderDiagram();
  renderCost();
  renderUsage();
  renderInfra();
}

function node(icon, title, sub, on, note = "") {
  return `<div class="node ${on ? "node-on" : ""}">
    <div class="flex items-center gap-2"><span class="text-lg">${icon}</span><span class="font-medium text-sm">${title}</span>
      ${on ? '<span class="ml-auto w-2 h-2 rounded-full bg-blue-500 pulse-dot"></span>' : ""}</div>
    <div class="text-xs muted mt-1">${sub}</div>${note ? `<div class="text-[11px] muted mt-1">${note}</div>` : ""}</div>`;
}

function conn(label, on) {
  return `<div class="py-1"><div class="conn ${on ? "conn-on" : ""}"></div><div class="text-[11px] text-center muted mt-1">${label}</div></div>`;
}

function renderDiagram() {
  const vm = vultrInfo?.vm || {};
  const sb = vultrInfo?.sandboxes || {};
  const nIn = live.inflight.size;
  const nSbx = live.sandboxes.size;
  const busy = live.busy || nIn > 0 || nSbx > 0;
  const roles = [...new Set([...live.inflight.values()].map((c) => c.role))];
  const where = [vm.region, vm.country].filter(Boolean).join(", ");
  const machine = [where, vm.plan, vm.cpus && `${vm.cpus} vCPU`, vm.memory_gb && `${vm.memory_gb} GB`].filter(Boolean).join(" · ");
  $("diagram").innerHTML =
    node("🌐", "Your browser", "This page. Progress streams in live over HTTPS.", busy) +
    conn("HTTPS · Caddy + Let's Encrypt", busy) +
    node("🖥️", "Vultr Cloud Compute VM", esc(machine || "loading…"), busy,
      "Runs the orchestrator: plans the work, sends it out, checks the results. Holds the only API key.") +
    `<div class="grid grid-cols-2 gap-3">
       <div>${conn("API calls", nIn > 0)}${node("🧠", "Vultr Serverless Inference",
         nIn ? `${plural(nIn, "model call")} running: ${esc(roles.join(", "))}` : "Idle. Called for every AI step.", nIn > 0)}</div>
       <div>${conn("Docker + gVisor", nSbx > 0)}${node("📦", "Sandboxes on the VM",
         `${nSbx} running · max ${sb.max_parallel ?? "–"} at once`, nSbx > 0, "No network, no secrets, destroyed after each use.")}</div>
     </div>`;
}

function renderCost() {
  const total = vultrInfo?.inference?.totals || {};
  const prices = vultrInfo?.inference?.prices_per_million_tokens || {};
  const u = live.run;
  $("costBox").innerHTML = `
    <div class="grid grid-cols-2 gap-3">
      <div class="node"><div class="text-xs muted">This run</div><div class="text-2xl font-semibold mt-0.5">${usd(u.cost)}</div>
        <div class="text-xs muted">${plural(u.calls, "model call")} · ${tok(u.prompt + u.completion)} tokens</div></div>
      <div class="node"><div class="text-xs muted">Since the server started</div><div class="text-2xl font-semibold mt-0.5">${usd(total.cost_usd)}</div>
        <div class="text-xs muted">${plural(total.calls || 0, "call")} · ${tok((total.prompt_tokens || 0) + (total.completion_tokens || 0))} tokens</div></div>
    </div>
    <div class="text-xs muted mt-3">Estimated from Vultr's published per-token prices (per 1M tokens):</div>
    <ul class="text-xs mt-1 space-y-0.5">${Object.entries(prices).map(([m, p]) =>
      `<li class="flex justify-between gap-2"><span class="font-mono truncate">${esc(m)}</span>
         <span class="muted whitespace-nowrap">${p ? `$${p.input} in · $${p.output} out` : "price n/a"}</span></li>`).join("")}</ul>`;
}

function callRow(c, running) {
  return `<li class="flex items-center gap-2 text-xs">
    <span class="chip ${running ? "chip-blue" : "chip-gray"} !text-[10px]">${running ? '<span class="w-1.5 h-1.5 rounded-full bg-blue-500 pulse-dot"></span>' : ""}${esc(c.role)}</span>
    <span class="font-mono truncate flex-1">${esc(c.model)}</span>
    ${running ? '<span class="muted">thinking…</span>'
      : `<span class="muted whitespace-nowrap">${tok(c.prompt_tokens)}→${tok(c.completion_tokens)} · ${secs(c.seconds)} · ${usd(c.cost_usd)}</span>`}</li>`;
}

function renderUsage() {
  const byModel = usageView === "run" ? live.run.byModel : vultrInfo?.inference?.totals?.by_model || {};
  const rows = Object.entries(byModel).map(([m, u]) => `
    <tr class="border-t border-slate-100 dark:border-slate-800 align-top">
      <td class="py-1.5 pr-2"><div class="font-mono text-xs break-all">${esc(m)}</div>
        <div class="flex flex-wrap gap-1 mt-1">${u.roles.map((r) => `<span class="chip chip-gray !text-[10px] !px-1.5">${esc(r)}</span>`).join("")}</div></td>
      <td class="text-right text-xs pt-1.5">${u.calls}</td>
      <td class="text-right text-xs pt-1.5">${tok(u.prompt_tokens + u.completion_tokens)}</td>
      <td class="text-right text-xs pt-1.5">${secs(u.seconds / Math.max(1, u.calls))}</td>
      <td class="text-right text-xs pt-1.5">${usd(u.cost_usd)}</td></tr>`).join("");
  const calls = [...live.inflight.values()].map((c) => callRow(c, true)).join("") + live.recent.slice(0, 8).map((c) => callRow(c, false)).join("");
  $("usageBox").innerHTML = `
    <div class="flex gap-1 mb-2 text-xs">${["run", "all"].map((v) =>
      `<button data-usage="${v}" class="px-2 py-1 rounded-md ${usageView === v ? "bg-slate-100 dark:bg-slate-800 font-medium" : "muted"}">${v === "run" ? "This run" : "All runs"}</button>`).join("")}</div>
    ${rows ? `<table class="w-full"><thead class="text-[11px] muted"><tr><th class="text-left font-normal">Model · used as</th>
      <th class="text-right font-normal">Calls</th><th class="text-right font-normal">Tokens</th><th class="text-right font-normal">Avg</th>
      <th class="text-right font-normal">Cost</th></tr></thead><tbody>${rows}</tbody></table>`
      : `<div class="text-sm muted">No model calls ${usageView === "run" ? "in this run yet" : "yet"}.</div>`}
    ${calls ? `<div class="text-[11px] muted mt-4 mb-1.5">Latest calls</div><ul class="space-y-1">${calls}</ul>` : ""}`;
}

function renderInfra() {
  const v = vultrInfo;
  if (!v) return ($("infraBox").innerHTML = '<div class="text-sm muted">Loading…</div>');
  const vm = v.vm, sb = v.sandboxes, inf = v.inference, L = sb.limits;
  const row = (k, val) => `<div class="flex justify-between gap-4 py-2 border-t border-slate-100 dark:border-slate-800"><dt class="muted whitespace-nowrap">${k}</dt><dd class="text-right">${val}</dd></div>`;
  const region = [vm.region, vm.country].filter(Boolean).join(", ") + (vm.region_code ? ` (${vm.region_code})` : "");
  $("infraBox").innerHTML = `<dl class="text-sm">
    ${row("Compute", `${esc(vm.product)}${vm.plan ? `<br><span class="font-mono text-xs">${esc(vm.plan)}</span>` : ""}`)}
    ${row("Region", esc(region || "–"))}
    ${row("Machine", esc([vm.cpus && `${vm.cpus} vCPU`, vm.memory_gb && `${vm.memory_gb} GB RAM`].filter(Boolean).join(" · ")) + `<br><span class="text-xs muted">${esc(vm.os || "")}</span>`)}
    ${row("Web", `${esc(v.web.server)}<br><span class="text-xs muted">HTTPS: ${esc(v.web.https)}</span>`)}
    ${row("AI models", `${esc(inf.product)}<br>${Object.entries(inf.models).filter(([, m]) => m).map(([r, m]) =>
      `<span class="text-xs muted">${esc(r.replace("_", " "))}:</span> <span class="font-mono text-xs">${esc(m)}</span>`).join("<br>")}`)}
    ${row("Sandboxes", `${esc(sb.engine)} + ${esc(sb.isolation)}<br><span class="font-mono text-xs">runtime: ${esc(sb.runtime)}</span>`)}
    ${row("Each sandbox", `${L.memory_mb} MB · ${esc(L.cpus)} CPU · ${L.processes} processes · ${L.timeout_s}s<br><span class="text-xs muted">no network · read-only · non-root · ${L.secrets} secrets</span>`)}
    ${row("Capacity", `up to ${sb.max_parallel} sandboxes and ${v.runs.max} runs at once`)}
    ${row("So far", `${sb.created} sandboxes created · ${sb.destroyed} destroyed`)}
    ${row("API key", "Only on the VM. Never inside a sandbox.")}
  </dl>`;
}

// =====================================================================================================
// ANALYSE: step 1 (data) and step 2 (question)
// =====================================================================================================
const DEMO_QUESTIONS = [
  "The heat pump was installed 1-14 March 2025 and the contractor guaranteed 40% lower electricity use. Was the guarantee met after adjusting for weather, and how much was saved in kWh and SEK?",
  "How much of the drop in electricity use is due to the milder winter rather than the heat pump?",
  "How does daily electricity use depend on outdoor temperature, before and after the retrofit?",
  "Are there days with missing or unusual meter readings?",
];
const GENERIC_QUESTIONS = [
  "Give me a short overview of this dataset.",
  "What are the most important trends in this data?",
  "Which columns have missing values, and how many?",
];
let dataset = null;
let run = null;

async function chooseDataset(resp, demo) {
  const d = await resp.json().catch(() => ({}));
  $("datasetInfo").classList.remove("hidden");
  if (!resp.ok) {
    $("datasetInfo").innerHTML = `<span class="chip chip-red">${esc(d.detail || "Upload failed")}</span>`;
    return;
  }
  dataset = { ...d, demo };
  $("pickDemo").classList.toggle("tile-on", demo);
  $("pickUpload").classList.toggle("tile-on", !demo);
  $("datasetInfo").innerHTML = `<span class="chip chip-green">✓ Ready</span><span class="font-mono">${esc(d.filename)}</span><span class="muted">· ${esc(d.size_kb)} KB</span>`;
  renderSuggestions();
  if (demo && !$("question").value.trim()) $("question").value = DEMO_QUESTIONS[0];
  updateRunButton();
}
$("pickDemo").addEventListener("click", async () => chooseDataset(await fetch("/api/datasets/demo", { method: "POST" }), true));
$("file").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const body = new FormData();
  body.append("file", f);
  chooseDataset(await fetch("/api/datasets", { method: "POST", body }), false);
});

function suggestionList() {
  return dataset && !dataset.demo ? GENERIC_QUESTIONS : DEMO_QUESTIONS;
}
function renderSuggestions() {
  $("suggestions").innerHTML = '<span class="text-xs muted self-center">Try:</span>' +
    suggestionList().map((q, i) => `<button class="suggest" data-suggest="${i}">${esc(q)}</button>`).join("");
}
renderSuggestions();
$("question").addEventListener("input", updateRunButton);

function updateRunButton() {
  const busy = run && !run.finished;
  $("run").disabled = Boolean(busy || !dataset || !$("question").value.trim());
  $("runHint").textContent = !dataset ? "Choose data first (step 1)." : busy ? "Working… follow it in step 3."
    : "Takes about a minute. You can watch every step.";
}

// =====================================================================================================
// ANALYSE: step 3 (watch it work)
// =====================================================================================================
const STEPS = [
  { key: "profile", title: "Read your data", idle: "Open the file inside a sandbox and learn its columns." },
  { key: "plan", title: "Plan the analysis", idle: "Split the question into small tasks that can run side by side." },
  { key: "execute", title: "Write and run the code", idle: "The AI writes Python. Each task runs in its own throwaway sandbox." },
  { key: "assure", title: "Double-check everything", idle: "Recompute each key number independently, re-run every task, review the charts." },
  { key: "write", title: "Write the answer", idle: "Using only facts that passed the checks." },
  { key: "seal", title: "Sign the receipt", idle: "Seal the whole run with a digital signature anyone can verify." },
];
const STEP_KEYS = STEPS.map((s) => s.key);
const TASK_CHIP = {
  waiting: ["chip-blue", "AI is writing code"], queued: ["chip-gray", "Queued: VM busy"], running: ["chip-blue", "Running in a sandbox"],
  fixing: ["chip-amber", "Fixing an error"], blocked: ["chip-amber", "Unsafe code blocked"], done: ["chip-green", "Done"],
  failed: ["chip-red", "Failed"],
};
let runStream = null;
let tick = null;

function newRun(question) {
  return { question, t0: Date.now(), endedAt: null, stage: null, times: {}, error: null, finished: false, profile: null,
    tasks: {}, order: [], sbx: {}, metrics: [], replays: {}, reviews: {}, healed: 0, blocked: 0, assure: null,
    report: null, finalMetrics: null, sealed: null, cost: 0, calls: 0 };
}

$("run").addEventListener("click", startRun);

function startRun() {
  const q = $("question").value.trim();
  if (!dataset || !q || (run && !run.finished)) return;
  run = newRun(q);
  resetLive();
  $("step3").classList.remove("hidden");
  $("step4").classList.add("hidden");
  $("log").innerHTML = "";
  renderSteps();
  updateRunButton();
  $("step3").scrollIntoView({ behavior: "smooth", block: "start" });
  clearInterval(tick);
  tick = setInterval(renderClock, 500);
  let got = false;
  runStream = new EventSource(`/api/runs?dataset_id=${encodeURIComponent(dataset.dataset_id)}&question=${encodeURIComponent(q)}`);
  runStream.onmessage = (m) => {
    got = true;
    onRunEvent(JSON.parse(m.data));
  };
  runStream.onerror = () => {
    runStream.close();
    if (!run.finished) finishRun(got ? "The connection to the server was lost. Please try again." : BUSY);
  };
}

function log(text) {
  $("log").insertAdjacentHTML("beforeend", `<li>${esc(new Date().toLocaleTimeString())}  ${esc(text)}</li>`);
}

function enterStage(key, ev) {
  const now = Date.now();
  if (run.stage && run.times[run.stage]) run.times[run.stage].end = now;
  run.stage = key;
  run.times[key] = { start: now };
  if (key === "assure") run.assure = { numbers: ev.numbers ?? 0, replays: ev.replays ?? 0, charts: ev.charts ?? 0 };
}

function onRunEvent(ev) {
  trackLive(ev);
  const t = ev.task_id;
  const task = run.tasks[t];
  switch (ev.type) {
    case "stage":
      enterStage(ev.stage, ev);
      log(`${ev.stage}: ${ev.message}`);
      break;
    case "profile":
      run.profile = ev;
      log(`read ${ev.rows} rows x ${ev.columns.length} columns in ${ev.sandbox_id}`);
      break;
    case "plan":
      ev.tasks.forEach((x) => {
        run.tasks[x.id] = { id: x.id, title: x.title, status: "waiting", notes: [] };
        run.order.push(x.id);
        log(`task ${x.id}: ${x.title}`);
      });
      break;
    case "sandbox_queued":
      if (ev.label === "task" && task) task.status = "queued";
      break;
    case "sandbox_start":
      run.sbx[ev.sandbox_id] = { label: ev.label, task: t, mem: 0, limit: ev.limits?.memory_mb || 512, done: null };
      if (ev.label === "task" && task) Object.assign(task, { status: "running", live: ev.sandbox_id });
      break;
    case "sandbox_sample":
      if (run.sbx[ev.sandbox_id]) run.sbx[ev.sandbox_id].mem = ev.mem_mb;
      break;
    case "sandbox_end":
      if (run.sbx[ev.sandbox_id]) run.sbx[ev.sandbox_id].done = ev.status;
      if (task && task.live === ev.sandbox_id) task.live = null;
      log(`${ev.label} ${ev.sandbox_id}: ${ev.status} in ${ev.duration_s}s, peak ${ev.peak_mem_mb} MB, destroyed`);
      break;
    case "policy_block":
      if (ev.label === "task") run.blocked += 1;
      if (ev.label === "task" && task) {
        task.status = "blocked";
        task.notes.push(`🛡️ Unsafe or broken code was stopped before it ran: ${ev.violations[0] || ""}`);
      }
      log(`safety check blocked ${ev.label} ${t}: ${ev.violations.join("; ")}`);
      break;
    case "attempt":
      if (ev.label === "task" && task && !ev.ok && ev.status !== "policy_blocked") {
        task.status = "fixing";
        task.notes.push(`❌ Attempt ${ev.n} failed: ${lastLine(ev.stderr) || (ev.status === "ok" ? "no result line printed" : ev.status)}`);
      }
      log(`${ev.label} ${t} attempt ${ev.n}: ${ev.ok ? "ok" : ev.status}`);
      break;
    case "healing":
      if (ev.label === "task") {
        run.healed += 1;
        if (task) {
          task.status = "fixing";
          task.notes.push(`🔧 The AI read the error and rewrote its code (attempt ${ev.attempt})`);
        }
      }
      break;
    case "task_done":
      if (task) Object.assign(task, ev, { status: ev.ok ? "done" : "failed", live: null });
      break;
    case "verify_mismatch":
      log(`check disagreed on ${ev.name}: ${ev.claimed} vs ${ev.recomputed}; running a tie-breaker`);
      break;
    case "verify_result":
      run.metrics.push(ev);
      break;
    case "replay_result":
      run.replays[t] = ev;
      break;
    case "chart_review":
      run.reviews[t] = ev;
      break;
    case "report":
      run.report = ev.report;
      run.finalMetrics = ev.metrics;
      break;
    case "sealed":
      run.sealed = ev;
      break;
    case "error":
      run.error = ev;
      log(`ERROR ${ev.message}: ${ev.detail || ""}`);
      break;
    case "done":
      runStream.close();
      finishRun();
      return;
    default:
      break;
  }
  scheduleSteps();
}

function finishRun(message) {
  run.finished = true;
  run.endedAt = Date.now();
  if (run.stage && run.times[run.stage] && !run.times[run.stage].end) run.times[run.stage].end = run.endedAt;
  if (message && !run.error) run.error = { message };
  run.cost = live.run.cost;
  run.calls = live.run.calls;
  clearInterval(tick);
  stopLive();
  renderSteps();
  updateRunButton();
  if (run.report || run.sealed) {
    renderAnswer();
    $("step4").classList.remove("hidden");
    setTimeout(() => $("step4").scrollIntoView({ behavior: "smooth", block: "start" }), 200);
  }
}

function stepState(key) {
  const idx = STEP_KEYS.indexOf(key);
  const cur = STEP_KEYS.indexOf(run.stage);
  if (idx < cur) return "done";
  if (idx > cur) return "pending";
  if (run.error) return "error";
  return run.finished ? "done" : "active";
}

let stepsQueued = false;
function scheduleSteps() {
  if (stepsQueued) return;
  stepsQueued = true;
  requestAnimationFrame(() => {
    stepsQueued = false;
    renderSteps();
  });
}

function renderClock() {
  if (!run) return;
  $("elapsed").textContent = `${(((run.endedAt || Date.now()) - run.t0) / 1000).toFixed(0)}s`;
}

function renderSteps() {
  if (!run) return;
  $("stepper").innerHTML = STEPS.map(stepItem).join("");
  const all = Object.values(run.sbx);
  const destroyed = all.filter((x) => x.done).length;
  $("fleetLine").textContent = `📦 ${plural(all.length, "sandbox")} started · ${destroyed} destroyed · ${all.length - destroyed} running`.replace("sandboxs", "sandboxes");
  const current = STEPS.find((s) => s.key === run.stage);
  $("progressLine").innerHTML = run.error
    ? `<span class="text-red-600 dark:text-red-400">${esc(run.error.message)}${run.error.detail ? `: ${esc(String(run.error.detail).slice(0, 200))}` : ""}</span>`
    : run.finished ? "Finished. The answer is below." : `Now: ${esc(current ? current.title.toLowerCase() : "starting")}…`;
  renderClock();
}

function stepItem(step, i) {
  const st = stepState(step.key);
  const tm = run.times[step.key];
  const dur = tm ? `${(((tm.end || Date.now()) - tm.start) / 1000).toFixed(1)}s` : "";
  const dot = {
    pending: `<span class="step-dot border-2 border-slate-200 dark:border-slate-700 text-slate-400">${i + 1}</span>`,
    active: '<span class="step-dot bg-blue-600"><span class="w-4 h-4 rounded-full border-2 border-white/40 border-t-white spin"></span></span>',
    done: '<span class="step-dot bg-emerald-500 text-white">✓</span>',
    error: '<span class="step-dot bg-red-500 text-white">!</span>',
  }[st];
  const line = i < STEPS.length - 1
    ? `<span class="w-px flex-1 my-1 ${st === "done" ? "bg-emerald-300 dark:bg-emerald-800" : "bg-slate-200 dark:bg-slate-800"}"></span>` : "";
  return `<li class="flex gap-4">
    <div class="flex flex-col items-center">${dot}${line}</div>
    <div class="flex-1 min-w-0 pb-5">
      <div class="flex items-baseline justify-between gap-3">
        <span class="font-medium ${st === "pending" ? "muted" : ""}">${step.title}</span><span class="text-xs font-mono muted">${dur}</span></div>
      <div class="text-sm muted">${st === "pending" ? step.idle : stepDetail(step.key)}</div>
      ${st === "pending" ? "" : stepExtra(step.key)}
    </div></li>`;
}

function stepDetail(key) {
  const tasks = run.order.map((id) => run.tasks[id]);
  switch (key) {
    case "profile":
      return run.profile
        ? `Read ${run.profile.rows.toLocaleString()} rows × ${run.profile.columns.length} columns inside a sandbox. Only the column names and 5 sample rows are shown to the AI.`
        : "Opening the file inside a sandbox…";
    case "plan":
      return tasks.length ? `Split your question into ${plural(tasks.length, "independent task")}.` : "Working out how to answer…";
    case "execute": {
      const done = tasks.filter((x) => x.status === "done").length;
      return `${done} of ${tasks.length} tasks finished` +
        (run.healed ? ` · fixed itself ${plural(run.healed, "time")} after an error` : "") +
        (run.blocked ? ` · ${plural(run.blocked, "unsafe or broken attempt")} stopped before running` : "") + ".";
    }
    case "assure": {
      const a = run.assure || {};
      return `${run.metrics.length} of ${a.numbers ?? "?"} numbers re-computed independently · ` +
        `${Object.keys(run.replays).length} of ${a.replays ?? "?"} tasks re-run · ${Object.keys(run.reviews).length} of ${a.charts ?? "?"} charts reviewed.`;
    }
    case "write":
      return run.report ? "Answer written from checked facts only." : "Writing the answer from checked facts only…";
    case "seal":
      return run.sealed ? `Signed with key ${esc(run.sealed.key_fingerprint)}. Change one character and the signature breaks.` : "Signing…";
    default:
      return "";
  }
}

function memBar(sbx, id) {
  const pct = Math.min(100, (sbx.mem / (sbx.limit || 512)) * 100);
  const color = pct > 85 ? "bg-red-500" : pct > 60 ? "bg-amber-500" : "bg-blue-500";
  return `<div class="mt-2"><div class="h-1.5 rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden">
      <div class="h-full ${color}" style="width:${pct}%"></div></div>
    <div class="text-[11px] muted mt-0.5 font-mono">${esc(id)} · memory ${sbx.mem} / ${sbx.limit} MB</div></div>`;
}

function taskRow(k) {
  const [cls, label] = TASK_CHIP[k.status] || TASK_CHIP.waiting;
  const sbx = k.live && run.sbx[k.live];
  return `<div class="rounded-xl border border-slate-200 dark:border-slate-800 px-3 py-2">
    <div class="flex items-center justify-between gap-2">
      <span class="text-sm font-medium truncate">${k.id}. ${esc(k.title)}</span>
      <span class="chip ${cls}">${k.status === "running" ? '<span class="w-1.5 h-1.5 rounded-full bg-blue-500 pulse-dot"></span>' : ""}${label}</span></div>
    ${sbx ? memBar(sbx, k.live) : ""}
    ${k.notes.slice(-3).map((n) => `<div class="text-xs muted mt-1">${esc(n)}</div>`).join("")}</div>`;
}

function stepExtra(key) {
  if (key === "execute" && run.order.length) {
    return `<div class="mt-2 space-y-2">${run.order.map((id) => taskRow(run.tasks[id])).join("")}</div>`;
  }
  if (key === "assure") {
    const chips = [
      ...run.metrics.map((m) => `<span class="chip ${STATUS_CHIP[m.status]}" title="${esc(m.description)}">${STATUS_WORD[m.status]}: ${esc(m.name)}</span>`),
      ...Object.values(run.replays).map((r) => `<span class="chip ${r.reproducible ? "chip-green" : "chip-red"}">🔁 Task ${r.task_id} ${r.reproducible ? "re-ran identically" : "re-ran differently"}</span>`),
      ...Object.values(run.reviews).map((r) => `<span class="chip ${r.verdict === "good" ? "chip-green" : "chip-amber"}">👁 Chart ${r.task_id} ${r.verdict === "good" ? "looks right" : "has issues"}</span>`),
    ];
    return chips.length ? `<div class="mt-2 flex flex-wrap gap-1.5">${chips.join("")}</div>` : "";
  }
  return "";
}

// =====================================================================================================
// ANALYSE: step 4 (the answer)
// =====================================================================================================
function badge(tone, text, title = "") {
  return `<span class="chip ${tone} !text-sm !px-3 !py-1" title="${esc(title)}">${text}</span>`;
}

function renderAnswer() {
  const r = run.report || {};
  const metrics = run.finalMetrics || run.metrics;
  const total = metrics.length;
  const confirmed = metrics.filter((m) => m.status !== "unverified").length;
  const corrected = metrics.filter((m) => m.status === "corrected").length;
  const reps = Object.values(run.replays);
  const repOk = reps.filter((x) => x.reproducible).length;
  const revs = Object.values(run.reviews);
  const revOk = revs.filter((x) => x.verdict === "good").length;
  const nSbx = Object.keys(run.sbx).length;

  $("badges").innerHTML = [
    total ? badge(confirmed === total ? "chip-green" : "chip-amber", `✓ ${confirmed}/${total} key numbers independently confirmed${corrected ? ` (${corrected} corrected)` : ""}`,
      "A second AI, which never saw the original code, recomputed each number in a fresh sandbox.") : "",
    reps.length ? badge(repOk === reps.length ? "chip-green" : "chip-amber", `🔁 ${repOk}/${reps.length} results reproduced exactly`,
      "Each task was run again in a brand-new sandbox. The output had to be identical.") : "",
    revs.length ? badge(revOk === revs.length ? "chip-green" : "chip-amber", `👁 ${revOk}/${revs.length} charts passed review`,
      "A vision model looked at each chart for mistakes or misleading axes.") : "",
    badge("chip-gray", `📦 ${nSbx} sandboxes used, all destroyed`),
    run.cost ? badge("chip-blue", `⚡ ${usd(run.cost)} of AI on Vultr`, `${run.calls} calls to Vultr Serverless Inference`) : "",
    run.sealed ? badge("chip-gray", "🔏 Signed receipt") : "",
  ].join("");

  $("answer").innerHTML = (r.summary || []).map((s, i) => {
    const chips = (s.metrics || []).map((name) => {
      const m = metrics.find((x) => x.name === name);
      return m ? `<span class="chip ${STATUS_CHIP[m.status]} !whitespace-normal" title="${esc(STATUS_WORD[m.status])}: ${esc(m.description)}">${STATUS_ICON[m.status]} ${esc(humanize(m.name))} = ${num(m.value ?? m.claimed)}</span>` : "";
    }).join("");
    return `<li class="rounded-xl border border-slate-200 dark:border-slate-800 p-4 flex gap-3 items-start">
      <span class="grid place-items-center w-6 h-6 rounded-full bg-blue-50 dark:bg-blue-950 text-blue-700 dark:text-blue-300 text-xs font-semibold flex-none">${i + 1}</span>
      <div class="flex-1"><p class="leading-relaxed">${esc(s.text)}</p>
        <div class="flex flex-wrap items-center gap-2 mt-2">${chips}
          <button class="text-xs text-blue-600 dark:text-blue-400 hover:underline" data-proof="${i}">See the proof →</button></div></div></li>`;
  }).join("") || `<li class="muted">${run.error ? "The run did not finish." : "No answer was produced."}</li>`;

  $("charts").innerHTML = run.order.map((id) => run.tasks[id]).filter((k) => k.chart_b64).map((k) => {
    const rv = run.reviews[k.id];
    const review = rv ? `<div class="mt-2 text-xs rounded-lg px-2 py-1 ${rv.verdict === "good" ? "bg-emerald-50 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300" : "bg-amber-50 text-amber-900 dark:bg-amber-950/50 dark:text-amber-200"}">
        👁 Vision check: ${rv.verdict === "good" ? "looks right" : esc((rv.issues || []).join("; "))}</div>` : "";
    return `<figure class="rounded-xl border border-slate-200 dark:border-slate-800 overflow-hidden">
      <button class="block w-full bg-white" data-task-proof="${k.id}" title="How this chart was made"><img src="data:image/png;base64,${k.chart_b64}" alt="${esc(k.title)}" class="w-full" /></button>
      <figcaption class="p-3 text-sm"><div class="font-medium">${esc(k.title)}</div>
        <div class="text-xs muted mt-0.5">Drawn by code that ran in sandbox <span class="font-mono">${esc(k.sandbox_id)}</span></div>${review}</figcaption></figure>`;
  }).join("");

  $("assumptions").innerHTML = (r.assumptions || []).map((a) => `<li>${esc(a)}</li>`).join("");
  $("caveats").innerHTML = (r.caveats || []).map((a) => `<li>${esc(a)}</li>`).join("");
  $("caveatsTitle").classList.toggle("hidden", !(r.caveats || []).length);
  $("assumptionsBox").classList.toggle("hidden", !(r.assumptions || []).length && !(r.caveats || []).length);

  $("work").innerHTML = workHtml(metrics);

  $("receiptBtn").classList.toggle("hidden", !run.sealed);
  $("sealLine").classList.toggle("hidden", !run.sealed);
  if (run.sealed) {
    $("receiptBtn").href = run.sealed.download;
    $("sealLine").textContent = `🔏 Signed with Ed25519 · key ${run.sealed.key_fingerprint} · signature ${run.sealed.signature} · run ${run.sealed.run_id}`;
  }
}

function workHtml(metrics) {
  const table = metrics.length ? `<div><h4 class="font-medium">Every key number, re-computed independently</h4>
    <p class="muted text-xs mt-1 mb-2">A second AI wrote its own code from each number's definition only (it never saw the original code)
      and ran it in a fresh sandbox. When the two disagreed, a third run broke the tie.</p>
    <div class="overflow-x-auto"><table class="w-full text-sm"><thead class="text-xs muted text-left"><tr>
      <th class="py-1 font-normal">Number</th><th class="font-normal text-right">Reported</th><th class="font-normal text-right">Re-computed</th><th class="font-normal text-right">Result</th></tr></thead>
      <tbody>${metrics.map((m) => `<tr class="border-t border-slate-100 dark:border-slate-800 align-top">
        <td class="py-2 pr-3"><div class="font-mono text-xs">${esc(m.name)}</div><div class="text-xs muted">${esc(m.description)}</div></td>
        <td class="text-right font-mono pt-2">${num(m.claimed)}</td>
        <td class="text-right font-mono pt-2">${num(m.recomputed)}${m.tiebreak != null ? `<div class="text-xs muted">tie-break ${num(m.tiebreak)}</div>` : ""}</td>
        <td class="text-right pt-2"><span class="chip ${STATUS_CHIP[m.status]}">${STATUS_WORD[m.status]}</span></td></tr>`).join("")}</tbody></table></div></div>` : "";

  const reps = Object.values(run.replays);
  const replay = reps.length ? `<div><h4 class="font-medium mb-2">Reproducibility</h4><ul class="space-y-1 text-xs">${reps.map((r) =>
    `<li class="flex flex-wrap justify-between gap-2"><span>Task ${r.task_id} re-run in a fresh sandbox <span class="font-mono muted">${esc(r.sandbox_id)}</span></span>
      <span class="${r.reproducible ? "text-emerald-600 dark:text-emerald-400" : "text-red-600"}">${r.reproducible ? "✓ identical output" : "✗ output differs"}
      <span class="font-mono muted">${esc(String(r.replay_sha256).slice(0, 12))}</span></span></li>`).join("")}</ul></div>` : "";

  const code = `<div><h4 class="font-medium mb-2">The code the AI wrote, and what it printed</h4><div class="space-y-2">${run.order.map((id) => {
    const k = run.tasks[id];
    const tries = Array.isArray(k.attempts) ? k.attempts.length : 0;
    return `<details class="rounded-lg border border-slate-200 dark:border-slate-800 p-3"><summary class="cursor-pointer text-sm">${k.id}. ${esc(k.title)}
      <span class="muted text-xs">· ${plural(tries, "attempt")} · ${esc(k.sandbox_id || "")}</span></summary>
      <pre class="code mt-2">${esc(k.code || "")}</pre><div class="text-xs muted mt-2">Output</div><pre class="code mt-1">${esc(k.stdout || "")}</pre></details>`;
  }).join("")}</div></div>`;

  const verify = run.sealed ? `<div><h4 class="font-medium">Check the receipt yourself</h4>
    <p class="muted text-xs mt-1 mb-2">The receipt holds the question, a fingerprint of the data, all the code, a hash of every output and every check,
      signed with the server's key. With the project's code:</p>
    <pre class="code">curl -o public-key.pem ${esc(location.origin)}/api/receipts/public-key
python -m scripts.verify_receipt receipt-${esc(run.sealed.run_id)}.json public-key.pem</pre></div>` : "";
  return table + replay + code + verify;
}

// ---------- proof modal ----------
function metricProof(m) {
  return `<div class="rounded-xl border border-violet-200 dark:border-violet-900 p-4 space-y-1.5">
    <div class="flex flex-wrap items-center gap-2"><span class="font-medium">Check of <span class="font-mono">${esc(m.name)}</span></span>
      <span class="chip ${STATUS_CHIP[m.status]}">${STATUS_WORD[m.status]}</span></div>
    <div class="text-xs muted">${esc(m.description)}</div>
    <div>Reported <b class="font-mono">${num(m.claimed)}</b> · re-computed <b class="font-mono">${num(m.recomputed)}</b> in sandbox
      <span class="font-mono">${esc(m.verifier_sandbox)}</span>${m.tiebreak != null ? ` · tie-break ${num(m.tiebreak)} in <span class="font-mono">${esc(m.tiebreak_sandbox)}</span>` : ""}</div>
    <details><summary class="cursor-pointer text-sm text-blue-600 dark:text-blue-400">The checker's own code</summary><pre class="code mt-1">${esc(m.verifier_code)}</pre></details></div>`;
}

function taskProof(k) {
  if (!k) return "";
  const rp = run.replays[k.id];
  const rv = run.reviews[k.id];
  const tries = Array.isArray(k.attempts) ? k.attempts : [];
  return `<div class="rounded-xl border border-slate-200 dark:border-slate-800 p-4 space-y-2">
    <div class="font-medium">Task ${k.id}: ${esc(k.title)}</div>
    <ul class="text-xs muted space-y-0.5">${tries.map((a) => `<li>${a.ok ? "✅" : a.status === "policy_blocked" ? "🛡️" : "❌"} Attempt ${a.n} ·
      ${a.sandbox_id ? `sandbox <span class="font-mono">${esc(a.sandbox_id)}</span>` : "stopped before running"} · ${a.duration_s ?? 0}s${a.peak_mem_mb ? ` · peak ${a.peak_mem_mb} MB` : ""}</li>`).join("")}</ul>
    ${rp ? `<div class="text-xs">${rp.reproducible ? "🔁 Re-ran identically" : "≠ Re-ran differently"} in <span class="font-mono">${esc(rp.sandbox_id)}</span></div>` : ""}
    ${rv ? `<div class="text-xs">👁 Chart review: ${esc(rv.verdict)}. ${esc(rv.shows)} ${(rv.issues || []).length ? esc(rv.issues.join("; ")) : ""}</div>` : ""}
    <details open><summary class="cursor-pointer text-sm text-blue-600 dark:text-blue-400">The code</summary><pre class="code mt-1">${esc(k.code)}</pre></details>
    <details><summary class="cursor-pointer text-sm text-blue-600 dark:text-blue-400">What it printed</summary><pre class="code mt-1">${esc(k.stdout)}</pre></details></div>`;
}

function showModal(title, body) {
  $("modalTitle").textContent = title;
  $("modalBody").innerHTML = body;
  $("modal").classList.replace("hidden", "flex");
}
function closeModal() {
  $("modal").classList.replace("flex", "hidden");
}

function openProof(i) {
  const s = run?.report?.summary?.[i];
  if (!s) return;
  const metrics = (s.metrics || []).map((n) => (run.finalMetrics || run.metrics).find((m) => m.name === n)).filter(Boolean);
  showModal("The proof behind this statement",
    `<blockquote class="border-l-4 border-blue-500 pl-3">${esc(s.text)}</blockquote>${metrics.map(metricProof).join("")}${(s.task_ids || []).map((id) => taskProof(run.tasks[id])).join("")}`);
}

// ---------- one click handler for everything rendered dynamically ----------
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-proof],[data-task-proof],[data-suggest],[data-usage],[data-open-vultr],[data-attack]");
  if (el?.dataset.proof !== undefined) return openProof(+el.dataset.proof);
  if (el?.dataset.taskProof !== undefined) return showModal("How this chart was made", taskProof(run?.tasks[+el.dataset.taskProof]));
  if (el?.dataset.suggest !== undefined) {
    $("question").value = suggestionList()[+el.dataset.suggest];
    return updateRunButton();
  }
  if (el?.dataset.usage !== undefined) {
    usageView = el.dataset.usage;
    return renderUsage();
  }
  if (el?.dataset.openVultr !== undefined) return setSidebar(true);
  if (el?.dataset.attack !== undefined) return launchAttack(el.dataset.attack);
  if (e.target.id === "modal" || e.target.closest("#modalClose")) closeModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
});

// =====================================================================================================
// TRY TO BREAK IT
// =====================================================================================================
const ATTACK_ICON = { rm_rf: "🗑️", infinite_loop: "♾️", memory_bomb: "💣", fork_bomb: "🍴", exfiltrate: "📡",
  steal_secrets: "🔑", persistence: "🪝", hostile_csv: "🧪" };
const END_WORD = { ok: "finished", error: "crashed", timeout: "killed at the time limit", oom: "killed at the memory limit" };

async function loadAttacks() {
  const list = await (await fetch("/api/attacks")).json();
  $("attacks").innerHTML = list.map((a) => `
    <div class="card p-4 flex flex-col gap-3" id="atk-${a.id}">
      <div class="flex items-start justify-between gap-2"><h3 class="font-semibold">${ATTACK_ICON[a.id] || "⚔️"} ${esc(a.title)}</h3>
        <span class="verdict chip chip-gray">Ready</span></div>
      <p class="text-sm"><span class="muted">Tries to:</span> ${esc(a.tries)}</p>
      <div class="lanes space-y-2"></div>
      <div class="progress text-xs muted space-y-1"></div>
      <div class="result hidden"></div>
      <details class="out hidden"><summary class="text-xs muted cursor-pointer">What the sandbox printed</summary><div class="outBody space-y-2 mt-2"></div></details>
      <button class="launch btn btn-ghost mt-auto" data-attack="${a.id}">Launch attack</button>
    </div>`).join("");
}
loadAttacks();

function laneHtml(id, x) {
  const running = ["starting", "queued", "running"].includes(x.status);
  const mem = running ? x.mem : x.peak || 0;
  const pct = Math.min(100, (mem / (x.limit || 512)) * 100);
  const color = !running ? (x.status === "oom" ? "bg-red-500" : "bg-slate-400") : pct > 85 ? "bg-red-500" : pct > 60 ? "bg-amber-500" : "bg-blue-500";
  const text = running ? `${x.status === "queued" ? "queued" : "running"} · memory ${mem} / ${x.limit} MB`
    : `${END_WORD[x.status] || x.status} after ${x.duration}s · peak ${x.peak} MB · destroyed`;
  return `<div><div class="text-[11px] font-mono muted">${esc(x.label)} · ${esc(id)}</div>
    <div class="h-1.5 rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden mt-0.5"><div class="h-full ${color}" style="width:${pct}%"></div></div>
    <div class="text-[11px] muted mt-0.5">${esc(text)}</div></div>`;
}

function laneEvent(container, lanes, ev) {
  const x = (lanes[ev.sandbox_id] ||= { mem: 0, peak: 0, limit: 512, status: "starting", label: ev.label || "sandbox" });
  if (ev.type === "sandbox_queued") x.status = "queued";
  if (ev.type === "sandbox_start") Object.assign(x, { status: "running", limit: ev.limits?.memory_mb || 512 });
  if (ev.type === "sandbox_sample") Object.assign(x, { mem: ev.mem_mb, peak: Math.max(x.peak, ev.mem_mb) });
  if (ev.type === "sandbox_end") Object.assign(x, { status: ev.status, duration: ev.duration_s, peak: ev.peak_mem_mb });
  container.innerHTML = Object.entries(lanes).slice(-6).map(([id, v]) => laneHtml(id, v)).join("");
}

function launchAttack(id) {
  const card = $(`atk-${id}`);
  const q = (sel) => card.querySelector(sel);
  const btn = q(".launch");
  const verdict = q(".verdict");
  btn.disabled = true;
  ["lanes", "progress", "outBody"].forEach((c) => (q(`.${c}`).innerHTML = ""));
  q(".result").classList.add("hidden");
  q(".out").classList.add("hidden");
  verdict.className = "verdict chip chip-blue";
  resetLive();
  const t0 = Date.now();
  const timer = setInterval(() => (verdict.textContent = `Attacking… ${((Date.now() - t0) / 1000).toFixed(0)}s`), 500);
  verdict.textContent = "Attacking…";
  const lanes = {};
  let got = false;
  const es = new EventSource(`/api/attacks/${id}/run`);
  const end = () => {
    clearInterval(timer);
    es.close();
    btn.disabled = false;
    stopLive();
  };
  es.onmessage = (m) => {
    got = true;
    const ev = JSON.parse(m.data);
    trackLive(ev);
    if (ev.type.startsWith("sandbox_")) return laneEvent(q(".lanes"), lanes, ev);
    if (ev.type === "attack_run") {
      q(".out").classList.remove("hidden");
      q(".outBody").insertAdjacentHTML("beforeend", `${ev.step ? `<div class="text-xs muted">${esc(ev.step)}</div>` : ""}<pre class="code">${esc(`${ev.stdout || ""}\n${ev.stderr || ""}`.trim())}</pre>`);
    } else if (ev.type === "stage") {
      q(".progress").insertAdjacentHTML("beforeend", `<div>→ ${esc(ev.message)}</div>`);
    } else if (ev.type === "policy_block") {
      q(".progress").insertAdjacentHTML("beforeend", `<div>🛡️ The safety check blocked code the AI wrote: ${esc(ev.violations.join("; "))}</div>`);
    } else if (ev.type === "report") {
      const said = (ev.report.summary || []).map((x) => esc(x.text)).join(" ");
      const warned = (ev.report.caveats || []).map(esc).join(" ");
      q(".progress").insertAdjacentHTML("beforeend", `<div class="mt-2 rounded-lg bg-slate-50 dark:bg-slate-950 p-2 text-slate-700 dark:text-slate-300">
        <b>The agent answered:</b> ${said}${warned ? `<br><b>It warned:</b> ${warned}` : ""}</div>`);
    } else if (ev.type === "attack_result") {
      clearInterval(timer);
      verdict.className = `verdict chip ${ev.contained ? "chip-green" : "chip-red"}`;
      verdict.textContent = ev.contained ? "Contained ✓" : "Check ⚠";
      const r = q(".result");
      r.className = `result rounded-xl p-3 text-sm ${ev.contained ? "bg-emerald-50 text-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200" : "bg-red-50 text-red-900 dark:bg-red-950/40 dark:text-red-200"}`;
      r.innerHTML = `<div class="font-medium">${ev.contained ? "Contained." : "Needs a look."}</div><div class="mt-0.5">${esc(ev.stopped_by)}</div>
        <div class="text-xs mt-1.5 opacity-80">Checked from outside: server file unchanged ${ev.host_canary_intact ? "✓" : "✗"} · sandbox engine healthy ${ev.host_healthy ? "✓" : "✗"}</div>`;
    } else if (ev.type === "error") {
      q(".progress").insertAdjacentHTML("beforeend", `<div class="text-red-600">Error: ${esc(ev.message)}</div>`);
    } else if (ev.type === "done") {
      if (verdict.textContent.startsWith("Attacking")) {
        verdict.className = "verdict chip chip-gray";
        verdict.textContent = "Finished";
      }
      end();
    }
  };
  es.onerror = () => {
    end();
    if (!got) {
      verdict.className = "verdict chip chip-amber";
      verdict.textContent = "Busy";
      q(".progress").innerHTML = `<div class="text-amber-700 dark:text-amber-300">${esc(BUSY)}</div>`;
    }
  };
}

// ---------- the AI hacker (red team) ----------
$("redteam").addEventListener("click", () => {
  const box = $("redteamOut");
  box.innerHTML = "";
  $("redteamSummary").innerHTML = '<span class="muted">The AI is writing its attacks…</span>';
  $("redteam").disabled = true;
  resetLive();
  const lanes = {};
  const card = (g) => {
    let c = document.getElementById(`rt-${g}`);
    if (!c) {
      c = document.createElement("div");
      c.id = `rt-${g}`;
      c.className = "rounded-xl border border-slate-200 dark:border-slate-800 p-3 space-y-2";
      c.innerHTML = '<div class="goal font-medium"></div><div class="state text-xs muted">Writing the attack…</div><div class="lanes space-y-2"></div><div class="body space-y-2"></div>';
      box.appendChild(c);
      lanes[g] = {};
    }
    return c;
  };
  let got = false;
  const es = new EventSource("/api/redteam/run");
  const end = () => {
    es.close();
    $("redteam").disabled = false;
    stopLive();
  };
  es.onmessage = (m) => {
    got = true;
    const ev = JSON.parse(m.data);
    trackLive(ev);
    if (ev.type.startsWith("sandbox_")) {
      if (ev.label === "red-team") {
        const c = card(ev.task_id);
        c.querySelector(".state").textContent = "Running its attack in a sandbox…";
        laneEvent(c.querySelector(".lanes"), lanes[ev.task_id], ev);
      }
      return;
    }
    if (ev.type === "redteam_attack") {
      const c = card(ev.goal_id);
      c.querySelector(".goal").textContent = `🎯 ${ev.goal}`;
      c.querySelector(".state").textContent = ev.declined ? (ev.no_reply ? "The model returned no attack." : "The model refused to write this attack.") : "Attack written.";
      if (!ev.declined) {
        c.querySelector(".body").innerHTML = `<details><summary class="cursor-pointer text-xs text-red-600 dark:text-red-400">The attack code the AI wrote</summary><pre class="code mt-1">${esc(ev.code)}</pre></details>`;
      }
    } else if (ev.type === "redteam_result") {
      const c = card(ev.goal_id);
      c.querySelector(".state").innerHTML = `<span class="chip chip-green">Contained ✓</span> <span class="muted">sandbox ${esc(END_WORD[ev.status] || ev.status)}</span>`;
      c.querySelector(".body").insertAdjacentHTML("beforeend", `<div class="text-xs">${ev.claimed_success
        ? `🤖 The AI claims: “${esc(ev.evidence)}”. <span class="muted">Anything it reached was inside its own throwaway sandbox.</span>`
        : "🤖 The AI admits the attack failed."}</div>
        <details><summary class="cursor-pointer text-xs muted">What the sandbox printed</summary><pre class="code mt-1">${esc((ev.stdout || ev.stderr || "").trim().slice(-1500))}</pre></details>`);
    } else if (ev.type === "redteam_summary") {
      const ok = ev.contained;
      $("redteamSummary").innerHTML = `<div class="rounded-xl p-4 ${ok ? "bg-emerald-50 text-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200" : "bg-red-50 text-red-900 dark:bg-red-950/40 dark:text-red-200"}">
        <div class="font-semibold">${ok ? "All contained." : "Check the results."} ${plural(ev.attacks, "AI-written attack")} ran${ev.declined ? ` (${ev.declined} declined by the model)` : ""}.</div>
        <div class="text-sm mt-1">Checked from outside: API key never seen ${ev.key_leaked ? "✗" : "✓"} · server file unchanged ${ev.host_canary_intact ? "✓" : "✗"} ·
          sandbox engine healthy ${ev.host_healthy ? "✓" : "✗"} · nothing left behind in a fresh sandbox ${ev.leftovers_in_fresh_sandbox ? "✗" : "✓"}</div></div>`;
    } else if (ev.type === "error") {
      $("redteamSummary").innerHTML = `<span class="text-red-600">Error: ${esc(ev.message)} ${esc(ev.detail)}</span>`;
    } else if (ev.type === "done") {
      end();
    }
  };
  es.onerror = () => {
    end();
    if (!got) $("redteamSummary").innerHTML = `<span class="text-amber-700 dark:text-amber-300">${esc(BUSY)}</span>`;
  };
});
