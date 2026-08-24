# Product

<!-- impeccable:product-schema 1 -->

> Product facts below are inferred from the repository documentation and the user's redesign brief unless explicitly stated otherwise.

## Platform

web

## Users

The primary user is a non-technical Windows hobbyist translating long Chinese web-novel EPUBs into English. They may work in either a local browser UI or an interactive terminal and need to understand cost, progress, and recovery without learning the translation engine.

## Product Purpose

Translate WebToEpub-style Chinese web novels into valid English EPUBs while preserving chapter structure, cover, CSS, and table of contents. Success means a user can prepare terminology, estimate spend, run or resume a long translation, and verify consistency with confidence.

## Positioning

The product combines a resumable, cache-aware EPUB translation pipeline with shared and per-book glossaries, explicit cost controls, terminology discovery, and offline consistency checks. The browser and terminal interfaces share the same local books, output, cache, settings, and glossary data.

## Operating Context

- Local, single-user Windows application launched from `run.bat`.
- Source EPUBs live in `cli/books/`; completed books appear in `cli/out/`.
- Translation may run for a long time and make paid API calls, so estimates, progress, stop behavior, token budgets, partial runs, and cache reuse are material.
- Users prepare a book, scan or curate its terminology, translate it, then run a quality check.
- Multiple OpenAI-compatible providers and models are supported through saved local settings or environment variables.

## Capabilities and Constraints

- Chinese-to-English only; not a general-purpose EPUB editor.
- Supports one-pass and legacy two-pass pipelines, provider/model selection, concurrency, token budgets, chapter limits, retries, and thinking modes.
- Can upload or discover EPUBs, estimate work, start/stop translation, resume from cache, download output, scan terminology, manage shared/per-book glossaries, and run glossary consistency checks.
- API keys are stored locally and must never be exposed in UI, logs, tests, or committed files.
- The Flask server binds to localhost and the WebUI is framework-free HTML/CSS/JavaScript; the TUI uses Rich and Questionary.

## Brand Commitments

- Working product name: Web Novel EPUB Translator.
- Explicit user commitment: both interfaces should feel modern, intentional, and product-specific—not generic “AI slop.”
- Voice should be practical, calm, and direct for a non-technical operator.

## Evidence on Hand

- Product and setup documentation: `README.md`, `cli/README.md`, `web/README.md`, and `cli/SPEC.md`.
- Existing feature routes, tests, and benchmark notes are present in the repository.
- No logo, customer proof, testimonials, or marketing claims are provided; future work must not fabricate them.

## Product Principles

1. Make long-running paid work predictable before it starts and legible while it runs.
2. Preserve the user's work: resume safely, communicate cache effects, and make destructive consequences explicit.
3. Keep terminology preparation, translation, and verification connected as one book workflow.
4. Expose advanced controls progressively while keeping the common path obvious.
5. Keep the WebUI and TUI recognizably the same product without forcing identical layouts.

## Accessibility & Inclusion

The WebUI should meet WCAG 2.2 AA for keyboard use, focus visibility, contrast, motion preferences, form labeling, and responsive operation. The TUI should remain usable without color alone and should degrade cleanly in narrow or non-Unicode terminals.
