const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text) n.textContent = text;
  return n;
};

let STATE = { books: [], out: [], glossaries: [], settings: null };

async function api(path, opts = {}) {
  const headers = opts.multipart ? {} : { "Content-Type": "application/json" };
  const body = opts.body === undefined ? undefined
    : opts.multipart ? opts.body
    : JSON.stringify(opts.body);
  const res = await fetch(path, { method: opts.method || "GET", headers, body });
  const data = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data;
}

let _toastTimer;
function toast(msg, kind = "info") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (kind === "error" ? " error" : "");
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { t.className = "toast hidden"; }, 4000);
}

function fillSelect(sel, options, valueKey, labelKey) {
  sel.innerHTML = "";
  for (const opt of options) {
    const o = document.createElement("option");
    o.value = opt[valueKey];
    o.textContent = opt[labelKey];
    sel.appendChild(o);
  }
}

const TABS = [
  ["translate", "Translate"],
  ["glossary", "Glossary"],
  ["scan", "Scan Terms"],
  ["qa", "Quality Check"],
  ["settings", "Settings"],
];

function renderTabs() {
  const nav = $("#tabs");
  nav.innerHTML = "";
  for (const [id, label] of TABS) {
    const b = el("button", "tab" + (id === "translate" ? " active" : ""), label);
    b.dataset.view = id;
    b.addEventListener("click", () => showView(id));
    nav.appendChild(b);
  }
}

function showView(id) {
  document.querySelectorAll(".view").forEach(v => v.classList.add("hidden"));
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.view === id));
  $("#view-" + id).classList.remove("hidden");
}

function updateButtons() {
  const hasKey = !!(STATE.settings && STATE.settings.api_key_set);
  const hasBooks = STATE.books.length > 0;
  $("#start-translate").disabled = !(hasKey && hasBooks && !!$("#book-select").value);
  $("#start-scan").disabled = !(hasKey && hasBooks);
  $("#run-qa").disabled = !($("#qa-source").value && $("#qa-target").value);
}

function renderSettings(s) {
  const provSel = $("#setting-provider");
  provSel.innerHTML = "";
  for (const p of s.providers) {
    const opt = el("option", "", p.label);
    opt.value = p.name;
    provSel.append(opt);
  }
  provSel.value = s.provider;
  $("#setting-key-hint").textContent =
    "Stored locally in settings.json. Env var override: " + s.env_key + ".";
  const modelOpts = $("#model-options");
  modelOpts.innerHTML = "";
  for (const m of s.models) {
    const opt = el("option", "", m);
    opt.value = m;
    modelOpts.append(opt);
  }
  $("#setting-model").value = s.model;
  $("#setting-base").value = s.base_url;
  $("#setting-concurrency").value = s.concurrency;
  $("#setting-thinking").value = s.thinking;
  $("#setting-fill").value = s.fill_thinking;
  $("#setting-key").placeholder = s.api_key_set ? "Current: " + s.api_key_masked : "(not set)";
  const warn = $("#setting-thinking-warn");
  if (s.thinking_supported) {
    warn.classList.add("hidden");
    $("#setting-thinking").disabled = false;
    $("#setting-fill").disabled = false;
  } else {
    warn.textContent = "Thinking modes apply to DeepSeek-family providers only; ignored by " + s.provider_label + ".";
    warn.classList.remove("hidden");
    $("#setting-thinking").disabled = true;
    $("#setting-fill").disabled = true;
  }
}

async function refresh() {
  const data = await api("/api/bootstrap");
  STATE = data;
  $("#api-state").textContent = data.settings.api_key_set ? "API key set" : "API key NOT set";
  $("#api-state").className = "pill " + (data.settings.api_key_set ? "ok" : "warn");
  fillSelect($("#book-select"), data.books, "name", "name");
  fillSelect($("#scan-book"), data.books, "name", "name");
  fillSelect($("#qa-source"), data.books, "name", "name");
  fillSelect($("#qa-target"), data.out.map(n => ({ name: n })), "name", "name");
  const scopeOpts = [{ key: "global", label: "Shared (all books)" }];
  for (const g of data.glossaries) if (g.key !== "global") scopeOpts.push({ key: g.key, label: g.label });
  fillSelect($("#scope-select"), scopeOpts, "key", "label");
  renderSettings(data.settings);
  updateButtons();
}

function appendLog(sel, entries) {
  const box = $(sel);
  for (const e of entries) {
    const line = el("div", "log-line" + (e.level === "warn" ? " log-warn" : e.level === "error" ? " log-error" : ""));
    const time = el("span", "t", new Date(e.t).toLocaleTimeString());
    const msg = el("span", "m", e.msg);
    line.append(time, msg);
    box.appendChild(line);
  }
  while (box.childElementCount > 500) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
}

function pollJob(id, opts = {}) {
  const { barSel, msgSel, onTick, onLog, onDone, onError, onStopped } = opts;
  let after = 0;
  const interval = setInterval(async () => {
    let job;
    try { job = await api("/api/jobs/" + id); }
    catch (e) { clearInterval(interval); toast(e.message, "error"); return; }
    if (barSel) $(barSel).style.width = job.progress + "%";
    if (msgSel) $(msgSel).textContent = job.message || "";
    if (job.status === "running" && onLog) {
      try {
        const lg = await api("/api/jobs/" + id + "/log?after=" + after);
        if (lg.total > after) { onLog(lg.entries); after = lg.total; }
      } catch (e) { /* transient log fetch — ignore */ }
    }
    if (onTick) onTick(job);
    if (job.status === "done") { clearInterval(interval); if (onDone) onDone(job); }
    else if (job.status === "error") { clearInterval(interval); if (onError) onError(job); }
    else if (job.status === "stopped") { clearInterval(interval); (onStopped || onError) && (onStopped || onError)(job); }
  }, 1000);
}

async function bootstrap() {
  try {
    await refresh();
    renderTabs();
    if (typeof initTranslate === "function") initTranslate();
    if (typeof initGlossary === "function") initGlossary();
    if (typeof initScan === "function") initScan();
    if (typeof initQa === "function") initQa();
    if (typeof initSettings === "function") initSettings();
    pickupCurrentJob();
  } catch (e) { toast(e.message, "error"); }
}

/* Adopt a job that was started elsewhere (another tab, or launched via the
   API) so a freshly opened page shows live progress instead of nothing. */
async function pickupCurrentJob() {
  let job;
  try { job = await api("/api/jobs/current"); }
  catch (e) { return; } // 404 = no job yet
  if (!job || !["running", "stopped"].includes(job.status)) return;
  const adopt = (sel) => {
    $(sel).classList.remove("hidden");
  };
  if (job.kind === "translate") {
    _currentJobId = job.id;
    adopt("#translate-job");
    adopt("#translate-log");
    adopt("#stop-translate");
    $("#start-translate").disabled = true;
    if (job.status === "stopped") {
      $("#start-translate").disabled = false;
      $("#stop-translate").classList.add("hidden");
      $("#translate-heartbeat").textContent = "Stopped — re-run resumes from cache.";
      $("#translate-heartbeat").classList.remove("hidden");
      fetchLogOnce(job.id, "#translate-log");
      return;
    }
    pollJob(job.id, {
      barSel: "#translate-bar",
      msgSel: "#translate-msg",
      onTick: updateTranslateTick,
      onLog: (entries) => appendLog("#translate-log", entries),
      onDone: finishTranslate,
      onError: (j) => {
        $("#start-translate").disabled = false;
        $("#stop-translate").classList.add("hidden");
        toast("Translation failed: " + j.error, "error");
      },
      onStopped: () => {
        $("#start-translate").disabled = false;
        $("#stop-translate").classList.add("hidden");
        $("#translate-heartbeat").textContent = "Stopped — re-run resumes from cache.";
        $("#translate-heartbeat").classList.remove("hidden");
      },
    });
  } else if (job.kind === "scan") {
    _scanJobId = job.id;
    adopt("#scan-progress");
    adopt("#scan-log");
    adopt("#stop-scan");
    $("#start-scan").disabled = true;
    if (job.status === "stopped") {
      $("#start-scan").disabled = false;
      $("#stop-scan").classList.add("hidden");
      toast("Scan stopped", "info");
      fetchLogOnce(job.id, "#scan-log");
      return;
    }
    pollJob(job.id, {
      barSel: "#scan-bar",
      msgSel: "#scan-msg",
      onTick: () => {},
      onLog: (entries) => appendLog("#scan-log", entries),
      onDone: renderScanResult,
      onError: (j) => {
        $("#start-scan").disabled = false;
        $("#stop-scan").classList.add("hidden");
        toast("Scan failed: " + j.error, "error");
      },
      onStopped: () => {
        $("#start-scan").disabled = false;
        $("#stop-scan").classList.add("hidden");
        toast("Scan stopped", "info");
      },
    });
  }
}

async function fetchLogOnce(jobId, sel) {
  try {
    const lg = await api("/api/jobs/" + jobId + "/log?after=0");
    appendLog(sel, lg.entries);
  } catch (e) { /* ignore */ }
}

document.addEventListener("DOMContentLoaded", bootstrap);
