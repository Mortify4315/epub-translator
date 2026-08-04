# SPEC — Web Novel EPUB Translator

Status: active development · Last updated: 2026-08-03 · Runtime: `.venv` (Python 3.13.14, supports 3.11–3.13)

## 1. Purpose

Mass-translate Chinese web-novel EPUBs into English using the DeepSeek API, with a
shared + per-novel glossary so names, skills, places, and cultivation terms stay
consistent across a book (and across a series).

Primary user is non-technical and drives everything from a terminal menu (`run.bat` → `app.py`).

## 2. Goals / non-goals

Goals:
- Translate a whole WebToEpub EPUB into a valid English EPUB: full text, preserved `<p>` structure, cover, CSS, TOC.
- Consistent terminology via user-curated glossaries.
- Low cost and reasonable speed on a hobbyist budget.
- Resume/crash safety: a re-run must not re-bill work already done.

Non-goals:
- No web/mobile UI, no in-app translation editor, no batch queue.
- Chinese→English only; no other language pairs.
- Not a general-purpose EPUB tool — only handles the WebToEpub layout this pipeline normalizes.

## 3. Architecture

```
run.bat ──> app.py (questionary/rich TUI)
              ├─ translate_book.py ──> epub-translator (third-party engine) ──> DeepSeek API
              ├─ scan_glossary.py  ──> jieba + DeepSeek API
              ├─ qa_check.py       ──> glossary-consistency scan (offline)
              ├─ glossary.py       ──> shared + per-book JSON
              └─ config.py         ──> settings, folders, API config
```

Directories: `books/` (sources), `out/` (English output), `glossaries/`, `cache/` (per-book
translation cache + `prep/` normalized copies). `cache/`, `out/`, `books/*.epub`,
`settings.json` are gitignored.

## 4. Translation pipeline (`translate_book.py`)

1. **Prepare** (`prepare_epub`) — read source, normalize each chapter, add missing EPUB3 nav, write `cache/prep/<name>.prep.epub`; original file untouched.
2. **Glossary prompt** (`glossary.build_translation_prompt`) — merge shared + per-book glossary; injected as `<rules>` into the translate system prompt.
3. **Translate** — engine `translate()` over the prepared EPUB with per-group concurrency; output written to `out/<name>.en.epub`; every request cached under `cache/<book-key>/`.

### Engine model (third-party `epub-translator`)

The engine splits each chapter into *serial groups* bounded by `max_group_tokens`
(our default 5000), processes groups concurrently (`ThreadPoolExecutor`, our default 8),
and per group makes **two LLM calls**:

- **translate pass** — render the source text of the group (plus head/tail context from
  neighbouring groups) into English.
- **fill pass** — given source, translation, and an XML template of the group, the model
  must map the English back into the template, preserving tags/ids (`data-orig-len`
  attribute hints per element). Validated **structurally only** (tag/id/counts).

The two passes use **separate `LLM` instances** in `translate_book.py` (`translation_llm` + `fill_llm`),
so the fill pass can run with adaptive thinking while the translate pass stays fast.

### Why two passes

Chinese→English reorders words, so the translation can't be mapped to the source
structure positionally; a separate fill model does semantic alignment. This is also the
fragile step (see Known Issues §9.1).

## 5. Glossary subsystem (`glossary.py`)

- Files: `glossaries/global.json` (shared, all books) + `glossaries/<book-key>.json`.
- `book_key(name)` = sanitized filename stem (alnum + `-_`).
- Lookup = `merge_glossaries`: global, then per-book overrides.
- Writes are alphabetized; each save is a full rewrite.
- **Cache coupling**: the translation cache key = sha512(messages + cache_seed). The
  glossary prompt is part of the messages, so **any glossary edit invalidates that book's
  translation cache** → the next run re-translates from scratch.

## 6. Glossary scan (`scan_glossary.py`)

1. Extract chapter text; tokenize with `jieba`; keep pure-CJK tokens of length 2–12 not in a large stopword list.
2. Rank by frequency; take top `max_terms` (default 60) meeting `min_count` (default 5).
3. Batch-propose English via DeepSeek (temperature 0.2), parse `src => dst` lines (or JSON).
4. Merge fresh terms into the per-book (or global) glossary.

## 7. QA (`qa_check.py`)

Offline, no API. For each glossary term, looks for whitespace / hyphen / underscore
variants of the canonical translation appearing more than the canonical form; reports
term → chapter → variant → count. Pure heuristic; "variant is actually fine" is a common
false-positive and is handled by adding the variant to the glossary.

## 8. API & configuration (`config.py`)

- Provider: DeepSeek OpenAI-compatible API. Base URL `https://api.deepseek.com`, model `deepseek-v4-flash` (also `deepseek-v4-pro`).
- Auth: `settings.json` (gitignored) or env vars `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`, `DEEPSEEK_BASE_URL`.
- Thinking: toggleable (`{"thinking": {"type": "enabled|disabled"}}` sent via `extra_body`). **Default: disabled (fast).** Disabling removes reasoning tokens → dramatically lower cost/latency.
- Pricing (per 1M tokens): $0.14 input / $0.28 output (flash); $0.0028 cache-hit input. Peak hours (09:00–12:00, 14:00–18:00 Beijing) are 2x. Concurrency limit: 2500.
- `token_encoding = cl100k_base` is used by the engine only as an approximation for grouping; not the model's real tokenizer.
- Tuning knobs in `settings.json`: `concurrency` (default 8), `max_group_tokens` (default 5000), `thinking`, `fill_thinking` (default `adaptive`), `model`, `api_key`, `token_budget` (default 1,500,000; `Test_` books auto-use `token_budget_test` default 300,000), `max_retries`/`retry_times` (default 2).
- **Token budget guard**: `run_translation` polls cumulative tokens (both LLMs) after each chapter; over budget raises `BudgetExceeded`, aborting the run. Cache is kept, so a re-run resumes. Test books (`Test_*.epub`) get a tight default budget so failed test runs can't burn tokens.
- **Two LLMs**: the translate pass uses `thinking` (speed/quality toggle); the fill pass uses `fill_thinking`
  (`adaptive` | `enabled` | `disabled`). The engine's cache key does NOT include thinking mode, so any change
  to `thinking`/`fill_thinking`/`model` clears that book's translation cache (config marker in `cache/<book>/config.json`).

## 9. Known issues / open items

1. **Fill-step can backfill Chinese (RESOLVED 2026-08-03).** When the fill model can't align the
   translation to the template it falls back to the original source text — an explicitly
   permitted "last resort" in the engine's `fill.jinja` — and the structure-only validator
   accepts it. Symptom: chapters partially untranslated (Chinese blocks in output) while
   complete English translations exist in cache. Cause confirmed: thinking-off degrades the
   fill pass's alignment → lazy source-copy. Fix: the fill pass now runs on a **separate
   `fill_llm`** whose thinking mode is set by `fill_thinking` (default `adaptive`), independent
   of the fast translate pass. Verified 2026-08-03 on the full book: diag cache with
   fill-thinking on showed ~0 CJK (2 stray chars in 67 fills vs 15/37 catastrophic fills
   thinking-off); the real-book run produced **TOTAL CJK 0, qa_check 0 issues, `<p>` counts
   preserved in all 13 chapters** (cost ~$0.08–0.09/run). Remaining strays are glossary-shaped
   terms (笼罩/后天/瘸猴) — fixed by adding them to the book glossary. Caveat: changing
   thinking/fill/model invalidates the book cache (config marker auto-clear).
2. Glossary edits silently invalidate the book's translation cache → forced full re-translation.
3. Progress UI is per-request, not per-chapter; CLI prints nothing between requests.
4. No `max_tokens` cap on engine calls → runaway reasoning when thinking is enabled (mitigated by the token-budget guard).
5. `normalize.py` has no unit tests; it's the piece most likely to regress on other WebToEpub variants.
6. Glossary scan default `max_terms=60` after a 120-term run timed out; not stress-tested on a full book.
7. Global glossary still empty (`{}`) — scan→commit workflow not yet exercised end-to-end via the app.

## 10. Verification discipline

- This repo has a history of "looks done, actually broken" (engine XML mapping).
- Before claiming success: re-run the translated EPUB through a CJK-remnant scan
  (all CJK in output should be ~0) and `qa_check`; inspect chapter structure (`<p>` preserved).
- Enable engine logging via `LLM(log_dir_path=...)` to read `[[Request]]`/`[[Response]]` and see exactly what the model returned.
