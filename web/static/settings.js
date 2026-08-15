function initSettings() {
  $("#save-key").addEventListener("click", saveKey);
  $("#save-settings").addEventListener("click", saveSettings);
  syncOnePassControls(STATE.settings);
}

function syncOnePassControls(s) {
  if (!s) return;
  $("#setting-pipeline").value = s.pipeline || "two-pass";
  $("#setting-strict-one-pass").checked = !!s.strict_one_pass;
}

async function saveKey() {
  const key = $("#setting-key").value.trim();
  if (!key) { toast("Enter a key", "error"); return; }
  try {
    const s = await api("/api/settings", {
      method: "POST",
      body: { api_key: key, provider: $("#setting-provider").value },
    });
    $("#setting-key").value = "";
    STATE.settings = s;
    renderSettings(s);
    updateButtons();
    toast("API key saved for " + s.provider_label);
  } catch (e) { toast(e.message, "error"); }
}

async function saveSettings() {
  const body = {
    provider: $("#setting-provider").value,
    model: $("#setting-model").value.trim(),
    base_url: $("#setting-base").value.trim(),
    concurrency: parseInt($("#setting-concurrency").value, 10),
    pipeline: $("#setting-pipeline").value,
    strict_one_pass: $("#setting-strict-one-pass").checked,
    thinking: $("#setting-thinking").value,
    fill_thinking: $("#setting-fill").value,
  };
  try {
    const s = await api("/api/settings", { method: "POST", body });
    STATE.settings = s;
    renderSettings(s);
    syncOnePassControls(s);
    toast("Settings saved. Pipeline cache is isolated; changing it preserves the sibling pipeline cache.");
  } catch (e) { toast(e.message, "error"); }
}
