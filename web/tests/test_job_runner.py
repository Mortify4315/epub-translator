import json

import pytest

import job_runner


def _build_epub(path, with_nav: bool) -> None:
    from ebooklib import epub
    book = epub.EpubBook()
    book.set_identifier("id-1234")
    book.set_title("Test Book")
    book.set_language("en")
    chapter = epub.EpubHtml(title="Chapter 1", file_name="chap1.xhtml", lang="en")
    chapter.content = "<html><body><p>Hello</p></body></html>"
    book.add_item(chapter)
    if with_nav:
        book.add_item(epub.EpubNav())
        book.spine = ["nav", chapter]
    else:
        book.add_item(epub.EpubNcx())
        book.toc = [chapter]
        book.spine = [chapter]
    epub.write_epub(path, book)


def test_count_headers_with_nav(tmp_path):
    path = tmp_path / "with_nav.epub"
    _build_epub(path, with_nav=True)
    assert job_runner.count_headers(path) == 2


def test_count_headers_without_nav(tmp_path):
    path = tmp_path / "without_nav.epub"
    _build_epub(path, with_nav=False)
    assert job_runner.count_headers(path) == 1


def test_progress_callback_chapter_counting(monkeypatch):
    recorded = []
    monkeypatch.setattr(job_runner, "_emit", recorded.append)
    on_progress, _ = job_runner.make_progress_callback(estimate_total=13, headers=2)
    fractions = [0.05, 0.10] + [0.10 + k * 0.9 / 13 for k in range(1, 14)]
    for frac in fractions:
        on_progress(frac)

    assert len(recorded) == 15
    expected_done = [0, 0] + list(range(1, 14))
    for event, frac, done in zip(recorded, fractions, expected_done):
        assert event["type"] == "progress"
        assert event["frac"] == pytest.approx(frac)
        assert event["chapters_done"] == done
        assert event["chapters_done"] <= event["chapters_total"]
        assert event["chapters_total"] <= 13

    assert recorded[-1]["frac"] >= 0.999
    assert recorded[-1]["chapters_done"] == 13
    assert recorded[-1]["chapters_total"] == 13
    assert recorded[-1]["msg"] == "Chapter 13/13 done"


def test_emit_log_builds_log_event(monkeypatch):
    recorded = []
    monkeypatch.setattr(job_runner, "_emit", recorded.append)
    job_runner.emit_log("info", "hello 世界")
    assert recorded == [{"type": "log", "level": "info", "msg": "hello 世界"}]


def test_emit_serializes_utf8_without_ascii_escaping(capsys):
    job_runner._emit({"type": "log", "level": "warn", "msg": "hello 世界"})
    out = capsys.readouterr().out.strip()
    assert '"hello 世界"' in out
    assert "\\u4e16" not in out


class _StopHeartbeat:
    def __init__(self):
        self.set_called = False

    def set(self):
        self.set_called = True


class _FakeLLM:
    def __init__(self, total_tokens=0):
        self.total_tokens = total_tokens
        self.input_tokens = 10
        self.output_tokens = 5


def _patch_run_env(monkeypatch, tmp_path, book_name="test_book.epub"):
    """Patch config dirs and peripheral functions so run_translate is hermetic."""
    books = tmp_path / "books"
    out = tmp_path / "out"
    cache = tmp_path / "cache"
    books.mkdir()
    (books / book_name).write_bytes(b"not-a-real-epub")

    monkeypatch.setattr(job_runner.core.config, "BOOKS_DIR", books)
    monkeypatch.setattr(job_runner.core.config, "OUT_DIR", out)
    monkeypatch.setattr(job_runner.core.config, "CACHE_DIR", cache)
    monkeypatch.setattr(job_runner.core.config, "get_api_key", lambda: "test-key")
    monkeypatch.setattr(job_runner.core.translate_book, "prepare_epub",
                        lambda src, work_dir: src)
    monkeypatch.setattr(job_runner.core.translate_book, "estimate",
                        lambda src: {"chapters": 5})
    monkeypatch.setattr(job_runner, "count_headers", lambda prepared: 1)
    monkeypatch.setattr(job_runner.core.glossary, "merge_glossaries", lambda key: {})
    monkeypatch.setattr(job_runner.core.glossary, "build_translation_prompt",
                        lambda glossary: "translate")
    stop = _StopHeartbeat()
    monkeypatch.setattr(job_runner, "start_heartbeat", lambda elapsed: stop)
    return books, out, cache, stop


def test_budget_exceeded_emits_error_deletes_target_keeps_cache(monkeypatch, tmp_path):
    books, out, cache, stop = _patch_run_env(monkeypatch, tmp_path)
    out.mkdir()
    target = out / "test_book.en.epub"
    target.write_bytes(b"partial output")

    monkeypatch.setattr(job_runner.core.config, "get_token_budget", lambda name: 100)
    monkeypatch.setattr(job_runner.core.translate_book, "LLM",
                        lambda **kwargs: _FakeLLM(total_tokens=200_000))

    def fake_translate(**kwargs):
        kwargs["on_progress"](0.5)

    monkeypatch.setattr(job_runner.core.translate_book, "translate", fake_translate)
    recorded = []
    monkeypatch.setattr(job_runner, "_emit", recorded.append)

    with pytest.raises(job_runner.core.translate_book.BudgetExceeded):
        job_runner.run_translate("test_book.epub")

    error_logs = [e for e in recorded if e["type"] == "log" and e["level"] == "error"]
    assert any("Budget exceeded: used 400000 of 100 tokens" in e["msg"] for e in error_logs)
    assert any("Cache kept; re-run to resume" in e["msg"] for e in error_logs)
    assert not target.exists()
    cache_path = cache / job_runner.core.glossary.book_key("test_book.epub")
    assert cache_path.is_dir()
    assert (cache_path / "config.json").is_file()
    assert stop.set_called


def test_retry_knobs_flow_into_llm_and_translate(monkeypatch, tmp_path):
    books, out, cache, stop = _patch_run_env(monkeypatch, tmp_path)
    llm_calls = []
    translate_calls = {}

    monkeypatch.setattr(job_runner.core.config, "get_retry_times", lambda: 3)
    monkeypatch.setattr(job_runner.core.config, "get_max_retries", lambda: 2)

    def fake_llm(**kwargs):
        llm_calls.append(kwargs)
        return _FakeLLM()

    monkeypatch.setattr(job_runner.core.translate_book, "LLM", fake_llm)
    monkeypatch.setattr(job_runner.core.translate_book, "translate",
                        lambda **kwargs: translate_calls.update(kwargs))
    recorded = []
    monkeypatch.setattr(job_runner, "_emit", recorded.append)

    job_runner.run_translate("test_book.epub")

    assert len(llm_calls) == 2
    assert all(c["retry_times"] == 3 for c in llm_calls)
    assert all(c["retry_interval_seconds"] == 6.0 for c in llm_calls)
    assert translate_calls["max_retries"] == 2


def test_cache_config_marker_includes_max_group_tokens(monkeypatch, tmp_path):
    books, out, cache, stop = _patch_run_env(monkeypatch, tmp_path)
    monkeypatch.setattr(job_runner.core.config, "get_max_group_tokens", lambda: 4321)
    monkeypatch.setattr(job_runner.core.translate_book, "LLM",
                        lambda **kwargs: _FakeLLM())
    monkeypatch.setattr(job_runner.core.translate_book, "translate", lambda **kwargs: None)
    recorded = []
    monkeypatch.setattr(job_runner, "_emit", recorded.append)

    job_runner.run_translate("test_book.epub")

    cache_path = cache / job_runner.core.glossary.book_key("test_book.epub")
    marker = json.loads((cache_path / "config.json").read_text(encoding="utf-8"))
    assert marker["max_group_tokens"] == 4321
