"""Tests for the one-pass numbered-paragraph translation engine (cli/onepass.py).

Covers, in order:
  * numbered renderer/parser exact round trips
  * continuation lines and dialogue punctuation
  * code-like brackets that must NOT be parsed as indices
  * duplicate / missing / unexpected / empty indices
  * suspicious-truncation and CJK-remnant validation
  * token-aware grouping (units never split; oversized unit alone)
  * the bounded repair ladder driven by a fake LLM (no paid API)
  * flat-vs-complex structural eligibility and pure-punctuation skipping
  * positional reinsertion that preserves tag/attribute order
  * chapter-scoped legacy fallback: non-flat chapters are translated by
    the current two-pass engine (TRANSLATE + FILL) while flat siblings
    stay on the one-pass protocol, with media/structure intact
  * end-to-end EPUB translation (TOC/metadata/chapters), callbacks,
    chapter-limit finalization, strict abort, and source-copy fallback
"""
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

# Make cli/ importable (mirrors core_loader's EPUB_TRANSLATOR_PATH contract).
CLI_DIR = Path(__file__).resolve().parent.parent.parent / "cli"
sys.path.insert(0, str(CLI_DIR))

import onepass  # noqa: E402
from onepass import (  # noqa: E402
    OnePassProtocolError,
    ParsedGroup,
    TranslationUnit,
    classify_body,
    group_units,
    parse_group,
    render_group,
    translate_one_pass,
    translate_units,
    validate_group,
)

# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class _SimpleEncoding:
    """Deterministic token counter: 1 token per character."""

    def encode(self, text):
        return [0] * len(text)

    def decode(self, tokens):
        return "".join(chr(t) for t in tokens)


class _FakeTemplate:
    """Minimal jinja2.Template stand-in for the two-pass engine's
    ``llm.template(name).render(...)`` calls."""

    def __init__(self, name):
        self.name = name

    def render(self, **kwargs):
        return f"<system template {self.name}>"


class _FakeLLM:
    """Stand-in for epub_translator.LLM. Responses are consumed in order;
    a handler(prompt) -> str replaces the queue when provided.

    Also satisfies the two-pass engine surface used by the legacy
    chapter-scoped fallback: ``encoding``, ``template()``, and
    ``context()`` whose request() accepts str or a list of Messages.
    """

    def __init__(self, responses=None, handler=None):
        self.responses = list(responses or [])
        self.handler = handler
        self.calls = []
        self.seeds = []
        self.encoding = _SimpleEncoding()

    def context(self, cache_seed_content=None):
        self.seeds.append(cache_seed_content)
        return _FakeCtx(self)

    def template(self, name):
        return _FakeTemplate(name)


class _FakeCtx:
    def __init__(self, llm):
        self.llm = llm

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def request(self, input, **kwargs):
        if isinstance(input, list):
            # Two-pass engine passes [Message(system), Message(user)]; the
            # handler only needs the user text (last message).
            prompt = input[-1].message
        else:
            prompt = input
        self.llm.calls.append(prompt)
        if self.llm.handler:
            return self.llm.handler(prompt)
        if self.llm.responses:
            return self.llm.responses.pop(0)
        raise AssertionError("fake LLM ran out of responses")


def _auto_handler(prefix="T"):
    """Echoes every numbered line back as `[N] {prefix}{N}`, dropping
    continuation lines — a valid response for any group."""

    def handler(prompt):
        out = []
        for line in prompt.splitlines():
            m = re.match(r"^\[(\d+)\]", line)
            if m:
                out.append(f"[{m.group(1)}] {prefix}{m.group(1)}")
        return "\n".join(out) + "\n"

    return handler


def _length_handler(prefix="G"):
    """Returns `[N] {prefix}{len(source)}` — index-independent, so it stays
    unambiguous even when every group is a single renumbered item."""

    def handler(prompt):
        out = []
        for line in prompt.splitlines():
            m = re.match(r"^\[(\d+)\] (.*)$", line)
            if m:
                out.append(f"[{m.group(1)}] {prefix}{len(m.group(2))}")
        return "\n".join(out) + "\n"

    return handler


def _garbage_handler(prompt):
    return "Sorry, I cannot help with that."


def _legacy_mixed_handler(prefix="L"):
    """Serves all three request shapes of a one-pass run that contains
    non-flat chapters:

      * one-pass protocol requests (``[N] source`` lines) -> ``[N] T``
        echo, exactly like ``_auto_handler``;
      * two-pass translate pass (plain source text, no protocol) ->
        a plain-text translation;
      * two-pass fill pass (prompt carries the ``XML template:`` block)
        -> the template echoed back with every text node replaced by a
        deterministic ``{prefix}{block-id}`` marker (block id ``0`` for
        inline elements without an id). Structure, tags, and attributes
        are preserved verbatim, which is all the fill validation checks.
    """

    def handler(prompt):
        if "XML template:" in prompt:
            match = re.search(r"```XML\n(.*?)\n```", prompt, re.S)
            assert match, "fill pass prompt must carry the XML template"
            root = ET.fromstring(match.group(1))
            for el in root.iter():
                block_id = el.get("id") or "0"
                if el.text and el.text.strip():
                    el.text = f"{prefix}{block_id}"
                if el.tail and el.tail.strip():
                    el.tail = f"{prefix}{block_id}"
            return ET.tostring(root, encoding="unicode")
        if re.search(r"^\[\d+\]", prompt, re.M):
            return _auto_handler("T")(prompt)
        return "TRANSLATED"

    return handler


def _unit(i, text, element=None):
    return TranslationUnit(index=i, source_text=text, target_element=element or ET.Element("p"))


def _body(html):
    return ET.fromstring(f"<body>{html}</body>")


# --------------------------------------------------------------------------
# Renderer / parser
# --------------------------------------------------------------------------


def test_render_parse_exact_round_trip():
    units = [_unit(1, "第一段。"), _unit(2, "第二段……"), _unit(3, "第三段！")]
    text = render_group(units)
    assert text == "[1] 第一段。\n\n[2] 第二段……\n\n[3] 第三段！"
    parsed = parse_group(text, 3)
    assert parsed == ParsedGroup(
        translations={1: "第一段。", 2: "第二段……", 3: "第三段！"},
        missing=(), duplicate=(), unexpected=(),
    )


def test_parse_joins_continuation_lines():
    text = "[1] 第一行\n第二行\n\n[2] 第二段"
    parsed = parse_group(text, 2)
    assert parsed.translations[1] == "第一行\n第二行"
    assert parsed.translations[2] == "第二段"
    assert parsed.missing == () and parsed.unexpected == ()


def test_parse_continuation_dialogue_punctuation():
    text = "[1] “你好。”\n“你好吗？”\n\n[2] ——当然。"
    parsed = parse_group(text, 2)
    assert parsed.translations[1] == "“你好。”\n“你好吗？”"
    assert parsed.translations[2] == "——当然。"


def test_parse_code_like_brackets_not_indices():
    # Bracketed numbers inside the item text are not parsed as indices;
    # only a line STARTING with [N] opens a new item.
    text = "[7] Use [1] and [2] here; see [3] for details\nstill item seven"
    parsed = parse_group(text, 7)
    assert parsed.translations[7] == "Use [1] and [2] here; see [3] for details\nstill item seven"
    assert parsed.missing == (1, 2, 3, 4, 5, 6)


def test_parse_missing_index():
    parsed = parse_group("[1] a\n\n[3] c", 3)
    assert parsed.translations == {1: "a", 3: "c"}
    assert parsed.missing == (2,)


def test_parse_duplicate_index():
    parsed = parse_group("[1] first\n\n[1] second\n\n[2] b", 2)
    assert parsed.translations[1] == "first"  # deterministic: first occurrence wins
    assert parsed.duplicate == (1,)
    assert parsed.missing == () and parsed.unexpected == ()


def test_parse_unexpected_index_zero_and_out_of_range():
    parsed = parse_group("[0] zero\n\n[1] a\n\n[2] b\n\n[9] nine", 2)
    assert parsed.unexpected == (0, 9)
    assert parsed.translations == {1: "a", 2: "b"}


def test_parse_empty_item_is_missing():
    parsed = parse_group("[1] a\n\n[2] \n\n[3] c", 3)
    assert parsed.missing == (2,)
    assert parsed.translations == {1: "a", 3: "c"}


def test_parse_stray_text_before_first_item_recorded():
    parsed = parse_group("preamble\n\n[1] a\n\n[2] b", 2)
    assert parsed.unexpected == (0,)
    assert parsed.translations == {1: "a", 2: "b"}


def test_parse_leading_whitespace_line_is_continuation():
    # The protocol anchors ^\[(\d+)\] at the line start (per plan), so an
    # indented line is a continuation — here stray text before any item.
    parsed = parse_group("  [2] b\n\n[1] a", 2)
    assert parsed.unexpected == (0,)
    assert parsed.translations == {1: "a"}


def test_parse_empty_response_all_missing():
    parsed = parse_group("", 3)
    assert parsed.missing == (1, 2, 3)
    assert parsed.translations == {}


# --------------------------------------------------------------------------
# Validation (truncation / CJK / source copies)
# --------------------------------------------------------------------------


def _issues_for(source, translation):
    unit = _unit(1, source)
    parsed = parse_group(f"[1] {translation}", 1)
    return validate_group([unit], parsed)


def test_validate_clean_translation():
    issues = _issues_for("这是一段足够长的中文原文。", "This is a clean English translation.")
    assert issues == []


def test_validate_truncation_flagged():
    # 28 source chars => 20% threshold = 5.6 chars; "short" (5) is below it.
    issues = _issues_for("这是一段很长很长的中文原文段落，用来触发截断检查的阈值。", "short")
    assert any(i.kind == "truncated" for i in issues)


def test_validate_truncation_short_source_exempt():
    issues = _issues_for("短句", "ok")
    assert not any(i.kind == "truncated" for i in issues)


def test_validate_cjk_remnant_flagged():
    issues = _issues_for("这是一段足够长的中文原文。", "He said 你好 and left.")
    assert any(i.kind == "cjk" for i in issues)


def test_validate_source_copy_flagged_as_source_copy():
    issues = _issues_for("这是一段足够长的中文原文，用于检测原文回显。",
                         "这是一段足够长的中文原文，用于检测原文回显。")
    assert any(i.kind == "source_copy" for i in issues)


def test_validate_missing_and_duplicate_reported():
    units = [_unit(1, "甲"), _unit(2, "乙")]
    parsed = parse_group("[1] a\n\n[1] a", 2)
    issues = validate_group(units, parsed)
    kinds = {i.kind for i in issues}
    assert "duplicate" in kinds and "missing" in kinds


# --------------------------------------------------------------------------
# Token-aware grouping
# --------------------------------------------------------------------------


def _count(text):
    return len(text) // 2  # deterministic fake


def test_group_respects_budget():
    # Each unit costs _count("[1] xxxx") + 2 = 6 tokens; two fit in 12.
    units = [_unit(i, "x" * 4) for i in range(1, 5)]
    groups = group_units(units, max_group_tokens=12, count_tokens=_count)
    assert [len(g) for g in groups] == [2, 2]
    assert [u.index for u in groups[0]] == [1, 2]


def test_group_never_splits_unit():
    units = [_unit(1, "x" * 100), _unit(2, "y" * 4), _unit(3, "z" * 4)]
    # Small units cost 6 tokens each, so two of them fit in 12.
    groups = group_units(units, max_group_tokens=12, count_tokens=_count)
    assert [len(g) for g in groups] == [1, 2]  # oversized alone, rest grouped
    assert groups[0][0].index == 1


def test_group_oversized_unit_alone_in_middle():
    units = [_unit(1, "x" * 4), _unit(2, "y" * 100), _unit(3, "z" * 4), _unit(4, "w" * 4)]
    groups = group_units(units, max_group_tokens=12, count_tokens=_count)
    assert [len(g) for g in groups] == [1, 1, 2]
    assert groups[1][0].index == 2


def test_group_empty_units():
    assert group_units([], 100, _count) == []


def test_group_nonpositive_budget_means_unlimited():
    units = [_unit(i, "x" * 10) for i in range(1, 4)]
    assert len(group_units(units, 0, _count)) == 1
    assert len(group_units(units, -1, _count)) == 1


# --------------------------------------------------------------------------
# Repair ladder (fake LLM, no paid API)
# --------------------------------------------------------------------------


def test_ladder_success_first_try_single_request():
    fake = _FakeLLM(responses=["[1] one\n\n[2] two"])
    units = [_unit(1, "一"), _unit(2, "二")]
    out = translate_units(units, "prompt", fake, max_retries=2, strict=False, seed="s")
    assert out == {1: "one", 2: "two"}
    assert len(fake.calls) == 1


def test_ladder_full_group_retry_with_validation_errors():
    fake = _FakeLLM(responses=["[1] one", "[1] one\n\n[2] two"])
    units = [_unit(1, "一"), _unit(2, "二")]
    out = translate_units(units, "prompt", fake, max_retries=2, strict=False, seed="s")
    assert out == {1: "one", 2: "two"}
    assert len(fake.calls) == 2
    assert "missing" in fake.calls[1]  # deterministic error text


def test_ladder_full_group_retries_bounded_at_two():
    fake = _FakeLLM(responses=["bad"] * 10)
    units = [_unit(1, "一"), _unit(2, "二")]
    report = onepass.OnePassReport()
    out = translate_units(units, "prompt", fake, max_retries=5, strict=False,
                          seed="s", report=report)
    # initial + 2 full-group retries + 1 subset + 1 individual per unit
    assert len(fake.calls) == 3 + 1 + 2
    assert report.full_group_retries == 2
    assert out[1] == "一" and out[2] == "二"  # source preserved


def test_ladder_subset_after_full_group_fails():
    fake = _FakeLLM(responses=[
        "[1] one\n\n[3] three",          # initial: [2] missing
        "[1] one\n\n[3] three",          # retry: same flaw
        "[1] two",                       # subset of the failed item only
    ])
    units = [_unit(1, "一"), _unit(2, "二"), _unit(3, "三")]
    report = onepass.OnePassReport()
    out = translate_units(units, "prompt", fake, max_retries=1, strict=False,
                          seed="s", report=report)
    assert out == {1: "one", 2: "two", 3: "three"}
    assert len(fake.calls) == 3
    assert report.subset_requests == 1
    # Subset prompt contains only the failed item, renumbered from [1].
    subset_prompt = fake.calls[2]
    assert "[1] 二" in subset_prompt
    assert "一" not in subset_prompt and "三" not in subset_prompt


def test_ladder_individual_after_subset_fails():
    fake = _FakeLLM(responses=[
        "[1] one\n\n[2] ",               # initial: [2] empty
        "[1] one\n\n[2] ",               # retry
        "[1] ",                          # subset still empty
        "[1] two",                       # individual succeeds
    ])
    units = [_unit(1, "一"), _unit(2, "二")]
    report = onepass.OnePassReport()
    out = translate_units(units, "prompt", fake, max_retries=1, strict=False,
                          seed="s", report=report)
    assert out == {1: "one", 2: "two"}
    assert len(fake.calls) == 4
    assert report.individual_requests == 1


def test_ladder_source_preserved_and_event_emitted_on_final_failure():
    events = []
    fake = _FakeLLM(handler=_garbage_handler)
    units = [_unit(1, "第一段"), _unit(2, "第二段")]
    report = onepass.OnePassReport()

    def on_failed(event):
        events.append(event)

    out = translate_units(units, "prompt", fake, max_retries=2, strict=False,
                          seed="s", report=report, on_protocol_failed=on_failed)
    assert out == {1: "第一段", 2: "第二段"}  # compatibility: source preserved
    assert report.fallback_units == 2
    assert report.failures
    assert len(events) == 2
    for event in events:
        assert event.over_maximum_retries is True
        assert event.retried_count > 0
        assert event.error_message


def test_ladder_strict_aborts():
    fake = _FakeLLM(handler=_garbage_handler)
    units = [_unit(1, "第一段")]
    with pytest.raises(OnePassProtocolError):
        translate_units(units, "prompt", fake, max_retries=2, strict=True, seed="s")


def test_ladder_empty_units_makes_no_request():
    fake = _FakeLLM(responses=["[1] nope"])
    assert translate_units([], "prompt", fake, max_retries=2, strict=False, seed="s") == {}
    assert fake.calls == []


def test_ladder_cjk_remnant_recorded_in_report():
    fake = _FakeLLM(responses=["[1] He said 你好", "[1] 还是中文", "[1] 还是中文"])
    units = [_unit(1, "这是一段足够长的中文。")]
    report = onepass.OnePassReport()
    out = translate_units(units, "prompt", fake, max_retries=0, strict=False,
                          seed="s", report=report)
    assert out[1] == "这是一段足够长的中文。"  # fell through to source
    assert any(r["kind"] == "cjk" for r in report.cjk_remnants)


# --------------------------------------------------------------------------
# Eligibility / classification / reinsertion
# --------------------------------------------------------------------------


def test_flat_chapter_inventory_preserves_order():
    body = _body('<p>第一段</p><h1>标题</h1><p>第二段</p><h3>小节</h3>')
    units, reason = classify_body(body)
    assert reason is None
    assert [u.index for u in units] == [1, 2, 3, 4]
    assert [u.source_text for u in units] == ["第一段", "标题", "第二段", "小节"]
    assert [u.target_element.tag for u in units] == ["p", "h1", "p", "h3"]


def test_flat_br_handling_within_paragraph():
    body = _body("<p>a<br/>b</p><p>c</p>")
    units, reason = classify_body(body)
    assert reason is None
    assert units[0].source_text == "a\nb"


def test_body_level_br_ignored():
    body = _body("<p>a</p><br/><p>b</p>")
    units, reason = classify_body(body)
    assert reason is None
    assert [u.source_text for u in units] == ["a", "b"]


def test_nested_inline_element_legacy_only():
    body = _body("<p>a <em>b</em></p>")
    units, reason = classify_body(body)
    assert units is None and "inline" in reason


def test_tail_text_legacy_only():
    body = _body("<p>a</p> stray tail")
    units, reason = classify_body(body)
    assert units is None and "tail" in reason


def test_table_legacy_only():
    body = _body("<table><tr><td>x</td></tr></table>")
    units, reason = classify_body(body)
    assert units is None and "table" in reason


def test_link_footnote_math_legacy_only():
    body = _body('<p><a href="x">link</a></p><p><math>x</math></p>')
    units, reason = classify_body(body)
    assert units is None


def test_raw_body_text_legacy_only():
    body = _body("raw text <p>ok</p>")
    units, reason = classify_body(body)
    assert units is None and "text" in reason


def test_whitespace_only_body_has_no_units():
    body = _body("\n   \n<p></p>\n")
    units, reason = classify_body(body)
    assert reason is None
    assert units == []


def test_pure_punctuation_filtered_out():
    body = _body("<p>……</p><p>正文</p><p>——</p>")
    units, reason = classify_body(body)
    assert reason is None
    assert [u.source_text for u in units] == ["正文"]
    assert [u.index for u in units] == [1]


def test_reinsertion_alters_text_only_preserves_attributes():
    body = _body('<p id="para-1" class="prose">原文</p><h2 data-x="1">标题</h2>')
    units, _ = classify_body(body)
    from onepass import _set_unit_text
    _set_unit_text(units[0].target_element, "translated")
    _set_unit_text(units[1].target_element, "heading")
    serialized = ET.tostring(body, encoding="unicode")
    assert 'id="para-1"' in serialized and 'class="prose"' in serialized
    assert 'data-x="1"' in serialized
    assert "原文" not in serialized and "标题" not in serialized
    assert "translated" in serialized and "heading" in serialized
    # Attribute order preserved (id before class).
    assert serialized.index('id="para-1"') < serialized.index('class="prose"')


# --------------------------------------------------------------------------
# End-to-end EPUB translation
# --------------------------------------------------------------------------


def _build_epub(path, chapters, *, with_nav=True, title="测试书名", nested=False,
                custom=None, media=False):
    """Build a test epub.

    ``chapters`` is a list of paragraph lists. ``nested`` makes selected
    chapters non-flat (``<p>嵌套 <em>...</em></p>``). ``custom`` maps
    chapter index (1-based) to a full body inner-HTML override. ``media``
    adds a real image entry (images/cover.png) to the archive.
    """
    from ebooklib import epub
    if isinstance(nested, (list, set, tuple)):
        nested_idx = set(nested)
    else:
        nested_idx = set(range(1, len(chapters) + 1)) if nested else set()
    custom = custom or {}
    book = epub.EpubBook()
    book.set_identifier("id-onepass-test")
    book.set_title(title)
    book.set_language("zh")
    items = []
    if media:
        img = epub.EpubImage()
        img.file_name = "images/cover.png"
        img.media_type = "image/png"
        img.content = b"\x89PNG\r\n\x1a\n" + b"0" * 32
        book.add_item(img)
    for i, paras in enumerate(chapters, 1):
        if i in custom:
            body = custom[i]
        elif i in nested_idx:
            body = "".join(f"<p>嵌套 <em>{p}</em></p>" for p in paras)
        else:
            body = "".join(f"<p>{p}</p>" for p in paras)
        chapter = epub.EpubHtml(title=f"Chapter {i}", file_name=f"chap{i}.xhtml", lang="zh")
        chapter.content = f"<html><body>{body}</body></html>"
        book.add_item(chapter)
        items.append(chapter)
    if with_nav:
        book.add_item(epub.EpubNav())
        book.toc = items
        book.spine = ["nav"] + items
    else:
        book.spine = items
    epub.write_epub(path, book)
    return book


def _read_chapter(epub_path, name):
    from ebooklib import epub
    book = epub.read_epub(epub_path)
    for item in book.get_items():
        if item.get_name().endswith(name):
            return item.get_content().decode("utf-8")
    raise AssertionError(f"chapter {name} not found in {epub_path}")


def _read_chapter_raw(epub_path, name):
    """zipfile-level read for books that ebooklib's reader cannot reopen
    (e.g. a book with no nav and no ncx at all)."""
    with zipfile.ZipFile(epub_path) as zf:
        for entry in zf.namelist():
            if entry.endswith(name):
                return zf.read(entry).decode("utf-8")
    raise AssertionError(f"chapter {name} not found in {epub_path}")


def test_translate_one_pass_flat_book_end_to_end(tmp_path):
    src = tmp_path / "src.epub"
    tgt = tmp_path / "out.epub"
    _build_epub(src, [["第一章内容", "继续"], ["第二章内容"]])
    fake = _FakeLLM(handler=_auto_handler("T"))
    progress = []
    report = translate_one_pass(
        src, tgt, "en", "translate it", fake,
        max_group_tokens=5000, max_retries=2, strict=False, chapter_limit=0,
        on_progress=progress.append,
    )
    assert report.chapters_done == 2
    assert report.chapters_total == 2
    assert report.legacy_chapters == []
    assert report.fallback_units == 0
    assert report.toc_translated and report.metadata_translated
    content = _read_chapter(tgt, "chap1.xhtml")
    assert "T1" in content and "T2" in content
    assert "第一章内容" not in content
    assert "T1" in _read_chapter(tgt, "chap2.xhtml")
    # Progress: TOC 0.05, metadata 0.05, then 0.45 per chapter.
    assert progress == pytest.approx([0.05, 0.10, 0.55, 1.0])


def test_translate_one_pass_multiple_groups_per_chapter(tmp_path):
    src = tmp_path / "src.epub"
    tgt = tmp_path / "out.epub"
    # Distinct lengths: under the tiny budget each paragraph is its own
    # group, and the length-based handler maps each source to a unique
    # translation (a pure index echo would collide across groups).
    long_paras = [f"第{i}段" + "很" * i for i in range(1, 5)]
    _build_epub(src, [long_paras])
    fake = _FakeLLM(handler=_length_handler("G"))
    report = translate_one_pass(
        src, tgt, "en", "prompt", fake,
        max_group_tokens=12, max_retries=2, strict=False, chapter_limit=0,
    )
    assert report.groups >= 2
    content = _read_chapter(tgt, "chap1.xhtml")
    for i in range(1, 5):
        assert f"G{3 + i}" in content  # "第{i}段" + i*"很" is 3+i chars


def test_legacy_chapter_routed_to_two_pass_fallback(tmp_path):
    """Plan C3: a non-flat chapter is automatically translated by the
    chapter-scoped two-pass fallback, while a flat sibling stays on the
    one-pass protocol (and never sees the two-pass route)."""
    src = tmp_path / "src.epub"
    tgt = tmp_path / "out.epub"
    _build_epub(src, [["flat one"], ["嵌套二"]], nested=[2])
    fake = _FakeLLM(handler=_legacy_mixed_handler("L"))
    report = translate_one_pass(
        src, tgt, "en", "prompt", fake,
        max_group_tokens=5000, max_retries=2, strict=False, chapter_limit=0,
    )
    assert len(report.legacy_chapters) == 1
    assert report.legacy_chapters[0].endswith("chap2.xhtml")
    # The ineligible chapter was routed, not merely reported.
    assert report.legacy_two_pass_chapters == report.legacy_chapters
    assert report.chapters_done == 2
    # Flat sibling: one-pass protocol only — no two-pass markers.
    flat = _read_chapter(tgt, "chap1.xhtml")
    assert "T1" in flat and "flat one" not in flat
    assert "L" not in flat
    # Legacy chapter: translated by the two-pass route, source gone,
    # inline structure preserved.
    content = _read_chapter(tgt, "chap2.xhtml")
    assert "嵌套二" not in content
    assert "L1" in content and "L0" in content
    assert "<em>" in content
    # The two-pass route reused the legacy engine's cache namespace.
    from importlib import metadata as _metadata
    legacy_seed = f"{_metadata.version('epub-translator')}:en"
    assert legacy_seed in fake.seeds


def test_legacy_fallback_preserves_media_and_structure(tmp_path):
    """The legacy fallback must not damage the archive: media entries and
    non-translated structure survive, the flat sibling still runs the
    one-pass protocol, and every source file is still present."""
    src = tmp_path / "src.epub"
    tgt = tmp_path / "out.epub"
    custom = {
        2: '<img src="images/cover.png" alt="插图"/>'
           '<div class="poem"><p>嵌套 <em>重点</em></p></div>',
    }
    _build_epub(src, [["flat one"], ["unused"]], custom=custom, media=True)
    fake = _FakeLLM(handler=_legacy_mixed_handler("L"))
    report = translate_one_pass(
        src, tgt, "en", "prompt", fake,
        max_group_tokens=5000, max_retries=2, strict=False, chapter_limit=0,
    )
    assert report.chapters_done == 2
    assert len(report.legacy_two_pass_chapters) == 1
    assert report.legacy_two_pass_chapters[0].endswith("chap2.xhtml")
    # Media/structure: every original archive entry survives.
    with zipfile.ZipFile(src) as zf:
        src_names = set(zf.namelist())
    with zipfile.ZipFile(tgt) as zf:
        tgt_names = set(zf.namelist())
    assert tgt_names == src_names
    assert any(n.endswith("images/cover.png") for n in tgt_names)
    # Flat sibling stays one-pass.
    flat = _read_chapter(tgt, "chap1.xhtml")
    assert "T1" in flat and "L" not in flat
    # Legacy chapter: translated, block attributes and the image
    # reference preserved.
    content = _read_chapter(tgt, "chap2.xhtml")
    assert "嵌套" not in content and "重点" not in content
    assert "L1" in content
    assert 'class="poem"' in content
    assert 'src="images/cover.png"' in content
    assert "<em>" in content


def test_chapter_limit_finalizes_partial_epub_then_raises(tmp_path):
    src = tmp_path / "src.epub"
    tgt = tmp_path / "out.epub"
    _build_epub(src, [["a"], ["b"], ["c"]])
    fake = _FakeLLM(handler=_auto_handler("T"))
    from onepass import ChapterLimitReached
    with pytest.raises(ChapterLimitReached) as err:
        translate_one_pass(
            src, tgt, "en", "prompt", fake,
            max_group_tokens=5000, max_retries=2, strict=False, chapter_limit=1,
        )
    assert err.value.limit == 1
    # Partial output is finalized: every original file is present.
    with zipfile.ZipFile(tgt) as zf:
        names = set(zf.namelist())
    with zipfile.ZipFile(src) as zf:
        assert names == set(zf.namelist())
    assert "T1" in _read_chapter(tgt, "chap1.xhtml")
    assert "b" in _read_chapter(tgt, "chap2.xhtml")  # untouched, but migrated


def test_toc_and_metadata_translated(tmp_path):
    src = tmp_path / "src.epub"
    tgt = tmp_path / "out.epub"
    _build_epub(src, [["正文"]], title="中文书名")
    fake = _FakeLLM(handler=_auto_handler("H"))
    report = translate_one_pass(
        src, tgt, "en", "prompt", fake,
        max_group_tokens=5000, max_retries=2, strict=False, chapter_limit=0,
    )
    assert report.toc_translated and report.metadata_translated
    # Metadata: title replaced in the OPF.
    with zipfile.ZipFile(tgt) as zf:
        opf = next(n for n in zf.namelist() if n.endswith(".opf"))
        opf_text = zf.read(opf).decode("utf-8")
    assert "中文书名" not in opf_text
    assert "H" in opf_text
    # TOC: nav.xhtml titles replaced.
    with zipfile.ZipFile(tgt) as zf:
        nav = next(n for n in zf.namelist() if "nav" in n.lower())
        nav_text = zf.read(nav).decode("utf-8")
    assert "Chapter 1" not in nav_text
    assert "H1" in nav_text


def test_protocol_failure_fallback_preserves_source_and_emits(tmp_path):
    src = tmp_path / "src.epub"
    tgt = tmp_path / "out.epub"
    _build_epub(src, [["必须翻译的段落"]])
    fake = _FakeLLM(handler=_garbage_handler)
    events = []
    report = translate_one_pass(
        src, tgt, "en", "prompt", fake,
        max_group_tokens=5000, max_retries=2, strict=False, chapter_limit=0,
        on_protocol_failed=events.append,
    )
    assert report.fallback_units >= 1
    assert events and all(e.over_maximum_retries for e in events)
    assert "必须翻译的段落" in _read_chapter(tgt, "chap1.xhtml")  # source kept


def test_strict_mode_aborts_with_finalized_zip(tmp_path):
    src = tmp_path / "src.epub"
    tgt = tmp_path / "out.epub"
    _build_epub(src, [["第一段"], ["第二段"]])
    fake = _FakeLLM(handler=_garbage_handler)
    with pytest.raises(OnePassProtocolError) as err:
        translate_one_pass(
            src, tgt, "en", "prompt", fake,
            max_group_tokens=5000, max_retries=2, strict=True, chapter_limit=0,
        )
    assert err.value.report is not None
    assert err.value.report.fallback_units == 0
    with zipfile.ZipFile(tgt) as zf:
        assert set(zf.namelist()) == set(zipfile.ZipFile(src).namelist())


def test_all_punctuation_chapter_makes_no_requests(tmp_path):
    src = tmp_path / "src.epub"
    tgt = tmp_path / "out.epub"
    _build_epub(src, [["……", "——", "！？"]])
    fake = _FakeLLM(handler=_auto_handler())
    report = translate_one_pass(
        src, tgt, "en", "prompt", fake,
        max_group_tokens=5000, max_retries=2, strict=False, chapter_limit=0,
    )
    # The punctuation unit never enters a request (headers still translate).
    assert not any("……" in c or "——" in c or "！？" in c for c in fake.calls)
    assert report.translated_units == 0
    assert "……" in _read_chapter(tgt, "chap1.xhtml")  # position unchanged


def test_legacy_only_book_routed_to_two_pass(tmp_path):
    src = tmp_path / "src.epub"
    tgt = tmp_path / "out.epub"
    _build_epub(src, [["嵌套"]], nested=True)
    fake = _FakeLLM(handler=_legacy_mixed_handler("L"))
    report = translate_one_pass(
        src, tgt, "en", "prompt", fake,
        max_group_tokens=5000, max_retries=2, strict=False, chapter_limit=0,
    )
    # Headers (TOC/metadata) still translate via the one-pass protocol;
    # the one-pass engine itself translated no flat units.
    assert report.translated_units == 0
    assert len(report.legacy_chapters) == 1
    assert report.legacy_two_pass_chapters == report.legacy_chapters
    # The two-pass route saw the chapter's source text in a request.
    assert any("嵌套" in c for c in fake.calls)
    # ... and the chapter body was translated, not left as source.
    content = _read_chapter(tgt, "chap1.xhtml")
    assert "嵌套" not in content
    assert "L" in content
    assert tgt.exists()


def test_book_without_toc_metadata_still_works(tmp_path):
    src = tmp_path / "src.epub"
    tgt = tmp_path / "out.epub"
    _build_epub(src, [["正文一"]], with_nav=False)
    fake = _FakeLLM(handler=_auto_handler("T"))
    report = translate_one_pass(
        src, tgt, "en", "prompt", fake,
        max_group_tokens=5000, max_retries=2, strict=False, chapter_limit=0,
    )
    assert report.chapters_done == 1
    assert "T1" in _read_chapter_raw(tgt, "chap1.xhtml")


def test_cache_seed_identifies_protocol_version_and_language(tmp_path):
    src = tmp_path / "src.epub"
    tgt = tmp_path / "out.epub"
    _build_epub(src, [["正文"]])
    fake = _FakeLLM(handler=_auto_handler("T"))
    translate_one_pass(
        src, tgt, "fr", "prompt", fake,
        max_group_tokens=5000, max_retries=2, strict=False, chapter_limit=0,
    )
    assert fake.seeds and all(seed == "one-pass-v1:fr" for seed in fake.seeds)


def test_report_round_trip_after_success(tmp_path):
    src = tmp_path / "src.epub"
    tgt = tmp_path / "out.epub"
    _build_epub(src, [["甲"], ["乙"]])
    fake = _FakeLLM(handler=_auto_handler("T"))
    report = translate_one_pass(
        src, tgt, "en", "prompt", fake,
        max_group_tokens=5000, max_retries=2, strict=False, chapter_limit=0,
    )
    assert report.target == tgt
    assert report.translated_units == 2
    assert report.requests >= 3  # toc + metadata + chapters
