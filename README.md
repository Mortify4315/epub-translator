<div align="center">

# Jade Scroll Press

Translate Chinese web-novel EPUBs into readable English books with a resumable pipeline, terminology control, and a local-first workflow.

[![GitHub repository](https://img.shields.io/badge/GitHub-Mortify4315%2Fepub--translator-181717?style=flat-square&logo=github)](https://github.com/Mortify4315/epub-translator)
[![Python](https://img.shields.io/badge/Python-3.11--3.13-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-190%20passed-2ea44f?style=flat-square)](#testing)

**Prepare terminology → Translate with recovery → Verify consistency**

</div>

Jade Scroll Press is a Windows-friendly EPUB translation desk for long Chinese web novels. It keeps the browser UI and terminal UI on the same engine and the same local books, cache, glossaries, settings, and output folders.

> [!IMPORTANT]
> Translation calls may incur provider costs. Review the estimate, provider, model, limits, and glossary before starting a long run.

## What it does

- Translates EPUB chapter content while preserving the book structure, cover, CSS, and table of contents.
- Resumes interrupted work from a per-book cache instead of starting from zero.
- Estimates chapters, tokens, and cost before a run.
- Uses shared and per-book glossaries for names, skills, places, and recurring terms.
- Scans source text for new terminology and lets you approve proposed translations.
- Runs an offline quality check for inconsistent terminology in finished books.
- Supports one-pass and legacy two-pass pipelines, concurrency, chapter limits, group-size limits, retries, and thinking modes.
- Routes through any OpenAI-compatible API, including the local 9Router preset.

## Choose an interface

| Interface | Best for | Start here |
| --- | --- | --- |
| WebUI | Visual workflow, live jobs, mobile use, glossary editing | `web\run.bat` |
| TUI | Keyboard-first operation, low overhead, scripts and terminals | `cli\run.bat` |

Both interfaces share the same data and translation engine. You can prepare a glossary in the WebUI and run the translation from the TUI, or the other way around.

## Quick start on Windows

### 1. Install Python

Install [Python 3.13](https://www.python.org/downloads/) (Python 3.11–3.13 are supported) and enable **Add python.exe to PATH**.

### 2. Add a source book

Put an `.epub` file in `cli\books\`.

### 3. Start Jade Scroll Press

For the browser interface:

```powershell
cd web
.\run.bat
```

Open <http://127.0.0.1:8177> if it does not open automatically.

For the terminal interface:

```powershell
cd cli
.\run.bat
```

The launchers create a local virtual environment and install the frontend's dependencies on first run.

### 4. Configure a provider

Open **Settings** in the WebUI or TUI and select a provider. API keys are stored locally in `cli\settings.json`; that file is ignored by Git and should never be committed.

> [!TIP]
> If you already run 9Router locally, select **9Router (local)**. No cloud API key is required by the preset unless your router is configured to require one.

## Local 9Router

The built-in preset routes OpenAI-compatible requests to:

```text
http://localhost:20128/v1
```

Preset model choices:

- `fusion-panel`
- `qd/auto`
- `qd/ultimate`

The preset can be overridden with environment variables:

```powershell
$env:NINEROUTER_BASE_URL = "http://localhost:20128/v1"
$env:NINEROUTER_MODEL = "fusion-panel"
# Optional when the local router does not require authentication:
$env:NINEROUTER_API_KEY = ""
```

Jade Scroll Press uses the OpenAI chat-completions wire format. That means a compatible gateway can be used through a provider preset or the custom endpoint option.

## Recommended workflow

1. **Prepare** — choose a source EPUB, inspect the estimate, and scan or curate glossary terms.
2. **Translate** — choose the provider/model, review concurrency and limits, then start the run. Stop and resume safely when needed.
3. **Verify** — download the finished EPUB and run the offline quality check for terminology drift.

Editing a glossary or changing translation-identity settings clears the affected book's cache because the previous cache may no longer match the requested output.

## Data layout

The `cli` folder is the shared core and single source of truth:

| Path | Purpose |
| --- | --- |
| `cli\books\` | Source EPUBs |
| `cli\out\` | Finished and partial translated EPUBs |
| `cli\cache\` | Resumable per-book translation cache |
| `cli\glossaries\` | Shared and per-book glossary JSON files |
| `cli\settings.json` | Local provider and pipeline settings; ignored by Git |
| `web\static\workspace.html` | Current WebUI shell |
| `web\static\pressroom.css` | WebUI design system and responsive styles |
| `web\static\pressroom.js` | WebUI behavior and API wiring |

If the core project is moved, set `EPUB_TRANSLATOR_PATH` to the directory containing the `cli` folder before starting the WebUI.

## Providers and environment variables

`EPUB_PROVIDER` selects the active provider. Supported values include:

```text
deepseek, opencode-go, 9router, openai, anthropic, gemini,
xai, groq, mistral, openrouter, custom
```

Provider-specific settings follow the same pattern:

| Setting | Example |
| --- | --- |
| Provider | `EPUB_PROVIDER=9router` |
| API key | `NINEROUTER_API_KEY=...` |
| Model | `NINEROUTER_MODEL=fusion-panel` |
| Base URL | `NINEROUTER_BASE_URL=http://localhost:20128/v1` |

Additional pipeline overrides:

- `EPUB_THINKING` — translation thinking mode.
- `EPUB_FILL_THINKING` — structure/fill thinking mode.

Provider, model, base URL, and thinking-mode changes can invalidate a book's cache; the app communicates that before applying the change.

## Long-book controls

For large books, use the WebUI Settings panel or edit local settings through the TUI:

- `chapter_limit`: stop cleanly after a chosen number of spine items (`0` means unlimited).
- `max_group_tokens`: cap the amount of source text grouped into one request.
- `concurrency`: control parallel requests and provider pressure.
- `pipeline`: choose `one-pass` or legacy `two-pass`.
- `strict_one_pass`: keep one-pass behavior explicit when the compatibility path matters.

A chapter-limited run keeps its readable partial EPUB. Increase the limit and run again to continue from cache.

## Development

The WebUI owns the test suite and loads the shared CLI core at runtime.

```powershell
cd web
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe app.py
```

The local server binds to `127.0.0.1` and is intended for a single user on the local machine.

Useful areas of the codebase:

| Area | Responsibility |
| --- | --- |
| `cli\config.py` | Providers, settings, paths, pricing, and pipeline defaults |
| `cli\translate_book.py` | Translation orchestration and EPUB output |
| `cli\onepass.py` | One-pass translation engine |
| `cli\glossary.py` | Shared and per-book terminology |
| `cli\scan_glossary.py` | Terminology discovery |
| `cli\qa_check.py` | Offline consistency checks |
| `web\routes_*.py` | Browser API routes |
| `web\job_runner.py` | Background translation and scan jobs |
| `web\tests\` | Automated regression and route tests |

## Testing

Run the complete suite from `web\`:

```powershell
.venv\Scripts\python.exe -m pytest
```

The current suite covers translation engines, cache/resume behavior, jobs, provider settings, glossary import/export, book routes, and smoke checks.

## Troubleshooting

### The launcher says Python is missing

Install Python 3.11, 3.12, or 3.13, make sure the Python launcher is available, and run the launcher again.

### The WebUI cannot find my books

Confirm the EPUB is in `cli\books\`. If the core folder moved, set `EPUB_TRANSLATOR_PATH` to the project directory that contains `cli\`.

### A run stopped or the process closed

Start the same book again. Jade Scroll Press reads the existing cache and resumes completed work. Check `cli\cache\` and `cli\out\` before deleting anything.

### 9Router is unavailable

Confirm the router is running, then open `http://localhost:20128/v1/models` in a browser or use the provider readiness status in Settings. Verify the configured base URL includes `/v1`.

### Output terminology is inconsistent

Add the preferred terms to the shared or per-book glossary, then rerun the affected chapters or translation. Use **Quality Check** to locate remaining inconsistencies.

## Project notes

- The app is designed for local, single-user operation.
- API keys and local book data are intentionally excluded from version control.
- The WebUI visual language is documented in [`DESIGN.md`](DESIGN.md), and the product constraints are captured in [`PRODUCT.md`](PRODUCT.md).
