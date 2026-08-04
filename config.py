import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BOOKS_DIR = BASE_DIR / "books"
OUT_DIR = BASE_DIR / "out"
GLOSSARY_DIR = BASE_DIR / "glossaries"
CACHE_DIR = BASE_DIR / "cache"
SETTINGS_FILE = BASE_DIR / "settings.json"

DEEPSEEK_BASE_URL_DEFAULT = "https://api.deepseek.com"
DEEPSEEK_MODEL_DEFAULT = "deepseek-v4-flash"
TOKEN_ENCODING = "cl100k_base"
INPUT_PRICE_PER_M = 0.14
OUTPUT_PRICE_PER_M = 0.28
MAX_GROUP_TOKENS_DEFAULT = 5000
CONCURRENCY_DEFAULT = 8
FILL_THINKING_DEFAULT = "adaptive"
TOKEN_BUDGET_DEFAULT = 1_500_000
TOKEN_BUDGET_TEST_DEFAULT = 300_000
MAX_RETRIES_DEFAULT = 2
RETRY_TIMES_DEFAULT = 2

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
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        key = str(load_settings().get("api_key", "")).strip()
    return key


def set_api_key(key: str) -> None:
    settings = load_settings()
    settings["api_key"] = key.strip()
    save_settings(settings)


def get_model() -> str:
    return (os.environ.get("DEEPSEEK_MODEL", "").strip()
            or str(load_settings().get("model", "")).strip()
            or DEEPSEEK_MODEL_DEFAULT)


def get_base_url() -> str:
    return (os.environ.get("DEEPSEEK_BASE_URL", "").strip()
            or DEEPSEEK_BASE_URL_DEFAULT)


def get_concurrency() -> int:
    try:
        return int(load_settings().get("concurrency", CONCURRENCY_DEFAULT))
    except (TypeError, ValueError):
        return CONCURRENCY_DEFAULT


def get_max_group_tokens() -> int:
    try:
        return int(load_settings().get("max_group_tokens", MAX_GROUP_TOKENS_DEFAULT))
    except (TypeError, ValueError):
        return MAX_GROUP_TOKENS_DEFAULT


def get_extra_body() -> dict:
    thinking = str(load_settings().get("thinking", "disabled")).strip().lower()
    return _thinking_extra_body(thinking)


def get_fill_thinking() -> str:
    thinking = (os.environ.get("DEEPSEEK_FILL_THINKING", "").strip()
                or str(load_settings().get("fill_thinking", "")).strip().lower())
    return thinking if thinking in ("adaptive", "enabled", "disabled") else FILL_THINKING_DEFAULT


def get_fill_extra_body() -> dict:
    return _thinking_extra_body(get_fill_thinking())


def _thinking_extra_body(thinking: str) -> dict:
    if thinking == "adaptive":
        return {"thinking": {"type": "adaptive"}}
    return {"thinking": {"type": "enabled" if thinking == "enabled" else "disabled"}}


def get_token_budget(source_name: str) -> int:
    key = "token_budget_test" if source_name.startswith("Test_") else "token_budget"
    default = TOKEN_BUDGET_TEST_DEFAULT if source_name.startswith("Test_") else TOKEN_BUDGET_DEFAULT
    try:
        return int(load_settings().get(key, default))
    except (TypeError, ValueError):
        return default


def get_max_retries() -> int:
    try:
        return int(load_settings().get("max_retries", MAX_RETRIES_DEFAULT))
    except (TypeError, ValueError):
        return MAX_RETRIES_DEFAULT


def get_retry_times() -> int:
    try:
        return int(load_settings().get("retry_times", RETRY_TIMES_DEFAULT))
    except (TypeError, ValueError):
        return RETRY_TIMES_DEFAULT


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens / 1_000_000 * INPUT_PRICE_PER_M
            + output_tokens / 1_000_000 * OUTPUT_PRICE_PER_M)
