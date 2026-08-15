import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import benchmark_onepass as bench


def _epub_bytes(*, translated=False, missing_media=False):
    body = (
        "<html><body>"
        "<h1>Chapter</h1>"
        f"<p>{'First translated paragraph.' if translated else '第一段。'}</p>"
        f"<p>{'Second translated paragraph.' if translated else '第二段。'}</p>"
        "</body></html>"
    )
    entries = {
        "mimetype": b"application/epub+zip",
        "META-INF/container.xml": b"<container/>\n",
        "OEBPS/content.opf": b"<package><manifest/></package>\n",
        "OEBPS/nav.xhtml": b"<html><body><nav/></body></html>\n",
        "OEBPS/styles.css": b"body { color: black; }\n",
        "OEBPS/chapter.xhtml": body.encode("utf-8"),
    }
    if not missing_media:
        entries["OEBPS/images/cover.jpg"] = b"fake-cover-bytes"
    from io import BytesIO

    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("mimetype", entries.pop("mimetype"), compress_type=zipfile.ZIP_STORED)
        for name, content in entries.items():
            archive.writestr(name, content)
    return output.getvalue()


def _write_source(path):
    path.write_bytes(_epub_bytes())


def test_run_benchmark_sandboxes_paths_and_collects_fake_telemetry(tmp_path):
    source = tmp_path / "source.epub"
    _write_source(source)
    observed = []

    def fake_runner(job, collector):
        observed.append(job)
        assert job.source_path.parent == job.sandbox.books_dir
        assert job.cache_dir.parent == job.sandbox.cache_dir
        assert job.output_path.parent == job.sandbox.out_dir
        job.output_path.write_bytes(_epub_bytes(translated=True))
        collector.record_usage(
            fresh_input_tokens=100,
            cached_input_tokens=20,
            output_tokens=40,
            reasoning_tokens=10,
        )
        collector.record_request()
        collector.record_blocks(expected=2, translated=2)
        return {"group_count": 10}

    result = bench.run_benchmark(
        source,
        baseline_pipeline="two-pass",
        candidate_pipeline="one-pass",
        token_budget=1_000,
        settings=bench.PinnedSettings(
            provider="fake", model="fake-model", base_url="https://fake.invalid"
        ),
        runners={"two-pass": fake_runner, "one-pass": fake_runner},
        sandbox_root=tmp_path / "sandbox",
    )

    assert len(observed) == 2
    assert all(job.settings.model == "fake-model" for job in observed)
    assert all(job.settings.provider == "fake" for job in observed)
    assert all(job.output_path.parent == result.sandbox.out_dir for job in observed)
    assert result.baseline.metrics.fresh_input_tokens == 100
    assert result.baseline.metrics.cached_input_tokens == 20
    assert result.baseline.metrics.output_tokens == 40
    assert result.baseline.metrics.reasoning_tokens == 10
    assert result.baseline.metrics.total_tokens == 160
    assert result.candidate.metrics.request_count == 1
    assert result.candidate.metrics.valid_archive is True
    assert not (tmp_path / "settings.json").exists()
    assert result.sandbox.settings_file.exists()


def test_archive_inventory_detects_removed_media_and_invalid_archive(tmp_path):
    source = tmp_path / "source.epub"
    candidate = tmp_path / "candidate.epub"
    invalid = tmp_path / "invalid.epub"
    source.write_bytes(_epub_bytes())
    candidate.write_bytes(_epub_bytes(missing_media=True))
    invalid.write_bytes(b"not an epub")

    source_inventory = bench.inspect_epub(source)
    candidate_inventory = bench.inspect_epub(candidate)
    invalid_inventory = bench.inspect_epub(invalid)
    comparison = bench.compare_epub_inventories(source_inventory, candidate_inventory)

    assert source_inventory["valid_archive"] is True
    assert candidate_inventory["valid_archive"] is True
    assert invalid_inventory["valid_archive"] is False
    assert "OEBPS/images/cover.jpg" in comparison["media_removed"]
    assert comparison["regressions"] is True


def test_gate_evaluation_rejects_quality_regressions_and_accepts_go_result():
    baseline = bench.JobMetrics(
        name="two-pass",
        total_tokens=1_000,
        request_count=10,
        estimated_cost_usd=1.0,
        wall_time_seconds=90,
        expected_blocks=10,
        translated_blocks=10,
        group_count=10,
        valid_archive=True,
        glossary_checks={"violations": 0},
    )
    candidate = bench.JobMetrics(
        name="one-pass",
        total_tokens=400,
        request_count=5,
        estimated_cost_usd=0.4,
        wall_time_seconds=60,
        expected_blocks=10,
        translated_blocks=10,
        group_count=10,
        valid_archive=True,
        glossary_checks={"violations": 0},
    )

    report = bench.evaluate_gates(baseline, candidate)

    assert report.go is True
    assert report.gates["token_savings"]["passed"] is True
    assert report.gates["request_savings"]["passed"] is True
    assert report.gates["repair_rate"]["passed"] is True

    candidate.fallback_count = 1
    no_go = bench.evaluate_gates(baseline, candidate)
    assert no_go.go is False
    assert no_go.gates["zero_fallbacks"]["passed"] is False


def test_markdown_summary_is_deterministic_and_includes_gate_decision(tmp_path):
    source = tmp_path / "source.epub"
    _write_source(source)
    metrics = bench.JobMetrics(
        name="two-pass",
        total_tokens=100,
        request_count=2,
        wall_time_seconds=1.25,
        valid_archive=True,
    )
    report = bench.BenchmarkResult(
        source_path=source,
        baseline=bench.JobRun(pipeline="two-pass", metrics=metrics),
        candidate=bench.JobRun(
            pipeline="one-pass",
            metrics=bench.JobMetrics(
                name="one-pass",
                total_tokens=40,
                request_count=1,
                wall_time_seconds=0.5,
                valid_archive=True,
            ),
        ),
        evaluation=bench.Evaluation(go=True, gates={"token_savings": {"passed": True}}),
        sandbox=None,
    )

    first = bench.render_markdown(report)
    second = bench.render_markdown(report)

    assert first == second
    assert "GO" in first
    assert "token_savings" in first
    assert first.index("token_savings") < first.index("two-pass")


def test_paid_cli_requires_confirmation_before_dispatch(tmp_path):
    source = tmp_path / "source.epub"
    _write_source(source)
    output = tmp_path / "report.json"
    called = []

    def fail_runner(*args, **kwargs):
        called.append(True)
        raise AssertionError("runner must not be called")

    with pytest.raises(SystemExit):
        bench.main(
            [
                "--source",
                str(source),
                "--baseline",
                "two-pass",
                "--candidate",
                "one-pass",
                "--output",
                str(output),
                "--token-budget",
                "1000",
            ],
            runners={"two-pass": fail_runner, "one-pass": fail_runner},
        )

    assert called == []
    assert not output.exists()


def test_paid_cli_rejects_nonpositive_budget(tmp_path):
    source = tmp_path / "source.epub"
    _write_source(source)

    with pytest.raises(SystemExit):
        bench.main(
            [
                "--source",
                str(source),
                "--output",
                str(tmp_path / "report.json"),
                "--token-budget",
                "0",
                "--confirm-paid",
            ],
            runners={"two-pass": lambda *a, **k: None, "one-pass": lambda *a, **k: None},
        )


def test_json_report_is_machine_readable(tmp_path):
    source = tmp_path / "source.epub"
    _write_source(source)
    result = bench.run_benchmark(
        source,
        baseline_pipeline="two-pass",
        candidate_pipeline="one-pass",
        token_budget=1,
        settings=bench.PinnedSettings("fake", "fake", "https://fake.invalid"),
        runners={
            "two-pass": lambda job, collector: (
                job.output_path.write_bytes(_epub_bytes(translated=True)),
                collector.record_blocks(expected=2, translated=2),
            ),
            "one-pass": lambda job, collector: (
                job.output_path.write_bytes(_epub_bytes(translated=True)),
                collector.record_blocks(expected=2, translated=2),
            ),
        },
        sandbox_root=tmp_path / "sandbox",
    )
    payload = result.to_dict()
    round_trip = json.loads(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    assert round_trip["source_path"].endswith("source.epub")
    assert round_trip["baseline"]["pipeline"] == "two-pass"
    assert "evaluation" in round_trip


def test_cli_writes_json_and_markdown_with_injected_runners(tmp_path, capsys):
    source = tmp_path / "source.epub"
    _write_source(source)
    output = tmp_path / "reports" / "benchmark.json"

    def runner(job, collector):
        job.output_path.write_bytes(_epub_bytes(translated=True))
        if job.pipeline == "two-pass":
            collector.record_usage(fresh_input_tokens=100, output_tokens=0)
            collector.record_request(10)
        else:
            collector.record_usage(fresh_input_tokens=40, output_tokens=0)
            collector.record_request(5)
        collector.record_group(100)
        collector.record_blocks(expected=2, translated=2)

    rc = bench.main(
        [
            "--source",
            str(source),
            "--output",
            str(output),
            "--token-budget",
            "1000",
            "--confirm-paid",
            "--provider",
            "fake",
            "--model",
            "fake-model",
            "--base-url",
            "https://fake.invalid",
            "--sandbox-root",
            str(tmp_path / "sandbox"),
        ],
        settings=bench.PinnedSettings(
            "fake", "fake-model", "https://fake.invalid",
            fresh_input_price_per_m=1.0,
            output_price_per_m=1.0,
            pricing_source="test",
        ),
        runners=runner,
    )

    assert rc == 0
    assert output.is_file()
    assert output.with_suffix(".md").is_file()
    assert "Requested token budget: 1,000 tokens" in capsys.readouterr().out


def test_configured_settings_load_without_serializing_api_key(tmp_path):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "provider": "opencode-go",
                "model": "deepseek-v4-flash",
                "provider_keys": {"opencode-go": "placeholder-key"},
                "thinking": "disabled",
                "fill_thinking": "adaptive",
                "max_group_tokens": 10000,
                "max_retries": 2,
                "retry_times": 3,
                "concurrency": 7,
                "strict_one_pass": True,
            }
        ),
        encoding="utf-8",
    )

    settings, api_key = bench.load_configured_settings(settings_file)

    assert settings.provider == "opencode-go"
    assert settings.model == "deepseek-v4-flash"
    assert settings.base_url == "https://opencode.ai/zen/go/v1"
    assert settings.thinking == "disabled"
    assert settings.fill_thinking == "adaptive"
    assert settings.max_group_tokens == 10000
    assert settings.retry_times == 3
    assert settings.concurrency == 7
    assert settings.strict is True
    assert api_key == "placeholder-key"
    assert "placeholder-key" not in json.dumps(settings.to_dict())


def test_cli_uses_configured_settings_and_keeps_key_out_of_report(tmp_path):
    source = tmp_path / "source.epub"
    _write_source(source)
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "provider": "fake",
                "model": "configured-model",
                "base_url": "https://fake.invalid",
                "provider_keys": {"fake": "placeholder-key"},
                "max_group_tokens": 321,
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "report.json"
    observed = []

    def runner(job, collector):
        observed.append(job)
        job.output_path.write_bytes(_epub_bytes(translated=True))
        collector.record_usage(fresh_input_tokens=10)
        collector.record_request(1)
        collector.record_blocks(expected=2, translated=2)
        collector.record_group(1)

    rc = bench.main(
        [
            "--source",
            str(source),
            "--output",
            str(output),
            "--token-budget",
            "1000",
            "--confirm-paid",
            "--settings-file",
            str(settings_file),
            "--sandbox-root",
            str(tmp_path / "sandbox"),
        ],
        runners=runner,
    )

    assert rc == 1  # fake equal-sized runs fail the savings gates
    assert len(observed) == 2
    assert all(job.settings.model == "configured-model" for job in observed)
    assert all(job.settings.max_group_tokens == 321 for job in observed)
    assert all(job.api_key == "placeholder-key" for job in observed)
    assert "placeholder-key" not in output.read_text(encoding="utf-8")


def test_glossary_is_copied_into_sandbox_and_checked(tmp_path):
    source = tmp_path / "source.epub"
    _write_source(source)
    glossary = tmp_path / "glossary-source"
    glossary.mkdir()
    (glossary / "global.json").write_text(
        json.dumps({"第一段。": "First translated paragraph."}, ensure_ascii=False),
        encoding="utf-8",
    )

    def runner(job, collector):
        assert (job.glossary_dir / "global.json").is_file()
        job.output_path.write_bytes(_epub_bytes(translated=True))
        collector.record_blocks(expected=2, translated=2)

    result = bench.run_benchmark(
        source,
        token_budget=1000,
        settings=bench.PinnedSettings("fake", "fake", "https://fake.invalid"),
        runners=runner,
        glossary_source_dir=glossary,
        sandbox_root=tmp_path / "sandbox",
    )

    assert result.candidate.metrics.glossary_checks["terms_checked"] == 1
    assert result.candidate.metrics.glossary_checks["violations"] == 0


def test_structure_inventory_detects_changed_stylesheet(tmp_path):
    source = tmp_path / "source.epub"
    candidate = tmp_path / "candidate.epub"
    source.write_bytes(_epub_bytes())

    from io import BytesIO

    output = BytesIO()
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(output, "w") as archive:
        for info in original.infolist():
            content = original.read(info.filename)
            if info.filename == "OEBPS/styles.css":
                content = b"body { color: red; }\\n"
            archive.writestr(info, content)
    candidate.write_bytes(output.getvalue())

    source_inventory = bench.inspect_epub(source)
    candidate_inventory = bench.inspect_epub(candidate)
    comparison = bench.compare_epub_inventories(source_inventory, candidate_inventory)

    assert "OEBPS/styles.css" in comparison["structure_changed"]
    assert comparison["regressions"] is True


def test_onepass_report_merges_repairs_fallbacks_and_cjk_counts():
    report = type(
        "Report",
        (),
        {
            "groups": 5,
            "full_group_retries": 1,
            "subset_requests": 1,
            "individual_requests": 1,
            "fallback_units": 2,
            "cjk_remnants": [{"index": 1}, {"index": 2}],
            "failures": [],
        },
    )()
    collector = bench.MetricsCollector("one-pass")

    bench._merge_onepass_report(report, collector)

    assert collector.metrics.group_count == 5
    assert collector.metrics.repair_count == 3
    assert collector.metrics.fallback_count == 2
    assert collector.metrics.cjk_remnants == 2


def test_usage_mapping_collects_cached_and_reasoning_tokens():
    collector = bench.MetricsCollector("one-pass")

    bench._record_usage_object(
        collector,
        {
            "prompt_tokens": 120,
            "prompt_tokens_details": {"cached_tokens": 20},
            "completion_tokens": 40,
            "completion_tokens_details": {"reasoning_tokens": 10},
            "total_tokens": 160,
        },
    )

    assert collector.metrics.fresh_input_tokens == 100
    assert collector.metrics.cached_input_tokens == 20
    assert collector.metrics.output_tokens == 40
    assert collector.metrics.reasoning_tokens == 10
    assert collector.metrics.total_tokens == 160


def test_analysis_counts_br_blocks_and_keeps_heading():
    raw = "<html><body><h1>Chapter</h1>第一段<br/>第二段<br/></body></html>"
    assert bench._extract_blocks(raw) == ["Chapter", "第一段", "第二段"]


def test_archive_comparison_flags_new_duplicate_zip_entries(tmp_path):
    source = tmp_path / "source.epub"
    candidate = tmp_path / "candidate.epub"
    source.write_bytes(_epub_bytes())
    from io import BytesIO

    output = BytesIO()
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(output, "w") as archive:
        for info in original.infolist():
            archive.writestr(info, original.read(info.filename))
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("OEBPS/nav.xhtml", b"<html><body><nav/></body></html>")
    candidate.write_bytes(output.getvalue())

    comparison = bench.compare_epub_inventories(
        bench.inspect_epub(source), bench.inspect_epub(candidate)
    )

    assert comparison["duplicate_entries_added"] == ["OEBPS/nav.xhtml"]
    assert comparison["regressions"] is True


def test_instrumented_usage_stops_before_budget_overrun():
    class Stats:
        def submit_usage(self, usage):
            return usage

    class Executor:
        def _invoke_model(self, **kwargs):
            return "ok"

    class LLM:
        def __init__(self):
            self._statistics = Stats()
            self._executor = Executor()

    collector = bench.MetricsCollector("one-pass")
    llm = bench._instrument_llm(LLM(), collector, token_budget=10)
    with pytest.raises(bench.BenchmarkError, match="token budget"):
        llm._statistics.submit_usage({"total_tokens": 11, "prompt_tokens": 8, "completion_tokens": 3})


def test_metered_cost_gate_uses_pinned_prices():
    baseline = bench.JobMetrics(total_tokens=100, request_count=2, valid_archive=True, estimated_cost_usd=1.0)
    candidate = bench.JobMetrics(total_tokens=40, request_count=1, valid_archive=True, estimated_cost_usd=0.5)
    baseline.expected_blocks = baseline.translated_blocks = 1
    candidate.expected_blocks = candidate.translated_blocks = 1
    first = bench.evaluate_gates(baseline, candidate)
    assert first.gates["metered_cost_ratio"]["passed"] is True

    candidate.estimated_cost_usd = 0.61
    second = bench.evaluate_gates(baseline, candidate)
    assert second.gates["metered_cost_ratio"]["passed"] is False
