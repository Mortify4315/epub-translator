# Web Novel EPUB Translator — Web GUI

Browser interface for the [epub-translator](https://github.com/Mortify4315/epub-translator) project.
Lets a non-technical user upload books, translate with live progress, manage glossaries,
scan for terms, run quality checks, and edit settings — no terminal menus.

## Requirements

- Windows, Python 3.11, 3.12, or 3.13 installed and on PATH.
- The sibling `epub-translator` project at `..\epub-translator` (its `books/`, `out/`,
  `cache/`, `glossaries/`, `settings.json` are the single source of truth for data).
- An API key for most supported providers (DeepSeek, OpenCode Go, OpenAI, Anthropic,
  Gemini, xAI, Groq, Mistral, OpenRouter, or custom), set in `settings.json` or
  Settings. The local 9Router preset does not require one by default.

## How to run

Double-click `run.bat`. It sets up a `.venv`, installs dependencies, and opens
http://127.0.0.1:8177 in your browser.

If the epub-translator project lives somewhere else, set `EPUB_TRANSLATOR_PATH` to its
folder before running.

## What it can do

- **Translate** — pick a book (or upload one), see a cost estimate, translate with a live
  progress bar, download the finished EPUB. Re-running resumes from the cache.
- **Glossary** — search, add, edit, delete, import, or export terms in the shared or
  a per-book glossary. Editing a glossary clears that book's translation cache.
- **Scan Terms** — auto-find names/skills/terms in a book, review proposed English
  translations, and add the ones you want.
- **Quality Check** — offline scan for terms translated inconsistently.
- **Settings** — provider, API key, model, base URL, concurrency, pipeline,
  strict one-pass mode, chapter limit, group size, translate mode, and fill mode.
  Changing translation identity settings clears the affected cache.

The responsive Novel Press interface supports light/dark themes, keyboard navigation,
live job recovery, a completed-output shelf, and a 390px-wide mobile layout without
horizontal scrolling. `Alt+1` through `Alt+5` switch primary views.

### 9Router

Choose **9Router (local)** to route requests to `http://localhost:20128/v1`.
Available preset models are `fusion-panel`, `qd/auto`, and `qd/ultimate`. The API key
is optional unless your local router is configured to require one.

## Dev

```bash
.venv\Scripts\python -m pytest
.venv\Scripts\python app.py
```

The server binds `127.0.0.1` only — single user, local machine.
