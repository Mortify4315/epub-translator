"use strict";

const $ = (selector) => document.querySelector(selector);
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
};
const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"></use></svg>`;

let STATE = { books: [], out: [], glossaries: [], settings: null, readiness: [] };
let EDITING = null;
let GLOSSARY_SCOPES = [];
let SCAN_SCOPE = null;
let currentJobId = null;
let scanJobId = null;

const VIEWS = [
  { id: "translate", label: "Translate", icon: "translate", title: "Translate a book", description: "Prepare the source, review the press estimate, then start a resumable run." },
  { id: "glossary", label: "Glossary", icon: "glossary", title: "Manage terminology", description: "Curate the words that must stay consistent from the first chapter to the last." },
  { id: "scan", label: "Scan terms", icon: "scan", title: "Discover terminology", description: "Build a book-specific vocabulary sheet before paid translation begins." },
  { id: "qa", label: "Quality", icon: "quality", title: "Proof the edition", description: "Run a local consistency pass against the source and approved glossary." },
  { id: "settings", label: "Settings", icon: "settings", title: "Configure the press", description: "Route providers and tune translation defaults, budgets, and run limits." },
];

async function api(path, options = {}) {
  const headers = options.multipart ? {} : { "Content-Type": "application/json" };
  const body = options.body === undefined ? undefined : options.multipart ? options.body : JSON.stringify(options.body);
  const response = await fetch(path, { method: options.method || "GET", headers, body });
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) throw new Error((data && data.error) || `Request failed (${response.status})`);
  return data;
}

let toastTimer;
function toast(message, kind = "info") {
  const node = $("#toast");
  node.textContent = message;
  node.className = "toast" + (kind === "error" ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.className = "toast hidden"; }, 5000);
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("novel-press-theme", theme);
  const button = $("#theme-toggle");
  button.setAttribute("aria-label", `Switch to ${theme === "dark" ? "light" : "dark"} theme`);
}

function initTheme() {
  const saved = localStorage.getItem("novel-press-theme");
  const preferred = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  setTheme(saved === "dark" || saved === "light" ? saved : preferred);
  $("#theme-toggle").addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
}

function renderNavigation() {
  const nav = $("#tabs");
  nav.innerHTML = "";
  VIEWS.forEach((view, index) => {
    const button = el("button", "nav-item");
    button.type = "button";
    button.dataset.view = view.id;
    button.setAttribute("aria-controls", `view-${view.id}`);
    button.innerHTML = `${icon(view.icon)}<span>${view.label}</span><kbd>Alt ${index + 1}</kbd>`;
    button.addEventListener("click", () => showView(view.id));
    nav.appendChild(button);
  });
}

function showView(id, options = {}) {
  const view = VIEWS.find((item) => item.id === id) || VIEWS[0];
  document.querySelectorAll(".view").forEach((node) => node.classList.add("hidden"));
  document.querySelectorAll(".nav-item").forEach((node) => {
    const active = node.dataset.view === view.id;
    node.classList.toggle("active", active);
    active ? node.setAttribute("aria-current", "page") : node.removeAttribute("aria-current");
  });
  $("#view-" + view.id).classList.remove("hidden");
  $("#view-title").textContent = view.title;
  $("#view-description").textContent = view.description;
  document.title = `${view.title} — Novel Press`;
  if (!options.fromHash) history.replaceState(null, "", `#${view.id}`);
  if (options.focus) $("#main-content").focus();
}

function fillSelect(select, options, valueKey, labelKey, emptyLabel = "No options available") {
  const previous = select.value;
  select.innerHTML = "";
  if (!options.length) {
    const option = el("option", "", emptyLabel);
    option.value = "";
    select.appendChild(option);
    select.disabled = true;
    return;
  }
  select.disabled = false;
  for (const item of options) {
    const option = el("option", "", item[labelKey]);
    option.value = item[valueKey];
    select.appendChild(option);
  }
  if ([...select.options].some((option) => option.value === previous)) select.value = previous;
}

function updateButtons() {
  const ready = !!(STATE.settings && (STATE.settings.api_key_set || STATE.settings.api_key_optional));
  const hasBooks = STATE.books.length > 0;
  $("#start-translate").disabled = !(ready && hasBooks && $("#book-select").value);
  $("#start-scan").disabled = !(ready && hasBooks && $("#scan-book").value);
  $("#run-qa").disabled = !($("#qa-source").value && $("#qa-target").value);
}

function renderSetupState() {
  const settings = STATE.settings;
  const ready = settings.api_key_set || settings.api_key_optional;
  const button = $("#api-state");
  button.textContent = ready ? `${settings.provider_label} ready` : "Setup needed";
  button.className = "setup-state " + (ready ? "ok" : "warn");
  button.onclick = () => showView("settings", { focus: true });
}

function renderSettings(settings) {
  const provider = $("#setting-provider");
  provider.innerHTML = "";
  settings.providers.forEach((item) => {
    const option = el("option", "", item.label);
    option.value = item.name;
    provider.appendChild(option);
  });
  provider.value = settings.provider;
  const modelOptions = $("#model-options");
  modelOptions.innerHTML = "";
  settings.models.forEach((model) => {
    const option = document.createElement("option");
    option.value = model;
    modelOptions.appendChild(option);
  });
  $("#setting-model").value = settings.model;
  $("#setting-base").value = settings.base_url;
  $("#setting-concurrency").value = settings.concurrency;
  $("#setting-pipeline").value = settings.pipeline || "one-pass";
  $("#setting-strict-one-pass").checked = !!settings.strict_one_pass;
  $("#setting-thinking").value = settings.thinking;
  $("#setting-fill").value = settings.fill_thinking;
  $("#setting-chapter-limit").value = settings.chapter_limit;
  $("#setting-group-tokens").value = settings.max_group_tokens;
  $("#setting-key").placeholder = settings.api_key_optional && !settings.api_key_configured
    ? "Not required by this local router"
    : settings.api_key_set ? `Current: ${settings.api_key_masked}` : "Not set";
  $("#setting-key-hint").textContent = settings.api_key_optional
    ? `Optional for ${settings.provider_label}. Environment override: ${settings.env_key}.`
    : `Stored locally in settings.json. Environment override: ${settings.env_key}.`;
  const warning = $("#setting-thinking-warn");
  const enabled = !!settings.thinking_supported;
  $("#setting-thinking").disabled = !enabled;
  $("#setting-fill").disabled = !enabled;
  warning.textContent = enabled ? "" : `Thinking modes are not sent to ${settings.provider_label}.`;
  warning.classList.toggle("hidden", enabled);
  $("#run-pipeline").textContent = settings.pipeline === "two-pass" ? "Two-pass pipeline" : "One-pass pipeline";
  $("#run-limit").textContent = `${settings.chapter_limit ? settings.chapter_limit + " chapters this run" : "All chapters"} · resumable cache`;
}

function renderOutputs() {
  const list = $("#output-list");
  list.innerHTML = "";
  if (!STATE.out.length) {
    const empty = el("div", "empty-state");
    empty.append(el("strong", "", "No finished editions yet."), el("span", "", "Completed translations will be ready to download here."));
    list.appendChild(empty);
    return;
  }
  [...STATE.out].reverse().slice(0, 8).forEach((name) => {
    const row = el("div", "output-item");
    row.appendChild(el("span", "output-name", name));
    const link = el("a", "", "Download");
    link.href = "/api/download/" + encodeURIComponent(name);
    link.setAttribute("download", "");
    row.appendChild(link);
    list.appendChild(row);
  });
}

async function refresh() {
  const data = await api("/api/bootstrap");
  STATE = data;
  $("#summary-books").textContent = data.books.length;
  $("#summary-output").textContent = data.out.length;
  fillSelect($("#book-select"), data.books, "name", "name", "Add an EPUB to begin");
  fillSelect($("#scan-book"), data.books, "name", "name", "Add an EPUB to begin");
  fillSelect($("#qa-source"), data.books, "name", "name", "No source EPUBs");
  fillSelect($("#qa-target"), data.out.map((name) => ({ name })), "name", "name", "No finished EPUBs");
  const scopes = [{ key: "global", label: "Shared — all books" }];
  data.glossaries.filter((item) => item.key !== "global").forEach((item) => scopes.push({ key: item.key, label: item.label }));
  fillSelect($("#scope-select"), scopes, "key", "label");
  renderSettings(data.settings);
  renderSetupState();
  renderOutputs();
  updateButtons();
}

function appendLog(selector, entries) {
  const box = $(selector);
  for (const entry of entries) {
    const line = el("div", "log-line" + (entry.level === "warn" ? " log-warn" : entry.level === "error" ? " log-error" : ""));
    line.append(el("span", "t", new Date(entry.t).toLocaleTimeString()), el("span", "m", entry.msg));
    box.appendChild(line);
  }
  while (box.childElementCount > 500) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
}

function updateProgress(barSelector, progress) {
  const value = Math.max(0, Math.min(100, Number(progress) || 0));
  const bar = $(barSelector);
  bar.style.setProperty("--progress", String(value / 100));
  const progressbar = bar.closest("[role=progressbar]");
  if (progressbar) progressbar.setAttribute("aria-valuenow", String(Math.round(value)));
}

function pollJob(id, options = {}) {
  let after = 0;
  const poll = async () => {
    let job;
    try { job = await api("/api/jobs/" + id); }
    catch (error) { toast(error.message, "error"); return; }
    if (options.barSel) updateProgress(options.barSel, job.progress);
    if (options.msgSel) $(options.msgSel).textContent = job.message || "Working…";
    if (job.status === "running" && options.onLog) {
      try {
        const log = await api(`/api/jobs/${id}/log?after=${after}`);
        if (log.total > after) { options.onLog(log.entries); after = log.total; }
      } catch (_) { /* a transient log read should not stop the run */ }
    }
    if (options.onTick) options.onTick(job);
    if (job.status === "done") return options.onDone && options.onDone(job);
    if (job.status === "error") return options.onError && options.onError(job);
    if (job.status === "stopped") return (options.onStopped || options.onError) && (options.onStopped || options.onError)(job);
    setTimeout(poll, 1000);
  };
  setTimeout(poll, 250);
}

async function fetchLogOnce(jobId, selector) {
  try { appendLog(selector, (await api(`/api/jobs/${jobId}/log?after=0`)).entries); }
  catch (_) { /* log recovery is best effort */ }
}

async function loadEstimate() {
  const name = $("#book-select").value;
  if (!name) { $("#estimate").classList.add("hidden"); return; }
  try {
    const estimate = await api("/api/books/" + encodeURIComponent(name) + "/estimate");
    $("#est-chapters").textContent = estimate.chapters.toLocaleString();
    $("#est-tokens").textContent = estimate.tokens.toLocaleString();
    $("#est-cost").textContent = "$" + estimate.cost.toFixed(2);
    $("#est-model").textContent = estimate.model;
    $("#estimate").classList.remove("hidden");
  } catch (error) { toast(`Could not estimate this book: ${error.message}`, "error"); }
}

function updateTranslateTick(job) {
  if (job.chapters_total) $("#translate-chapters").textContent = `Chapter ${job.chapters_done.toLocaleString()} of ${job.chapters_total.toLocaleString()}`;
  const last = Date.parse(job.last_event_at);
  const stale = job.status === "running" && last && Date.now() - last > 15000;
  $("#translate-heartbeat").textContent = stale ? `Still working — last update ${Math.floor((Date.now() - last) / 1000)} seconds ago.` : "";
  $("#translate-heartbeat").classList.toggle("hidden", !stale);
}

function translationStopped() {
  $("#start-translate").disabled = false;
  $("#stop-translate").classList.add("hidden");
  $("#translate-heartbeat").textContent = "Stopped safely. Start again to resume from cache.";
  $("#translate-heartbeat").classList.remove("hidden");
}

function finishTranslate(job) {
  $("#start-translate").disabled = false;
  $("#stop-translate").classList.add("hidden");
  const result = job.result;
  $("#translate-summary").textContent = `Edition finished — ${result.input_tokens.toLocaleString()} input / ${result.output_tokens.toLocaleString()} output tokens, estimated $${result.cost.toFixed(2)}.`;
  $("#download-link").href = "/api/download/" + encodeURIComponent(result.target);
  $("#cache-warning").classList.toggle("hidden", !result.cache_cleared);
  $("#translate-result").classList.remove("hidden");
  refresh().catch((error) => toast(error.message, "error"));
}

async function startTranslate() {
  const book = $("#book-select").value;
  if (!book) return;
  try {
    const job = await api("/api/translate", { method: "POST", body: { book } });
    currentJobId = job.id;
    updateProgress("#translate-bar", 0);
    $("#translate-msg").textContent = "Preparing the press run…";
    $("#translate-chapters").textContent = "Preparing chapters…";
    $("#translate-heartbeat").classList.add("hidden");
    $("#translate-log").innerHTML = "";
    $("#translate-result").classList.add("hidden");
    $("#stop-translate").classList.remove("hidden");
    $("#translate-job").classList.remove("hidden");
    $("#start-translate").disabled = true;
    pollJob(job.id, {
      barSel: "#translate-bar", msgSel: "#translate-msg", onTick: updateTranslateTick,
      onLog: (entries) => appendLog("#translate-log", entries), onDone: finishTranslate,
      onError: (failed) => { $("#start-translate").disabled = false; $("#stop-translate").classList.add("hidden"); toast(`Translation failed: ${failed.error}`, "error"); },
      onStopped: translationStopped,
    });
  } catch (error) { toast(error.message, "error"); }
}

async function stopTranslate() {
  if (!currentJobId || !confirm("Stop this run safely? Completed chapters stay cached for the next run.")) return;
  try { await api(`/api/jobs/${currentJobId}/stop`, { method: "POST", body: {} }); }
  catch (error) { toast(error.message, "error"); }
}

async function uploadBook(event) {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  const body = new FormData();
  body.append("file", file);
  try {
    const added = await api("/api/books", { method: "POST", body, multipart: true });
    toast(`Added ${added.name} to the source shelf.`);
    await refresh();
    $("#book-select").value = added.name;
    await loadEstimate();
  } catch (error) { toast(error.message, "error"); }
}

async function renderGlossary() {
  const scope = $("#scope-select").value || "global";
  try { GLOSSARY_SCOPES = await api("/api/glossary"); }
  catch (error) { toast(error.message, "error"); return; }
  const selected = GLOSSARY_SCOPES.find((item) => item.key === scope) || { terms: {} };
  const query = $("#term-search").value.trim().toLocaleLowerCase();
  const all = Object.entries(selected.terms).sort((a, b) => a[0].localeCompare(b[0]));
  const entries = query ? all.filter(([source, target]) => `${source}\n${target}`.toLocaleLowerCase().includes(query)) : all;
  const body = $("#term-table tbody");
  body.innerHTML = "";
  entries.forEach(([source, target]) => {
    const row = el("tr");
    row.append(el("td", "", source), el("td", "", target));
    const actions = el("td", "actions");
    const edit = el("button", "btn small quiet", "Edit");
    edit.type = "button";
    edit.addEventListener("click", () => editTerm(scope, source, target));
    const remove = el("button", "btn small danger", "Delete");
    remove.type = "button";
    remove.addEventListener("click", () => deleteTerm(scope, source));
    actions.append(edit, remove);
    row.appendChild(actions);
    body.appendChild(row);
  });
  $("#term-count").textContent = `${all.length.toLocaleString()} ${all.length === 1 ? "term" : "terms"}`;
  $("#term-filter-state").textContent = query ? `${entries.length.toLocaleString()} matching` : "";
  $("#glossary-empty").classList.toggle("hidden", entries.length > 0);
  $("#term-table").classList.toggle("hidden", entries.length === 0);
  $("#export-glossary").href = `/api/glossary/${encodeURIComponent(scope)}/export`;
}

function openTermEditor(source = "", target = "") {
  $("#new-src").value = source;
  $("#new-dst").value = target;
  $("#new-src").disabled = !!source;
  $("#term-form-title").textContent = source ? "Edit term" : "New term";
  $("#add-term-form").classList.remove("hidden");
  (source ? $("#new-dst") : $("#new-src")).focus();
}

function closeTermEditor() {
  EDITING = null;
  $("#new-src").value = "";
  $("#new-dst").value = "";
  $("#new-src").disabled = false;
  $("#add-term-form").classList.add("hidden");
}

function editTerm(scope, source, target) { EDITING = { scope, source }; openTermEditor(source, target); }

async function saveTerm() {
  const scope = $("#scope-select").value;
  const source = $("#new-src").value.trim();
  const target = $("#new-dst").value.trim();
  if (!source || !target) { toast("Enter both the Chinese source and English translation.", "error"); return; }
  try {
    if (EDITING) await api(`/api/glossary/${encodeURIComponent(scope)}/term/${encodeURIComponent(EDITING.source)}`, { method: "PUT", body: { src: source, dst: target } });
    else await api(`/api/glossary/${encodeURIComponent(scope)}/term`, { method: "POST", body: { src: source, dst: target } });
    toast(EDITING ? "Term updated." : "Term added.");
    closeTermEditor();
    await renderGlossary();
  } catch (error) { toast(error.message, "error"); }
}

async function deleteTerm(scope, source) {
  if (!confirm(`Delete “${source}” from this glossary?`)) return;
  try { await api(`/api/glossary/${encodeURIComponent(scope)}/term/${encodeURIComponent(source)}`, { method: "DELETE", body: {} }); toast("Term deleted."); await renderGlossary(); }
  catch (error) { toast(error.message, "error"); }
}

async function importGlossary(event) {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  const body = new FormData();
  body.append("file", file);
  try {
    const result = await api(`/api/glossary/${encodeURIComponent($("#scope-select").value)}/import`, { method: "POST", body, multipart: true });
    toast(`Imported ${result.added} new term${result.added === 1 ? "" : "s"}; ${result.skipped} existing skipped.`);
    await renderGlossary();
  } catch (error) { toast(error.message, "error"); }
}

function scanHeartbeat(job) {
  const last = Date.parse(job.last_event_at);
  const stale = job.status === "running" && last && Date.now() - last > 15000;
  $("#scan-heartbeat").textContent = stale ? `Still working — last update ${Math.floor((Date.now() - last) / 1000)} seconds ago.` : "";
  $("#scan-heartbeat").classList.toggle("hidden", !stale);
}

async function startScan() {
  const name = $("#scan-book").value;
  const book = STATE.books.find((item) => item.name === name);
  if (!book) return;
  SCAN_SCOPE = book.key;
  try {
    const job = await api("/api/scan", { method: "POST", body: { book: name } });
    scanJobId = job.id;
    updateProgress("#scan-bar", 0);
    $("#scan-result").classList.add("hidden");
    $("#scan-progress").classList.remove("hidden");
    $("#scan-log-wrap").classList.remove("hidden");
    $("#scan-log").innerHTML = "";
    $("#stop-scan").classList.remove("hidden");
    $("#start-scan").disabled = true;
    pollJob(job.id, {
      barSel: "#scan-bar", msgSel: "#scan-msg", onTick: scanHeartbeat,
      onLog: (entries) => appendLog("#scan-log", entries), onDone: renderScanResult,
      onError: (failed) => { $("#start-scan").disabled = false; $("#stop-scan").classList.add("hidden"); toast(`Scan failed: ${failed.error}`, "error"); },
      onStopped: () => { $("#start-scan").disabled = false; $("#stop-scan").classList.add("hidden"); toast("Scan stopped safely."); },
    });
  } catch (error) { toast(error.message, "error"); }
}

async function stopScan() {
  if (!scanJobId || !confirm("Stop this terminology scan?")) return;
  try { await api(`/api/jobs/${scanJobId}/stop`, { method: "POST", body: {} }); }
  catch (error) { toast(error.message, "error"); }
}

function renderScanResult(job) {
  $("#start-scan").disabled = false;
  $("#stop-scan").classList.add("hidden");
  const entries = Object.entries((job.result && job.result.fresh) || {}).sort((a, b) => a[0].localeCompare(b[0]));
  const list = $("#scan-list");
  list.innerHTML = "";
  $("#scan-summary").textContent = entries.length ? `${entries.length} new terms proposed. Review what joins the book glossary.` : "No new terms were found; this book's candidates are already covered.";
  entries.forEach(([source, target]) => {
    const label = el("label", "scan-item");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = true;
    checkbox.dataset.src = source;
    checkbox.dataset.dst = target;
    checkbox.addEventListener("change", updateAcceptScan);
    label.append(checkbox, el("span", "", `${source} → ${target}`));
    list.appendChild(label);
  });
  $("#scan-result").classList.remove("hidden");
  updateAcceptScan();
}

function updateAcceptScan() { $("#accept-scan").disabled = !document.querySelector("#scan-list input:checked"); }

async function acceptScan() {
  const terms = {};
  document.querySelectorAll("#scan-list input:checked").forEach((checkbox) => { terms[checkbox.dataset.src] = checkbox.dataset.dst; });
  if (!Object.keys(terms).length) return;
  try { const result = await api("/api/scan/accept", { method: "POST", body: { scope: SCAN_SCOPE, terms } }); toast(`Added ${result.added} terms to the book glossary.`); $("#scan-result").classList.add("hidden"); await refresh(); }
  catch (error) { toast(error.message, "error"); }
}

async function runQa() {
  const source = $("#qa-source").value;
  const target = $("#qa-target").value;
  if (!source || !target) return;
  const button = $("#run-qa");
  button.disabled = true;
  button.textContent = "Proofing…";
  try {
    const result = await api("/api/qa", { method: "POST", body: { source, target } });
    const output = $("#qa-result");
    output.innerHTML = "";
    output.classList.remove("hidden");
    if (!result.count) output.append(el("strong", "status-ok", "Proof passed."), el("p", "muted", "No glossary consistency issues were found."));
    else {
      output.appendChild(el("strong", "status-warn", `${result.count} potential consistency issue${result.count === 1 ? "" : "s"}`));
      const list = el("ul");
      result.issues.slice(0, 200).forEach(([src, dst, chapter, variant, count]) => list.appendChild(el("li", "", `Chapter ${chapter}: “${src}” expects “${dst}” but may appear as “${variant}” (${count}×).`)));
      output.append(list, el("p", "muted", "Approve acceptable variants in the glossary; correct the glossary and rerun translation for the rest."));
    }
  } catch (error) { toast(error.message, "error"); }
  finally { button.innerHTML = `${icon("quality")}Run proof`; updateButtons(); }
}

async function saveKey() {
  const key = $("#setting-key").value.trim();
  if (!key) { toast("Enter an API key, or leave it blank for a keyless local router.", "error"); return; }
  try {
    const settings = await api("/api/settings", { method: "POST", body: { api_key: key, provider: $("#setting-provider").value } });
    $("#setting-key").value = "";
    STATE.settings = settings;
    renderSettings(settings);
    renderSetupState();
    updateButtons();
    toast(`API key saved for ${settings.provider_label}.`);
  } catch (error) { toast(error.message, "error"); }
}

async function saveSettings(event) {
  event.preventDefault();
  const body = {
    provider: $("#setting-provider").value,
    model: $("#setting-model").value.trim(),
    base_url: $("#setting-base").value.trim(),
    concurrency: Number.parseInt($("#setting-concurrency").value, 10),
    pipeline: $("#setting-pipeline").value,
    strict_one_pass: $("#setting-strict-one-pass").checked,
    thinking: $("#setting-thinking").value,
    fill_thinking: $("#setting-fill").value,
    chapter_limit: Number.parseInt($("#setting-chapter-limit").value, 10),
    max_group_tokens: Number.parseInt($("#setting-group-tokens").value, 10),
  };
  try { const settings = await api("/api/settings", { method: "POST", body }); STATE.settings = settings; renderSettings(settings); renderSetupState(); updateButtons(); toast("Press configuration saved for the next run."); }
  catch (error) { toast(error.message, "error"); }
}

async function pickupCurrentJob() {
  let job;
  try { job = await api("/api/jobs/current"); }
  catch (_) { return; }
  if (!job || !["running", "stopped"].includes(job.status)) return;
  if (job.kind === "translate") {
    currentJobId = job.id;
    $("#translate-job").classList.remove("hidden");
    await fetchLogOnce(job.id, "#translate-log");
    if (job.status === "stopped") return translationStopped();
    $("#stop-translate").classList.remove("hidden");
    $("#start-translate").disabled = true;
    pollJob(job.id, { barSel: "#translate-bar", msgSel: "#translate-msg", onTick: updateTranslateTick, onLog: (entries) => appendLog("#translate-log", entries), onDone: finishTranslate, onError: (failed) => toast(`Translation failed: ${failed.error}`, "error"), onStopped: translationStopped });
  } else if (job.kind === "scan") {
    scanJobId = job.id;
    $("#scan-progress").classList.remove("hidden");
    $("#scan-log-wrap").classList.remove("hidden");
    await fetchLogOnce(job.id, "#scan-log");
    if (job.status === "stopped") return;
    $("#stop-scan").classList.remove("hidden");
    pollJob(job.id, { barSel: "#scan-bar", msgSel: "#scan-msg", onTick: scanHeartbeat, onLog: (entries) => appendLog("#scan-log", entries), onDone: renderScanResult, onError: (failed) => toast(`Scan failed: ${failed.error}`, "error") });
  }
}

function bindEvents() {
  $("#book-select").addEventListener("change", () => { loadEstimate(); updateButtons(); });
  $("#scan-book").addEventListener("change", updateButtons);
  $("#qa-source").addEventListener("change", updateButtons);
  $("#qa-target").addEventListener("change", updateButtons);
  $("#start-translate").addEventListener("click", startTranslate);
  $("#stop-translate").addEventListener("click", stopTranslate);
  $("#upload-input").addEventListener("change", uploadBook);
  $("#scope-select").addEventListener("change", renderGlossary);
  $("#term-search").addEventListener("input", renderGlossary);
  $("#add-term-btn").addEventListener("click", () => { EDITING = null; openTermEditor(); });
  $("#cancel-term-btn").addEventListener("click", closeTermEditor);
  $("#save-term-btn").addEventListener("click", saveTerm);
  $("#import-glossary").addEventListener("change", importGlossary);
  $("#start-scan").addEventListener("click", startScan);
  $("#stop-scan").addEventListener("click", stopScan);
  $("#accept-scan").addEventListener("click", acceptScan);
  $("#run-qa").addEventListener("click", runQa);
  $("#save-key").addEventListener("click", saveKey);
  $("#setting-provider").addEventListener("change", () => {
    const selected = STATE.settings.providers.find((item) => item.name === $("#setting-provider").value);
    if (!selected) return;
    $("#setting-base").value = selected.base_url || "";
    $("#setting-model").value = selected.models[0] || "";
    const modelOptions = $("#model-options");
    modelOptions.innerHTML = "";
    selected.models.forEach((model) => { const option = document.createElement("option"); option.value = model; modelOptions.appendChild(option); });
    $("#setting-key-hint").textContent = selected.api_key_optional
      ? "API key is optional for this local router."
      : "This provider requires its own API key.";
  });
  $("#settings-form").addEventListener("submit", saveSettings);
  $("#toggle-key").addEventListener("click", () => {
    const input = $("#setting-key");
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    $("#toggle-key").textContent = show ? "Hide" : "Show";
    $("#toggle-key").setAttribute("aria-pressed", String(show));
  });
  window.addEventListener("hashchange", () => showView(location.hash.slice(1), { fromHash: true }));
  document.addEventListener("keydown", (event) => {
    if (!event.altKey || event.ctrlKey || event.metaKey) return;
    const index = Number(event.key) - 1;
    if (VIEWS[index]) { event.preventDefault(); showView(VIEWS[index].id, { focus: true }); }
  });
}

async function bootstrap() {
  initTheme();
  renderNavigation();
  bindEvents();
  try {
    await refresh();
    showView(location.hash.slice(1) || "translate", { fromHash: true });
    if (STATE.books.length) await loadEstimate();
    await renderGlossary();
    await pickupCurrentJob();
  } catch (error) { toast(`Could not open the workspace: ${error.message}`, "error"); }
}

document.addEventListener("DOMContentLoaded", bootstrap);
