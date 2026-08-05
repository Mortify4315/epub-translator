# Web Novel EPUB Translator

Translate Chinese web novels (EPUB) into English using the **DeepSeek** API, with a shared + per-novel glossary for consistent names, skills, and terms.

Two frontends live in this repository, sharing the same core:

| Subfolder | What it is |
|---|---|
| `cli/` | Interactive terminal app (original core: translate, glossary, scan, QA). Run `cli\run.bat`. |
| `web/` | Browser GUI on http://127.0.0.1:8177 (Flask). Run `web\run.bat`. |

## How the pieces fit together

- `cli/` is the **core engine**: `config.py`, `glossary.py`, `translate_book.py`, `qa_check.py`, `scan_glossary.py`. Data lives in `cli/books/`, `cli/out/`, `cli/cache/`, `cli/glossaries/`, `cli/settings.json`.
- `web/` loads the core from the sibling `cli/` folder at runtime (`core_loader.py` resolves `../cli`). Override with the `EPUB_TRANSLATOR_PATH` environment variable if you move things around.
- `web/` shares the same `books/`, `out/`, `glossaries/`, and settings as `cli/`.

## Getting started

1. Install **Python 3.13** (3.11–3.13 all work) and tick **"Add python.exe to PATH"**.
2. Double-click `cli\run.bat` or `web\run.bat`. First run installs the required packages automatically (each frontend keeps its own `.venv`).
3. Paste your **DeepSeek API key** on first use (stored locally in `cli/settings.json`, never committed).
4. Drop `.epub` files into `cli/books/`.

## For developers

- Run the web tests: `cd web` then `python -m pytest` (uses `web\.venv`).
- Git history: the `web/` subfolder was merged from the former `epub-translator-web` repository (`--allow-unrelated-histories`); its old history is preserved, and its old GitHub remote is kept as `origin-web`.
