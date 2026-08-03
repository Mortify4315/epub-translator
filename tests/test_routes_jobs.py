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


def test_translate_requires_api_key(client, sandbox):
    (sandbox["books"] / "book.epub").write_bytes(b"x")
    resp = client.post("/api/translate", json={"book": "book.epub"})
    assert resp.status_code == 400


def test_translate_starts_and_completes_job(client, sandbox, monkeypatch):
    (sandbox["books"] / "book.epub").write_bytes(b"x")
    monkeypatch.setattr(core_loader.config, "get_api_key", lambda: "sk-test")
    monkeypatch.setattr(jobs, "run_translate", lambda job, name: (
        job.update(progress=50),
        job.finish(result={"target": "book.en.epub", "input_tokens": 10,
                           "output_tokens": 5, "cost": 0.01, "cache_cleared": False}),
    )[1])
    resp = client.post("/api/translate", json={"book": "book.epub"})
    assert resp.status_code == 200
    job = resp.get_json()
    assert job["kind"] == "translate"
    assert wait_until(lambda: jobs.manager.get(job["id"]).status == "done")
    got = client.get(f"/api/jobs/{job['id']}").get_json()
    assert got["status"] == "done"
    assert got["result"]["target"] == "book.en.epub"


def test_translate_409_when_busy(client, sandbox, monkeypatch):
    (sandbox["books"] / "book.epub").write_bytes(b"x")

    def slow(job, name):
        time.sleep(0.3)
        job.finish()

    monkeypatch.setattr(core_loader.config, "get_api_key", lambda: "sk-test")
    monkeypatch.setattr(jobs, "run_translate", slow)
    first = client.post("/api/translate", json={"book": "book.epub"})
    assert first.status_code == 200
    second = client.post("/api/translate", json={"book": "book.epub"})
    assert second.status_code == 409
    assert wait_until(lambda: jobs.manager.get(first.get_json()["id"]).status == "done")


def test_scan_starts_job(client, sandbox, monkeypatch):
    (sandbox["books"] / "book.epub").write_bytes(b"x")
    monkeypatch.setattr(core_loader.config, "get_api_key", lambda: "sk-test")
    monkeypatch.setattr(jobs, "run_scan", lambda job, name: job.finish(
        result={"candidates": {"丹田": 5}, "fresh": {"丹田": "dantian"}}))
    resp = client.post("/api/scan", json={"book": "book.epub"})
    assert resp.status_code == 200
    job = resp.get_json()
    assert job["kind"] == "scan"
    assert wait_until(lambda: jobs.manager.get(job["id"]).status == "done")


def test_job_unknown_id_404(client):
    assert client.get("/api/jobs/ffffff").status_code == 404
