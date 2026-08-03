import app as server


def test_index_serves_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"<html" in resp.data.lower()


def test_ping_reports_core(client):
    resp = client.get("/api/ping")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert (server.core.config.BASE_DIR / "translate_book.py").exists()


def test_bootstrap(client, sandbox):
    (sandbox["books"] / "a.epub").write_bytes(b"x")
    (sandbox["glossaries"] / "global.json").write_text('{"道": "Dao"}', encoding="utf-8")
    data = client.get("/api/bootstrap").get_json()
    assert data["books"][0]["name"] == "a.epub"
    assert data["books"][0]["key"] == "a"
    assert data["out"] == []
    assert data["glossaries"][0]["key"] == "global"
    assert data["glossaries"][0]["count"] == 1
    assert data["settings"]["api_key_set"] is False
