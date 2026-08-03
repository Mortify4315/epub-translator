import threading
import uuid

import core_loader as core


class Job:
    def __init__(self, kind, book_name):
        self.id = uuid.uuid4().hex[:8]
        self.kind = kind
        self.book_name = book_name
        self.status = "running"
        self.progress = 0.0
        self.message = ""
        self.result = None
        self.error = None

    def update(self, progress=None, message=None):
        if progress is not None:
            self.progress = max(0.0, min(100.0, float(progress)))
        if message is not None:
            self.message = message

    def finish(self, result=None):
        self.status = "done"
        self.progress = 100.0
        self.result = result

    def fail(self, error):
        self.status = "error"
        self.error = error

    def to_dict(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "book_name": self.book_name,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "result": self.result,
            "error": self.error,
        }


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

    def start(self, kind, book_name, worker):
        with self._lock:
            if self.busy():
                raise RuntimeError("Another job is already running. Wait for it to finish.")
            job = Job(kind, book_name)
            self._current = job

        def run():
            try:
                worker(job)
            except Exception as exc:
                job.fail(str(exc))

        threading.Thread(target=run, daemon=True).start()
        return job


manager = JobManager()


def run_translate(job, book_name):
    book = core.config.BOOKS_DIR / book_name
    job.update(progress=1, message="Preparing EPUB…")

    def on_progress(frac):
        job.update(progress=frac * 100, message="Translating…")

    result = core.translate_book.run_translation(book, on_progress=on_progress)
    job.finish(result={
        "target": result["target"].name,
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "cost": result["cost"],
        "cache_cleared": result["cache_cleared"],
    })


def run_scan(job, book_name):
    book = core.config.BOOKS_DIR / book_name
    key = core.glossary.book_key(book_name)
    job.update(progress=10, message="Extracting chapter text…")
    candidates = core.scan_glossary.candidate_terms(book, min_count=5, max_terms=60)
    if not candidates:
        job.finish(result={"candidates": {}, "fresh": {}})
        return
    job.update(progress=50, message=f"Proposing translations for {len(candidates)} terms…")
    proposed = core.scan_glossary.propose_translations(
        list(candidates.keys()),
        core.config.get_api_key(),
        core.config.get_base_url(),
        core.config.get_model(),
    )
    existing = core.glossary.merge_glossaries(key)
    fresh = {src: dst for src, dst in proposed.items() if src not in existing}
    job.finish(result={"candidates": candidates, "fresh": fresh})
