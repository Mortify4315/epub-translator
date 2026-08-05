# Web Novel EPUB Translator — Web GUI

Browser interface for the [epub-translator](https://github.com/Mortify4315/epub-translator) project.
Lets a non-technical user upload books, translate with live progress, manage glossaries,
scan for terms, run quality checks, and edit settings — no terminal menus.

## Requirements

- Windows, Python 3.11, 3.12, or 3.13 installed and on PATH.
- The sibling `epub-translator` project at `..\epub-translator` (its `books/`, `out/`,
  `cache/`, `glossaries/`, `settings.json` are the single source of truth for data).
- A DeepSeek API key set in the sibling's `settings.json` or via the Settings tab.

## How to run

Double-click `run.bat`. It sets up a `.venv`, installs dependencies, and opens
http://127.0.0.1:8177 in your browser.

If the epub-translator project lives somewhere else, set `EPUB_TRANSLATOR_PATH` to its
folder before running.

## What it can do

- **Translate** — pick a book (or upload one), see a cost estimate, translate with a live
  progress bar, download the finished EPUB. Re-running resumes from the cache.
- **Glossary** — add / edit / delete terms in the shared or a per-book glossary.
  Editing a glossary clears that book's translation cache.
- **Scan Terms** — auto-find names/skills/terms in a book, review proposed English
  translations, and add the ones you want.
- **Quality Check** — offline scan for terms translated inconsistently.
- **Settings** — API key, model, concurrency, translate mode, fill mode.
  Changing translate/fill mode clears the translation cache.

## Dev

```bash
.venv\Scripts\python -m pytest
.venv\Scripts\python app.py
```

The server binds `127.0.0.1` only — single user, local machine.
