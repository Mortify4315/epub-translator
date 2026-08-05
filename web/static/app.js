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
  $("#setting-model").value = s.model;
  $("#setting-concurrency").value = s.concurrency;
  $("#setting-thinking").value = s.thinking;
  $("#setting-fill").value = s.fill_thinking;
  $("#setting-key").placeholder = s.api_key_set ? "Current: " + s.api_key_masked : "(not set)";
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
  } catch (e) { toast(e.message, "error"); }
}

document.addEventListener("DOMContentLoaded", bootstrap);
