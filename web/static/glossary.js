let EDITING = null;

function initGlossary() {
  $("#scope-select").addEventListener("change", renderGlossary);
  $("#add-term-btn").addEventListener("click", () => $("#add-term-form").classList.remove("hidden"));
  $("#cancel-term-btn").addEventListener("click", () => $("#add-term-form").classList.add("hidden"));
  $("#save-term-btn").addEventListener("click", saveTerm);
  renderGlossary();
}

async function renderGlossary() {
  const scope = $("#scope-select").value;
  let scopes;
  try { scopes = await api("/api/glossary"); }
  catch (e) { toast(e.message, "error"); return; }
  const s = scopes.find(x => x.key === scope) || { terms: {} };
  const tbody = $("#term-table").querySelector("tbody");
  tbody.innerHTML = "";
  const entries = Object.entries(s.terms).sort((a, b) => a[0].localeCompare(b[0]));
  for (const [src, dst] of entries) {
    const tr = el("tr");
    tr.appendChild(el("td", null, src));
    tr.appendChild(el("td", null, dst));
    const td = el("td", "actions");
    const editBtn = el("button", "btn small", "Edit");
    editBtn.addEventListener("click", () => editTerm(scope, src, dst));
    const delBtn = el("button", "btn small danger", "Delete");
    delBtn.addEventListener("click", () => deleteTerm(scope, src));
    td.append(editBtn, delBtn);
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
}

function editTerm(scope, src, dst) {
  EDITING = { scope, src };
  $("#new-src").value = src;
  $("#new-dst").value = dst;
  $("#new-src").disabled = true;
  $("#add-term-form").classList.remove("hidden");
}

async function saveTerm() {
  const scope = $("#scope-select").value;
  const src = $("#new-src").value.trim();
  const dst = $("#new-dst").value.trim();
  if (!src || !dst) { toast("Both fields are required", "error"); return; }
  try {
    if (EDITING) {
      await api("/api/glossary/" + encodeURIComponent(scope) + "/term/" + encodeURIComponent(EDITING.src),
        { method: "PUT", body: { src, dst } });
      toast("Term updated");
    } else {
      await api("/api/glossary/" + encodeURIComponent(scope) + "/term",
        { method: "POST", body: { src, dst } });
      toast("Term added");
    }
  } catch (e) { toast(e.message, "error"); }
  EDITING = null;
  $("#new-src").value = ""; $("#new-dst").value = ""; $("#new-src").disabled = false;
  $("#add-term-form").classList.add("hidden");
  renderGlossary();
}

async function deleteTerm(scope, src) {
  if (!confirm("Delete '" + src + "'?")) return;
  try {
    await api("/api/glossary/" + encodeURIComponent(scope) + "/term/" + encodeURIComponent(src),
      { method: "DELETE", body: {} });
    toast("Term deleted");
  } catch (e) { toast(e.message, "error"); }
  renderGlossary();
}
