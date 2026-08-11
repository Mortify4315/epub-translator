import json

import core_loader


def test_qa_runs_check(client, sandbox, monkeypatch):
    (sandbox["books"] / "a.epub").write_bytes(b"x")
    (sandbox["out"] / "a.en.epub").write_bytes(b"x")
    monkeypatch.setattr(core_loader.qa_check, "check",
                        lambda s, t: [("道", "Dao", 3, "dao", 2)])
    data = client.post("/api/qa", json={"source": "a.epub", "target": "a.en.epub"}).get_json()
    assert data["count"] == 1 and data["issues"][0][0] == "道"


def test_qa_404_when_missing(client, sandbox):
    assert client.post("/api/qa", json={"source": "nope.epub", "target": "nope.en.epub"}).status_code == 404


def test_scan_accept_adds_terms(client, sandbox):
    (sandbox["glossaries"] / "global.json").write_text("{}", encoding="utf-8")
    resp = client.post("/api/scan/accept", json={"scope": "global", "terms": {"丹田": "dantian"}})
    assert resp.get_json()["added"] == 1
    assert (sandbox["glossaries"] / "global.json").read_text(encoding="utf-8").find("丹田") != -1


def test_settings_never_leaks_key(client, sandbox, monkeypatch):
    monkeypatch.setattr(core_loader.config, "get_api_key", lambda: "sk-abcdef1234567890wxyz")
    data = client.get("/api/settings").get_json()
    assert "abcdef1234567890wxyz" not in json.dumps(data)
    assert data["api_key_set"] is True
    assert "sk-abc" in data["api_key_masked"]


def test_settings_update_writes_and_masks(client, sandbox):
    resp = client.post("/api/settings", json={"api_key": "sk-12345678901234567890", "thinking": "enabled"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "12345678901234567890" not in json.dumps(data)
    assert data["api_key_set"] is True
    assert data["thinking"] == "enabled"


def test_settings_provider_roundtrip(client, sandbox):
    resp = client.post("/api/settings", json={"provider": "opencode-go", "model": "kimi-k3"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["provider"] == "opencode-go"
    assert data["model"] == "kimi-k3"
    assert data["thinking_supported"] is True
    assert data["env_key"] == "OPENCODE_GO_API_KEY"
    assert data["base_url"] == "https://opencode.ai/zen/go/v1"
    # back to deepseek
    resp = client.post("/api/settings", json={"provider": "deepseek", "model": "deepseek-v4-flash"})
    data = resp.get_json()
    assert data["provider"] == "deepseek"
    assert data["base_url"] == "https://api.deepseek.com"
    assert "deepseek-v4-flash" in data["models"]


def test_settings_unknown_provider_rejected(client, sandbox):
    resp = client.post("/api/settings", json={"provider": "not-a-provider"})
    assert resp.status_code == 400


def test_settings_bad_base_url_rejected(client, sandbox):
    resp = client.post("/api/settings", json={"base_url": "ftp://nope"})
    assert resp.status_code == 400


def test_settings_key_saved_to_provider_slot(client, sandbox):
    client.post("/api/settings", json={"provider": "opencode-go"})
    resp = client.post("/api/settings", json={"provider": "opencode-go", "api_key": "go-12345678901234567890"})
    assert resp.status_code == 200
    settings = core_loader.config.load_settings()
    assert settings["provider_keys"]["opencode-go"] == "go-12345678901234567890"
    # deepseek slot untouched
    assert "deepseek" not in settings["provider_keys"]


def test_settings_model_any_value_allowed(client, sandbox):
    resp = client.post("/api/settings", json={"model": "some-brand-new-model-x"})
    assert resp.status_code == 200
    assert resp.get_json()["model"] == "some-brand-new-model-x"


def test_settings_custom_provider_requires_base_url(client, sandbox):
    resp = client.post("/api/settings", json={"provider": "custom"})
    assert resp.status_code == 400
    assert "base_url" in resp.get_json()["error"]


def test_settings_custom_provider_accepted_with_base_url(client, sandbox):
    resp = client.post("/api/settings", json={
        "provider": "custom", "base_url": "https://my-relay.example/v1",
        "model": "my-model"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["provider"] == "custom"
    assert data["base_url"] == "https://my-relay.example/v1"


def test_settings_concurrency_accepts_32(client, sandbox):
    # The speed-experiment value (32) must survive a web save (regression for
    # the old silent 32 -> 16 clamp).
    resp = client.post("/api/settings", json={"concurrency": 32})
    assert resp.status_code == 200
    assert resp.get_json()["concurrency"] == 32


def test_settings_concurrency_clamped_at_max(client, sandbox):
    resp = client.post("/api/settings", json={"concurrency": 999})
    assert resp.status_code == 200
    assert resp.get_json()["concurrency"] == 64


def test_settings_chapter_limit_roundtrip(client, sandbox):
    resp = client.post("/api/settings", json={"chapter_limit": 400})
    assert resp.status_code == 200
    assert resp.get_json()["chapter_limit"] == 400
    # negative clamps to 0 (unlimited), like the concurrency clamp
    resp = client.post("/api/settings", json={"chapter_limit": -1})
    assert resp.status_code == 200
    assert resp.get_json()["chapter_limit"] == 0
    resp = client.post("/api/settings", json={"chapter_limit": 0})
    assert resp.status_code == 200
    assert resp.get_json()["chapter_limit"] == 0
