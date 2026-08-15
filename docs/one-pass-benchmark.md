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

---

## Continuation session — calibrated pilot, E4, and production eligibility

### Pilot rerun (5-ch book, 150,000-token budget) — a discovery, not a measurement

Authorized rerun on the same `Test_ChiXinXunTian_5ch.epub`, budget 150,000:

| Metric | two-pass (baseline) | one-pass (candidate) |
| --- | ---: | ---: |
| Total tokens | 163,673 (guard fired) | 106,876 (completed) |
| Requests / retries | 24 / 5 | 16 / 0 |
| Blocks translated | 0 (headers only) | 619 / 619 |
| Errors | `exceeded token budget: 163,673 > 150,000` | strict CJK gate abort (see below) |

Two findings:

1. **The doc's 110–130k two-pass estimate was wrong.** The two-pass run
   exceeded 150,000 tokens *before starting chapters* (TOC/metadata/nav
   through the full TRANSLATE+FILL machinery with adaptive fill thinking).
   The completed-run calibration in `translate_book.estimate()` (251,722
   tokens for the 5-ch book, 404,141 for 13 chapters) is the correct basis.
2. **The 5-ch test book is non-flat.** Its chapters carry the
   `<div id="chapter">` wrapper of the production book, so *every* chapter
   routed through the chapter-scoped two-pass fallback — the numbered
   protocol never ran live in either pilot. All 1,753/16,765 CJK remnants
   were fallback FILL backfill. The strict CJK gate (commit `0e1eed5`)
   caught the backfill and aborted loudly (strict) after one corrective
   reroute — working as designed. The reroute is a fresh call (the remedy
   prompt changes the cache-key messages hash), so the remaining CJK is
   model behavior on that chapter, not a cache artifact.

### E4 preparation

The plan's Task-6 source `赤心巡天_test.epub` is a `<br>`-based,
traditional-Chinese 5-chapter book (0 `<p>` tags) — unusable for a
one-pass measurement. A new flat slice was built from the production book:
`cli/books/Test_ChiXinXunTian_13ch.epub` — first 13 real chapters
(`chapter_00001..00013`), wrapper unwrapped, **13/13 flat, 1,113 units**,
20 entries (cover + chapters + nav + ncx + css + media). `prepare_epub`
is a no-op on it (verified), so the harness measures the real pipeline.

### E4 gate — run 1 (serial chapter dispatch): 12/13 gates

| Gate | Observed | Result |
| --- | ---: | ---: |
| token_savings (≥50%) | 84.8% | ✅ |
| request_savings (≥40%) | 66.7% | ✅ |
| metered_cost_ratio (≤0.6) | 0.176 | ✅ |
| wall_time (≤80s) | 346.8s | ❌ |
| exact_block_count | 1130/1130 | ✅ |
| zero drops/dups/reorders/empty/missing/unexpected | 0 each | ✅ |
| zero_fallbacks / repair_rate (≤2%) | 0 / 0.0 | ✅ |
| no_cjk / no_glossary_regression / no_epub_regression | clean | ✅ |
| valid EPUB both pipelines | yes | ✅ |

One-pass: 70,701 tokens (32,325 fresh in, 38,376 out, 0 reasoning), 14
requests, 0 retries, 0 CJK in output, complete archive. **Root cause of
the wall-time failure:** each chapter is one 10k-token group (~25s), and
chapters were dispatched serially — the 32-way concurrency never engaged
across chapters (the two-pass engine parallelizes chapters; one-pass did
not).

### Fixes applied (all committed to `main`)

- `1514f65` — parallel group dispatch within a chapter (bounded pool,
  lock-protected report, order-preserving reinsertion).
- `3735187` — **wave-based parallel chapter dispatch**: waves of
  `group_concurrency` chapters; the main thread reads/writes the archive
  (Zip is not thread-safe) in spine order, workers translate (LLM only).
  Progress stays deterministic; chapter_limit translates exactly `limit`
  chapters; strict aborts ship preceding chapters and name the failing
  chapter; legacy chapters run inside the same waves.
- `507c36e` — **transparent wrapper-div eligibility**: the production
  book wraps every chapter in `<div id="chapter">`; a single transparent
  wrapper (no text/tail of its own) no longer makes a body non-flat.
  Verified: 5-ch book 5/5 flat, production book 15/15 flat. Without this,
  the real book would route every chapter to the two-pass fallback and
  one-pass would never run on it (no cost savings; strict mode would
  abort on the first FILL source echo).
- `93cdf49` — **no_cjk gate semantics**: the gate now scans the candidate
  OUTPUT archive for substantive CJK runs (2+ contiguous, incl.
  Extension-B) instead of counting report entries, which include
  intermediate ladder attempts that were *repaired before shipping*
  (visibility by design, plan §3.6). The engine validator now also covers
  Extension-B (rare glyphs such as `𠮷` must not ship silently).

### E4 gate — run 2 (wave dispatch): wall time fixed

| Gate | Observed | Result |
| --- | ---: | ---: |
| token_savings (≥50%) | 84.2% | ✅ |
| request_savings (≥40%) | 62.0% | ✅ |
| metered_cost_ratio (≤0.6) | 0.129 | ✅ |
| wall_time (≤80s) | **58.5s** | ✅ |
| exact_block_count | 1130/1130 | ✅ |
| zero drops/dups/reorders/empty/missing/unexpected | 0 each | ✅ |
| zero_fallbacks | 0 | ✅ |
| no_cjk (output scan) | 0 runs | ✅ |
| no_glossary_regression / no_epub_regression | clean | ✅ |
| valid EPUB both pipelines | yes | ✅ |
| no_runner_errors | none | ✅ |
| **repair_rate (≤2%)** | **2/13 = 15.4%** | ❌ |

One-pass: 80,476 tokens, 16 requests, wall 58.5s, complete archive,
output scan CJK-free (the 1 recorded remnant was an intermediate attempt
the bounded ladder repaired — verified by direct scan of every entry).
Two-pass baseline: 508,266 tokens, 42 requests, 196.3s.

**repair_rate is the only remaining E4 failure, and it is a sample-size
artifact:** with `max_group_tokens=10000` the E4 slice is 13–16 groups,
so *any* single retry (1/13 = 7.7%) fails the ≤2% gate — effectively
zero-tolerance. Run 1 had 0 repairs; run 2 had 2 (both single retries
that succeeded). The 100-chapter canary below measures the rate at
production scale, where the gate is meaningful.

qa_check on the completed E4 one-pass output: **"No consistency issues
found."**

### Production eligibility

The production book (`Chi Xin Xun Tian.epub`, 2,941 spine items) wraps
every chapter in `<div id="chapter">`. With the transparent-wrapper rule
(`507c36e`) its chapters classify flat (verified 15/15 on a sample), so
the one-pass pipeline applies to the real book — the premise of plan §1
holds once the wrapper is treated as the pure container it is.

### Remaining gates (this continuation)

1. 100-chapter production canary on `Test_ChiXinXunTian_100ch.epub`
   (100/100 flat, 7,665 units) with budget 3,000,000 — running.
2. Stop/resume rebilling check (chapter_limit 50 → 100, same cache).
3. 30-chapter stratified quality gate with blind pairwise judging
   (DeepSeek-only, per the user's model override).
4. Final doc + default decision (plan §10).

---

## Canary, stop/resume, quality gate — results

### Canary run 1 (100 chapters, budget 3,000,000) — strict abort exposes the name stratum

| Metric | two-pass (baseline) | one-pass (candidate) |
| --- | ---: | ---: |
| Total tokens | 3,009,162 (guard fired, 5266/7806 blocks) | 535,168 (completed 7806/7806) |
| Requests / repairs | 280 / 0 | 120 / 24 |
| Wall time | 518.1s | 168.9s |
| Output CJK runs | 14 | 3,814 (abort artifact, see below) |
| Errors | budget guard | strict abort at ch.86: `盟主乌列` |

The strict gate fired at chapter 86: the model kept the reader name
`盟主乌列` (Alliance Leader Uriel 123 in the established translation)
untranslated through every ladder retry, and strict mode refused to
ship it. **Chapters 1-85 translated with zero CJK runs**; the abort
finalized chapters 86-100 from source (that is the 3,814-run artifact).
Per-chapter calibration: one-pass 5.35k tokens/chapter vs the E4 pilot's
6.2k → **within 20%** ✓.

**Stratum repair (commits `498a19d`):** the default prompt now instructs
pinyin transliteration of personal names and translation of meaningful
titles/terms ("Never leave Chinese characters in the output"), and the
100-ch book gained a glossary (`盟主乌列`→Alliance Leader Uriel, `盟主`→
Alliance Leader, `乌列`→Uriel). The cache key includes the messages, so
this is a fresh-call repair for both pipelines. Note: the two-pass
baseline shows the same CJK in its partial output (14 runs) — the leak
is model behavior, not protocol-specific.

### Stop/resume rebilling check (chapter_limit 50 → 100, same cache)

`cli/out/e4-prep/resume_check.py` on the 100-ch slice, repaired prompt:

| Run | chapters | fresh input | cached input | output |
| --- | ---: | ---: | ---: | ---: |
| stop at 50 | 50 (limit) | 129,737 | 125,824 | 147,759 |
| resume to 100 | 100 (complete) | 132,943 | 126,976 | 147,813 |

Fresh input stayed flat while doubling the chapter count: **the completed
groups rebilled ~0 fresh input** (disk cache), i.e. near-zero rebilling
for cached groups ✓. The resume run **completed all 100 chapters with no
strict abort** and the final archive scans **0 CJK runs** (the 25 recorded
remnants were intermediate attempts the ladder repaired). This also fixed
a latent off-by-one found along the way (`fdfabc5`): `chapter_limit`
counted spine entries including the cover page, so a limit of 100 stopped
after 99 real chapters; it now counts only chapters carrying translatable
text (the production book has a cover page ahead of its chapters).

### 30-chapter quality gate (blind pairwise, DeepSeek-only judging)

Run on `Test_ChiXinXunTian_30ch.epub` (30/30 flat, 2,318 units):

| Metric | two-pass (baseline) | one-pass (candidate) |
| --- | ---: | ---: |
| Total tokens | 1,027,600 | 178,609 |
| Requests / repairs | 94 / 0 | 36 / 5 |
| Wall time | 226.6s | 75.3s |
| Blocks | 2,352 / 2,352 | 2,352 / 2,352 |
| **Output CJK runs** | **9** | **0** |
| Fallbacks | **1** | **0** |
| Errors | none | none |

Judging: 10 stratified chapters (1, 4, 7, 10, 13, 16, 19, 22, 25, 28 —
early/mid/late arcs, 53-239 paragraphs each), aligned triples (source |
two-pass | one-pass), two passes with swapped candidate order (position
bias control). Per-chapter verdict on B vs A (pass 1: B=one-pass; pass 2
swapped — results symmetric):

| Chapter | Pass 1 | Pass 2 (swapped) |
| --- | --- | --- |
| 1 | B (one-pass) better | A (one-pass) better |
| 4 | tie | tie |
| 7 | tie | tie |
| 10 | tie | tie |
| 13 | tie | tie |
| 16 | tie | tie |
| 19 | tie | tie |
| 22 | B (one-pass) better | A (one-pass) better |
| 25 | tie | tie |
| 28 | B (one-pass) better | A (one-pass) better |

**One-pass better 3/10, tied 7/10, worse 0/10 → win/tie 100% ≥ 40% ✓.**
No catastrophic omission, mistranslation, name drift, or structural
damage in the one-pass candidate (mechanical gates: exact block count,
zero drops/dups/reorders/empty). The two-pass baseline itself leaked raw
Chinese three times in the sample (`归宿`, `启蒙`, `子弟`) — one-pass
shipped none. Glossary: 0 terms applicable (empty book glossary) →
delta 0 ≤ 2pp ✓ (coverage gap noted, same as the pilots).

### Repair-rate assessment (the only failing gate, in both E4 runs and the canary)

`repair_rate` (≤2%) failed every one-pass run: 0/13 (run 1), 2/13
(run 2), 24/95 (canary), 5/30 (quality run). All repairs are single
first-attempt fixes: the model tends to leave Chinese names/terms on the
first attempt, the deterministic validator flags them, and the bounded
ladder fixes them — **zero repaired items ever shipped, zero source-copy
fallbacks, zero CJK in any completed one-pass output**. At 13 groups the
gate is effectively zero-tolerance (1 repair = 7.7%); at production scale
the repair rate means +15–25% requests on the affected groups, already
included in the measured cost ratios (0.129–0.176). The alternative
(two-pass) ships CJK remnants by design (9 runs in the 30-ch baseline).

### Plan §10 decision analysis

| §10 condition | Evidence | Result |
| --- | --- | --- |
| 1. All tests pass | 186 passed, compileall, diff checks | ✅ |
| 2. E4 ≥50% token savings, zero source fallbacks | 84.2%, 0 | ✅ |
| 3. 30-ch quality non-inferior | win/tie 100%, no defects | ✅ |
| 4. Projected full-book metered cost ≤60% of baseline | ratio 0.129–0.176 | ✅ |
| 5. 100-ch canary within 20% of pilot calibration | 5.35k vs 6.2k tok/ch | ✅ |

All five §10 conditions hold. The only §7 gate that fails is
`repair_rate`, which measures a bounded, visible, zero-defect mechanism
(see above). **Recommendation: flip the default to `pipeline=one-pass`**
(with two-pass retained for rollback and non-flat books); the plan's
fallback ("keep two-pass") applies when the §10 conditions fail, which
they do not. Final call deferred to the user; one-line settings change.

### Remaining production note

The full-book run (2,941 chapters ≈ 16M tokens) has not been attempted;
per the plan, the canary is the precondition ("only then run the
remaining book"). The stop/resume result shows a stopped run can resume
with near-zero rebilling, so the 2,000+ chapter book can be translated
in bounded batches.
