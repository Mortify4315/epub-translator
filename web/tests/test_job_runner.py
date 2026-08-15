import json
import sys
import types

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


def test_progress_callback_chapter_limit(monkeypatch):
    # Batch mode: raising the limit stops the run with the partial epub kept.
    recorded = []
    monkeypatch.setattr(job_runner, "_emit", recorded.append)
    on_progress, _ = job_runner.make_progress_callback(
        estimate_total=13, headers=2, chapter_limit=3)
    fractions = [0.05, 0.10] + [0.10 + k * 0.9 / 13 for k in range(1, 14)]
    raised = None
    for frac in fractions:
        try:
            on_progress(frac)
        except job_runner.core.translate_book.ChapterLimitReached as err:
            raised = err
            break
    assert raised is not None and raised.limit == 3
    assert recorded[-1]["chapters_done"] == 3
    assert recorded[-1]["msg"] == "Chapter 3/13 done"


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


def test_one_pass_cache_is_isolated_and_marker_captures_pipeline_identity(monkeypatch, tmp_path):
    books, out, cache, stop = _patch_run_env(monkeypatch, tmp_path)
    key = job_runner.core.glossary.book_key("test_book.epub")
    legacy_cache = cache / key
    legacy_cache.mkdir(parents=True)
    (legacy_cache / "config.json").write_text('{"legacy": true}', encoding="utf-8")
    (legacy_cache / "legacy-entry").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(job_runner.core.config, "get_pipeline", lambda: "one-pass")
    monkeypatch.setattr(job_runner.core.config, "get_strict_one_pass", lambda: True)
    monkeypatch.setattr(job_runner.core.config, "get_provider", lambda: "opencode-go")
    monkeypatch.setattr(job_runner.core.config, "get_base_url", lambda: "https://relay.example/v1")
    monkeypatch.setattr(job_runner.core.config, "get_model", lambda: "deepseek-v4-flash")
    monkeypatch.setattr(job_runner.core.config, "get_extra_body",
                        lambda: {"thinking": {"type": "disabled"}})
    monkeypatch.setattr(job_runner.core.config, "get_max_group_tokens", lambda: 4321)
    monkeypatch.setattr(job_runner.core.translate_book, "get_provider", lambda: "opencode-go")
    monkeypatch.setattr(job_runner.core.translate_book, "get_base_url", lambda: "https://relay.example/v1")
    monkeypatch.setattr(job_runner.core.translate_book, "get_model", lambda: "deepseek-v4-flash")
    monkeypatch.setattr(job_runner.core.translate_book, "get_extra_body",
                        lambda: {"thinking": {"type": "disabled"}})
    monkeypatch.setattr(job_runner.core.translate_book, "get_max_group_tokens", lambda: 4321)
    monkeypatch.setattr(job_runner.core.translate_book, "LLM", lambda **kwargs: _FakeLLM())
    calls = []

    def fake_one_pass(**kwargs):
        calls.append(kwargs)
        kwargs["on_protocol_failed"]({"message": "duplicate index 2"})

    onepass = types.ModuleType("onepass")
    onepass.translate_one_pass = fake_one_pass
    monkeypatch.setitem(sys.modules, "onepass", onepass)
    events = []
    monkeypatch.setattr(job_runner, "_emit", events.append)

    job_runner.run_translate("test_book.epub")

    onepass_cache = legacy_cache / "pipelines" / "one-pass-v1"
    marker = json.loads((onepass_cache / "config.json").read_text(encoding="utf-8"))
    assert (legacy_cache / "legacy-entry").read_text(encoding="utf-8") == "keep"
    assert marker == {
        "base_url": "https://relay.example/v1",
        "glossary_prompt": "translate",
        "max_group_tokens": 4321,
        "model": "deepseek-v4-flash",
        "pipeline": "one-pass-v1",
        "provider": "opencode-go",
        "protocol_version": 1,
        "target_language": "English",
        "thinking": "disabled",
    }
    assert calls[0]["strict"] is True
    assert calls[0]["target_language"] == job_runner.core.translate_book.language.ENGLISH
    assert any("One-pass protocol fallback" in event["msg"] for event in events
               if event["type"] == "log")


def test_one_pass_marker_mismatch_only_clears_one_pass_namespace(monkeypatch, tmp_path):
    books, out, cache, stop = _patch_run_env(monkeypatch, tmp_path)
    key = job_runner.core.glossary.book_key("test_book.epub")
    legacy_cache = cache / key
    legacy_cache.mkdir(parents=True)
    (legacy_cache / "legacy-entry").write_text("keep", encoding="utf-8")
    onepass_cache = legacy_cache / "pipelines" / "one-pass-v1"
    onepass_cache.mkdir(parents=True)
    (onepass_cache / "config.json").write_text('{"protocol_version": 0}', encoding="utf-8")
    (onepass_cache / "stale-entry").write_text("discard", encoding="utf-8")
    monkeypatch.setattr(job_runner.core.config, "get_pipeline", lambda: "one-pass")
    monkeypatch.setattr(job_runner.core.config, "get_provider", lambda: "deepseek")
    monkeypatch.setattr(job_runner.core.config, "get_base_url", lambda: "https://api.deepseek.com")
    monkeypatch.setattr(job_runner.core.config, "get_model", lambda: "deepseek-v4-flash")
    monkeypatch.setattr(job_runner.core.translate_book, "get_provider", lambda: "deepseek")
    monkeypatch.setattr(job_runner.core.translate_book, "get_base_url", lambda: "https://api.deepseek.com")
    monkeypatch.setattr(job_runner.core.translate_book, "get_model", lambda: "deepseek-v4-flash")
    monkeypatch.setattr(job_runner.core.translate_book, "LLM", lambda **kwargs: _FakeLLM())
    onepass = types.ModuleType("onepass")
    onepass.translate_one_pass = lambda **kwargs: None
    monkeypatch.setitem(sys.modules, "onepass", onepass)

    job_runner.run_translate("test_book.epub")

    assert (legacy_cache / "legacy-entry").read_text(encoding="utf-8") == "keep"
    assert not (onepass_cache / "stale-entry").exists()


def test_two_pass_marker_mismatch_preserves_one_pass_namespace(tmp_path):
    """Changing a legacy marker must not rmtree the nested one-pass cache."""
    cache = tmp_path / "cache"
    legacy_cache = cache / "book"
    onepass_cache = legacy_cache / "pipelines" / "one-pass-v1"
    onepass_cache.mkdir(parents=True)
    (onepass_cache / "cached-response").write_text("keep", encoding="utf-8")
    (legacy_cache / "config.json").write_text('{"obsolete": true}', encoding="utf-8")
    (legacy_cache / "legacy-response").write_text("discard", encoding="utf-8")

    cache_path, cache_cleared = job_runner.core.translate_book.prepare_pipeline_cache(
        "book", "two-pass", "translate", cache_dir=cache)

    assert cache_cleared is True
    assert cache_path == legacy_cache
    assert (onepass_cache / "cached-response").read_text(encoding="utf-8") == "keep"
    assert not (legacy_cache / "legacy-response").exists()
    assert (legacy_cache / "config.json").is_file()


def test_core_run_translation_dispatches_one_pass_with_its_fixed_contract(monkeypatch, tmp_path):
    core = job_runner.core.translate_book
    source = tmp_path / "book.epub"
    source.write_bytes(b"source")
    out = tmp_path / "out"
    cache = tmp_path / "cache"
    llm_calls = []
    onepass_calls = []
    monkeypatch.setattr(core, "OUT_DIR", out)
    monkeypatch.setattr(core, "CACHE_DIR", cache)
    monkeypatch.setattr(core, "validate_ready", lambda: [])
    monkeypatch.setattr(core, "get_api_key", lambda: "test-key")
    monkeypatch.setattr(core, "get_pipeline", lambda: "one-pass")
    monkeypatch.setattr(core, "get_strict_one_pass", lambda: True)
    monkeypatch.setattr(core, "get_provider", lambda: "deepseek")
    monkeypatch.setattr(core, "get_base_url", lambda: "https://api.deepseek.com")
    monkeypatch.setattr(core, "get_model", lambda: "deepseek-v4-flash")
    monkeypatch.setattr(core, "get_extra_body", lambda: {"thinking": {"type": "disabled"}})
    monkeypatch.setattr(core, "get_max_group_tokens", lambda: 4321)
    monkeypatch.setattr(core, "get_max_retries", lambda: 2)
    monkeypatch.setattr(core, "get_retry_times", lambda: 3)
    monkeypatch.setattr(core, "get_token_budget", lambda name: 1_000_000)
    monkeypatch.setattr(core, "get_chapter_limit", lambda: 0)
    monkeypatch.setattr(core, "merge_glossaries", lambda key: {})
    monkeypatch.setattr(core, "build_translation_prompt", lambda glossary: "translate")
    monkeypatch.setattr(core, "prepare_epub", lambda path, work_dir: path)
    monkeypatch.setattr(core, "estimate", lambda path: {"chapters": 1})
    monkeypatch.setattr(core, "count_headers", lambda path: 0)
    monkeypatch.setattr(core, "LLM", lambda **kwargs: llm_calls.append(kwargs) or _FakeLLM())
    monkeypatch.setattr(core, "load_one_pass_translator",
                        lambda: lambda **kwargs: onepass_calls.append(kwargs))

    result = core.run_translation(source)

    expected_cache = cache / "book" / "pipelines" / "one-pass-v1"
    assert len(llm_calls) == 1
    assert onepass_calls[0]["strict"] is True
    assert onepass_calls[0]["max_group_tokens"] == 4321
    assert (expected_cache / "config.json").is_file()
    assert result["target"] == out / "book.en.epub"
    assert result["pipeline"] == "one-pass"
    assert result["cost_experimental"] is True
