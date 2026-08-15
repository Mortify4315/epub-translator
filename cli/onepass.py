"""One-pass numbered-paragraph translation engine.

Replaces the model-driven FILL pass with deterministic paragraph reinsertion
for structurally flat chapters: eligible chapter bodies are normalized into
flat ``p``/``h1``-``h6`` units, sent to one translation call as numbered
groups (``[N] source``), validated against the exact ``1..N`` contract, and
written back positionally in code. Structurally complex chapters are not
skipped: they are automatically routed through the current two-pass engine
(chapter-scoped legacy fallback, plan C3) — the same TRANSLATE + FILL
machinery the production ``translate()`` pipeline uses, applied to this
chapter's body only. Flat chapters never take the two-pass route.

The numbered protocol, parser, validator, token-aware grouping, and the
bounded repair ladder are pure and deterministic. LLM transport/cache and
EPUB ZIP/metadata/TOC handling are reused from the ``epub_translator``
package — no HTTP, cache, or ZIP code is invented here.

Validation rejects substantive CJK remnants (contiguous runs of 2+ CJK
characters) and source copies in strict and non-strict modes alike: the
bounded repair ladder reroutes the failed items, strict mode aborts with
OnePassProtocolError when the ladder cannot fix them, and non-strict mode
preserves the source text and records the failure visibly. A single
isolated CJK glyph (a quoted term, SPEC §9.1) is legitimate context and is
never treated as a remnant. The chapter-scoped two-pass fallback is gated
by the same rule: strict mode reroutes a fallback chapter once through a
corrective two-pass re-run and then aborts if CJK remains; non-strict mode
records the remnants in the report instead of shipping them silently.
"""

from __future__ import annotations

import dataclasses
import difflib
import importlib.metadata
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from epub_translator import FillFailedEvent
from epub_translator.epub import (
    Zip,
    read_metadata,
    read_toc,
    search_spine_paths,
    write_metadata,
    write_toc,
)
from epub_translator.segment import search_text_segments
from epub_translator.translation.xml_interrupter import XMLInterrupter
from epub_translator.xml import XMLLikeNode, deduplicate_ids_in_element, find_first
from epub_translator.xml_translator import SubmitKind, TranslationTask, XMLTranslator

from translate_book import ChapterLimitReached

# Protocol version participates in the LLM cache seed so cache identity
# includes the pipeline + protocol version (plan section 4).
PROTOCOL_VERSION = "one-pass-v1"

HEADING_TAGS = frozenset(f"h{i}" for i in range(1, 7))
FLAT_TAGS = frozenset({"p"} | HEADING_TAGS)

# Anchor strictly at line start per the plan: ^\[(\d+)\]\s*(.*)$
_INDEX_RE = re.compile(r"^\[(\d+)\]\s*(.*)$")
# Substantive CJK remnant: a contiguous run of 2+ CJK characters. A single
# isolated glyph (e.g. a quoted term like ``凹``, SPEC §9.1) is legitimate
# context, not a remnant, and never invalidates a group or a chapter.
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff]{2,}")

# Characters that may appear in a paragraph made purely of punctuation.
_PUNCTUATION_CHARS = frozenset(
    """。，、！？；：""''（）《》〈〉【】—…～·,.!?;:()[]{}<>"'`~-–—_+*/\\=@#$%^&| \t\n\r"""
)

PROTOCOL_CONTRACT = (
    "Translate every numbered paragraph separately and in order.\n"
    "Return exactly the same indices using `[N] translation`.\n"
    "Never merge, split, reorder, or omit paragraphs.\n"
    "Output only numbered translations."
)


@dataclass(frozen=True)
class TranslationUnit:
    """One indivisible translation unit: a flat paragraph/heading element
    (or a TOC title / metadata field), numbered for the protocol."""

    index: int
    source_text: str
    target_element: object


@dataclass(frozen=True)
class ParsedGroup:
    """Outcome of parsing one model response against the 1..N contract.

    ``translations`` holds only valid, non-empty items within 1..N.
    ``missing`` are indices in 1..N without a usable translation (absent or
    empty); ``duplicate`` are indices returned more than once (first
    occurrence wins, deterministically); ``unexpected`` are indices outside
    1..N plus stray un-numbered text (recorded as index 0).
    """

    translations: dict[int, str]
    missing: tuple[int, ...] = ()
    duplicate: tuple[int, ...] = ()
    unexpected: tuple[int, ...] = ()


@dataclass(frozen=True)
class UnitIssue:
    """A deterministic validation problem attached to one protocol index."""

    index: int
    kind: str
    message: str


@dataclass
class OnePassReport:
    """Structured outcome of a translate_one_pass run (also attached to
    OnePassProtocolError so a strict abort carries its numbers)."""

    target: Path | None = None
    chapters_done: int = 0
    chapters_total: int = 0
    toc_translated: bool = False
    metadata_translated: bool = False
    translated_units: int = 0
    requests: int = 0
    full_group_retries: int = 0
    subset_requests: int = 0
    individual_requests: int = 0
    fallback_units: int = 0
    failures: list[dict] = field(default_factory=list)
    cjk_remnants: list[dict] = field(default_factory=list)
    groups: int = 0
    legacy_chapters: list[str] = field(default_factory=list)
    # Chapters automatically translated by the chapter-scoped two-pass
    # fallback (plan C3). Subset of legacy_chapters: chapters that were
    # classified non-flat AND carried translatable text, so the two-pass
    # engine actually ran on them.
    legacy_two_pass_chapters: list[str] = field(default_factory=list)


class OnePassProtocolError(RuntimeError):
    """Raised in strict mode when the repair ladder cannot produce a valid
    translation for an item. The run aborts cleanly: the target archive is
    finalized with the chapters translated so far."""

    def __init__(self, message: str, chapter: str | None = None, report: OnePassReport | None = None):
        super().__init__(message)
        self.chapter = chapter
        self.report = report


# ---------------------------------------------------------------------------
# Numbered renderer / parser
# ---------------------------------------------------------------------------


def render_group(units: list[TranslationUnit]) -> str:
    """Render units as ``[N] source`` items separated by blank lines."""
    return "\n\n".join(f"[{u.index}] {u.source_text}" for u in units)


def parse_group(text: str, expected_count: int) -> ParsedGroup:
    """Parse a model response against the exact 1..N contract.

    Rules (plan section 3):
      1. Recognize ``^\\[(\\d+)\\]\\s*(.*)$``.
      2. Non-numbered, non-empty lines continue the current item.
      3. Blank lines are separators.
      4. Require exactly indices 1..N; everything invalid is recorded.
    """
    translations: dict[int, str] = {}
    duplicate: list[int] = []
    unexpected: list[int] = []
    current: int | None = None
    seen: set[int] = set()

    for line in text.splitlines():
        match = _INDEX_RE.match(line)
        if match:
            idx = int(match.group(1))
            if idx in seen:
                duplicate.append(idx)
                continue  # first occurrence wins; do not disturb `current`
            seen.add(idx)
            current = idx
            translations[idx] = match.group(2)
        elif line.strip() == "":
            continue  # separator between items
        elif current is None:
            unexpected.append(0)  # stray un-numbered text before any item
        else:
            translations[current] = translations.get(current, "") + "\n" + line

    missing = tuple(
        i for i in range(1, expected_count + 1)
        if i not in translations or not translations[i].strip()
    )
    clean = {
        i: t for i, t in translations.items()
        if 1 <= i <= expected_count and t.strip()
    }
    unexpected.extend(i for i in seen if not 1 <= i <= expected_count)
    return ParsedGroup(
        translations=clean,
        missing=missing,
        duplicate=tuple(dict.fromkeys(duplicate)),
        unexpected=tuple(dict.fromkeys(unexpected)),
    )


def validate_group(units: list[TranslationUnit], parsed: ParsedGroup) -> list[UnitIssue]:
    """Turn a ParsedGroup into deterministic per-index issues:
    missing / duplicate / unexpected plus empty, truncated, and CJK
    (including full source copies) checks on every usable translation."""
    issues: list[UnitIssue] = []
    n = len(units)
    by_index = {u.index: u for u in units}

    for i in parsed.missing:
        issues.append(UnitIssue(i, "missing", "translation is missing or empty"))
    for i in parsed.duplicate:
        issues.append(UnitIssue(i, "duplicate", "index returned more than once"))
    for i in parsed.unexpected:
        issues.append(UnitIssue(i, "unexpected", f"index outside 1..{n}"))
    for idx, text in sorted(parsed.translations.items()):
        unit = by_index.get(idx)
        if unit is not None:
            issues.extend(_validate_text(unit, text))
    return issues


def _validate_text(unit: TranslationUnit, text: str) -> list[UnitIssue]:
    issues: list[UnitIssue] = []
    stripped = text.strip()
    if not stripped:
        issues.append(UnitIssue(unit.index, "empty", "translation is empty"))
        return issues

    source_len = len(unit.source_text)
    if source_len > 10 and len(stripped) < 0.2 * source_len:
        issues.append(UnitIssue(
            unit.index, "truncated",
            f"translation suspiciously short ({len(stripped)} chars vs source {source_len})",
        ))

    # Only SUBSTANTIVE CJK (a contiguous run of 2+ characters) invalidates
    # the item; a single isolated glyph is legitimate context (SPEC §9.1).
    cjk_runs = _CJK_RUN_RE.findall(stripped)
    if cjk_runs:
        ratio = difflib.SequenceMatcher(None, unit.source_text, stripped).ratio()
        if ratio > 0.9:
            issues.append(UnitIssue(unit.index, "source_copy", "output is a copy of the source"))
        else:
            issues.append(UnitIssue(
                unit.index, "cjk",
                "untranslated CJK characters remain: " + "".join(cjk_runs[:2])[:30],
            ))
    return issues


def is_pure_punctuation(text: str) -> bool:
    """True when the text carries no letters/digits — only punctuation and
    whitespace. Such paragraphs are skipped without changing position."""
    return bool(text) and all(ch in _PUNCTUATION_CHARS for ch in text)


# ---------------------------------------------------------------------------
# Token-aware grouping
# ---------------------------------------------------------------------------


def group_units(
    units: list[TranslationUnit],
    max_group_tokens: int,
    count_tokens,
) -> list[list[TranslationUnit]]:
    """Greedy soft-budget grouping that never splits a unit.

    A unit whose own cost exceeds the budget becomes a group of one (the
    plan: an oversized paragraph becomes one oversized group). A
    non-positive budget means "unlimited" (single group).
    """
    if not units:
        return []
    if max_group_tokens is None or max_group_tokens <= 0:
        return [list(units)]

    groups: list[list[TranslationUnit]] = []
    current: list[TranslationUnit] = []
    used = 0
    for unit in units:
        cost = count_tokens(render_group([unit])) + 2  # + separator overhead
        if current and used + cost > max_group_tokens:
            groups.append(current)
            current, used = [], 0
        current.append(unit)
        used += cost
    if current:
        groups.append(current)
    return groups


# ---------------------------------------------------------------------------
# Repair ladder
# ---------------------------------------------------------------------------


def _request(llm, prompt: str, seed: str) -> str:
    """One cached request through the existing epub_translator.LLM context
    interface. The seed pins protocol version + target language into the
    cache identity."""
    with llm.context(cache_seed_content=seed) as ctx:
        return ctx.request(prompt)


def _validation_errors(units: list[TranslationUnit], parsed: ParsedGroup, issues: list[UnitIssue]) -> str:
    """Deterministic error block for the retry prompt — only parsed facts,
    no model judgment."""
    n = len(units)
    lines = [
        "The previous response was invalid. Fix exactly these problems and "
        "return ALL numbered items again:",
        "",
    ]
    for issue in issues:
        if issue.kind == "unexpected":
            lines.append(f"[{issue.index}] unexpected index — only indices 1..{n} are allowed")
        else:
            lines.append(f"[{issue.index}] {issue.message}")
    return "\n".join(lines)


# OnePassReport counters and lists are mutated from translate_units, which
# runs on worker threads when group dispatch is parallel (see
# _translate_chapter). All report mutations therefore go through these
# lock-protected helpers so a parallel run keeps exact, race-free numbers.
_REPORT_LOCK = threading.Lock()


def _report_bump(report: OnePassReport | None, attr: str, n: int = 1) -> None:
    """Thread-safe increment of one integer field on the shared report."""
    if report is None:
        return
    with _REPORT_LOCK:
        setattr(report, attr, getattr(report, attr) + n)


def _report_append(report: OnePassReport | None, attr: str, item) -> None:
    """Thread-safe append to one list field on the shared report."""
    if report is None:
        return
    with _REPORT_LOCK:
        getattr(report, attr).append(item)


def _record_issues(report: OnePassReport | None, issues: list[UnitIssue]) -> None:
    if report is None:
        return
    for issue in issues:
        if issue.kind in ("cjk", "source_copy"):
            _report_append(report, "cjk_remnants", {
                "index": issue.index,
                "kind": issue.kind,
                "message": issue.message,
            })


def translate_units(
    units: list[TranslationUnit],
    prompt: str,
    llm,
    *,
    max_retries: int = 2,
    strict: bool = False,
    seed: str = "",
    on_protocol_failed=None,
    report: OnePassReport | None = None,
) -> dict[int, str]:
    """Translate one group with the bounded deterministic repair ladder:

      1. full group, up to 1 + min(max_retries, 2) attempts, each retry
         carrying only the deterministic validation errors;
      2. one re-request of the failed subset as a fresh numbered group;
      3. one individual request per still-failing item;
      4. final failure: strict -> OnePassProtocolError; otherwise the
         source text is preserved, a FillFailedEvent-compatible
         on_protocol_failed event is emitted, and the failure is recorded
         in the report.

    Returns {unit.index: translation} for every unit.
    """
    if not units:
        return {}
    full_retries = min(max(0, int(max_retries)), 2)

    request = prompt + "\n\n" + render_group(units)
    parsed: ParsedGroup | None = None
    issues: list[UnitIssue] = []

    for attempt in range(full_retries + 1):
        response = _request(llm, request, seed)
        _report_bump(report, "requests")
        parsed = parse_group(response, len(units))
        issues = validate_group(units, parsed)
        _record_issues(report, issues)
        if not issues:
            return dict(parsed.translations)
        if attempt < full_retries:
            _report_bump(report, "full_group_retries")
            request = request + "\n\n" + _validation_errors(units, parsed, issues)

    failed_indices = {issue.index for issue in issues}
    translations = {
        u.index: parsed.translations[u.index]
        for u in units
        if u.index in parsed.translations and u.index not in failed_indices
    }
    failed = [u for u in units if u.index in failed_indices]

    # Stage 2: re-request only the failed items as a smaller numbered group.
    if failed:
        orig_failed = failed
        subset_units = [
            dataclasses.replace(u, index=k) for k, u in enumerate(orig_failed, 1)
        ]
        subset_response = _request(llm, prompt + "\n\n" + render_group(subset_units), seed)
        _report_bump(report, "requests")
        _report_bump(report, "subset_requests")
        subset_parsed = parse_group(subset_response, len(subset_units))
        subset_issues = validate_group(subset_units, subset_parsed)
        _record_issues(report, subset_issues)
        subset_failed = {issue.index for issue in subset_issues}
        ok = {
            orig.index: subset_parsed.translations[k]
            for k, orig in enumerate(orig_failed, 1)
            if k in subset_parsed.translations and k not in subset_failed
        }
        translations.update(ok)
        failed = [u for u in orig_failed if u.index not in ok]

    # Stage 3: one individual request per still-failing item.
    for unit in failed:
        single = dataclasses.replace(unit, index=1)
        response = _request(llm, prompt + "\n\n" + render_group([single]), seed)
        _report_bump(report, "requests")
        _report_bump(report, "individual_requests")
        single_parsed = parse_group(response, 1)
        single_issues = validate_group([single], single_parsed)
        _record_issues(report, single_issues)
        if not single_issues and 1 in single_parsed.translations:
            translations[unit.index] = single_parsed.translations[1]
            continue

        attempts = (full_retries + 1) + 1 + 1
        error_text = "; ".join(i.message for i in single_issues) or "response invalid"
        if strict:
            raise OnePassProtocolError(
                f"one-pass protocol failure for item {unit.index}: {error_text}",
                report=report,
            )
        # Compatibility mode: preserve the source text, emit the
        # on_fill_failed-compatible event, and record the failure.
        translations[unit.index] = unit.source_text
        _report_bump(report, "fallback_units")
        _report_append(report, "failures", {
            "index": unit.index,
            "kinds": sorted({i.kind for i in single_issues}) or ["invalid"],
            "message": error_text,
        })
        if on_protocol_failed is not None:
            on_protocol_failed(FillFailedEvent(
                error_message=f"item {unit.index}: {error_text}",
                retried_count=attempts,
                over_maximum_retries=True,
            ))

    return translations


# ---------------------------------------------------------------------------
# Structural eligibility and positional reinsertion
# ---------------------------------------------------------------------------


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if tag.startswith("{") else tag


def _flat_unit_text(element) -> str | None:
    """Text of a flat element: element text plus permitted ``br`` handling
    (each ``br`` becomes a newline). Returns None when the element contains
    any non-br child (nested inline markup => not flat)."""
    parts: list[str] = []
    if element.text:
        parts.append(element.text)
    for child in element:
        if _strip_ns(child.tag) == "br":
            parts.append("\n" + (child.tail or ""))
        else:
            return None
    return "".join(parts)


def classify_body(body) -> tuple[list[TranslationUnit] | None, str | None]:
    """Normalize an eligible chapter body into flat units.

    Eligible: only flat ``p``/``h1``-``h6`` (text plus permitted ``br``) and
    whitespace. Nested inline elements, tails, tables, links/footnotes/math,
    raw body text, or any other non-flat structure => (None, reason) and the
    chapter is legacy-only. Pure-punctuation paragraphs are dropped from the
    unit list (never sent, never moved). Returns (units, None) with units
    indexed 1..N in source order.
    """
    if body.text is not None and body.text.strip():
        return None, "raw text node directly in body"

    units: list[TranslationUnit] = []
    for child in list(body):
        tag = _strip_ns(child.tag)
        if tag in FLAT_TAGS:
            text = _flat_unit_text(child)
            if text is None:
                return None, f"nested inline markup inside <{tag}>"
            if child.tail is not None and child.tail.strip():
                return None, f"tail text after <{tag}>"
            if text.strip() and not is_pure_punctuation(text):
                units.append(TranslationUnit(
                    index=len(units) + 1, source_text=text, target_element=child,
                ))
        elif tag == "br":
            if child.tail is not None and child.tail.strip():
                return None, "tail text directly in body"
            continue  # permitted at body level: treated as whitespace
        elif child.tail is not None and child.tail.strip():
            return None, "tail text directly in body"
        else:
            return None, f"non-flat element <{tag}>"

    return units, None


def _set_unit_text(element, text: str) -> None:
    """Positional reinsertion that alters only text: attributes and tag
    order are untouched; any permitted ``br`` children are removed."""
    if len(element):
        for child in list(element):
            element.remove(child)
    element.text = text


def _translate_chapter(
    units: list[TranslationUnit],
    prompt: str,
    llm,
    *,
    seed: str,
    max_group_tokens: int,
    max_retries: int,
    strict: bool,
    on_protocol_failed,
    report: OnePassReport,
    count_tokens,
    group_concurrency: int = 1,
) -> bool:
    """Translate all groups of one chapter and reinsert positionally.
    Returns True when the document changed (and must be written back).

    With ``group_concurrency > 1`` groups are dispatched through a bounded
    thread pool: each group is one independent request (with its own
    bounded repair ladder), so N groups can be in flight at once — the
    same concurrency model the two-pass engine already uses. The repair
    ladder, validation, and report accounting are unchanged; reinsertion
    stays serial and positional in group order, so XML element mutation
    is single-threaded. ``translate_units`` is safe to call from worker
    threads (report mutations are lock-protected)."""
    groups = [g for g in group_units(units, max_group_tokens, count_tokens) if g]
    concurrency = max(1, int(group_concurrency or 1))
    if concurrency > 1 and len(groups) > 1:
        changed = False
        jobs = []
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            for group in groups:
                report.groups += 1
                local = [dataclasses.replace(u, index=k) for k, u in enumerate(group, 1)]
                future = pool.submit(
                    translate_units, local, prompt, llm,
                    max_retries=max_retries, strict=strict, seed=seed,
                    on_protocol_failed=on_protocol_failed, report=report,
                )
                jobs.append((local, future))
            # Collect in group order; a strict abort (OnePassProtocolError)
            # or transport failure raised in a worker is re-raised after
            # the executor has drained the remaining in-flight requests.
            # Every future's exception is retrieved (no unretrieved
            # warnings); groups after the failing one are not reinserted,
            # mirroring the serial abort semantics.
            first_error: BaseException | None = None
            for local, future in jobs:
                if first_error is not None:
                    try:
                        future.result()
                    except BaseException:  # noqa: BLE001 - drain only
                        pass
                    continue
                try:
                    translations = future.result()
                except BaseException as exc:  # noqa: BLE001 - re-raise after drain
                    first_error = exc
                    continue
                for unit in local:
                    text = translations.get(unit.index)
                    if text is None:
                        continue
                    report.translated_units += 1
                    if text != unit.source_text:
                        _set_unit_text(unit.target_element, text)
                        changed = True
            if first_error is not None:
                raise first_error
        return changed

    changed = False
    for group in groups:
        report.groups += 1
        local = [dataclasses.replace(u, index=k) for k, u in enumerate(group, 1)]
        translations = translate_units(
            local, prompt, llm,
            max_retries=max_retries, strict=strict, seed=seed,
            on_protocol_failed=on_protocol_failed, report=report,
        )
        for unit in local:
            text = translations.get(unit.index)
            if text is None:
                continue
            report.translated_units += 1
            if text != unit.source_text:
                _set_unit_text(unit.target_element, text)
                changed = True
    return changed


# ---------------------------------------------------------------------------
# EPUB adapter
# ---------------------------------------------------------------------------


def build_prompt(user_prompt: str) -> str:
    """Attach the fixed numbered-protocol contract to the caller's
    translation instructions (glossary prompt etc.)."""
    return f"{user_prompt.strip()}\n\n{PROTOCOL_CONTRACT}"


def _token_counter(llm):
    """Token estimator for grouping. Prefers the LLM's tiktoken encoding;
    falls back to a char/2 heuristic for fakes and odd LLM shapes."""
    encoding = getattr(llm, "encoding", None)
    if encoding is not None and hasattr(encoding, "encode"):
        return lambda text: len(encoding.encode(text))
    return lambda text: max(1, len(text) // 2)


# ---------------------------------------------------------------------------
# Chapter-scoped legacy fallback (plan C3)
# ---------------------------------------------------------------------------


def _two_pass_cache_seed(target_language: str) -> str:
    """Cache seed of the legacy two-pass engine, replicated from
    ``epub_translator.translation.translator._get_version()`` so the
    fallback shares the exact same cache identity the production
    two-pass pipeline uses (``0.1.10:<lang>`` today)."""
    try:
        engine_version = importlib.metadata.version("epub-translator")
    except Exception:  # pragma: no cover - mirrors the engine's own guard
        engine_version = "development"
    return f"{engine_version}:{target_language}"


def _translate_legacy_chapter(
    xml,
    body,
    *,
    user_prompt: str,
    target_language: str,
    llm,
    max_group_tokens: int,
    max_retries: int,
    on_protocol_failed,
) -> bool:
    """Translate one non-flat chapter with the current two-pass engine.

    Applies the exact machinery of the production ``translate()``
    pipeline — XMLTranslator with REPLACE submission, the MathML/LaTeX
    interrupter, id deduplication, and the legacy cache seed — scoped to
    this chapter's ``<body>`` element only. The one-pass API carries a
    single LLM, so that instance plays both the translate and the fill
    role (the engine treats them as independent contexts over the same
    transport and cache).

    Returns True when the two-pass engine ran and the chapter must be
    written back; False (no-op) when the chapter has no translatable
    text — the two-pass engine itself cannot process such a chapter and
    would raise ``RuntimeError`` ("Translation failed unexpectedly").
    """
    if body is None:
        return False
    if not any(segment.text.strip() for segment in search_text_segments(body)):
        return False

    interrupter = XMLInterrupter()
    translator = XMLTranslator(
        translation_llm=llm,
        fill_llm=llm,
        target_language=target_language,
        user_prompt=user_prompt,
        ignore_translated_error=False,
        max_retries=max_retries,
        max_fill_displaying_errors=10,
        max_group_score=max_group_tokens,
        cache_seed_content=_two_pass_cache_seed(target_language),
    )
    translator.translate_element(
        TranslationTask(
            element=body,
            action=SubmitKind.REPLACE,
            payload=body,
        ),
        concurrency=1,
        interrupt_source_text_segments=interrupter.interrupt_source_text_segments,
        interrupt_translated_text_segments=interrupter.interrupt_translated_text_segments,
        interrupt_block_element=interrupter.interrupt_block_element,
        on_fill_failed=on_protocol_failed,
    )
    deduplicate_ids_in_element(xml.element)
    return True


# --------------------------------------------------------------------------
# Strict CJK gate on the two-pass fallback (plan C3)
# --------------------------------------------------------------------------
#
# The chapter-scoped two-pass fallback reuses the engine's FILL pass, whose
# structure-only validator accepts source-text backfill (SPEC §9.1). A
# legacy chapter whose fallback output still contains substantive CJK must
# therefore be gated explicitly: strict mode reroutes the chapter through
# one corrective two-pass re-run and aborts (OnePassProtocolError) if the
# reroute still leaves CJK; non-strict mode keeps the output as-is but
# records every remnant in the report and emits the on_fill_failed-
# compatible event, so source copies are visible, never silent.


_LEGACY_REMEDY = (
    "The previous translation of this chapter was rejected because it still "
    "contained untranslated Chinese source text. Translate every text segment "
    "into English. Never reproduce, echo, or copy the original Chinese text."
)


def _legacy_cjk_issues(body) -> list[UnitIssue]:
    """Deterministic scan of a two-pass-translated chapter body for
    substantive CJK remnants. Uses the same rule as the numbered protocol:
    a contiguous run of 2+ CJK characters; a single isolated glyph is
    legitimate context (SPEC §9.1) and is not flagged. Returns one UnitIssue
    per offending text segment, indexed by segment ordinal (1-based)."""
    issues: list[UnitIssue] = []
    for ordinal, segment in enumerate(search_text_segments(body), 1):
        runs = _CJK_RUN_RE.findall(segment.text or "")
        if runs:
            issues.append(UnitIssue(
                ordinal, "cjk",
                "untranslated CJK characters remain: " + "".join(runs[:2])[:30],
            ))
    return issues


def _translate_one_chapter(
    path,
    xml,
    *,
    prompt: str,
    user_prompt: str,
    target_language: str,
    translation_llm,
    seed: str,
    max_group_tokens: int,
    max_retries: int,
    strict: bool,
    on_protocol_failed,
    report: OnePassReport,
    count_tokens,
    group_concurrency: int = 1,
) -> bool:
    """Translate a single chapter: flat chapters take the numbered
    protocol, non-flat chapters the chapter-scoped two-pass fallback
    (plan C3) with the strict CJK gate. Runs on a worker thread under
    parallel chapter dispatch, so it never touches the Zip object —
    the caller reads and writes the archive. Returns True when the
    chapter body changed and must be written back."""
    body = find_first(xml.element, "body")
    units, reason = classify_body(body) if body is not None else (None, "no <body> element")
    if units is None:
        _report_append(report, "legacy_chapters", path.as_posix())
        if not _translate_legacy_chapter(
            xml, body,
            user_prompt=user_prompt,
            target_language=target_language,
            llm=translation_llm,
            max_group_tokens=max_group_tokens,
            max_retries=max_retries,
            on_protocol_failed=on_protocol_failed,
        ):
            return False
        _report_append(report, "legacy_two_pass_chapters", path.as_posix())
        issues = _legacy_cjk_issues(body)
        _record_issues(report, issues)
        if issues and strict:
            # Bounded reroute: one corrective re-run of the chapter's
            # two-pass engine with an explicit no-source-copy directive
            # (the appended remedy changes the messages hash, busting the
            # chapter's cache keys so the retry is a fresh call), then
            # re-scan.
            if _translate_legacy_chapter(
                xml, body,
                user_prompt=user_prompt + "\n\n" + _LEGACY_REMEDY,
                target_language=target_language,
                llm=translation_llm,
                max_group_tokens=max_group_tokens,
                max_retries=max_retries,
                on_protocol_failed=on_protocol_failed,
            ):
                issues = _legacy_cjk_issues(body)
                _record_issues(report, issues)
        if issues:
            message = (
                f"{len(issues)} segment(s) still contain untranslated "
                f"CJK after the two-pass fallback"
            )
            _report_append(report, "failures", {
                "index": 0,
                "kinds": ["cjk"],
                "message": message,
            })
            if strict:
                raise OnePassProtocolError(
                    "two-pass fallback left substantive CJK remnants in "
                    f"{path.as_posix()}: {message}",
                    report=report,
                )
            if on_protocol_failed is not None:
                on_protocol_failed(FillFailedEvent(
                    error_message=message,
                    retried_count=0,
                    over_maximum_retries=True,
                ))
        return True
    if not units:
        return False
    return _translate_chapter(
        units, prompt, translation_llm,
        seed=seed, max_group_tokens=max_group_tokens,
        max_retries=max_retries, strict=strict,
        on_protocol_failed=on_protocol_failed, report=report,
        count_tokens=count_tokens, group_concurrency=group_concurrency,
    )


def translate_one_pass(
    source_path,
    target_path,
    target_language,
    user_prompt,
    translation_llm,
    *,
    max_group_tokens,
    max_retries,
    strict,
    chapter_limit,
    on_progress=None,
    on_protocol_failed=None,
    group_concurrency=None,
):
    """Translate an EPUB with the one-pass numbered protocol.

    Progress mirrors the two-pass engine: TOC 5%, metadata 5%, chapters 90%
    (weights shrink when a header type is absent). Chapter limit stops the
    run cleanly: remaining files are finalized into the target archive and
    ChapterLimitReached is raised afterwards, so the partial epub is
    readable. Strict protocol failure behaves the same way but raises
    OnePassProtocolError (which carries the OnePassReport). Non-flat
    chapters are automatically translated by the chapter-scoped two-pass
    fallback (plan C3) instead of being left untranslated; in strict mode a
    fallback chapter whose output still contains substantive CJK remnants
    is rerouted through one corrective two-pass re-run and then rejected
    with OnePassProtocolError, and in non-strict mode the remnants are
    recorded in the report and surfaced through on_protocol_failed.

    ``group_concurrency`` (default 1, serial) bounds parallel dispatch:
    within each chapter, groups are independent numbered requests, and
    whole chapters are dispatched in waves of `group_concurrency` —
    the same concurrency model the two-pass engine uses. Reads and
    writes of the archive stay on the calling thread (spine order,
    deterministic progress), so the protocol, the repair ladder, and
    positional reinsertion are unchanged. Callers that already know
    the configured engine concurrency (cli/config, the benchmark
    harness) pass it explicitly.
    """
    source = Path(source_path).resolve()
    target = Path(target_path).resolve()
    report = OnePassReport(target=target)
    prompt = build_prompt(user_prompt)
    seed = f"{PROTOCOL_VERSION}:{target_language}"
    count_tokens = _token_counter(translation_llm)
    limit = max(0, int(chapter_limit or 0))
    pending: Exception | None = None
    current_path: Path | None = None

    with Zip(source, target) as zip:
        zip.migrate(Path("mimetype"))
        try:
            # --- header inventory (weights mirror the two-pass engine) ---
            try:
                toc_list, toc_context = read_toc(zip)
            except ValueError:
                toc_list, toc_context = [], None
            try:
                metadata_fields, metadata_context = read_metadata(zip)
            except ValueError:
                metadata_fields, metadata_context = [], None

            toc_weight = 0.05 if toc_list else 0.0
            metadata_weight = 0.05 if metadata_fields else 0.0
            chapters_weight = 1.0 - toc_weight - metadata_weight
            # Navigation documents are headers, not chapters (mirrors
            # prepare_epub, which never normalizes nav.xhtml/toc.xhtml).
            spine = [
                (path, media_type)
                for path, media_type in search_spine_paths(zip)
                if not path.name.lower().endswith(("nav.xhtml", "toc.xhtml"))
            ]
            report.chapters_total = len(spine)
            per_chapter = chapters_weight / len(spine) if spine else 0.0
            progress = 0.0

            # --- TOC strings through the existing EPUB TOC layer ---
            if toc_list:
                candidates = [
                    toc for toc in toc_list
                    if toc.title and toc.title.strip() and not is_pure_punctuation(toc.title)
                ]
                toc_units = [
                    TranslationUnit(i, toc.title, toc)
                    for i, toc in enumerate(candidates, 1)
                ]
                translations = translate_units(
                    toc_units, prompt, translation_llm,
                    max_retries=max_retries, strict=strict, seed=seed,
                    on_protocol_failed=on_protocol_failed, report=report,
                )
                changed = False
                for unit in toc_units:
                    text = translations.get(unit.index)
                    if text and text != unit.source_text:
                        unit.target_element.title = text
                        changed = True
                if changed:
                    write_toc(zip, toc_list, toc_context)
                report.toc_translated = True
                progress += toc_weight
                if on_progress:
                    on_progress(progress)

            # --- metadata strings through the existing metadata layer ---
            if metadata_fields:
                candidates = [
                    f for f in metadata_fields
                    if f.text and f.text.strip() and not is_pure_punctuation(f.text)
                ]
                meta_units = [
                    TranslationUnit(i, f.text, f)
                    for i, f in enumerate(candidates, 1)
                ]
                translations = translate_units(
                    meta_units, prompt, translation_llm,
                    max_retries=max_retries, strict=strict, seed=seed,
                    on_protocol_failed=on_protocol_failed, report=report,
                )
                changed = False
                for unit in meta_units:
                    text = translations.get(unit.index)
                    if text and text != unit.source_text:
                        unit.target_element.text = text
                        changed = True
                if changed:
                    write_metadata(zip, metadata_fields, metadata_context)
                report.metadata_translated = True
                progress += metadata_weight
                if on_progress:
                    on_progress(progress)

            # --- chapters ---
            # Wave-based dispatch: chapters are read and written by this
            # (main) thread — the Zip object is not thread-safe — while
            # translation runs on worker threads (LLM calls only). Waves
            # of `group_concurrency` chapters bound memory for the full
            # book; write-back, progress, chapter-limit accounting, and
            # abort finalization stay in spine order.
            wave_size = max(1, int(group_concurrency or 1))
            pos = 0
            while pos < len(spine):
                if limit and report.chapters_done >= limit:
                    raise ChapterLimitReached(limit)
                take = min(len(spine) - pos, wave_size)
                if limit:
                    take = min(take, limit - report.chapters_done)
                wave = spine[pos:pos + take]
                pos += take

                # Read the wave serially (zipfile is not thread-safe).
                loaded = []
                for path, media_type in wave:
                    with zip.read(path) as f:
                        xml = XMLLikeNode(f, is_html_like=(media_type == "text/html"))
                    loaded.append((path, xml))

                # Translate the wave concurrently.
                results = []
                first_error: BaseException | None = None
                if wave_size > 1 and len(loaded) > 1:
                    with ThreadPoolExecutor(max_workers=wave_size) as pool:
                        jobs = [
                            (path, xml, pool.submit(
                                _translate_one_chapter,
                                path, xml,
                                prompt=prompt, user_prompt=user_prompt,
                                target_language=target_language,
                                translation_llm=translation_llm,
                                seed=seed, max_group_tokens=max_group_tokens,
                                max_retries=max_retries, strict=strict,
                                on_protocol_failed=on_protocol_failed,
                                report=report, count_tokens=count_tokens,
                                group_concurrency=group_concurrency,
                            ))
                            for path, xml in loaded
                        ]
                        for path, xml, future in jobs:
                            if first_error is not None:
                                try:
                                    future.result()
                                except BaseException:  # noqa: BLE001 - drain only
                                    pass
                                continue
                            try:
                                changed = future.result()
                            except OnePassProtocolError as exc:
                                if exc.chapter is None:
                                    exc.chapter = path.as_posix()
                                current_path = path
                                first_error = exc
                                continue
                            except BaseException as exc:  # noqa: BLE001 - re-raise after drain
                                current_path = path
                                first_error = exc
                                continue
                            results.append((path, xml, changed))
                else:
                    for path, xml in loaded:
                        changed = _translate_one_chapter(
                            path, xml,
                            prompt=prompt, user_prompt=user_prompt,
                            target_language=target_language,
                            translation_llm=translation_llm,
                            seed=seed, max_group_tokens=max_group_tokens,
                            max_retries=max_retries, strict=strict,
                            on_protocol_failed=on_protocol_failed,
                            report=report, count_tokens=count_tokens,
                            group_concurrency=group_concurrency,
                        )
                        results.append((path, xml, changed))

                # Write back serially in spine order. Chapters before a
                # failing one ship even when a strict abort follows
                # (serial semantics); the failing chapter and its
                # successors are finalized from the source by Zip.
                for path, xml, changed in results:
                    if changed:
                        with zip.replace(path) as out:
                            xml.save(out)
                    report.chapters_done += 1
                    progress += per_chapter
                    if on_progress:
                        on_progress(min(progress, 1.0))
                if first_error is not None:
                    raise first_error
        except (ChapterLimitReached, OnePassProtocolError) as exc:
            if isinstance(exc, OnePassProtocolError) and exc.chapter is None and current_path is not None:
                exc.chapter = current_path.as_posix()
            pending = exc
        # Zip.__exit__ runs here: on success (incl. chapter limit / strict
        # abort) every remaining file is migrated, finalizing the archive.

    if pending is not None:
        raise pending
    return report
