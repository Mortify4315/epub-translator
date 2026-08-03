function initTranslate() {
  $("#book-select").addEventListener("change", loadEstimate);
  $("#start-translate").addEventListener("click", startTranslate);
  $("#upload-input").addEventListener("change", uploadBook);
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
    $("#translate-bar").style.width = "0%";
    $("#translate-msg").textContent = "Starting…";
    $("#translate-result").classList.add("hidden");
    $("#cache-warning").classList.add("hidden");
    $("#translate-job").classList.remove("hidden");
    $("#start-translate").disabled = true;
    pollJob(job.id, "#translate-bar", "#translate-msg", (done) => {
      $("#start-translate").disabled = false;
      const r = done.result;
      $("#translate-summary").textContent =
        `Done — ${r.input_tokens.toLocaleString()} in / ${r.output_tokens.toLocaleString()} out, est. cost $${r.cost.toFixed(2)}.`;
      $("#download-link").href = "/api/download/" + encodeURIComponent(r.target);
      if (r.cache_cleared) $("#cache-warning").classList.remove("hidden");
      $("#translate-result").classList.remove("hidden");
      refresh();
    }, (job) => {
      $("#start-translate").disabled = false;
      toast("Translation failed: " + job.error, "error");
    });
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
