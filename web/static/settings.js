function initSettings() {
  $("#save-key").addEventListener("click", saveKey);
  $("#save-settings").addEventListener("click", saveSettings);
}

async function saveKey() {
  const key = $("#setting-key").value.trim();
  if (!key) { toast("Enter a key", "error"); return; }
  try {
    const s = await api("/api/settings", { method: "POST", body: { api_key: key } });
    $("#setting-key").value = "";
    STATE.settings = s;
    renderSettings(s);
    updateButtons();
    toast("API key saved");
  } catch (e) { toast(e.message, "error"); }
}

async function saveSettings() {
  const body = {
    model: $("#setting-model").value,
    concurrency: parseInt($("#setting-concurrency").value, 10),
    thinking: $("#setting-thinking").value,
    fill_thinking: $("#setting-fill").value,
  };
  try {
    const s = await api("/api/settings", { method: "POST", body });
    STATE.settings = s;
    renderSettings(s);
    toast("Settings saved. Changing translate/fill mode clears the book's translation cache.");
  } catch (e) { toast(e.message, "error"); }
}
