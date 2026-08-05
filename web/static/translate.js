let _currentJobId = null;

function initTranslate() {
  $("#book-select").addEventListener("change", loadEstimate);
  $("#start-translate").addEventListener("click", startTranslate);
  $("#upload-input").addEventListener("change", uploadBook);
  $("#stop-translate").addEventListener("click", stopTranslate);
  if (STATE.books.length) loadEstimate();
}

async function loadEstimate() {
  const name = $("#book-select").value;
  if (!name) { $("#estimate").classList.add("hidden"); return; }
  try {
    const est = await api("/api/books/" + encodeURIComponent(name) + "/estimate");
    $("#est-chapters").textContent = est.chapters;
    $("#est-tokens").textContent = est.tokens.toLocaleString();
    $("#est-cost").textContent = "$" + est.cost.toFixed(2);
    $("#est-model").textContent = est.model;
    $("#estimate").classList.remove("hidden");
  } catch (e) { toast(e.message, "error"); }
}

async function startTranslate() {
  const name = $("#book-select").value;
  if (!name) return;
  try {
    const job = await api("/api/translate", { method: "POST", body: { book: name } });
    _currentJobId = job.id;
    $("#translate-bar").style.width = "0%";
    $("#translate-msg").textContent = "Starting…";
    $("#translate-chapters").textContent = "";
    $("#translate-heartbeat").classList.add("hidden");
    $("#translate-log").classList.remove("hidden");
    $("#translate-log").innerHTML = "";
    $("#translate-result").classList.add("hidden");
    $("#cache-warning").classList.add("hidden");
    $("#stop-translate").classList.remove("hidden");
    $("#translate-job").classList.remove("hidden");
    $("#start-translate").disabled = true;
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
  } catch (e) { toast(e.message, "error"); }
}

function updateTranslateTick(job) {
  if (job.chapters_total) {
    $("#translate-chapters").textContent = `Chapter ${job.chapters_done} / ${job.chapters_total}`;
  }
  const last = Date.parse(job.last_event_at);
  if (job.status === "running" && last && Date.now() - last > 15000) {
    const secs = Math.floor((Date.now() - last) / 1000);
    $("#translate-heartbeat").textContent = `Still working… (no update in ${secs}s)`;
    $("#translate-heartbeat").classList.remove("hidden");
  } else {
    $("#translate-heartbeat").classList.add("hidden");
  }
}

function finishTranslate(job) {
  $("#start-translate").disabled = false;
  $("#stop-translate").classList.add("hidden");
  const r = job.result;
  $("#translate-summary").textContent =
    `Done — ${r.input_tokens.toLocaleString()} in / ${r.output_tokens.toLocaleString()} out, est. cost $${r.cost.toFixed(2)}.`;
  $("#download-link").href = "/api/download/" + encodeURIComponent(r.target);
  if (r.cache_cleared) $("#cache-warning").classList.remove("hidden");
  $("#translate-result").classList.remove("hidden");
  refresh();
}

async function stopTranslate() {
  if (!confirm("Stop the current translation?\nCompleted work stays cached — re-running resumes from where it stopped.")) return;
  if (!_currentJobId) return;
  try {
    await api("/api/jobs/" + _currentJobId + "/stop", { method: "POST", body: {} });
  } catch (e) { toast(e.message, "error"); }
}

async function uploadBook(ev) {
  const file = ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    await api("/api/books", { method: "POST", body: fd, multipart: true });
    toast("Uploaded " + file.name);
    await refresh();
    loadEstimate();
  } catch (e) { toast(e.message, "error"); }
}
