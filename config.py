import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BOOKS_DIR = BASE_DIR / "books"
OUT_DIR = BASE_DIR / "out"
GLOSSARY_DIR = BASE_DIR / "glossaries"
CACHE_DIR = BASE_DIR / "cache"
SETTINGS_FILE = BASE_DIR / "settings.json"

GO_BASE_URL_DEFAULT = "https://opencode.ai/zen/go/v1"
GO_MODEL_DEFAULT = "deepseek-v4-flash"
TOKEN_ENCODING = "cl100k_base"
INPUT_PRICE_PER_M = 0.14
OUTPUT_PRICE_PER_M = 0.28

for _dir in (BOOKS_DIR, OUT_DIR, GLOSSARY_DIR, CACHE_DIR):
    _dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_settings(settings: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def get_api_key() -> str:
    key = os.environ.get("OPENCODE_GO_API_KEY", "").strip()
    if not key:
        key = str(load_settings().get("api_key", "")).strip()
    return key


def set_api_key(key: str) -> None:
    settings = load_settings()
    settings["api_key"] = key.strip()
    save_settings(settings)


def get_model() -> str:
    return (os.environ.get("OPENCODE_GO_MODEL", "").strip()
            or str(load_settings().get("model", "")).strip()
            or GO_MODEL_DEFAULT)


def get_base_url() -> str:
    return os.environ.get("OPENCODE_GO_BASE_URL", "").strip() or GO_BASE_URL_DEFAULT


def get_concurrency() -> int:
    try:
        return int(load_settings().get("concurrency", 4))
    except (TypeError, ValueError):
        return 4


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens / 1_000_000 * INPUT_PRICE_PER_M
            + output_tokens / 1_000_000 * OUTPUT_PRICE_PER_M)
