"""Isolated two-pass versus one-pass EPUB benchmark harness.

This module deliberately keeps the benchmark boundary separate from the
translation implementations.  Unit tests can inject runners (and fake LLMs)
without importing the application or making a network request.  The default
runners are only used by the guarded CLI after ``--confirm-paid`` and a
positive token budget have been supplied.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import html
import importlib
import inspect
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

PIPELINES = ("two-pass", "one-pass")
DEFAULT_PROVIDER = "deepseek"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_BASE_URLS = {
    "deepseek": "https://api.deepseek.com",
    "opencode-go": "https://opencode.ai/zen/go/v1",
    "openai": "https://api.openai.com/v1",
}
DEFAULT_SETTINGS_FILE = Path(__file__).resolve().parents[1] / "cli" / "settings.json"
DEFAULT_FILL_THINKING = "adaptive"
# Pinned rates used by the acceptance report. These are metered USD per
# million tokens; a zero-valued fallback leaves the cost gate explicitly
# unmeasured rather than inventing a provider price.
PRICING_TABLE: dict[tuple[str, str], tuple[float, float, float, str]] = {
    (
        "opencode-go",
        "deepseek-v4-flash",
    ): (0.14, 0.0028, 0.28, "OpenCode Go table dated 2026-08-14"),
}
PROVIDER_KEY_ENV = {
    "deepseek": "DEEPSEEK_API_KEY",
    "opencode-go": "OPENCODE_GO_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "xai": "XAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "custom": "CUSTOM_LLM_API_KEY",
}
PROVIDER_MODEL_ENV = {
    provider: env.removesuffix("_API_KEY") + "_MODEL"
    for provider, env in PROVIDER_KEY_ENV.items()
}
PROVIDER_BASE_ENV = {
    provider: env.removesuffix("_API_KEY") + "_BASE_URL"
    for provider, env in PROVIDER_KEY_ENV.items()
}

TEXT_SUFFIXES = {".html", ".htm", ".xhtml", ".xml", ".opf", ".ncx", ".css", ".txt"}
MEDIA_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".tif", ".tiff",
    ".avif", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3",
    ".m4a", ".ogg", ".wav", ".mp4", ".m4v", ".webm", ".avi",
}
STRUCTURE_SUFFIXES = {".opf", ".ncx", ".css", ".xml"}
BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6"}
CJK_RANGES = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2FA1F),
)


class BenchmarkError(RuntimeError):
    """Base error for invalid benchmark setup or execution."""


@dataclass(frozen=True)
class SandboxPaths:
    """All filesystem locations a benchmark is allowed to write."""

    root: Path
    settings_file: Path
    cache_dir: Path
    out_dir: Path
    books_dir: Path
    glossary_dir: Path

    @classmethod
    def create(cls, root: Path) -> SandboxPaths:
        root = Path(root).resolve()
        paths = cls(
            root=root,
            settings_file=root / "settings.json",
            cache_dir=root / "cache",
            out_dir=root / "out",
            books_dir=root / "books",
            glossary_dir=root / "glossaries",
        )
        for directory in (
            paths.cache_dir,
            paths.out_dir,
            paths.books_dir,
            paths.glossary_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        if not paths.settings_file.exists():
            paths.settings_file.write_text("{}\n", encoding="utf-8")
        return paths

    def as_dict(self) -> dict[str, str]:
        return {
            "root": str(self.root),
            "settings_file": str(self.settings_file),
            "cache_dir": str(self.cache_dir),
            "out_dir": str(self.out_dir),
            "books_dir": str(self.books_dir),
            "glossary_dir": str(self.glossary_dir),
        }


@dataclass(frozen=True)
class PinnedSettings:
    """Settings shared by baseline and candidate jobs."""

    provider: str
    model: str
    base_url: str
    target_language: str = "English"
    thinking: str = "disabled"
    fill_thinking: str = DEFAULT_FILL_THINKING
    max_group_tokens: int = 5000
    max_retries: int = 2
    retry_times: int = 2
    concurrency: int = 1
    strict: bool = True
    user_prompt: str = ""
    fresh_input_price_per_m: float = 0.0
    cached_input_price_per_m: float = 0.0
    output_price_per_m: float = 0.0
    pricing_source: str = "unpriced"

    def __post_init__(self) -> None:
        if not str(self.provider).strip():
            raise ValueError("provider must not be empty")
        if not str(self.model).strip():
            raise ValueError("model must not be empty")
        if not str(self.base_url).strip():
            raise ValueError("base_url must not be empty")
        if self.thinking not in {"adaptive", "enabled", "disabled"}:
            raise ValueError("thinking must be adaptive, enabled, or disabled")
        if self.fill_thinking not in {"adaptive", "enabled", "disabled"}:
            raise ValueError("fill_thinking must be adaptive, enabled, or disabled")
        if self.max_group_tokens <= 0:
            raise ValueError("max_group_tokens must be positive")
        if self.max_retries < 0 or self.retry_times < 0:
            raise ValueError("retry settings must not be negative")
        if self.concurrency <= 0:
            raise ValueError("concurrency must be positive")
        if min(
            self.fresh_input_price_per_m,
            self.cached_input_price_per_m,
            self.output_price_per_m,
        ) < 0:
            raise ValueError("pricing values must not be negative")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _coerce_setting_int(value: Any, default: int, *, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def _read_settings_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read settings file {path}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ValueError(f"settings file must contain a JSON object: {path}")  # noqa: TRY004
    return dict(data)


def _pricing_for(provider: str, model: str) -> tuple[float, float, float, str]:
    return PRICING_TABLE.get((provider, model), (0.0, 0.0, 0.0, "unpriced"))


def load_configured_settings(
    settings_file: str | Path | None = None,
    *,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    target_language: str = "English",
    thinking: str | None = None,
    fill_thinking: str | None = None,
    max_group_tokens: int | None = None,
    max_retries: int | None = None,
    retry_times: int | None = None,
    concurrency: int | None = None,
    strict: bool | None = None,
) -> tuple[PinnedSettings, str | None]:
    """Read production settings without writing or serializing credentials.

    The returned key is held only in memory and is passed to the injected job
    spec. It is intentionally absent from :class:`PinnedSettings`, reports,
    and console output. Environment variables have the same precedence as the
    application config for provider credentials and endpoint overrides.
    """

    path = Path(settings_file or DEFAULT_SETTINGS_FILE).expanduser()
    data = _read_settings_file(path)
    selected_provider = str(
        provider or data.get("provider") or os.environ.get("EPUB_PROVIDER") or DEFAULT_PROVIDER
    ).strip()
    selected_model = str(
        model
        or data.get("model")
        or os.environ.get(PROVIDER_MODEL_ENV.get(selected_provider, ""), "")
        or DEFAULT_MODEL
    ).strip()
    selected_base_url = str(
        base_url
        or data.get("base_url")
        or os.environ.get(PROVIDER_BASE_ENV.get(selected_provider, ""), "")
        or DEFAULT_BASE_URLS.get(selected_provider, "")
    ).strip()
    if not selected_base_url:
        raise ValueError(f"no base URL configured for provider {selected_provider!r}")

    selected_thinking = str(
        thinking if thinking is not None else data.get("thinking", "disabled")
    ).strip().lower()
    selected_fill_thinking = str(
        fill_thinking
        if fill_thinking is not None
        else data.get("fill_thinking", DEFAULT_FILL_THINKING)
    ).strip().lower()
    selected_strict = (
        bool(strict) if strict is not None else data.get("strict_one_pass", False) is True
    )
    fresh_price, cached_price, output_price, pricing_source = _pricing_for(
        selected_provider, selected_model
    )
    settings = PinnedSettings(
        provider=selected_provider,
        model=selected_model,
        base_url=selected_base_url,
        target_language=target_language,
        thinking=selected_thinking,
        fill_thinking=selected_fill_thinking,
        max_group_tokens=_coerce_setting_int(
            max_group_tokens if max_group_tokens is not None else data.get("max_group_tokens", 5000),
            5000,
            minimum=1,
        ),
        max_retries=_coerce_setting_int(
            max_retries if max_retries is not None else data.get("max_retries", 2),
            2,
        ),
        retry_times=_coerce_setting_int(
            retry_times if retry_times is not None else data.get("retry_times", 2),
            2,
        ),
        concurrency=_coerce_setting_int(
            concurrency if concurrency is not None else data.get("concurrency", 1),
            1,
            minimum=1,
        ),
        strict=selected_strict,
        fresh_input_price_per_m=fresh_price,
        cached_input_price_per_m=cached_price,
        output_price_per_m=output_price,
        pricing_source=pricing_source,
    )

    key_env = PROVIDER_KEY_ENV.get(selected_provider, "")
    api_key = os.environ.get(key_env, "").strip() if key_env else ""
    provider_keys = data.get("provider_keys")
    if not api_key and isinstance(provider_keys, Mapping):
        api_key = str(provider_keys.get(selected_provider, "")).strip()
    if not api_key:
        owner = str(data.get("api_key_provider", "deepseek")).strip()
        if owner == selected_provider:
            api_key = str(data.get("api_key", "")).strip()
    return settings, (api_key or None)


@dataclass(frozen=True)
class JobSpec:
    """Concrete, sandboxed invocation handed to an injected runner."""

    pipeline: str
    source_path: Path
    output_path: Path
    cache_dir: Path
    glossary_dir: Path
    sandbox: SandboxPaths
    settings: PinnedSettings
    token_budget: int
    llm_factory: Callable[..., Any] | None = None
    api_key: str | None = field(default=None, repr=False)
    chapter_limit: int = 0

    @property
    def target_path(self) -> Path:
        return self.output_path

    @property
    def source(self) -> Path:
        return self.source_path

    @property
    def output(self) -> Path:
        return self.output_path


@dataclass
class JobMetrics:
    """Measured and quality metrics for one pipeline run."""

    name: str = ""
    input_tokens: int = 0
    fresh_input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    request_count: int = 0
    retry_count: int = 0
    group_count: int = 0
    repair_count: int = 0
    fallback_count: int = 0
    drop_count: int = 0
    duplicate_count: int = 0
    reorder_count: int = 0
    missing_blocks: int = 0
    unexpected_blocks: int = 0
    empty_count: int = 0
    expected_blocks: int = 0
    translated_blocks: int = 0
    cjk_remnants: int = 0
    source_characters: int = 0
    estimated_cost_usd: float = 0.0
    wall_time_seconds: float = 0.0
    valid_archive: bool = False
    archive: dict[str, Any] = field(default_factory=dict)
    archive_comparison: dict[str, Any] = field(default_factory=dict)
    glossary_checks: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def normalize(self) -> JobMetrics:
        """Fill derived counters without double-counting reasoning tokens."""
        if self.input_tokens and not (self.fresh_input_tokens or self.cached_input_tokens):
            self.fresh_input_tokens = max(0, int(self.input_tokens))
        self.input_tokens = max(
            0, int(self.fresh_input_tokens) + int(self.cached_input_tokens)
        )
        if not self.total_tokens:
            # reasoning_tokens are generally a subset of completion_tokens in
            # provider usage payloads; never add them a second time.
            self.total_tokens = self.input_tokens + max(0, int(self.output_tokens))
        if self.expected_blocks and not self.translated_blocks and not self.errors:
            # A runner may only report expected blocks for an empty output;
            # leave translated_blocks at zero so the gate catches it.
            pass
        self.wall_time_seconds = round(max(0.0, float(self.wall_time_seconds)), 6)
        self.source_characters = max(0, int(self.source_characters))
        self.estimated_cost_usd = round(max(0.0, float(self.estimated_cost_usd)), 8)
        return self

    def to_dict(self) -> dict[str, Any]:
        self.normalize()
        return {
            "name": self.name,
            "tokens": {
                "input": self.input_tokens,
                "fresh_input": self.fresh_input_tokens,
                "cached_input": self.cached_input_tokens,
                "output": self.output_tokens,
                "reasoning": self.reasoning_tokens,
                "total": self.total_tokens,
            },
            "input_tokens": self.input_tokens,
            "fresh_input_tokens": self.fresh_input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
            "request_count": self.request_count,
            "retry_count": self.retry_count,
            "group_count": self.group_count,
            "repair_count": self.repair_count,
            "fallback_count": self.fallback_count,
            "drop_count": self.drop_count,
            "duplicate_count": self.duplicate_count,
            "reorder_count": self.reorder_count,
            "missing_blocks": self.missing_blocks,
            "unexpected_blocks": self.unexpected_blocks,
            "empty_count": self.empty_count,
            "expected_blocks": self.expected_blocks,
            "translated_blocks": self.translated_blocks,
            "cjk_remnants": self.cjk_remnants,
            "source_characters": self.source_characters,
            "estimated_cost_usd": self.estimated_cost_usd,
            "wall_time_seconds": self.wall_time_seconds,
            "valid_archive": self.valid_archive,
            "archive": self.archive,
            "archive_comparison": self.archive_comparison,
            "glossary_checks": self.glossary_checks,
            "errors": list(self.errors),
        }


class MetricsCollector:
    """Small dependency-injection seam for fake runners and LLM telemetry."""

    def __init__(self, name: str = "") -> None:
        self.metrics = JobMetrics(name=name)

    def record_usage(
        self,
        *,
        input_tokens: int | None = None,
        fresh_input_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        output_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> None:
        if input_tokens is not None and fresh_input_tokens is None:
            cached = max(0, int(cached_input_tokens or 0))
            fresh_input_tokens = max(0, int(input_tokens) - cached)
        if fresh_input_tokens is not None:
            self.metrics.fresh_input_tokens += max(0, int(fresh_input_tokens))
        if cached_input_tokens is not None:
            self.metrics.cached_input_tokens += max(0, int(cached_input_tokens))
        if output_tokens is not None:
            self.metrics.output_tokens += max(0, int(output_tokens))
        if reasoning_tokens is not None:
            self.metrics.reasoning_tokens += max(0, int(reasoning_tokens))
        if total_tokens is not None:
            self.metrics.total_tokens += max(0, int(total_tokens))
        self.metrics.normalize()

    def set_usage_snapshot(
        self,
        *,
        input_tokens: int = 0,
        cached_input_tokens: int = 0,
        output_tokens: int = 0,
        reasoning_tokens: int = 0,
        total_tokens: int = 0,
    ) -> None:
        """Merge cumulative LLM properties without counting them twice."""
        fresh = max(0, int(input_tokens) - int(cached_input_tokens))
        self.metrics.fresh_input_tokens = max(self.metrics.fresh_input_tokens, fresh)
        self.metrics.cached_input_tokens = max(
            self.metrics.cached_input_tokens, max(0, int(cached_input_tokens))
        )
        self.metrics.output_tokens = max(self.metrics.output_tokens, max(0, int(output_tokens)))
        self.metrics.reasoning_tokens = max(
            self.metrics.reasoning_tokens, max(0, int(reasoning_tokens))
        )
        if total_tokens:
            self.metrics.total_tokens = max(self.metrics.total_tokens, int(total_tokens))
        self.metrics.normalize()

    def record_request(self, count: int = 1) -> None:
        self.metrics.request_count += max(0, int(count))

    def record_retry(self, count: int = 1) -> None:
        self.metrics.retry_count += max(0, int(count))

    def record_group(self, count: int = 1) -> None:
        self.metrics.group_count += max(0, int(count))

    def record_repair(self, count: int = 1) -> None:
        self.metrics.repair_count += max(0, int(count))

    def record_fallback(self, count: int = 1) -> None:
        self.metrics.fallback_count += max(0, int(count))

    def record_blocks(
        self,
        *,
        expected: int | None = None,
        translated: int | None = None,
        missing: int = 0,
        unexpected: int = 0,
        duplicate: int = 0,
        reordered: int = 0,
        dropped: int = 0,
        empty: int = 0,
    ) -> None:
        if expected is not None:
            self.metrics.expected_blocks = max(0, int(expected))
        if translated is not None:
            self.metrics.translated_blocks = max(0, int(translated))
        self.metrics.missing_blocks += max(0, int(missing))
        self.metrics.unexpected_blocks += max(0, int(unexpected))
        self.metrics.duplicate_count += max(0, int(duplicate))
        self.metrics.reorder_count += max(0, int(reordered))
        self.metrics.drop_count += max(0, int(dropped))
        self.metrics.empty_count += max(0, int(empty))

    def record_event(self, event: Mapping[str, Any]) -> None:
        """Accept the event vocabulary used by the web job runner/core API."""
        kind = str(event.get("type", event.get("event", ""))).lower()
        if kind in {"request", "llm_request", "attempt"}:
            self.record_request(int(event.get("count", 1)))
        elif kind in {"retry", "llm_retry"}:
            self.record_retry(int(event.get("count", 1)))
        elif kind in {"repair", "protocol_repair"}:
            self.record_repair(int(event.get("count", 1)))
        elif kind in {"fallback", "source_copy_fallback"}:
            self.record_fallback(int(event.get("count", 1)))
        elif kind in {"blocks", "protocol", "protocol_failed"}:
            self.record_blocks(
                expected=event.get("expected", event.get("expected_blocks")),
                translated=event.get("translated", event.get("translated_blocks")),
                missing=int(event.get("missing", event.get("missing_blocks", 0)) or 0),
                unexpected=int(event.get("unexpected", event.get("unexpected_blocks", 0)) or 0),
                duplicate=int(event.get("duplicate", event.get("duplicate_blocks", 0)) or 0),
                reordered=int(event.get("reordered", event.get("reorder_count", 0)) or 0),
                dropped=int(event.get("dropped", event.get("drop_count", 0)) or 0),
                empty=int(event.get("empty", event.get("empty_blocks", 0)) or 0),
            )
        usage = event.get("usage")
        if isinstance(usage, Mapping):
            self.record_usage(
                input_tokens=_number(usage, "input_tokens", "prompt_tokens", "input"),
                fresh_input_tokens=_number(
                    usage, "fresh_input_tokens", "fresh_tokens", "uncached_input_tokens"
                ),
                cached_input_tokens=_number(
                    usage, "cached_input_tokens", "cache_read_tokens", "cached_tokens"
                ),
                output_tokens=_number(usage, "output_tokens", "completion_tokens", "output"),
                reasoning_tokens=_number(usage, "reasoning_tokens", "reasoning"),
                total_tokens=_number(usage, "total_tokens", "total"),
            )

    def merge_result(self, result: Any) -> None:
        """Merge a runner's optional dict/dataclass result into telemetry."""
        if result is None:
            return
        if isinstance(result, JobMetrics):
            data = result.to_dict()
        elif isinstance(result, Mapping):
            data = dict(result)
        elif isinstance(result, (str, Path)):
            return
        else:
            data = _object_mapping(result)
        nested = data.get("metrics") or data.get("telemetry")
        if isinstance(nested, Mapping):
            data = {**data, **nested}

        aliases: dict[str, tuple[str, ...]] = {
            "input_tokens": ("input_tokens", "prompt_tokens", "input"),
            "fresh_input_tokens": ("fresh_input_tokens", "fresh_tokens", "uncached_input_tokens"),
            "cached_input_tokens": ("cached_input_tokens", "cache_read_tokens", "cached_tokens"),
            "output_tokens": ("output_tokens", "completion_tokens", "output"),
            "reasoning_tokens": ("reasoning_tokens", "reasoning"),
            "total_tokens": ("total_tokens", "total"),
            "request_count": ("request_count", "requests", "network_requests"),
            "retry_count": ("retry_count", "retries"),
            "group_count": ("group_count", "groups"),
            "repair_count": ("repair_count", "repairs"),
            "fallback_count": ("fallback_count", "fallbacks", "source_copy_fallbacks"),
            "drop_count": ("drop_count", "dropped", "dropped_blocks"),
            "duplicate_count": ("duplicate_count", "duplicates", "duplicate_blocks"),
            "reorder_count": ("reorder_count", "reordered", "reordered_blocks"),
            "missing_blocks": ("missing_blocks", "missing"),
            "unexpected_blocks": ("unexpected_blocks", "unexpected", "extras"),
            "empty_count": ("empty_count", "empty_blocks", "empty"),
            "expected_blocks": ("expected_blocks", "block_count", "expected"),
            "translated_blocks": ("translated_blocks", "output_block_count", "translated"),
            "cjk_remnants": ("cjk_remnants", "cjk_count", "cjk"),
            "source_characters": ("source_characters", "source_chars", "characters"),
            "estimated_cost_usd": ("estimated_cost_usd", "cost_usd", "cost"),
            "wall_time_seconds": ("wall_time_seconds", "wall_time", "elapsed_seconds"),
        }
        for target, names in aliases.items():
            value = _first_value(data, names)
            if value is None:
                continue
            current = getattr(self.metrics, target)
            if target == "wall_time_seconds":
                if not current:
                    setattr(self.metrics, target, float(value))
            elif target in {"expected_blocks", "translated_blocks", "source_characters"}:
                if not current:
                    setattr(self.metrics, target, int(value))
            elif target == "estimated_cost_usd":
                if not current:
                    setattr(self.metrics, target, float(value))
            elif target in {"input_tokens", "total_tokens"}:
                if not current:
                    setattr(self.metrics, target, int(value))
            else:
                if not current:
                    setattr(self.metrics, target, int(value))
        for key in ("archive", "archive_comparison", "glossary_checks"):
            value = data.get(key)
            if isinstance(value, Mapping) and not getattr(self.metrics, key):
                setattr(self.metrics, key, dict(value))
        errors = data.get("errors")
        if isinstance(errors, Sequence) and not isinstance(errors, (str, bytes)):
            self.metrics.errors.extend(str(item) for item in errors)
        events = data.get("events")
        if isinstance(events, Sequence) and not isinstance(events, (str, bytes)):
            for event in events:
                if isinstance(event, Mapping):
                    self.record_event(event)
        self.metrics.normalize()

    def snapshot(self) -> JobMetrics:
        self.metrics.normalize()
        return self.metrics


@dataclass
class JobRun:
    pipeline: str
    metrics: JobMetrics

    def to_dict(self) -> dict[str, Any]:
        return {"pipeline": self.pipeline, "metrics": self.metrics.to_dict()}


@dataclass
class Evaluation:
    go: bool
    gates: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"decision": "GO" if self.go else "NO-GO", "go": self.go, "gates": self.gates}


@dataclass
class BenchmarkResult:
    source_path: Path
    baseline: JobRun
    candidate: JobRun
    evaluation: Evaluation
    sandbox: SandboxPaths | None = None
    token_budget: int = 0
    settings: PinnedSettings | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": str(self.source_path),
            "token_budget": self.token_budget,
            "settings": self.settings.to_dict() if self.settings else None,
            "baseline": self.baseline.to_dict(),
            "candidate": self.candidate.to_dict(),
            "evaluation": self.evaluation.to_dict(),
            "sandbox": self.sandbox.as_dict() if self.sandbox else None,
        }


@contextlib.contextmanager
def sandboxed_filesystem(root: Path | None = None) -> Iterator[SandboxPaths]:
    """Set sandbox path environment variables before application imports.

    The current application versions use module constants rather than all of
    these environment variables, so ``_patch_loaded_module_paths`` is also
    applied by the default runner after import.  This context still provides a
    stable contract for future config modules and fake runners.
    """

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if root is None:
        temporary = tempfile.TemporaryDirectory(prefix="epub-onepass-benchmark-")
        root = Path(temporary.name)
    paths = SandboxPaths.create(Path(root))
    names = {
        "EPUB_TRANSLATOR_SETTINGS_FILE": paths.settings_file,
        "EPUB_TRANSLATOR_CACHE_DIR": paths.cache_dir,
        "EPUB_TRANSLATOR_OUT_DIR": paths.out_dir,
        "EPUB_TRANSLATOR_BOOKS_DIR": paths.books_dir,
        "EPUB_TRANSLATOR_GLOSSARY_DIR": paths.glossary_dir,
        # Short aliases make the boundary usable by test doubles and the
        # post-merge config integration without coupling to one spelling.
        "EPUB_SETTINGS_FILE": paths.settings_file,
        "EPUB_CACHE_DIR": paths.cache_dir,
        "EPUB_OUT_DIR": paths.out_dir,
        "EPUB_BOOKS_DIR": paths.books_dir,
        "EPUB_GLOSSARY_DIR": paths.glossary_dir,
    }
    previous = {key: os.environ.get(key) for key in names}
    os.environ.update({key: str(value) for key, value in names.items()})
    try:
        yield paths
    finally:
        for key, old_value in previous.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value
        if temporary is not None:
            temporary.cleanup()


@contextlib.contextmanager
def sandbox_config_paths(paths: SandboxPaths) -> Iterator[None]:
    """Temporarily redirect already-imported application modules."""

    targets = ("config", "glossary", "translate_book", "onepass")
    attrs = {
        "SETTINGS_FILE": paths.settings_file,
        "CACHE_DIR": paths.cache_dir,
        "OUT_DIR": paths.out_dir,
        "BOOKS_DIR": paths.books_dir,
        "GLOSSARY_DIR": paths.glossary_dir,
    }
    originals: list[tuple[Any, str, Any]] = []
    modules = [sys.modules.get(name) for name in targets]
    try:
        for module in modules:
            if module is None:
                continue
            for name, value in attrs.items():
                if hasattr(module, name):
                    originals.append((module, name, getattr(module, name)))
                    setattr(module, name, value)
        yield
    finally:
        for module, name, old_value in reversed(originals):
            setattr(module, name, old_value)


def _patch_loaded_module_paths(paths: SandboxPaths, modules: Sequence[Any]) -> None:
    attrs = {
        "SETTINGS_FILE": paths.settings_file,
        "CACHE_DIR": paths.cache_dir,
        "OUT_DIR": paths.out_dir,
        "BOOKS_DIR": paths.books_dir,
        "GLOSSARY_DIR": paths.glossary_dir,
    }
    for module in modules:
        if module is None:
            continue
        for name, value in attrs.items():
            if hasattr(module, name):
                setattr(module, name, value)


def run_benchmark(
    source_path: str | Path,
    *,
    baseline_pipeline: str = "two-pass",
    candidate_pipeline: str = "one-pass",
    token_budget: int,
    settings: PinnedSettings,
    runners: Mapping[str, Callable[..., Any]] | Callable[..., Any] | None = None,
    sandbox_root: str | Path | None = None,
    glossary_source_dir: str | Path | None = None,
    api_key: str | None = None,
    chapter_limit: int = 0,
    llm_factory: Callable[..., Any] | None = None,
) -> BenchmarkResult:
    """Run equivalent baseline/candidate jobs in an isolated filesystem.

    ``runners`` is the no-network seam used by unit tests.  A runner receives a
    :class:`JobSpec` and a :class:`MetricsCollector` (the invocation helper also
    supports one-argument and keyword-only fakes).  Runner exceptions are
    recorded as a NO-GO metric instead of being hidden or turning into a false
    success report.
    """

    if token_budget is None or int(token_budget) <= 0:
        raise ValueError("token_budget must be a positive integer")
    if chapter_limit is None or int(chapter_limit) < 0:
        raise ValueError("chapter_limit must be zero or a positive integer")
    if baseline_pipeline not in PIPELINES or candidate_pipeline not in PIPELINES:
        raise ValueError(f"pipelines must be one of {PIPELINES}")
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"source EPUB does not exist: {source}")

    with sandboxed_filesystem(Path(sandbox_root) if sandbox_root else None) as sandbox:
        sandbox_source = sandbox.books_dir / source.name
        if source.resolve() != sandbox_source.resolve():
            shutil.copy2(source, sandbox_source)
        else:
            sandbox_source = source
        glossary_source = (
            Path(glossary_source_dir).expanduser().resolve()
            if glossary_source_dir is not None
            else source.parent.parent / "glossaries"
        )
        if glossary_source.is_dir():
            for entry in sorted(glossary_source.glob("*.json")):
                if entry.is_file():
                    shutil.copy2(entry, sandbox.glossary_dir / entry.name)
        sandbox.settings_file.write_text(
            json.dumps(
                {
                    "provider": settings.provider,
                    "model": settings.model,
                    "base_url": settings.base_url,
                    "thinking": settings.thinking,
                    "max_group_tokens": settings.max_group_tokens,
                    "max_retries": settings.max_retries,
                    "retry_times": settings.retry_times,
                    "concurrency": settings.concurrency,
                    "token_budget": int(token_budget),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        runs: dict[str, JobRun] = {}
        for pipeline in (baseline_pipeline, candidate_pipeline):
            output_path = sandbox.out_dir / f"{source.stem}.{pipeline}.epub"
            cache_dir = sandbox.cache_dir / pipeline
            cache_dir.mkdir(parents=True, exist_ok=True)
            job = JobSpec(
                pipeline=pipeline,
                source_path=sandbox_source,
                output_path=output_path,
                cache_dir=cache_dir,
                glossary_dir=sandbox.glossary_dir,
                sandbox=sandbox,
                settings=settings,
                token_budget=int(token_budget),
                llm_factory=llm_factory,
                api_key=api_key,
                chapter_limit=int(chapter_limit),
            )
            runner = _resolve_runner(runners, pipeline)
            runs[pipeline] = _run_job(job, runner)

        baseline = runs[baseline_pipeline]
        candidate = runs[candidate_pipeline]
        source_inventory = inspect_epub(sandbox_source)
        for job_run in (baseline, candidate):
            job_run.metrics.archive_comparison = compare_epub_inventories(
                source_inventory, job_run.metrics.archive
            )
        candidate.metrics.archive_comparison["baseline_comparison"] = compare_epub_inventories(
            baseline.metrics.archive, candidate.metrics.archive
        )
        evaluation = evaluate_gates(baseline.metrics, candidate.metrics)
        return BenchmarkResult(
            source_path=source,
            baseline=baseline,
            candidate=candidate,
            evaluation=evaluation,
            sandbox=sandbox,
            token_budget=int(token_budget),
            settings=settings,
        )


def _resolve_runner(
    runners: Mapping[str, Callable[..., Any]] | Callable[..., Any] | None,
    pipeline: str,
) -> Callable[..., Any]:
    if runners is None:
        return _default_runner
    if callable(runners):
        return runners
    runner = runners.get(pipeline)
    if runner is None:
        raise ValueError(f"no runner supplied for pipeline {pipeline!r}")
    return runner


def _estimate_cost_usd(metrics: JobMetrics, settings: PinnedSettings) -> float:
    return round(
        (
            metrics.fresh_input_tokens * settings.fresh_input_price_per_m
            + metrics.cached_input_tokens * settings.cached_input_price_per_m
            + metrics.output_tokens * settings.output_price_per_m
        )
        / 1_000_000,
        8,
    )


def _run_job(job: JobSpec, runner: Callable[..., Any]) -> JobRun:
    collector = MetricsCollector(job.pipeline)
    started = time.perf_counter()
    try:
        result = _invoke_runner(runner, job, collector)
        collector.merge_result(result)
        result_path = _result_output_path(result)
        if result_path is not None and result_path != job.output_path and Path(result_path).is_file():
            shutil.copy2(result_path, job.output_path)
    except Exception as exc:  # noqa: BLE001 - a report must explain failed jobs
        collector.metrics.errors.append(f"{type(exc).__name__}: {exc}")
    collector.metrics.wall_time_seconds = time.perf_counter() - started
    metrics = collector.snapshot()
    metrics.archive = inspect_epub(job.output_path)
    metrics.valid_archive = bool(metrics.archive.get("valid_archive", False))
    source_analysis = analyze_epub_text(job.source_path)
    output_analysis = analyze_epub_text(job.output_path)
    metrics.source_characters = len(source_analysis["text"])
    if not metrics.estimated_cost_usd:
        metrics.estimated_cost_usd = _estimate_cost_usd(metrics, job.settings)
    if not metrics.expected_blocks:
        metrics.expected_blocks = source_analysis["block_count"]
    if not metrics.translated_blocks:
        metrics.translated_blocks = output_analysis["block_count"]
    metrics.empty_count = max(metrics.empty_count, output_analysis["empty_blocks"])
    metrics.cjk_remnants = max(metrics.cjk_remnants, output_analysis["cjk_count"])
    derived_glossary = check_glossary(job.source_path, job.output_path, job.glossary_dir)
    metrics.glossary_checks = _merge_check_dicts(derived_glossary, metrics.glossary_checks)
    metrics.normalize()
    return JobRun(pipeline=job.pipeline, metrics=metrics)


def _invoke_runner(runner: Callable[..., Any], job: JobSpec, collector: MetricsCollector) -> Any:
    """Call common fake-runner shapes without masking runner exceptions."""

    try:
        signature = inspect.signature(runner)
    except (TypeError, ValueError):
        return runner(job, collector)
    parameters = list(signature.parameters.values())
    has_kwargs = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters)
    if has_kwargs:
        return runner(job=job, sandbox=job.sandbox, collector=collector, telemetry=collector)

    kwargs: dict[str, Any] = {}
    positional: list[Any] = []
    for parameter in parameters:
        if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
            continue
        name = parameter.name.lower()
        if name in {"job", "spec", "job_spec"}:
            value = job
        elif name in {"sandbox", "sandbox_paths", "paths"}:
            value = job.sandbox
        elif name in {"collector", "telemetry", "metrics", "telemetry_collector"}:
            value = collector
        else:
            # Preserve compatibility with simple ``runner(job, collector)``
            # fakes whose parameters have arbitrary names.
            value = job if not positional else collector
        if parameter.kind == inspect.Parameter.KEYWORD_ONLY:
            kwargs[parameter.name] = value
        else:
            positional.append(value)
    return runner(*positional, **kwargs)


def _result_output_path(result: Any) -> Path | None:
    if isinstance(result, (str, Path)):
        return Path(result)
    if isinstance(result, Mapping):
        for key in ("target", "target_path", "output", "output_path"):
            value = result.get(key)
            if value:
                return Path(value)
    return None


def evaluate_gates(baseline: JobMetrics, candidate: JobMetrics) -> Evaluation:
    """Apply the approved E4 GO/NO-GO gates deterministically."""

    baseline.normalize()
    candidate.normalize()
    gates: dict[str, dict[str, Any]] = {}
    token_savings = _savings_ratio(baseline.total_tokens, candidate.total_tokens)
    request_savings = _savings_ratio(baseline.request_count, candidate.request_count)
    cost_ratio = (
        round(candidate.estimated_cost_usd / baseline.estimated_cost_usd, 6)
        if baseline.estimated_cost_usd > 0
        else None
    )
    expected_blocks = baseline.translated_blocks or baseline.expected_blocks
    candidate_blocks = candidate.translated_blocks
    repair_rate = (
        candidate.repair_count / candidate.group_count
        if candidate.group_count
        else (0.0 if candidate.repair_count == 0 else float("inf"))
    )
    baseline_glossary = _check_count(baseline.glossary_checks, "violations")
    candidate_glossary = _check_count(candidate.glossary_checks, "violations")
    archive_regression = bool(
        candidate.archive_comparison.get("regressions", not candidate.valid_archive)
    )

    gates["token_savings"] = _gate(
        token_savings >= 0.50,
        observed=token_savings,
        threshold=0.50,
        comparison=">=",
    )
    gates["request_savings"] = _gate(
        request_savings >= 0.40,
        observed=request_savings,
        threshold=0.40,
        comparison=">=",
    )
    gates["metered_cost_ratio"] = _gate(
        cost_ratio is not None and cost_ratio <= 0.60,
        observed=cost_ratio,
        threshold=0.60,
        comparison="<=",
    )
    gates["wall_time"] = _gate(
        candidate.wall_time_seconds <= 80.0,
        observed=candidate.wall_time_seconds,
        threshold=80.0,
        comparison="<=",
    )
    gates["exact_block_count"] = _gate(
        bool(expected_blocks) and candidate_blocks == expected_blocks,
        observed={"expected": expected_blocks, "candidate": candidate_blocks},
        threshold="candidate == baseline translated block count",
        comparison="==",
    )
    gates["zero_fallbacks"] = _gate(candidate.fallback_count == 0, candidate.fallback_count, 0, "==")
    gates["zero_drops"] = _gate(candidate.drop_count == 0, candidate.drop_count, 0, "==")
    gates["zero_duplicates"] = _gate(
        candidate.duplicate_count == 0, candidate.duplicate_count, 0, "=="
    )
    gates["zero_reorders"] = _gate(candidate.reorder_count == 0, candidate.reorder_count, 0, "==")
    gates["zero_empty"] = _gate(candidate.empty_count == 0, candidate.empty_count, 0, "==")
    gates["zero_missing_or_unexpected"] = _gate(
        candidate.missing_blocks == 0 and candidate.unexpected_blocks == 0,
        {
            "missing": candidate.missing_blocks,
            "unexpected": candidate.unexpected_blocks,
        },
        0,
        "==",
    )
    gates["repair_rate"] = _gate(
        repair_rate <= 0.02,
        None if repair_rate == float("inf") else repair_rate,
        0.02,
        "<=",
    )
    gates["no_cjk"] = _gate(candidate.cjk_remnants == 0, candidate.cjk_remnants, 0, "==")
    gates["no_glossary_regression"] = _gate(
        candidate_glossary <= baseline_glossary,
        {"baseline": baseline_glossary, "candidate": candidate_glossary},
        "candidate <= baseline",
        "<=",
    )
    gates["valid_epub"] = _gate(candidate.valid_archive, candidate.valid_archive, True, "==")
    gates["baseline_valid_epub"] = _gate(
        baseline.valid_archive, baseline.valid_archive, True, "=="
    )
    gates["no_epub_regression"] = _gate(not archive_regression, archive_regression, False, "==")
    gates["no_runner_errors"] = _gate(not candidate.errors, list(candidate.errors), "empty", "==")
    return Evaluation(go=all(item["passed"] for item in gates.values()), gates=gates)


def _gate(passed: bool, observed: Any, threshold: Any, comparison: str) -> dict[str, Any]:
    return {
        "passed": bool(passed),
        "observed": _jsonable(observed),
        "threshold": _jsonable(threshold),
        "comparison": comparison,
    }


def _savings_ratio(baseline: int, candidate: int) -> float:
    if baseline <= 0:
        return 0.0
    return round((baseline - candidate) / baseline, 6)


def _check_count(checks: Mapping[str, Any] | None, key: str) -> int:
    if not checks:
        return 0
    try:
        return max(0, int(checks.get(key, 0)))
    except (TypeError, ValueError):
        return 0


def render_markdown(result: BenchmarkResult) -> str:
    """Render a stable report without timestamps or filesystem-dependent order."""

    lines = [
        "# One-pass benchmark",
        "",
        f"Decision: **{'GO' if result.evaluation.go else 'NO-GO'}**",
        "",
        "## Gates",
        "",
        "| Gate | Result | Observed | Threshold |",
        "| --- | --- | --- | --- |",
    ]
    for name in sorted(result.evaluation.gates):
        gate = result.evaluation.gates[name]
        result_text = "PASS" if gate.get("passed") else "FAIL"
        lines.append(
            f"| `{name}` | {result_text} | "
            f"{_markdown_value(gate.get('observed'))} | "
            f"{_markdown_value(gate.get('threshold'))} |"
        )
    lines.extend(
        [
            "",
            "## Metrics",
            "",
            "| Pipeline | Total tokens | Fresh input | Cached input | Output | Reasoning | Cost USD | Requests | Retries | Repairs | Fallbacks | Wall seconds | Valid EPUB |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for job in (result.baseline, result.candidate):
        metrics = job.metrics
        metrics.normalize()
        lines.append(
            "| {pipeline} | {total:,} | {fresh:,} | {cached:,} | {output:,} | {reasoning:,} | {cost:.8f} | "
            "{requests:,} | {retries:,} | {repairs:,} | {fallbacks:,} | {wall:.6f} | {valid} |".format(
                pipeline=job.pipeline,
                total=metrics.total_tokens,
                fresh=metrics.fresh_input_tokens,
                cached=metrics.cached_input_tokens,
                output=metrics.output_tokens,
                reasoning=metrics.reasoning_tokens,
                cost=metrics.estimated_cost_usd,
                requests=metrics.request_count,
                retries=metrics.retry_count,
                repairs=metrics.repair_count,
                fallbacks=metrics.fallback_count,
                wall=metrics.wall_time_seconds,
                valid="yes" if metrics.valid_archive else "no",
            )
        )
    lines.extend(
        [
            "",
            "## Quality and structure",
            "",
            "| Pipeline | Blocks expected/translated | Missing | Unexpected | Drops | Duplicates | Reorders | Empty | CJK remnants | Glossary violations | Media removed/changed | Structure removed/unexpected changed | ZIP duplicates added |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for job in (result.baseline, result.candidate):
        metrics = job.metrics
        comparison = metrics.archive_comparison or {}
        lines.append(
            "| {pipeline} | {expected}/{translated} | {missing} | {unexpected} | {drops} | "
            "{duplicates} | {reorders} | {empty} | {cjk} | {glossary} | {media_removed}/{media_changed} | "
            "{structure_removed}/{structure_changed} | {duplicate_entries_added} |".format(
                pipeline=job.pipeline,
                expected=metrics.expected_blocks,
                translated=metrics.translated_blocks,
                missing=metrics.missing_blocks,
                unexpected=metrics.unexpected_blocks,
                drops=metrics.drop_count,
                duplicates=metrics.duplicate_count,
                reorders=metrics.reorder_count,
                empty=metrics.empty_count,
                cjk=metrics.cjk_remnants,
                glossary=_check_count(metrics.glossary_checks, "violations"),
                media_removed=len(comparison.get("media_removed", [])),
                media_changed=len(comparison.get("media_changed", [])),
                structure_removed=len(comparison.get("structure_removed", [])),
                structure_changed=(
                    len(comparison.get("structure_regression_changed", []))
                    + len(comparison.get("structure_added_unexpected", []))
                ),
                duplicate_entries_added=len(comparison.get("duplicate_entries_added", [])),
            )
        )
    lines.extend(
        [
            "",
            "## Source",
            "",
            f"- EPUB: `{result.source_path.name}`",
            f"- Token budget: `{result.token_budget:,}`",
            f"- Provider: `{result.settings.provider if result.settings else 'unspecified'}`",
            f"- Model: `{result.settings.model if result.settings else 'unspecified'}`",
            f"- Pricing: `{result.settings.pricing_source if result.settings else 'unpriced'}`",
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(result: BenchmarkResult, output_path: str | Path) -> tuple[Path, Path]:
    """Write JSON and its adjacent deterministic Markdown summary."""

    json_path = Path(output_path).expanduser()
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path = json_path.with_suffix(".md")
    markdown_path.write_text(render_markdown(result), encoding="utf-8")
    return json_path, markdown_path


def inspect_epub(path: str | Path) -> dict[str, Any]:
    """Inspect ZIP validity and deterministic structure/media inventories."""

    path = Path(path)
    inventory: dict[str, Any] = {
        "path": str(path),
        "valid_archive": False,
        "zip_test_error": None,
        "entry_count": 0,
        "entries": [],
        "duplicate_entries": [],
        "non_text_entries": [],
        "structure_entries": [],
        "structure_inventory": {},
        "media_entries": [],
        "media_inventory": {},
    }
    if not path.is_file() or not zipfile.is_zipfile(path):
        inventory["zip_test_error"] = "not a ZIP archive"
        return inventory
    try:
        with zipfile.ZipFile(path) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            names = sorted(info.filename for info in infos)
            inventory["entry_count"] = len(names)
            inventory["entries"] = names
            inventory["duplicate_entries"] = sorted(
                name for name, count in Counter(names).items() if count > 1
            )
            bad_name = archive.testzip()
            if bad_name:
                inventory["zip_test_error"] = f"CRC failure: {bad_name}"
                return inventory
            if not infos or infos[0].filename != "mimetype":
                inventory["zip_test_error"] = "mimetype must be the first ZIP entry"
                return inventory
            if infos[0].compress_type != zipfile.ZIP_STORED:
                inventory["zip_test_error"] = "mimetype must be uncompressed"
                return inventory
            mimetype = archive.read("mimetype").decode("ascii", errors="strict").strip()
            if mimetype != "application/epub+zip":
                inventory["zip_test_error"] = "mimetype is not application/epub+zip"
                return inventory
            media: dict[str, dict[str, Any]] = {}
            structure: dict[str, dict[str, Any]] = {}
            non_text: list[str] = []
            for info in infos:
                name = info.filename
                suffix = Path(name).suffix.lower()
                if suffix in MEDIA_SUFFIXES:
                    digest = hashlib.sha256(archive.read(name)).hexdigest()
                    media[name] = {"size": info.file_size, "sha256": digest}
                if suffix in STRUCTURE_SUFFIXES or Path(name).name.lower() in {
                    "container.xml", "nav.xhtml", "toc.xhtml", "toc.ncx"
                }:
                    structure[name] = {
                        "size": info.file_size,
                        "sha256": hashlib.sha256(archive.read(name)).hexdigest(),
                    }
                if suffix not in TEXT_SUFFIXES:
                    non_text.append(name)
            inventory["media_inventory"] = {key: media[key] for key in sorted(media)}
            inventory["media_entries"] = sorted(media)
            inventory["structure_inventory"] = {
                key: structure[key] for key in sorted(structure)
            }
            inventory["structure_entries"] = sorted(structure)
            inventory["non_text_entries"] = sorted(non_text)
            inventory["valid_archive"] = True
    except (OSError, ValueError, KeyError, UnicodeError, zipfile.BadZipFile) as exc:
        inventory["zip_test_error"] = f"{type(exc).__name__}: {exc}"
    return inventory


def _logical_epub_name(name: str) -> str:
    """Normalize the application OEBPS/EPUB packaging prefix for comparison."""

    normalized = str(name).replace("\\", "/")
    for prefix in ("OEBPS/", "EPUB/"):
        if normalized.startswith(prefix):
            return normalized[len(prefix) :]
    return normalized


def _inventory_value_map(
    inventory: Mapping[str, Any], mapping_key: str, entries_key: str
) -> dict[str, Any]:
    raw = inventory.get(mapping_key, {})
    if isinstance(raw, Mapping):
        return {_logical_epub_name(name): value for name, value in raw.items()}
    return {
        _logical_epub_name(name): True for name in inventory.get(entries_key, [])
    }


def _display_names(
    logical_names: Sequence[str],
    source_map: Mapping[str, Any],
    candidate_map: Mapping[str, Any],
) -> list[str]:
    """Prefer source spelling in reports, falling back to candidate spelling."""

    source_names = {
        _logical_epub_name(name): str(name)
        for name in source_map.get("_raw_names", [])
    }
    candidate_names = {
        _logical_epub_name(name): str(name)
        for name in candidate_map.get("_raw_names", [])
    }
    return sorted(
        source_names.get(name, candidate_names.get(name, name)) for name in logical_names
    )


def _raw_name_map(inventory: Mapping[str, Any], entries_key: str) -> dict[str, Any]:
    """Return logical entries plus raw names used for human-readable output."""

    values = _inventory_value_map(inventory, entries_key.replace("_entries", "_inventory"), entries_key)
    values["_raw_names"] = list(inventory.get(entries_key, []))
    return values


def _logical_keys(mapping: Mapping[str, Any]) -> set[str]:
    return {key for key in mapping if key != "_raw_names"}


def compare_epub_inventories(
    source: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, Any]:
    """Compare preservation-sensitive EPUB entries and report regressions.

    ``prepare_epub`` legitimately renames the package directory from OEBPS to
    EPUB and rewrites container/OPF/navigation text.  Compare logical paths so
    those expected transformations do not look like removed media or structure.
    Only non-text changes and unexpected structure changes are regressions.
    """

    source_media = _raw_name_map(source, "media_entries")
    candidate_media = _raw_name_map(candidate, "media_entries")
    source_structure = _raw_name_map(source, "structure_entries")
    candidate_structure = _raw_name_map(candidate, "structure_entries")
    source_non_text = _raw_name_map(source, "non_text_entries")
    candidate_non_text = _raw_name_map(candidate, "non_text_entries")
    source_media_hashes = _inventory_value_map(source, "media_inventory", "media_entries")
    candidate_media_hashes = _inventory_value_map(candidate, "media_inventory", "media_entries")
    source_structure_hashes = _inventory_value_map(
        source, "structure_inventory", "structure_entries"
    )
    candidate_structure_hashes = _inventory_value_map(
        candidate, "structure_inventory", "structure_entries"
    )
    source_duplicates = {
        _logical_epub_name(name) for name in source.get("duplicate_entries", [])
    }
    candidate_duplicates = {
        _logical_epub_name(name) for name in candidate.get("duplicate_entries", [])
    }
    source_media_keys = _logical_keys(source_media)
    candidate_media_keys = _logical_keys(candidate_media)
    source_structure_keys = _logical_keys(source_structure)
    candidate_structure_keys = _logical_keys(candidate_structure)
    source_non_text_keys = _logical_keys(source_non_text)
    candidate_non_text_keys = _logical_keys(candidate_non_text)
    media_common = source_media_keys & candidate_media_keys
    structure_common = source_structure_keys & candidate_structure_keys
    media_changed = _display_names(
        [name for name in media_common if source_media_hashes.get(name) != candidate_media_hashes.get(name)],
        source_media,
        candidate_media,
    )
    structure_changed = _display_names(
        [
            name
            for name in structure_common
            if source_structure_hashes.get(name) != candidate_structure_hashes.get(name)
        ],
        source_structure,
        candidate_structure,
    )
    media_removed = _display_names(
        sorted(source_media_keys - candidate_media_keys), source_media, candidate_media
    )
    media_added = _display_names(
        sorted(candidate_media_keys - source_media_keys), source_media, candidate_media
    )
    structure_removed = _display_names(
        sorted(source_structure_keys - candidate_structure_keys), source_structure, candidate_structure
    )
    structure_added = _display_names(
        sorted(candidate_structure_keys - source_structure_keys), source_structure, candidate_structure
    )
    non_text_removed = _display_names(
        sorted(source_non_text_keys - candidate_non_text_keys), source_non_text, candidate_non_text
    )
    non_text_added = _display_names(
        sorted(candidate_non_text_keys - source_non_text_keys), source_non_text, candidate_non_text
    )
    source_duplicate_map = {"_raw_names": list(source.get("duplicate_entries", []))}
    candidate_duplicate_map = {"_raw_names": list(candidate.get("duplicate_entries", []))}
    duplicate_added = _display_names(
        sorted(candidate_duplicates - source_duplicates), source_duplicate_map, candidate_duplicate_map
    )
    mutable_structure = {"content.opf", "nav.xhtml", "toc.ncx", "container.xml"}
    generated_structure = {"nav.xhtml"}
    structure_regression_changed = [
        name
        for name in structure_changed
        if Path(name).name.lower() not in mutable_structure
    ]
    structure_added_unexpected = [
        name
        for name in structure_added
        if Path(name).name.lower() not in generated_structure
    ]
    regressions = (
        not bool(candidate.get("valid_archive", False))
        or bool(media_removed)
        or bool(media_changed)
        or bool(structure_removed)
        or bool(structure_regression_changed)
        or bool(structure_added_unexpected)
        or bool(non_text_removed)
        or bool(duplicate_added)
    )
    return {
        "regressions": regressions,
        "source_valid_archive": bool(source.get("valid_archive", False)),
        "candidate_valid_archive": bool(candidate.get("valid_archive", False)),
        "media_removed": media_removed,
        "media_added": media_added,
        "media_changed": media_changed,
        "structure_removed": structure_removed,
        "structure_added": structure_added,
        "structure_changed": structure_changed,
        "structure_regression_changed": structure_regression_changed,
        "structure_added_unexpected": structure_added_unexpected,
        "non_text_removed": non_text_removed,
        "non_text_added": non_text_added,
        "source_duplicate_entries": sorted(source_duplicates),
        "candidate_duplicate_entries": sorted(candidate_duplicates),
        "duplicate_entries_added": duplicate_added,
    }


def analyze_epub_text(path: str | Path) -> dict[str, Any]:
    """Extract text/block counts without requiring BeautifulSoup."""

    result = {"text": "", "block_count": 0, "empty_blocks": 0, "cjk_count": 0}
    path = Path(path)
    if not path.is_file() or not zipfile.is_zipfile(path):
        return result
    chunks: list[str] = []
    blocks: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for name in sorted(archive.namelist()):
                if Path(name).suffix.lower() not in {".html", ".htm", ".xhtml", ".xml"}:
                    continue
                try:
                    content = archive.read(name).decode("utf-8", errors="replace")
                except KeyError:
                    continue
                chunks.append(_strip_markup(content))
                suffix = Path(name).suffix.lower()
                basename = Path(name).name.lower()
                if suffix in {".opf", ".ncx"} or basename in {"nav.xhtml", "toc.xhtml", "toc.ncx"}:
                    continue
                blocks.extend(_extract_blocks(content))
    except (OSError, zipfile.BadZipFile):
        return result
    text = "\n".join(chunks)
    result["text"] = text
    result["block_count"] = len(blocks)
    result["empty_blocks"] = sum(not block.strip() for block in blocks)
    result["cjk_count"] = sum(_is_cjk(character) for character in text)
    return result


def check_glossary(
    source_path: str | Path,
    output_path: str | Path,
    glossary_dir: str | Path,
) -> dict[str, Any]:
    """Check only glossary terms actually present in the source EPUB."""

    source_text = analyze_epub_text(source_path)["text"]
    output_text = analyze_epub_text(output_path)["text"]
    terms = _load_glossary(Path(glossary_dir), Path(source_path).name)
    source_remnants = 0
    missing_targets = 0
    target_hits = 0
    applicable = 0
    for source_term, target_term in sorted(terms.items()):
        source_term = str(source_term)
        target_term = str(target_term)
        occurrences = source_text.count(source_term)
        if occurrences <= 0:
            continue
        applicable += 1
        source_remnants += output_text.count(source_term)
        target_hits += output_text.count(target_term)
        if target_term not in output_text:
            missing_targets += 1
    violations = source_remnants + missing_targets
    return {
        "terms_available": len(terms),
        "terms_checked": applicable,
        "target_hits": target_hits,
        "source_remnants": source_remnants,
        "missing_targets": missing_targets,
        "violations": violations,
        "passed": violations == 0,
    }


def _load_glossary(glossary_dir: Path, source_name: str) -> dict[str, str]:
    names = ["global.json", f"{_safe_stem(source_name)}.json"]
    terms: dict[str, str] = {}
    for name in names:
        path = glossary_dir / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, Mapping):
            terms.update({str(key): str(value) for key, value in data.items()})
    return terms


def _safe_stem(name: str) -> str:
    stem = Path(name).stem
    return "".join(character for character in stem if character.isalnum() or character in "-_") or "untitled"


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _strip_markup(content: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(content)
        parser.close()
        text = "".join(parser.parts)
    except Exception:  # noqa: BLE001 - malformed XHTML still gets a report
        text = re.sub(r"<[^>]*>", " ", content)
    return html.unescape(text)


def _extract_blocks(content: str) -> list[str]:
    blocks: list[str] = []
    pattern = re.compile(
        r"<(?P<tag>p|h[1-6])\b[^>]*>(?P<body>.*?)</(?P=tag)\s*>",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(content):
        blocks.append(_strip_markup(match.group("body")).strip())
    if re.search(r"<p\b", content, re.IGNORECASE):
        return blocks
    body_match = re.search(r"<body\b[^>]*>(?P<body>.*?)</body\s*>", content, re.IGNORECASE | re.DOTALL)
    if not body_match:
        return blocks
    body = body_match.group("body")
    body = re.sub(r"<h[1-6]\b[^>]*>.*?</h[1-6]\s*>", "", body, flags=re.IGNORECASE | re.DOTALL)
    for chunk in re.split(r"<br\s*/?>", body, flags=re.IGNORECASE):
        text = _strip_markup(chunk).strip()
        if text:
            blocks.append(text)
    return blocks


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return any(start <= codepoint <= end for start, end in CJK_RANGES)


def _entry_signature(inventory: Mapping[str, Any], name: str) -> Any:
    if name in inventory.get("media_inventory", {}):
        return inventory["media_inventory"].get(name)
    return name in inventory.get("entries", [])


def _merge_check_dicts(derived: Mapping[str, Any], supplied: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(derived)
    for key, value in supplied.items():
        if key in {"violations", "source_remnants", "missing_targets", "target_hits"}:
            try:
                merged[key] = max(int(merged.get(key, 0)), int(value))
            except (TypeError, ValueError):
                merged[key] = value
        else:
            merged[key] = value
    if "violations" in merged:
        merged["passed"] = int(merged["violations"]) == 0
    return merged


def _default_runner(job: JobSpec, collector: MetricsCollector) -> Mapping[str, Any]:
    """Run the selected pipeline against real services after CLI guards."""

    cli_dir = Path(__file__).resolve().parents[1] / "cli"
    if not cli_dir.is_dir():
        raise BenchmarkError(f"CLI directory not found: {cli_dir}")
    modules = _load_cli_modules(cli_dir, job.sandbox)
    provider_env = PROVIDER_KEY_ENV.get(job.settings.provider, "")
    api_key = (job.api_key or "").strip()
    if not api_key and provider_env:
        api_key = os.environ.get(provider_env, "").strip()
    if job.llm_factory is None and not api_key:
        raise BenchmarkError(
            f"no API key in {provider_env or 'the configured settings file or provider environment'}"
        )

    from epub_translator import LLM, SubmitKind, language, translate

    def extra_body(thinking: str) -> dict[str, Any]:
        if thinking in {"adaptive", "enabled", "disabled"} and job.settings.provider in {
            "deepseek",
            "opencode-go",
        }:
            return {"thinking": {"type": thinking}}
        return {}

    def make_llm(thinking: str) -> Any:
        if job.llm_factory is not None:
            llm = _invoke_llm_factory(job.llm_factory, job, collector)
        else:
            llm = LLM(
                key=api_key,
                url=job.settings.base_url,
                model=job.settings.model,
                token_encoding="cl100k_base",
                cache_path=str(job.cache_dir),
                retry_times=job.settings.retry_times,
                retry_interval_seconds=6.0,
                temperature=0.4,
                extra_body=extra_body(thinking),
            )
        return _instrument_llm(llm, collector, token_budget=job.token_budget)

    glossary_module = modules["glossary"]
    book_key = glossary_module.book_key(job.source_path.name)
    glossary = glossary_module.merge_glossaries(book_key)
    prompt = job.settings.user_prompt or glossary_module.build_translation_prompt(glossary)
    translation_llm = make_llm(job.settings.thinking)
    fill_llm = None
    prepared_source = modules["translate_book"].prepare_epub(
        job.source_path, job.cache_dir / "prep"
    )
    onepass_report = None
    pending: Exception | None = None
    try:
        if job.pipeline == "two-pass":
            fill_llm = make_llm(job.settings.fill_thinking)
            translate(
                source_path=str(prepared_source),
                target_path=str(job.output_path),
                target_language=language.ENGLISH,
                submit=SubmitKind.REPLACE,
                user_prompt=prompt,
                max_retries=job.settings.max_retries,
                translation_llm=translation_llm,
                fill_llm=fill_llm,
                concurrency=job.settings.concurrency,
                max_group_tokens=job.settings.max_group_tokens,
                on_fill_failed=lambda *_args, **_kwargs: collector.record_fallback(),
            )
        else:
            onepass_module = modules.get("onepass")
            if onepass_module is None or not hasattr(onepass_module, "translate_one_pass"):
                raise BenchmarkError(
                    "one-pass core API is unavailable; merge cli/onepass.py before live benchmarking"
                )

            def on_protocol_failed(*_args: Any, **_kwargs: Any) -> None:
                return None

            onepass_report = onepass_module.translate_one_pass(
                source_path=prepared_source,
                target_path=job.output_path,
                target_language=language.ENGLISH,
                user_prompt=prompt,
                translation_llm=translation_llm,
                max_group_tokens=job.settings.max_group_tokens,
                max_retries=job.settings.max_retries,
                strict=job.settings.strict,
                chapter_limit=job.chapter_limit,
                on_protocol_failed=on_protocol_failed,
                group_concurrency=job.settings.concurrency,
            )
    except Exception as exc:  # noqa: BLE001 - report runner failures without masking telemetry
        pending = exc
        if onepass_report is None:
            onepass_report = getattr(exc, "report", None)
    finally:
        _snapshot_llm(translation_llm, collector)
        if fill_llm is not None:
            _snapshot_llm(fill_llm, collector)

    if onepass_report is not None:
        _merge_onepass_report(onepass_report, collector)
    if collector.metrics.total_tokens > job.token_budget:
        raise BenchmarkError(
            f"{job.pipeline} exceeded token budget: "
            f"{collector.metrics.total_tokens:,} > {job.token_budget:,}"
        )
    if pending is not None:
        raise pending
    return {"target": str(job.output_path)}


def _merge_onepass_report(report: Any, collector: MetricsCollector) -> None:
    """Merge deterministic one-pass protocol counters into benchmark metrics."""

    if report is None:
        return
    groups = int(getattr(report, "groups", 0) or 0)
    if groups:
        collector.record_group(groups)
    full_retries = int(getattr(report, "full_group_retries", 0) or 0)
    subset_requests = int(getattr(report, "subset_requests", 0) or 0)
    individual_requests = int(getattr(report, "individual_requests", 0) or 0)
    collector.record_repair(full_retries + subset_requests + individual_requests)
    fallback_units = int(getattr(report, "fallback_units", 0) or 0)
    if fallback_units:
        collector.record_fallback(fallback_units)
    cjk_remnants = getattr(report, "cjk_remnants", ())
    if cjk_remnants:
        collector.metrics.cjk_remnants = max(
            collector.metrics.cjk_remnants, len(cjk_remnants)
        )
    failures = getattr(report, "failures", ())
    if failures:
        collector.metrics.errors.extend(
            f"one-pass protocol failure: {item}" for item in failures
        )



def _load_cli_modules(cli_dir: Path, sandbox: SandboxPaths) -> dict[str, Any]:
    cli_text = str(cli_dir)
    if cli_text not in sys.path:
        sys.path.insert(0, cli_text)
    config = importlib.import_module("config")
    _patch_loaded_module_paths(sandbox, [config])
    glossary = importlib.import_module("glossary")
    _patch_loaded_module_paths(sandbox, [glossary])
    translate_book = importlib.import_module("translate_book")
    _patch_loaded_module_paths(sandbox, [translate_book])
    onepass = None
    try:
        onepass = importlib.import_module("onepass")
        _patch_loaded_module_paths(sandbox, [onepass])
    except ModuleNotFoundError:
        pass
    return {
        "config": config,
        "glossary": glossary,
        "translate_book": translate_book,
        "onepass": onepass,
    }


def _invoke_llm_factory(
    factory: Callable[..., Any], job: JobSpec, collector: MetricsCollector
) -> Any:
    """Call injected LLM factories without requiring one exact signature."""

    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return factory(job, collector)
    parameters = list(signature.parameters.values())
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters):
        return factory(job=job, settings=job.settings, collector=collector, telemetry=collector)
    positional: list[Any] = []
    kwargs: dict[str, Any] = {}
    for parameter in parameters:
        name = parameter.name.lower()
        if name in {"job", "spec", "job_spec"}:
            value = job
        elif name in {"settings", "config"}:
            value = job.settings
        elif name in {"collector", "telemetry", "metrics"}:
            value = collector
        else:
            value = job if not positional else collector
        if parameter.kind == inspect.Parameter.KEYWORD_ONLY:
            kwargs[parameter.name] = value
        else:
            positional.append(value)
    return factory(*positional, **kwargs)


class _InstrumentedExecutor:
    """Internal adapter used only by the default live runner."""


def _instrument_llm(
    llm: Any, collector: MetricsCollector, *, token_budget: int | None = None
) -> Any:
    statistics = getattr(llm, "_statistics", None)
    if statistics is not None and hasattr(statistics, "submit_usage"):
        original_submit = statistics.submit_usage

        def submit_usage(usage: Any) -> Any:
            _record_usage_object(collector, usage)
            if token_budget is not None and collector.metrics.total_tokens > int(token_budget):
                raise BenchmarkError(
                    f"{collector.metrics.name} exceeded token budget: "
                    f"{collector.metrics.total_tokens:,} > {int(token_budget):,}"
                )
            return original_submit(usage)

        statistics.submit_usage = submit_usage
    executor = getattr(llm, "_executor", None)
    original_invoke = getattr(executor, "_invoke_model", None)
    if executor is not None and callable(original_invoke):

        def invoke_model(*args: Any, **kwargs: Any) -> Any:
            collector.record_request()
            try:
                return original_invoke(*args, **kwargs)
            except Exception:
                collector.record_retry()
                raise

        executor._invoke_model = invoke_model
    return llm


def _record_usage_object(collector: MetricsCollector, usage: Any) -> None:
    if usage is None:
        return
    if isinstance(usage, Mapping):
        details = usage.get("prompt_tokens_details") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        cached = (
            details.get("cached_tokens", 0)
            if isinstance(details, Mapping)
            else getattr(details, "cached_tokens", 0)
        )
        reasoning = (
            completion_details.get("reasoning_tokens", 0)
            if isinstance(completion_details, Mapping)
            else getattr(completion_details, "reasoning_tokens", 0)
        )
        collector.record_usage(
            input_tokens=_number(usage, "prompt_tokens", "input_tokens", "input"),
            cached_input_tokens=int(cached or 0),
            output_tokens=_number(usage, "completion_tokens", "output_tokens", "output"),
            reasoning_tokens=int(reasoning or 0),
            total_tokens=_number(usage, "total_tokens", "total"),
        )
        return
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) if details is not None else 0
    completion_details = getattr(usage, "completion_tokens_details", None)
    reasoning = getattr(completion_details, "reasoning_tokens", 0) if completion_details else 0
    collector.record_usage(
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        cached_input_tokens=int(cached or 0),
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        reasoning_tokens=int(reasoning or 0),
        total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
    )


def _snapshot_llm(llm: Any, collector: MetricsCollector) -> None:
    collector.set_usage_snapshot(
        input_tokens=int(getattr(llm, "input_tokens", 0) or 0),
        cached_input_tokens=int(getattr(llm, "input_cache_tokens", 0) or 0),
        output_tokens=int(getattr(llm, "output_tokens", 0) or 0),
        reasoning_tokens=int(getattr(llm, "reasoning_tokens", 0) or 0),
        total_tokens=int(getattr(llm, "total_tokens", 0) or 0),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare equivalent two-pass and one-pass EPUB translation jobs."
    )
    parser.add_argument("--source", required=True, help="source EPUB path")
    parser.add_argument("--baseline", choices=PIPELINES, default="two-pass")
    parser.add_argument("--candidate", choices=PIPELINES, default="one-pass")
    parser.add_argument("--output", required=True, help="JSON report output path")
    parser.add_argument(
        "--token-budget",
        type=int,
        default=None,
        help="positive per-run token budget; required for a paid run",
    )
    parser.add_argument(
        "--confirm-paid",
        action="store_true",
        help="explicitly authorize live paid provider requests",
    )
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--settings-file", default=None, help="read-only production settings JSON")
    parser.add_argument("--glossary-dir", default=None, help="read-only glossary directory")
    parser.add_argument("--target-language", default="English")
    parser.add_argument(
        "--thinking", choices=("adaptive", "enabled", "disabled"), default=None
    )
    parser.add_argument(
        "--fill-thinking", choices=("adaptive", "enabled", "disabled"), default=None
    )
    parser.add_argument("--max-group-tokens", type=int, default=None)
    parser.add_argument("--max-retries", type=int, default=None)
    parser.add_argument("--retry-times", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--strict", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--chapter-limit", type=int, default=0)
    parser.add_argument("--sandbox-root", default=None)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    runners: Mapping[str, Callable[..., Any]] | Callable[..., Any] | None = None,
    settings: PinnedSettings | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.token_budget is None:
        parser.error("--token-budget is required; refusing an unbounded paid run")
    if args.token_budget <= 0:
        parser.error("--token-budget must be positive")
    # This is deliberately the first normal console output: the operator sees
    # the maximum requested spend before the paid-confirmation gate dispatches.
    print(f"Requested token budget: {args.token_budget:,} tokens")
    if not args.confirm_paid:
        parser.error("live benchmark requires explicit --confirm-paid")
    if args.chapter_limit < 0:
        parser.error("--chapter-limit must be zero or positive")
    source = Path(args.source).expanduser()
    if not source.is_file():
        parser.error(f"source EPUB does not exist: {source}")

    inferred_settings_file = source.parent.parent / "settings.json"
    settings_file = Path(args.settings_file).expanduser() if args.settings_file else (
        inferred_settings_file if inferred_settings_file.is_file() else DEFAULT_SETTINGS_FILE
    )
    glossary_source_dir = (
        Path(args.glossary_dir).expanduser()
        if args.glossary_dir
        else source.parent.parent / "glossaries"
    )
    api_key: str | None = None
    try:
        if settings is None:
            settings, api_key = load_configured_settings(
                settings_file,
                provider=args.provider,
                model=args.model,
                base_url=args.base_url,
                target_language=args.target_language,
                thinking=args.thinking,
                fill_thinking=args.fill_thinking,
                max_group_tokens=args.max_group_tokens,
                max_retries=args.max_retries,
                retry_times=args.retry_times,
                concurrency=args.concurrency,
                strict=args.strict,
            )
        else:
            # Programmatic callers may inject settings while still using the
            # configured key; this path never includes the key in the report.
            _, api_key = load_configured_settings(
                settings_file,
                provider=settings.provider,
                model=settings.model,
                base_url=settings.base_url,
                target_language=settings.target_language,
                thinking=settings.thinking,
                fill_thinking=settings.fill_thinking,
                max_group_tokens=settings.max_group_tokens,
                max_retries=settings.max_retries,
                retry_times=settings.retry_times,
                concurrency=settings.concurrency,
                strict=settings.strict,
            )
    except ValueError as exc:
        parser.error(str(exc))

    result = run_benchmark(
        source,
        baseline_pipeline=args.baseline,
        candidate_pipeline=args.candidate,
        token_budget=args.token_budget,
        settings=settings,
        runners=runners,
        sandbox_root=args.sandbox_root,
        glossary_source_dir=glossary_source_dir,
        api_key=api_key,
        chapter_limit=args.chapter_limit,
    )
    json_path, markdown_path = write_reports(result, args.output)
    print(f"Decision: {'GO' if result.evaluation.go else 'NO-GO'}")
    print(f"JSON report: {json_path}")
    print(f"Markdown summary: {markdown_path}")
    return 0 if result.evaluation.go else 1


def _number(mapping: Mapping[str, Any], *names: str) -> int | None:
    value = _first_value(mapping, names)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_value(mapping: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if name in mapping and mapping[name] is not None:
            return mapping[name]
    return None


def _object_mapping(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        data = value.to_dict()
        return dict(data) if isinstance(data, Mapping) else {}
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "item") and callable(value.item):
        try:
            return value.item()
        except Exception:  # noqa: BLE001, S110
            pass
    return value


def _markdown_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.6f}"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
