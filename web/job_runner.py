"""Child-process job runner for the web GUI.

Mirrors translate_book.run_translation so we can forward on_fill_failed into
the job log. If the sibling run_translation gains new knobs, update this file
to match (the cli/ subfolder is treated as read-only input).
"""
import json
import shutil
import sys
import threading
import time
from pathlib import Path

import core_loader as core


def _emit(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def emit_log(level: str, msg: str) -> None:
    _emit({"type": "log", "level": level, "msg": msg})


def count_headers(prepared_epub: Path) -> int:
    """Number of pre-chapter on_progress calls the engine makes (TOC + metadata)."""
    from ebooklib import epub
    book = epub.read_epub(prepared_epub)
    headers = 0
    if any(isinstance(item, epub.EpubNav) for item in book.get_items()):
        headers += 1
    if book.metadata:
        headers += 1
    return headers


def make_progress_callback(estimate_total: int, headers: int):
    state = {"n": 0, "total": estimate_total, "last": time.monotonic()}

    def on_progress(frac: float) -> None:
        state["n"] += 1
        done = max(0, min(state["total"], state["n"] - headers))
        if frac >= 0.999:
            state["total"] = done
        _emit({"type": "progress", "frac": frac,
               "chapters_done": done, "chapters_total": state["total"],
               "msg": f"Chapter {done}/{state['total']} done"})
        state["last"] = time.monotonic()

    def elapsed_since_last() -> float:
        return time.monotonic() - state["last"]

    return on_progress, elapsed_since_last


def start_heartbeat(elapsed_since_last):
    stop = threading.Event()

    def run() -> None:
        while not stop.wait(60):
            secs = int(elapsed_since_last())
            if secs >= 60:
                emit_log("info", f"Still working… (no chapter completed in {secs}s)")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return stop


def run_translate(book_name: str) -> None:
    problems = core.config.validate_ready()
    if problems:
        raise RuntimeError("; ".join(problems))
    api_key = core.config.get_api_key()
    key = core.glossary.book_key(book_name)
    glossary = core.glossary.merge_glossaries(key)
    prompt = core.glossary.build_translation_prompt(glossary)
    source_path = core.config.BOOKS_DIR / book_name
    if not source_path.is_file():
        raise RuntimeError(f"Book not found: {book_name}")
    target_path = core.config.OUT_DIR / f"{source_path.stem}.en.epub"
    cache_path = core.config.CACHE_DIR / key

    emit_log("info", f"Translating {book_name}…")
    emit_log("info", f"Glossary: {len(glossary)} term(s)")
    source_for_translation = core.translate_book.prepare_epub(
        source_path, core.config.CACHE_DIR / "prep")
    budget = core.config.get_token_budget(book_name)

    config = {
        "provider": core.config.get_provider(),
        "base_url": core.config.get_base_url(),
        "thinking": core.config.get_extra_body().get("thinking", {}).get("type", "none"),
        "fill_thinking": core.config.get_fill_thinking(),
        "model": core.config.get_model(),
        "max_group_tokens": core.config.get_max_group_tokens(),
    }
    cache_cleared = False
    if cache_path.exists():
        marker = cache_path / "config.json"
        saved = None
        try:
            saved = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = None
        if saved != config:
            shutil.rmtree(cache_path, ignore_errors=True)
            cache_cleared = True
            emit_log("warn", "Translation cache cleared (provider/base URL/model or mode changed).")
    cache_path.mkdir(parents=True, exist_ok=True)
    (cache_path / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    def make_llm(extra_body):
        return core.translate_book.LLM(
            key=api_key,
            url=core.config.get_base_url(),
            model=core.config.get_model(),
            token_encoding=core.config.TOKEN_ENCODING,
            cache_path=str(cache_path),
            retry_times=core.config.get_retry_times(),
            retry_interval_seconds=6.0,
            temperature=0.4,
            extra_body=extra_body,
        )

    translation_llm = make_llm(core.config.get_extra_body())
    fill_llm = make_llm(core.config.get_fill_extra_body())

    est = core.translate_book.estimate(source_path)
    headers = count_headers(source_for_translation)
    chapters_total = est["chapters"]
    on_progress, elapsed_since_last = make_progress_callback(chapters_total, headers)
    stop_heartbeat = start_heartbeat(elapsed_since_last)

    def on_fill_failed(event):
        level = "warn" if not event.over_maximum_retries else "error"
        emit_log(level, f"Fill fallback (retried {event.retried_count}x): {event.error_message}")

    def budget_aware_progress(frac: float) -> None:
        used = translation_llm.total_tokens + fill_llm.total_tokens
        if used > budget:
            raise core.translate_book.BudgetExceeded(budget, used)
        on_progress(frac)

    emit_log("info", f"Translating {chapters_total} chapters (model {core.config.get_model()})…")
    try:
        core.translate_book.translate(
            source_path=str(source_for_translation),
            target_path=str(target_path),
            target_language=core.translate_book.language.ENGLISH,
            submit=core.translate_book.SubmitKind.REPLACE,
            user_prompt=prompt,
            max_retries=core.config.get_max_retries(),
            translation_llm=translation_llm,
            fill_llm=fill_llm,
            concurrency=core.config.get_concurrency(),
            max_group_tokens=core.config.get_max_group_tokens(),
            on_progress=budget_aware_progress,
            on_fill_failed=on_fill_failed,
        )
    except core.translate_book.BudgetExceeded as err:
        target_path.unlink(missing_ok=True)
        emit_log("error", f"Budget exceeded: used {err.used} of {err.budget} tokens — stopped. "
                          f"Cache kept; re-run to resume.")
        stop_heartbeat.set()
        raise
    stop_heartbeat.set()

    input_tokens = translation_llm.input_tokens + fill_llm.input_tokens
    output_tokens = translation_llm.output_tokens + fill_llm.output_tokens
    cost = core.config.estimate_cost(input_tokens, output_tokens)
    emit_log("info", f"Done — {input_tokens:,} in / {output_tokens:,} out, est. cost ${cost:.2f}")
    _emit({"type": "result", "result": {
        "target": target_path.name,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost": cost,
        "cache_cleared": cache_cleared,
    }})


def run_scan(book_name: str) -> None:
    problems = core.config.validate_ready()
    if problems:
        raise RuntimeError("; ".join(problems))
    api_key = core.config.get_api_key()
    source_path = core.config.BOOKS_DIR / book_name
    if not source_path.is_file():
        raise RuntimeError(f"Book not found: {book_name}")
    key = core.glossary.book_key(book_name)
    emit_log("info", f"Scanning {book_name} for candidate terms…")
    _emit({"type": "progress", "frac": 0.1, "chapters_done": 0, "chapters_total": 0,
           "msg": "Extracting chapter text…"})
    candidates = core.scan_glossary.candidate_terms(source_path, min_count=5, max_terms=60)
    if not candidates:
        emit_log("info", "No candidate terms found.")
        _emit({"type": "result", "result": {"candidates": {}, "fresh": {}}})
        return
    emit_log("info", f"Found {len(candidates)} candidates. Asking the model for translations…")
    _emit({"type": "progress", "frac": 0.5, "chapters_done": 0, "chapters_total": 0,
           "msg": f"Proposing translations for {len(candidates)} terms…"})
    proposed = core.scan_glossary.propose_translations(
        list(candidates.keys()), api_key,
        core.config.get_base_url(), core.config.get_model())
    existing = core.glossary.merge_glossaries(key)
    fresh = {src: dst for src, dst in proposed.items() if src not in existing}
    _emit({"type": "progress", "frac": 0.9, "chapters_done": 0, "chapters_total": 0,
           "msg": "Done scanning."})
    emit_log("info", f"Proposed {len(fresh)} new term(s).")
    _emit({"type": "result", "result": {"candidates": candidates, "fresh": fresh}})


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) != 3:
        _emit({"type": "error", "error": "usage: job_runner.py <translate|scan> <book_name>"})
        return 2
    kind, book_name = sys.argv[1], sys.argv[2]
    try:
        if kind == "translate":
            run_translate(book_name)
        elif kind == "scan":
            run_scan(book_name)
        else:
            raise RuntimeError(f"Unknown job kind: {kind}")
    except Exception as exc:
        _emit({"type": "error", "error": str(exc)})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
