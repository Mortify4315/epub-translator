function initQa() {
  $("#run-qa").addEventListener("click", runQa);
}

async function runQa() {
  const source = $("#qa-source").value;
  const target = $("#qa-target").value;
  if (!source || !target) return;
  try {
    const r = await api("/api/qa", { method: "POST", body: { source, target } });
    const out = $("#qa-result");
    out.classList.remove("hidden");
    out.innerHTML = "";
    if (!r.count) { out.appendChild(el("p", "ok", "No consistency issues found.")); return; }
    out.appendChild(el("p", "warn", r.count + " potential issue(s):"));
    const ul = el("ul");
    for (const [src, dst, ch, variant, count] of r.issues.slice(0, 200)) {
      ul.appendChild(el("li", null, `Ch ${ch}: '${src}' expected '${dst}' but may appear as '${variant}' (x${count})`));
    }
    out.appendChild(ul);
    out.appendChild(el("p", "hint", "If the variant is fine, add it to the glossary. If not, fix the glossary and re-translate."));
  } catch (e) { toast(e.message, "error"); }
}
