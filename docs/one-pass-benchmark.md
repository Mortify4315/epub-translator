# One-pass benchmark acceptance

Verification timestamp: 2026-08-15T13:08:56Z
Worktree: `D:/HobbyProjects/epub-translator/.worktrees/t_008f0f58`
Branch: `onepass-benchmark`

## Verdict

**NO-GO.** The benchmark harness and its tests are mechanically sound enough to commit, but one-pass is not accepted as a production default. The live E4 comparison is not a valid measurement, and the 30-chapter quality and 100-chapter canary gates were not run. The existing two-pass default must remain in place.

## Verification evidence

The following verification was run in this worktree with the requested project interpreter. The inherited Hermes `PYTHONPATH` was cleared so the interpreter used its own `web/.venv` packages.

| Check | Command/result |
| --- | --- |
| Focused benchmark tests | `env PYTHONPATH= D:/HobbyProjects/epub-translator/web/.venv/Scripts/python.exe -m pytest web/tests/test_benchmark_onepass.py -q` — **18 passed in 0.23s** |
| Full suite | `env PYTHONPATH= D:/HobbyProjects/epub-translator/web/.venv/Scripts/python.exe -m pytest -q` — **152 passed in 1.88s** |
| Compile check | `env PYTHONPATH= D:/HobbyProjects/epub-translator/web/.venv/Scripts/python.exe -m compileall -q cli web tools` — **exit 0** |
| Ruff | `uv run --python 3.13 --no-project --with ruff ruff check tools/benchmark_onepass.py web/tests/test_benchmark_onepass.py` — **All checks passed!** |
| Diff whitespace check | `git diff --check` — **no errors**; Git emitted only LF-to-CRLF normalization warnings |
| Legacy behavior evidence | `web/tests/test_onepass.py::test_legacy_chapter_routed_and_left_unchanged` — **1 passed in 0.20s**; this test also documents the blocker below |

The focused harness changes cover dependency-injected runners, isolated settings/cache/output paths, paid-run confirmation, token-budget input, usage telemetry, request/retry/repair/fallback metrics, EPUB validity and structure/media comparisons, glossary checks, deterministic JSON/Markdown reports, and budget-overrun instrumentation. No benchmark source or test file outside this acceptance worktree was changed.

## Measured-but-invalid live run caveat

A prior worker began an authorized live two-pass/one-pass run, but the captured result had **zero baseline telemetry**. Because the baseline token/request measurements were zero, savings, cost, and request-reduction ratios are undefined and cannot be used as an E4 result. No trustworthy completed report was captured. The run is therefore recorded as **measured-but-invalid**, not as a pass or a failure, and all live chapter gates are treated as **UNRUN**.

This worker did **not** start another paid API benchmark.

## Independent core/integration review

The review was bounded to the approved plan and the merged implementation. The benchmark owner did not edit core or integration files.

### Blocker 1 — non-flat chapters are not actually routed to two-pass

The approved plan requires non-flat chapters to automatically use the existing two-pass engine (plan C3). `cli/onepass.py` classifies such chapters as legacy at lines 690–693 and appends them to `legacy_chapters`, but does not invoke the two-pass engine. Both `cli/translate_book.py` lines 313–325 and `web/job_runner.py` lines 127–140 select one-pass for the whole EPUB; neither performs per-chapter fallback/routing. The resulting one-pass archive leaves a nested/complex chapter's source text unchanged rather than translating it.

The passing test `test_legacy_chapter_routed_and_left_unchanged` makes the behavior explicit: it asserts that the complex chapter is untouched and says that legacy routing is the caller's job. That is useful characterization, but it is not the approved acceptance behavior. This is a core/integration ownership blocker and was not edited here.

### Blocker 2 — web one-pass bypasses the token-budget guard

`web/job_runner.py` defines `budget_aware_progress` at lines 117–121, and the two-pass call passes that wrapper at line 153. The one-pass call at lines 127–140 instead passes the raw `on_progress` callback. `translate_one_pass()` has no token-budget parameter or independent budget check, so a web one-pass run can exceed the configured hard token budget without raising `BudgetExceeded`. This violates the plan's budget-guard/canary requirement and must be corrected by the integration owner. It was not edited here.

No additional specific core/integration blocker was established within this bounded review. The two blockers above must be resolved and retested before any live gate can support a GO decision.

## Remaining acceptance gates

1. **Live E4 rerun with valid baseline telemetry.** Run the same source and pinned provider/model configuration for two-pass and one-pass, confirm that baseline input/output/total tokens and request count are nonzero and internally consistent, then verify the approved thresholds: at least 50% total-token reduction, at least 40% request reduction, candidate wall time at most 80 seconds, exact translated block count, zero drops/duplicates/reorders/empty blocks/source-copy fallbacks, repair rate at most 2%, zero CJK/glossary regressions, valid EPUB, and preserved media/structure.
2. **30-chapter quality gate.** Use the approved stratified sample and blind pairwise review against two-pass, with independent Luna max and Terra high judgments, glossary delta no worse than two percentage points, and at least 40% candidate win/tie rate with no catastrophic omission, mistranslation, name drift, or structural damage.
3. **100-chapter production canary.** Run with `chapter_limit=100` and a hard token budget calibrated from valid E4 telemetry; stop if tokens/chapter or cost/chapter exceeds pilot calibration by more than 20%, and verify stop/resume causes near-zero rebilling for completed cached groups.
4. **Implementation blockers above.** Fix the per-chapter legacy-to-two-pass routing and the web one-pass budget guard in their owning core/integration files, then rerun the relevant tests before repeating paid gates.

Until all four items are closed, the acceptance decision remains **NO-GO** and `pipeline=two-pass` remains the safe default.
