import json
import threading
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


class FakeProc:
    def __init__(self):
        self.pid = 9999
        self.terminated = False
        self.returncode = 0
        self.stderr = None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return self.returncode


def blocking_lines():
    yield json.dumps({"type": "log", "level": "info", "msg": "started"})
    threading.Event().wait()


def test_apply_event_parses_progress_log_result():
    job = jobs.Job("translate", "book.epub")
    jobs.apply_event(
        job,
        json.dumps({
            "type": "progress", "frac": 0.42,
            "chapters_done": 3, "chapters_total": 10, "msg": "working",
        }),
    )
    assert job.chapters_done == 3
    assert job.chapters_total == 10
    assert job.progress == 42.0
    assert job.message == "working"
    assert job.status == "running"

    jobs.apply_event(job, json.dumps({"type": "log", "level": "warn", "msg": "slow"}))
    assert len(job.log) == 1
    assert job.log[0]["level"] == "warn"
    assert job.log[0]["msg"] == "slow"

    jobs.apply_event(job, json.dumps({"type": "result", "result": {"ok": True}}))
    assert job.status == "done"
    assert job.result == {"ok": True}
    assert job.progress == 100.0


def test_apply_event_error():
    job = jobs.Job("translate", "book.epub")
    jobs.apply_event(job, json.dumps({"type": "error", "error": "boom"}))
    assert job.status == "error"
    assert job.error == "boom"


def test_apply_event_ignores_garbage():
    job = jobs.Job("translate", "book.epub")
    jobs.apply_event(job, "not json")
    jobs.apply_event(job, "{broken json")
    assert job.status == "running"
    assert job.error is None


def test_manager_processes_spawned_lines():
    lines = [
        json.dumps({"type": "progress", "frac": 0.5, "chapters_done": 5,
                    "chapters_total": 10, "msg": "half"}),
        json.dumps({"type": "log", "level": "info", "msg": "note"}),
        json.dumps({"type": "result", "result": {"target": "out.epub"}}),
    ]

    def fake_spawn(kind, book_name):
        return FakeProc(), iter(lines)

    job = jobs.manager.start("translate", "book.epub", spawn=fake_spawn)
    assert wait_until(lambda: jobs.manager.get(job.id).status == "done")
    assert jobs.manager.get(job.id).result == {"target": "out.epub"}
    d = jobs.manager.get(job.id).to_dict()
    assert d["chapters_done"] == 5
    assert d["chapters_total"] == 10
    for key in ("started_at", "last_event_at", "stopped"):
        assert key in d


def test_unexpected_exit_marks_error():
    lines = [json.dumps({"type": "log", "level": "info", "msg": "before exit"})]

    def fake_spawn(kind, book_name):
        return FakeProc(), iter(lines)

    job = jobs.manager.start("translate", "book.epub", spawn=fake_spawn)
    assert wait_until(lambda: jobs.manager.get(job.id).status == "error")
    assert "ended unexpectedly" in jobs.manager.get(job.id).error
    assert len(jobs.manager.get(job.id).log) == 1


def test_stop_terminates_and_marks_stopped():
    def fake_spawn(kind, book_name):
        return FakeProc(), blocking_lines()

    job = jobs.manager.start("translate", "book.epub", spawn=fake_spawn)
    assert wait_until(lambda: jobs.manager.get(job.id).status == "running"
                      and jobs.manager.get(job.id).log)
    jobs.manager.stop(job.id)
    stopped = jobs.manager.get(job.id)
    assert stopped.status == "stopped"
    assert stopped.stopped is True
    assert stopped._proc.terminated is True
    assert any("Stopped by user" in entry["msg"] for entry in stopped.log)


def test_busy_rejects_second_job():
    def fake_spawn(kind, book_name):
        return FakeProc(), blocking_lines()

    first = jobs.manager.start("translate", "a.epub", spawn=fake_spawn)
    assert wait_until(lambda: jobs.manager.get(first.id).status == "running")
    with pytest.raises(RuntimeError):
        jobs.manager.start("scan", "b.epub", spawn=fake_spawn)
    jobs.manager.stop(first.id)


def test_stop_unknown_raises_KeyError():
    with pytest.raises(KeyError):
        jobs.manager.stop("nope")


def test_stop_not_running_raises():
    def fake_spawn(kind, book_name):
        return FakeProc(), iter([json.dumps({"type": "result", "result": {}})])

    job = jobs.manager.start("translate", "book.epub", spawn=fake_spawn)
    assert wait_until(lambda: jobs.manager.get(job.id).status == "done")
    with pytest.raises(RuntimeError):
        jobs.manager.stop(job.id)


def test_stopped_job_ignores_late_result_event():
    gate = threading.Event()
    consumed = {"late": False}

    def fake_spawn(kind, book_name):
        def lines():
            yield json.dumps({"type": "log", "level": "info", "msg": "started"})
            gate.wait(timeout=5)
            consumed["late"] = True
            yield json.dumps({"type": "result", "result": {"late": True}})

        return FakeProc(), lines()

    job = jobs.manager.start("translate", "book.epub", spawn=fake_spawn)
    assert wait_until(lambda: jobs.manager.get(job.id).log)
    jobs.manager.stop(job.id)
    gate.set()
    # Let the reader thread actually consume the late result line.
    assert wait_until(lambda: consumed["late"])
    time.sleep(0.1)
    stopped = jobs.manager.get(job.id)
    assert stopped.status == "stopped"
    assert stopped.stopped is True
    assert stopped.result is None


def test_child_stderr_captured_on_crash():
    class CrashingProc(FakeProc):
        def __init__(self):
            super().__init__()
            self.returncode = 1

    def fake_spawn(kind, book_name):
        proc = CrashingProc()
        proc.stderr = iter([
            "Traceback (most recent call last):\n",
            "  File \"job_runner.py\", line 42, in run\n",
            "RuntimeError: boom\n",
        ])
        return proc, iter([json.dumps({"type": "log", "level": "info", "msg": "started"})])

    job = jobs.manager.start("translate", "book.epub", spawn=fake_spawn)
    assert wait_until(lambda: jobs.manager.get(job.id).status == "error")
    err = jobs.manager.get(job.id).error
    assert "ended unexpectedly" in err
    assert "RuntimeError: boom" in err
    assert "".join(job._stderr) == (
        "Traceback (most recent call last):\n"
        "  File \"job_runner.py\", line 42, in run\n"
        "RuntimeError: boom\n"
    )
