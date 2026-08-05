import os
import sys
from pathlib import Path

CORE_DIR = Path(
    os.environ.get("EPUB_TRANSLATOR_PATH", str(Path(__file__).resolve().parent.parent / "epub-translator"))
).resolve()

if not (CORE_DIR / "translate_book.py").exists():
    raise SystemExit(
        f"epub-translator core not found at {CORE_DIR}. "
        "Clone it there or set the EPUB_TRANSLATOR_PATH environment variable."
    )

sys.path.insert(0, str(CORE_DIR))

import config  # noqa: E402
import glossary  # noqa: E402
import qa_check  # noqa: E402
import scan_glossary  # noqa: E402
import translate_book  # noqa: E402
