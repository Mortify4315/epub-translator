import time

import pytest

import jobs


def wait_until(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_job_runs_worker_and_finishes():
    mgr = jobs.JobManager()

    def worker(job):
        time.sleep(0.02)
        job.update(progress=50, message="halfway")
        job.finish(result={"ok": True})

    job = mgr.start("translate", "book.epub", worker)
    assert job.status == "running"
    assert wait_until(lambda: job.status == "done")
    assert job.progress == 100.0
    assert job.result == {"ok": True}
    assert job.to_dict()["status"] == "done"


def test_job_error_is_captured():
    mgr = jobs.JobManager()

    def worker(job):
        raise RuntimeError("boom")

    job = mgr.start("scan", "book.epub", worker)
    assert wait_until(lambda: job.status == "error")
    assert "boom" in job.error


def test_manager_rejects_second_job_while_running():
    mgr = jobs.JobManager()

    def slow(job):
        time.sleep(0.2)
        job.finish()

    job1 = mgr.start("translate", "a.epub", slow)
    with pytest.raises(RuntimeError):
        mgr.start("scan", "b.epub", slow)
    assert wait_until(lambda: job1.status == "done")


def test_get_only_returns_current_job():
    mgr = jobs.JobManager()

    def fast(job):
        job.finish()

    job1 = mgr.start("translate", "a.epub", fast)
    assert wait_until(lambda: job1.status == "done")
    assert mgr.get(job1.id) is job1
    assert mgr.get("nope") is None
