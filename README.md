# Web Novel EPUB Translator

Translate Chinese web novels (EPUB) into English using the **OpenCode Go** API, with a shared + per-novel glossary for consistent names, skills, and terms.

## How to use

1. **Install Python 3.13** from <https://www.python.org/downloads/> and tick **"Add python.exe to PATH"** during install. (Python 3.11, 3.12, or 3.13 all work.)
2. Double-click **`run.bat`**. First run installs the required packages automatically.
3. The first time you open it, paste your **OpenCode Go API key** (get one at <https://opencode.ai/auth>). It is stored locally in `settings.json` (never committed to git).
4. Put your `.epub` files in the **`books`** folder.
5. Use the menu:
   - **Scan a book for new terms** — automatically finds character names / skills / terms and proposes English translations for you to approve.
   - **Manage glossary** — add, edit, or delete terms (shared = all books, or per book).
   - **Translate a book** — shows a cost estimate, then translates with a live progress bar. The English book appears in **`out`**.
   - **Check translation quality** — scans the finished book for terms translated inconsistently.
6. Re-running a translation resumes from its cache (`cache/`), so a crash costs nothing.

## Files and folders

| Path | Purpose |
|---|---|
| `run.bat` | double-click launcher (checks Python, sets up environment, opens the app) |
| `app.py` | interactive terminal menu |
| `translate_book.py` | translation pipeline (engine: `epub-translator`) |
| `scan_glossary.py` | auto-suggest glossary terms from a book |
| `qa_check.py` | consistency check on a translated book |
| `glossary.py` | shared + per-novel glossary handling |
| `config.py` | settings, folders, API details |
| `books/` | drop `.epub` files here |
| `out/` | translated `.epub` files appear here |
| `glossaries/` | glossary JSON files (managed by the app) |
| `cache/` | per-book translation cache (safe to delete) |

## Environment variables (optional)

- `OPENCODE_GO_API_KEY` — overrides the saved API key.
- `OPENCODE_GO_MODEL` — overrides the model (default `deepseek-v4-flash`).
- `OPENCODE_GO_BASE_URL` — overrides the API endpoint (default `https://opencode.ai/zen/go/v1`).

## Notes

- Cost is estimated at $0.14 per million input tokens and $0.28 per million output tokens (DeepSeek V4 Flash via OpenCode Go). A ~1M-character novel typically costs well under $1.
- `epub-translator` requires Python 3.11–3.13; the launcher will warn you if your Python is incompatible.
