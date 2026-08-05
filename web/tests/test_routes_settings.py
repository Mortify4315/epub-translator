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
