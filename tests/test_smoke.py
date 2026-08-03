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
