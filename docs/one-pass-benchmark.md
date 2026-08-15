# One-pass benchmark acceptance

Verification timestamp: 2026-08-15T20:55:00Z
Worktree: `D:/HobbyProjects/epub-translator/.worktrees/t_008f0f58`
Branch: `onepass-benchmark` @ `60a3591` (post-merge: `8b9db3d` routing fix + `1509f62`/`60a3591` budget-guard fix)

## Verdict

**NO-GO.** The 5-chapter pilot was executed exactly once against the live
provider and is **measured-but-incomplete**: both pipelines were hard-stopped
by the 100,000-token per-run budget guard before completing, so no gate
comparison is valid. Telemetry is now nonzero and internally consistent (the
prior zero-telemetry defect is resolved), and the two previously open
implementation blockers (plan C3 non-flat routing, web one-pass budget guard)
are verified resolved in this worktree. The 13-chapter E4, 30-chapter quality,
and 100-chapter canary gates remain **UNRUN**. The existing two-pass default
must remain in place.

## Verification evidence

The following verification was run in this worktree with the parent web venv
(`D:/HobbyProjects/epub-translator/web/.venv`, Python 3.13.14). The inherited
Hermes `PYTHONPATH` was cleared so the interpreter used the venv's own
packages.

| Check | Command/result |
| --- | --- |
| Focused benchmark tests | `env -u PYTHONPATH D:/HobbyProjects/epub-translator/web/.venv/Scripts/python.exe -m pytest web/tests/test_benchmark_onepass.py -q` — **18 passed in 0.75s** |
| Full suite | same interpreter `-m pytest -q` — **159 passed in 3.44s** (up from 152; +7 budget-guard/one-pass integration tests) |
| One-pass core tests | `-m pytest web/tests/test_onepass.py -q` — **55 passed in 0.55s**, including `test_legacy_chapter_routed_to_two_pass_fallback`, `test_legacy_fallback_preserves_media_and_structure` |
| Compile check | `-m compileall -q cli web tools` — **exit 0** |
| Ruff | `uv run --python 3.13 --no-project --with ruff ruff check tools/benchmark_onepass.py web/tests/test_benchmark_onepass.py` — **All checks passed!** |
| Diff whitespace check | `git diff --check` — **no errors** |
| Sandbox/report hygiene | `report.json` contains no `sk-`/`api_key`/`provider_keys` strings; deterministic JSON + Markdown |

No benchmark source or test file needed changes this round: the harness and
its tests from the prior NO-GO round were already mechanically sound and
passed unchanged.

## Live pilot (5-chapter, run exactly once)

Authorized paid run, executed once on 2026-08-15 20:43–20:49 UTC+7:

```
python tools/benchmark_onepass.py
  --source cli/books/Test_ChiXinXunTian_5ch.epub
  --output cli/out/final-onepass-gate/report.json
  --token-budget 100000 --confirm-paid
  --provider opencode-go --model deepseek-v4-flash
  --thinking disabled --strict
  --settings-file cli/settings.json            (current settings, read-only)
  --sandbox-root cli/out/final-onepass-gate
```

Source: `Test_ChiXinXunTian_5ch.epub` — 5 chapters, 13 ZIP entries, 24,209
source text characters, 619 p/h blocks, cover.jpg media, style.css/nav/toc
structure. Settings pinned: provider `opencode-go`
(`https://opencode.ai/zen/go/v1`), model `deepseek-v4-flash`, thinking
`disabled` (fill `adaptive` per current settings), strict `True`,
max_group_tokens 10,000, concurrency 32, pricing `OpenCode Go table dated
2026-08-14` (0.14/0.0028/0.28 USD per M fresh/cached/output).

### Outcome: budget-guard abort on both pipelines

Both runs were hard-stopped by the harness budget guard (`_instrument_llm`,
which raises `BenchmarkError` at the first streamed response whose cumulative
total crosses the budget). Exit code 1, decision **NO-GO**.

| Metric | two-pass (baseline) | one-pass (candidate) |
| --- | ---: | ---: |
| Total tokens (at abort) | 102,805 | 101,032 |
| Fresh input / cached input | 45,200 / 8,960 | 46,788 / 19,968 |
| Output tokens | 48,645 | 34,276 |
| Reasoning tokens | 12,681 (fill pass, adaptive) | 0 |
| Requests | 24 | 16 |
| Retries | 8 | 1 |
| Repairs / fallbacks | 0 / 0 | 0 / 0 |
| Wall time (s) | 72.40 | 247.78 |
| Est. cost USD | 0.01997369 | 0.01620351 |
| Blocks expected / translated | 619 / 0 | 619 / 360 |
| Missing / unexpected / drops / dups / reorders / empty | 0 each | 0 each |
| CJK remnants in output | 0 | 1,753 (all in ch.1) |
| Glossary violations (0 terms applicable) | 0 | 0 |
| ZIP valid (mimetype-first, CRC-clean) | yes | yes |
| Archive completeness | **partial**: mimetype + nav.xhtml + content.opf only | **partial**: mimetype + ch.1 + ch.2 only |

Telemetry validation — **all pass**: totals nonzero for both pipelines;
`fresh_input + cached_input == input` (45,200+8,960=54,160; 46,788+19,968=66,756);
`input + output == total` (102,805; 101,032); request counts nonzero.

Archive comparison: the `media_removed` (cover.jpg), `structure_removed`
(content.opf, nav.xhtml, style.css, toc.ncx, container.xml) and the
0/619-vs-360/619 block gap are **abort artifacts of the budget stop**, not
pipeline regressions — the engine zips were finalized only up to the entries
already processed when the guard fired. Both partial archives are
zip-valid (mimetype first, stored, CRC-clean).

CJK characterization: all 1,753 remnant characters sit in chapter 1 — the
untranslated chapter title (`第1章 他惊人的毅力并无观众`) and several
untranslated/echoed paragraphs near the chapter tail; chapter 2 is fully
translated (0 CJK). Fallback/repair counters are zero on both pipelines, so
these are direct model output leftovers in the completed portion, not
source-copy fallbacks.

Glossary: the production glossary for this pilot book is empty — `global.json`
is `{}` and no `Test_ChiXinXunTian_5ch.json` exists — so `terms_checked=0` and
the glossary gate passes trivially. This is a **coverage gap** for the pilot:
glossary plumbing was exercised end-to-end (load/copy/check) but with no
applicable terms.

qa_check: **not compatible with these outputs**. `qa_check.check()` reads via
ebooklib, which requires `META-INF/container.xml`; both aborted partials lack
it (`KeyError: There is no item named 'META-INF/container.xml'`). qa_check is
compatible with completed EPUBs only; it must be rerun on the outputs of any
successful future gate run.

### Pilot interpretation

- The 100,000-token budget is **too tight for this book**: measured
  consumption at the stop was 102,805 (two-pass) / 101,032 (one-pass) tokens.
  A valid rerun requires a budget calibrated above the observed consumption
  (≈110–130k for a clean two-pass of this 5-chapter book, plus margin). Per
  the exactly-once authorization, **no rerun was performed**.
- The budget guard — the integration repair being gated — was demonstrated
  **live and working**: it stopped both runs at the first over-budget response
  and produced explicit `BenchmarkError` entries rather than a silent overrun.
- The failed gates (`token_savings` 0.0172, `request_savings` 0.3333,
  `metered_cost_ratio` 0.8112, `wall_time` 247.8s, `exact_block_count`,
  `no_cjk`, `no_epub_regression`, `no_runner_errors`) are **artifacts of the
  abort**, not E4 measurements. The structural gates that did hold on the
  completed portion (valid zip, zero drops/duplicates/reorders/empty/
  missing/unexpected, repair rate 0, zero fallbacks) are reported as-is.

## Independent core/integration review (blocker re-verification)

Both blockers from the prior NO-GO round are **RESOLVED** in this worktree;
verified by code reading and by the tests listed above. No core/integration
file was modified by this worker.

### Blocker 1 — plan C3 non-flat chapter routing: RESOLVED

`cli/onepass.py` `translate_one_pass()` classifies non-flat chapters and now
routes each through `_translate_legacy_chapter()` (lines 595–651): the real
two-pass `XMLTranslator` with `SubmitKind.REPLACE`, the MathML/LaTeX
interrupter, id deduplication, and the legacy cache seed (`0.1.10:en`),
scoped to the chapter `<body>`; the translated body is written back via
`zip.replace`. `OnePassReport.legacy_two_pass_chapters` records the routed
subset. `test_legacy_chapter_routed_to_two_pass_fallback` proves the
ineligible chapter is translated (source gone, legacy seed used, inline
structure preserved) while a flat sibling stays one-pass;
`test_legacy_fallback_preserves_media_and_structure` proves archive entry
sets and image/block attributes survive.

### Blocker 2 — web one-pass token-budget guard: RESOLVED

`web/job_runner.py` (lines 95–96, 119–121, 127–142) now wraps the one-pass
call in `cli/translate_book.py` `UsageBudgetGuard` (per-stream patch of
`LLM._statistics.submit_usage`, pre-patch check, restore-on-exit), passes
`budget_aware_progress` as `on_progress` (chapter-point checks), deletes the
target and keeps the cache on `BudgetExceeded`, and keeps the partial epub on
`ChapterLimitReached`. The CLI `run_translation` applies the same guard.
Covered by `test_one_pass_usage_budget_guard_stops_without_progress_and_
preserves_sibling_cache`, `test_core_one_pass_usage_budget_guard_stops_
without_progress`, `test_usage_budget_guard_restores_sink_on_exit`, and the
progress-point fallback test — all passing. The guard's live behavior was
additionally demonstrated by this pilot (both pipelines stopped at the hard
cap with `BenchmarkError`).

No new core/integration blocker was established within this review.

## Remaining acceptance gates

1. **Valid E4 comparison with a calibrated budget.** The 13-chapter E4 (and
   any 5-chapter rerun) must use a per-run budget calibrated from valid
   telemetry — the observed 5-chapter consumption (≈101–103k tokens, incl.
   retries and fill reasoning) implies ≥130,000 for the 5-chapter pilot and
   proportionally more for E4. Reuse the same pinned provider/model
   configuration; require nonzero, internally consistent telemetry (this
   round's run proves the plumbing); then verify: ≥50% total-token reduction,
   ≥40% request reduction, candidate wall time ≤80s, exact translated block
   count, zero drops/duplicates/reorders/empty blocks/source-copy fallbacks,
   repair rate ≤2%, zero CJK/glossary regressions, valid EPUB, preserved
   media/structure. **UNRUN** (this pilot is not a valid E4 measurement).
2. **30-chapter quality gate.** Stratified blind pairwise review against
   two-pass with independent Luna max and Terra high judgments; glossary
   delta ≤2 points; ≥40% candidate win/tie; no catastrophic omission,
   mistranslation, name drift, or structural damage. **UNRUN**.
3. **100-chapter production canary.** `chapter_limit=100`, budget calibrated
   from valid E4 telemetry; stop if tokens/cost per chapter exceed pilot
   calibration by >20%; verify stop/resume causes near-zero rebilling for
   completed cached groups. **UNRUN**.
4. **qa_check on completed outputs.** Not runnable on aborted partials (no
   container.xml); must be run on a successfully completed gate output.

Until the gates above close, the acceptance decision remains **NO-GO** and
`pipeline=two-pass` remains the safe default. The pilot's only executable
conclusion this round: the repaired budget guard works live, telemetry is
valid, and 100,000 tokens is insufficient for this 5-chapter pilot book.
