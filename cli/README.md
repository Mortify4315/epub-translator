# Web Novel EPUB Translator

Translate Chinese web novels (EPUB) into English using any OpenAI-compatible LLM API — DeepSeek, OpenCode Go, OpenAI, Anthropic Claude, Google Gemini, xAI Grok, Groq, Mistral, OpenRouter, or a custom endpoint — with a shared + per-novel glossary for consistent names, skills, and terms.

## How to use

1. **Install Python 3.13** from <https://www.python.org/downloads/> and tick **"Add python.exe to PATH"** during install. (Python 3.11, 3.12, or 3.13 all work.)
2. Double-click **`run.bat`**. First run installs the required packages automatically.
3. The first time you open it, paste your **API key** for the provider of your choice (Settings → Change provider, then Set / change API key). Keys are stored locally in `settings.json` (never committed to git); per-provider keys are kept in `provider_keys` so switching back and forth doesn't lose them.
4. Put your `.epub` files in the **`books`** folder.
5. Use the menu:
   - **Scan a book for new terms** — automatically finds character names / skills / terms and proposes English translations for you to approve.
   - **Manage glossary** — add, edit, or delete terms (shared = all books, or per book).
   - **Translate a book** — shows a cost estimate, then translates with a live progress bar. The English book appears in **`out`**.
   - **Check translation quality** — scans the finished book for terms translated inconsistently.
6. Re-running a translation resumes from its cache (`cache/`), so a crash costs nothing. Switching provider, model, or base URL clears the cache for that book (outputs may differ between providers).

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

- `EPUB_PROVIDER` — active provider (`deepseek`, `opencode-go`, `openai`, `anthropic`, `gemini`, `xai`, `groq`, `mistral`, `openrouter`, `custom`).
- `<PROVIDER>_API_KEY` — overrides the saved API key (e.g. `DEEPSEEK_API_KEY`, `OPENCODE_GO_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`).
- `<PROVIDER>_MODEL` — overrides the model (e.g. `DEEPSEEK_MODEL`, `OPENCODE_GO_MODEL`).
- `<PROVIDER>_BASE_URL` — overrides the API endpoint (provider defaults: DeepSeek `https://api.deepseek.com`, OpenCode Go `https://opencode.ai/zen/go/v1`, OpenAI `https://api.openai.com/v1`, Anthropic `https://api.anthropic.com/v1`, Gemini `https://generativelanguage.googleapis.com/v1beta/openai/`, …).
- `EPUB_THINKING`, `EPUB_FILL_THINKING` — override thinking modes.

The app speaks the OpenAI chat-completions wire format, so any OpenAI-compatible endpoint works. The "thinking" translate/fill modes apply to DeepSeek-family providers (DeepSeek, OpenCode Go); other providers ignore them.

## Notes

- Cost is estimated from the active provider's per-model price table (defaults to DeepSeek V4 Flash rates, $0.14/$0.28 per 1M tokens). A ~1M-character novel typically costs well under $1 on pay-per-token providers; on the OpenCode Go subscription it is flat-rate.
- **Fast mode (default):** thinking is disabled, so each request is a single fast pass with no reasoning tokens. Use **Settings → Change mode** to turn thinking on if you prefer maximum accuracy over speed.
- DeepSeek may charge 2x during peak hours (9:00–12:00 and 14:00–18:00 Beijing time).
- `epub-translator` requires Python 3.11–3.13; the launcher will warn you if your Python is incompatible.

## Batch translation (long books)

Set `chapter_limit` in `settings.json` (or via the web UI's settings API) to
stop the run cleanly after N spine items (~N-6 real chapters; the offset is
the cover/front/intro/volume pages). The run finalizes a readable partial
epub and keeps it (unlike `BudgetExceeded`, which deletes the output). Re-run
with a higher limit to continue — finished chapters are served from the
per-request cache and are not re-billed.

Post-processing of the partial epub (merge with the full source, optional
trim to English-only chapters) is documented in `tools/README.md`.
