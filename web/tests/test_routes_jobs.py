import threading
import time

import core_loader
import jobs


def wait_until(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


class FakeProc:
    def __init__(self):
        self.pid = 9999
        self.terminated = False

    def terminate(self):
        self.terminated = True


def blocked_lines():
    """Lines that emit one log then block forever."""
    yield '{"type":"log","level":"info","msg":"working..."}'
    threading.Event().wait()


def result_lines():
    yield '{"type":"log","level":"info","msg":"start"}'
    yield '{"type":"progress","frac":0.5,"chapters_done":3,"chapters_total":13,"msg":"Chapter 3/13 done"}'
    yield '{"type":"result","result":{"target":"book.en.epub","cost":0.01}}'


def scan_result_lines():
    yield '{"type":"log","level":"info","msg":"start"}'
    yield '{"type":"result","result":{"candidates":{"丹田":5},"fresh":{"丹田":"dantian"}}}'


def two_log_lines_then_result():
    yield '{"type":"log","level":"info","msg":"first"}'
    yield '{"type":"log","level":"info","msg":"second"}'
    yield '{"type":"result","result":{"target":"book.en.epub","cost":0.01}}'


def patch_spawn(monkeypatch, lines):
    proc = FakeProc()
    monkeypatch.setattr(jobs, "default_spawn", lambda kind, book_name: (proc, lines()))
    return proc


def stop_and_wait(client, job_id):
    resp = client.post(f"/api/jobs/{job_id}/stop")
    assert wait_until(lambda: jobs.manager.get(job_id).status in ("stopped", "done", "error"))
    return resp


def test_translate_requires_api_key(client, sandbox):
    (sandbox["books"] / "book.epub").write_bytes(b"x")
    resp = client.post("/api/translate", json={"book": "book.epub"})
    assert resp.status_code == 400


def test_translate_starts_and_completes(client, sandbox, monkeypatch):
    (sandbox["books"] / "book.epub").write_bytes(b"x")
    monkeypatch.setattr(core_loader.config, "get_api_key", lambda: "sk-test")
    patch_spawn(monkeypatch, result_lines)
    resp = client.post("/api/translate", json={"book": "book.epub"})
    assert resp.status_code == 200
    job = resp.get_json()
    assert job["kind"] == "translate"
    assert wait_until(lambda: jobs.manager.get(job["id"]).status == "done")
    got = client.get(f"/api/jobs/{job['id']}").get_json()
    assert got["status"] == "done"
    assert got["result"]["target"] == "book.en.epub"
    assert got["chapters_total"] == 13


def test_translate_409_when_busy(client, sandbox, monkeypatch):
    (sandbox["books"] / "book.epub").write_bytes(b"x")
    monkeypatch.setattr(core_loader.config, "get_api_key", lambda: "sk-test")
    patch_spawn(monkeypatch, blocked_lines)
    first = client.post("/api/translate", json={"book": "book.epub"})
    assert first.status_code == 200
    first_id = first.get_json()["id"]
    second = client.post("/api/translate", json={"book": "book.epub"})
    assert second.status_code == 409
    stop_and_wait(client, first_id)


def test_scan_starts_job(client, sandbox, monkeypatch):
    (sandbox["books"] / "book.epub").write_bytes(b"x")
    monkeypatch.setattr(core_loader.config, "get_api_key", lambda: "sk-test")
    patch_spawn(monkeypatch, scan_result_lines)
    resp = client.post("/api/scan", json={"book": "book.epub"})
    assert resp.status_code == 200
    job = resp.get_json()
    assert job["kind"] == "scan"
    assert wait_until(lambda: jobs.manager.get(job["id"]).status == "done")
    got = client.get(f"/api/jobs/{job['id']}").get_json()
    assert got["result"]["candidates"]["丹田"] == 5
    assert got["result"]["fresh"]["丹田"] == "dantian"


def test_stop_endpoint(client, sandbox, monkeypatch):
    (sandbox["books"] / "book.epub").write_bytes(b"x")
    monkeypatch.setattr(core_loader.config, "get_api_key", lambda: "sk-test")
    patch_spawn(monkeypatch, blocked_lines)
    resp = client.post("/api/translate", json={"book": "book.epub"})
    assert resp.status_code == 200
    job_id = resp.get_json()["id"]
    stopped = client.post(f"/api/jobs/{job_id}/stop")
    assert stopped.status_code == 200
    assert stopped.get_json()["status"] == "stopped"
    again = client.post(f"/api/jobs/{job_id}/stop")
    assert again.status_code == 409
    got = client.get(f"/api/jobs/{job_id}").get_json()
    assert got["status"] == "stopped"
    assert any("Stopped by user" in e["msg"] for e in jobs.manager.get(job_id).log)


def test_stop_unknown_404(client):
    assert client.post("/api/jobs/ffffff/stop").status_code == 404


def test_current_job_endpoint(client, sandbox, monkeypatch):
    # A fresh page must be able to adopt a job started elsewhere.
    jobs.manager._current = None  # reset shared manager state (tests share it)
    (sandbox["books"] / "book.epub").write_bytes(b"x")
    monkeypatch.setattr(core_loader.config, "get_api_key", lambda: "sk-test")
    patch_spawn(monkeypatch, blocked_lines)
    assert client.get("/api/jobs/current").status_code == 404
    resp = client.post("/api/translate", json={"book": "book.epub"})
    job_id = resp.get_json()["id"]
    cur = client.get("/api/jobs/current")
    assert cur.status_code == 200
    data = cur.get_json()
    assert data["id"] == job_id
    assert data["status"] == "running"
    assert data["kind"] == "translate"
    # leave no busy job behind for other tests
    client.post(f"/api/jobs/{job_id}/stop")


def test_log_endpoint_incremental(client, sandbox, monkeypatch):
    (sandbox["books"] / "book.epub").write_bytes(b"x")
    monkeypatch.setattr(core_loader.config, "get_api_key", lambda: "sk-test")
    patch_spawn(monkeypatch, two_log_lines_then_result)
    resp = client.post("/api/translate", json={"book": "book.epub"})
    assert resp.status_code == 200
    job_id = resp.get_json()["id"]
    assert wait_until(lambda: jobs.manager.get(job_id).status == "done")
    full = client.get(f"/api/jobs/{job_id}/log?after=0").get_json()
    assert full["total"] >= 2
    msgs = [e["msg"] for e in full["entries"]]
    assert "first" in msgs
    assert "second" in msgs
    rest = client.get(f"/api/jobs/{job_id}/log?after=1").get_json()
    assert len(rest["entries"]) == rest["total"] - 1
    past = client.get(f"/api/jobs/{job_id}/log?after=999999").get_json()
    assert past["entries"] == []
    assert client.get(f"/api/jobs/{job_id}/log?after=abc").status_code == 400


def test_job_unknown_id_404(client):
    assert client.get("/api/jobs/ffffff").status_code == 404
    assert client.get("/api/jobs/ffffff/log").status_code == 404
