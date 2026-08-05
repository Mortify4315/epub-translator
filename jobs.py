import json
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Job:
    def __init__(self, kind: str, book_name: str, lock=None):
        self.id = uuid.uuid4().hex[:8]
        self.kind = kind
        self.book_name = book_name
        self.status = "running"
        self.progress = 0.0
        self.message = ""
        self.result = None
        self.error = None
        self.chapters_done = 0
        self.chapters_total = 0
        self.stopped = False
        self.started_at = _now_iso()
        self.last_event_at = self.started_at
        self.log = []
        self._proc: subprocess.Popen | None = None
        self._lock = lock
        self._stderr = []
        self._stderr_thread: threading.Thread | None = None

    def update(self, progress=None, message=None):
        if progress is not None:
            self.progress = max(0.0, min(100.0, float(progress)))
        if message is not None:
            self.message = message
        self.last_event_at = _now_iso()

    def finish(self, result=None):
        self.status = "done"
        self.progress = 100.0
        self.result = result
        self.last_event_at = _now_iso()

    def fail(self, error):
        self.status = "error"
        self.error = error
        self.last_event_at = _now_iso()

    def _snapshot(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "book_name": self.book_name,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "chapters_done": self.chapters_done,
            "chapters_total": self.chapters_total,
            "stopped": self.stopped,
            "started_at": self.started_at,
            "last_event_at": self.last_event_at,
        }

    def to_dict(self):
        if self._lock is not None:
            with self._lock:
                return self._snapshot()
        return self._snapshot()


def default_spawn(kind: str, book_name: str):
    script = Path(__file__).resolve().parent / "job_runner.py"
    proc = subprocess.Popen(
        [sys.executable, str(script), kind, book_name],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    return proc, proc.stdout


def apply_event(job: Job, line: str) -> None:
    if job.stopped:
        return
    try:
        event = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return
    etype = event.get("type")
    if etype == "progress":
        job.chapters_done = int(event.get("chapters_done", 0))
        job.chapters_total = int(event.get("chapters_total", 0))
        job.update(progress=float(event.get("frac", 0.0)) * 100, message=event.get("msg", ""))
    elif etype == "log":
        job.log.append({"t": _now_iso(), "level": event.get("level", "info"), "msg": event.get("msg", "")})
        job.last_event_at = _now_iso()
    elif etype == "result":
        job.finish(result=event.get("result"))
    elif etype == "error":
        job.fail(event.get("error", "Unknown error"))


class JobManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._current = None

    def get(self, job_id):
        with self._lock:
            if self._current and self._current.id == job_id:
                return self._current
        return None

    def busy(self):
        with self._lock:
            return self._current is not None and self._current.status == "running"

    def start(self, kind, book_name, spawn=None):
        with self._lock:
            if self.busy():
                raise RuntimeError("Another job is already running. Wait for it to finish.")
            job = Job(kind, book_name, self._lock)
            self._current = job
        spawn = spawn or default_spawn
        proc, lines = spawn(kind, book_name)
        job._proc = proc
        stderr = getattr(proc, "stderr", None)
        if stderr is not None:
            job._stderr_thread = threading.Thread(
                target=self._drain_stderr, args=(job, stderr), daemon=True
            )
            job._stderr_thread.start()
        threading.Thread(target=self._read, args=(job, lines), daemon=True).start()
        return job

    def _drain_stderr(self, job, stream):
        # Runs independently of the stdout reader so a chatty child can never
        # deadlock the pipe: stderr is drained regardless of stdout progress.
        try:
            for chunk in stream:
                if not chunk:
                    continue
                job._stderr.append(chunk)
                total = sum(len(c) for c in job._stderr)
                while total > 4096:
                    total -= len(job._stderr.pop(0))
        except Exception:
            pass

    def _read(self, job, lines):
        try:
            for line in lines:
                if line and line.strip():
                    with self._lock:
                        apply_event(job, line)
        except Exception:
            pass
        if job._proc is not None:
            try:
                job._proc.wait(timeout=10)
            except Exception:
                pass
        with self._lock:
            if job.status == "running":
                error = "Job process ended unexpectedly."
                if job._proc is not None and job._proc.returncode not in (None, 0):
                    if job._stderr_thread is not None:
                        job._stderr_thread.join(timeout=1.0)
                    tail = "".join(job._stderr)[-200:] if job._stderr else ""
                    if tail:
                        error += f" Child stderr: {tail}"
                job.fail(error)

    def stop(self, job_id):
        with self._lock:
            job = self._current if self._current and self._current.id == job_id else None
            if job is None:
                raise KeyError(job_id)
            if job.status != "running":
                raise RuntimeError("Job is not running.")
            job.stopped = True
            job.status = "stopped"
            job.log.append({"t": _now_iso(), "level": "info",
                            "msg": "Stopped by user — re-run resumes from cache."})
            job.last_event_at = _now_iso()
        if job._proc is not None:
            try:
                job._proc.terminate()
            except Exception:
                pass
            try:
                job._proc.wait(timeout=5)
            except Exception:
                pass


manager = JobManager()
