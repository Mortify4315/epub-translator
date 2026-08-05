let SCAN_SCOPE = null;
let _scanJobId = null;

function initScan() {
  $("#start-scan").addEventListener("click", startScan);
  $("#accept-scan").addEventListener("click", acceptScan);
  $("#stop-scan").addEventListener("click", stopScan);
}

async function startScan() {
  const name = $("#scan-book").value;
  if (!name) return;
  const book = STATE.books.find(b => b.name === name);
  if (!book) return;
  SCAN_SCOPE = book.key;
  try {
    const job = await api("/api/scan", { method: "POST", body: { book: name } });
    _scanJobId = job.id;
    $("#scan-bar").style.width = "0%";
    $("#scan-result").classList.add("hidden");
    $("#scan-progress").classList.remove("hidden");
    $("#scan-heartbeat").classList.add("hidden");
    $("#scan-log").classList.remove("hidden");
    $("#scan-log").innerHTML = "";
    $("#stop-scan").classList.remove("hidden");
    $("#start-scan").disabled = true;
    pollJob(job.id, {
      barSel: "#scan-bar",
      msgSel: "#scan-msg",
      onTick: (j) => {
        const last = Date.parse(j.last_event_at);
        if (j.status === "running" && last && Date.now() - last > 15000) {
          const secs = Math.floor((Date.now() - last) / 1000);
          $("#scan-heartbeat").textContent = `Still working… (no update in ${secs}s)`;
          $("#scan-heartbeat").classList.remove("hidden");
        } else {
          $("#scan-heartbeat").classList.add("hidden");
        }
      },
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
  } catch (e) { toast(e.message, "error"); }
}

async function stopScan() {
  if (!confirm("Stop the scan?")) return;
  if (!_scanJobId) return;
  try {
    await api("/api/jobs/" + _scanJobId + "/stop", { method: "POST", body: {} });
  } catch (e) { toast(e.message, "error"); }
}

function renderScanResult(job) {
  $("#start-scan").disabled = false;
  const fresh = job.result.fresh || {};
  const list = $("#scan-list");
  list.innerHTML = "";
  const entries = Object.entries(fresh).sort((a, b) => a[0].localeCompare(b[0]));
  if (!entries.length) {
    $("#scan-summary").textContent = "All candidate terms are already in the glossary.";
    $("#accept-scan").disabled = true;
    $("#scan-result").classList.remove("hidden");
    return;
  }
  $("#scan-summary").textContent = entries.length + " new term(s) proposed — tick the ones to add:";
  for (const [src, dst] of entries) {
    const label = el("label", "scan-item");
    const cb = el("input");
    cb.type = "checkbox";
    cb.dataset.src = src;
    cb.dataset.dst = dst;
    cb.checked = true;
    cb.addEventListener("change", updateAcceptScan);
    label.append(cb, "  ", src, " → ", dst);
    list.appendChild(label);
  }
  updateAcceptScan();
  $("#scan-result").classList.remove("hidden");
}

function updateAcceptScan() {
  $("#accept-scan").disabled = !document.querySelector("#scan-list input:checked");
}

async function acceptScan() {
  const terms = {};
  document.querySelectorAll("#scan-list input:checked").forEach(cb => {
    terms[cb.dataset.src] = cb.dataset.dst;
  });
  if (!Object.keys(terms).length) return;
  try {
    const r = await api("/api/scan/accept", { method: "POST", body: { scope: SCAN_SCOPE, terms } });
    toast("Added " + r.added + " term(s)");
    $("#scan-result").classList.add("hidden");
    await refresh();
  } catch (e) { toast(e.message, "error"); }
}
