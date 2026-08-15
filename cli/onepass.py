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
"""

from __future__ import annotations

import dataclasses
import difflib
import importlib.metadata
import re
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
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff]")

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

    cjk = _CJK_RE.findall(stripped)
    if cjk:
        ratio = difflib.SequenceMatcher(None, unit.source_text, stripped).ratio()
        if ratio > 0.9:
            issues.append(UnitIssue(unit.index, "source_copy", "output is a copy of the source"))
        else:
            issues.append(UnitIssue(
                unit.index, "cjk",
                "untranslated CJK characters remain: " + "".join(cjk[:5]),
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


def _record_issues(report: OnePassReport | None, issues: list[UnitIssue]) -> None:
    if report is None:
        return
    for issue in issues:
        if issue.kind in ("cjk", "source_copy"):
            report.cjk_remnants.append({
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
        if report is not None:
            report.requests += 1
        parsed = parse_group(response, len(units))
        issues = validate_group(units, parsed)
        _record_issues(report, issues)
        if not issues:
            return dict(parsed.translations)
        if attempt < full_retries:
            if report is not None:
                report.full_group_retries += 1
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
        if report is not None:
            report.requests += 1
            report.subset_requests += 1
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
        if report is not None:
            report.requests += 1
            report.individual_requests += 1
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
        if report is not None:
            report.fallback_units += 1
            report.failures.append({
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
) -> bool:
    """Translate all groups of one chapter and reinsert positionally.
    Returns True when the document changed (and must be written back)."""
    changed = False
    for group in group_units(units, max_group_tokens, count_tokens):
        if not group:
            continue
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
):
    """Translate an EPUB with the one-pass numbered protocol.

    Progress mirrors the two-pass engine: TOC 5%, metadata 5%, chapters 90%
    (weights shrink when a header type is absent). Chapter limit stops the
    run cleanly: remaining files are finalized into the target archive and
    ChapterLimitReached is raised afterwards, so the partial epub is
    readable. Strict protocol failure behaves the same way but raises
    OnePassProtocolError (which carries the OnePassReport). Non-flat
    chapters are automatically translated by the chapter-scoped two-pass
    fallback (plan C3) instead of being left untranslated.
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
            for path, media_type in spine:
                if limit and report.chapters_done >= limit:
                    raise ChapterLimitReached(limit)
                current_path = path
                with zip.read(path) as f:
                    xml = XMLLikeNode(f, is_html_like=(media_type == "text/html"))
                body = find_first(xml.element, "body")
                units, reason = classify_body(body) if body is not None else (None, "no <body> element")
                if units is None:
                    report.legacy_chapters.append(path.as_posix())
                    # Plan C3: non-flat chapters are automatically
                    # translated by the current two-pass engine, scoped to
                    # this chapter. Flat chapters never take this route.
                    if _translate_legacy_chapter(
                        xml, body,
                        user_prompt=user_prompt,
                        target_language=target_language,
                        llm=translation_llm,
                        max_group_tokens=max_group_tokens,
                        max_retries=max_retries,
                        on_protocol_failed=on_protocol_failed,
                    ):
                        report.legacy_two_pass_chapters.append(path.as_posix())
                        with zip.replace(path) as out:
                            xml.save(out)
                elif units:
                    changed = _translate_chapter(
                        units, prompt, translation_llm,
                        seed=seed, max_group_tokens=max_group_tokens,
                        max_retries=max_retries, strict=strict,
                        on_protocol_failed=on_protocol_failed, report=report,
                        count_tokens=count_tokens,
                    )
                    if changed:
                        with zip.replace(path) as out:
                            xml.save(out)
                report.chapters_done += 1
                progress += per_chapter
                if on_progress:
                    on_progress(min(progress, 1.0))
        except (ChapterLimitReached, OnePassProtocolError) as exc:
            if isinstance(exc, OnePassProtocolError) and exc.chapter is None and current_path is not None:
                exc.chapter = current_path.as_posix()
            pending = exc
        # Zip.__exit__ runs here: on success (incl. chapter limit / strict
        # abort) every remaining file is migrated, finalizing the archive.

    if pending is not None:
        raise pending
    return report
