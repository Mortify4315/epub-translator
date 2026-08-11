import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BOOKS_DIR = BASE_DIR / "books"
OUT_DIR = BASE_DIR / "out"
GLOSSARY_DIR = BASE_DIR / "glossaries"
CACHE_DIR = BASE_DIR / "cache"
SETTINGS_FILE = BASE_DIR / "settings.json"

TOKEN_ENCODING = "cl100k_base"
MAX_GROUP_TOKENS_DEFAULT = 5000
CONCURRENCY_DEFAULT = 8
CONCURRENCY_MAX = 64
FILL_THINKING_DEFAULT = "adaptive"
TOKEN_BUDGET_DEFAULT = 1_500_000
TOKEN_BUDGET_TEST_DEFAULT = 500_000
MAX_RETRIES_DEFAULT = 2
RETRY_TIMES_DEFAULT = 2

DEFAULT_PROVIDER = "deepseek"

# Provider registry: OpenAI-compatible endpoints (the app speaks the OpenAI
# chat-completions wire format via the `openai` SDK). Each entry:
#   label      – display name
#   base_url   – default API endpoint (settings "base_url" or env overrides it)
#   env_key    – env var holding the API key for this provider
#   env_base   – env var overriding the endpoint
#   env_model  – env var overriding the model
#   thinking   – True if the provider honors DeepSeek-style {"thinking": {...}}
#                extra-body params (verified live on deepseek + opencode-go)
#   models     – suggested model names (UI datalist; any name is accepted)
#   prices     – {model: (input_per_m, output_per_m)} for cost estimates
#   default_price – fallback when a model isn't in `prices`
PROVIDER_PRESETS = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "env_key": "DEEPSEEK_API_KEY",
        "env_base": "DEEPSEEK_BASE_URL",
        "env_model": "DEEPSEEK_MODEL",
        "thinking": True,
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "prices": {
            "deepseek-v4-flash": (0.14, 0.28),
            "deepseek-v4-pro": (0.435, 0.87),
        },
        "default_price": (0.14, 0.28),
    },
    "opencode-go": {
        "label": "OpenCode Go",
        "base_url": "https://opencode.ai/zen/go/v1",
        "env_key": "OPENCODE_GO_API_KEY",
        "env_base": "OPENCODE_GO_BASE_URL",
        "env_model": "OPENCODE_GO_MODEL",
        "thinking": True,
        "models": [
            "deepseek-v4-flash", "deepseek-v4-pro", "mimo-v2.5", "hy3",
            "gpt-5.6-luna", "minimax-m3", "qwen3.5-plus", "qwen3.7-plus",
            "qwen3.6-plus", "kimi-k2.7-code", "kimi-k2.6", "kimi-k3",
            "glm-5.1", "glm-5.2", "qwen3.7-max", "qwen3.8-max", "mimo-v2.5-pro",
        ],
        "prices": {
            "deepseek-v4-flash": (0.14, 0.28),
            "deepseek-v4-pro": (0.435, 0.87),
            "mimo-v2.5": (0.14, 0.28),
            "mimo-v2.5-pro": (0.435, 0.87),
            "hy3": (0.14, 0.58),
            "gpt-5.6-luna": (0.20, 1.20),
            "minimax-m3": (0.30, 1.20),
            "minimax-m2.7": (0.30, 1.20),
            "qwen3.5-plus": (0.20, 1.20),
            "qwen3.6-plus": (0.50, 3.00),
            "qwen3.7-plus": (0.40, 1.60),
            "qwen3.7-max": (2.50, 7.50),
            "qwen3.8-max": (2.00, 6.00),
            "kimi-k2.6": (0.95, 4.00),
            "kimi-k2.7-code": (0.95, 4.00),
            "kimi-k3": (3.00, 15.00),
            "glm-5.1": (1.40, 4.40),
            "glm-5.2": (1.40, 4.40),
        },
        "default_price": (0.14, 0.28),
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
        "env_base": "OPENAI_BASE_URL",
        "env_model": "OPENAI_MODEL",
        "thinking": False,
        "models": [
            "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol",
            "gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano",
            "gpt-oss-120b", "gpt-4.1", "gpt-4.1-mini", "gpt-4o-mini",
        ],
        "prices": {
            "gpt-5.6-luna": (0.20, 1.20),
            "gpt-5.6-terra": (2.00, 12.00),
            "gpt-5.6-sol": (5.00, 30.00),
            "gpt-5.5": (5.00, 30.00),
            "gpt-5.4": (2.50, 15.00),
            "gpt-5.4-mini": (0.75, 4.50),
            "gpt-5.4-nano": (0.20, 1.25),
            "gpt-oss-120b": (0.15, 0.60),
            "gpt-4.1": (2.00, 8.00),
            "gpt-4.1-mini": (0.40, 1.60),
            "gpt-4o-mini": (0.15, 0.60),
        },
        "default_price": (2.50, 15.00),
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "base_url": "https://api.anthropic.com/v1",
        "env_key": "ANTHROPIC_API_KEY",
        "env_base": "ANTHROPIC_BASE_URL",
        "env_model": "ANTHROPIC_MODEL",
        "thinking": False,
        "models": [
            "claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5",
            "claude-sonnet-4-6", "claude-opus-4-8",
        ],
        "prices": {
            "claude-haiku-4-5": (1.00, 5.00),
            "claude-sonnet-5": (3.00, 15.00),
            "claude-opus-5": (5.00, 25.00),
            "claude-sonnet-4-6": (3.00, 15.00),
            "claude-opus-4-8": (5.00, 25.00),
        },
        "default_price": (3.00, 15.00),
    },
    "gemini": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "env_key": "GEMINI_API_KEY",
        "env_base": "GEMINI_BASE_URL",
        "env_model": "GEMINI_MODEL",
        "thinking": False,
        "models": [
            "gemini-3.6-flash", "gemini-3.1-pro", "gemini-2.5-flash",
            "gemini-2.5-pro", "gemini-3.5-flash-lite",
        ],
        "prices": {
            "gemini-3.6-flash": (0.25, 1.50),
            "gemini-3.5-flash-lite": (0.15, 0.60),
            "gemini-3.1-pro": (2.00, 12.00),
            "gemini-2.5-flash": (0.25, 1.50),
            "gemini-2.5-pro": (1.25, 10.00),
        },
        "default_price": (0.25, 1.50),
    },
    "xai": {
        "label": "xAI (Grok)",
        "base_url": "https://api.x.ai/v1",
        "env_key": "XAI_API_KEY",
        "env_base": "XAI_BASE_URL",
        "env_model": "XAI_MODEL",
        "thinking": False,
        "models": ["grok-4.5", "grok-4", "grok-3"],
        "prices": {
            "grok-4.5": (2.00, 6.00),
            "grok-4": (2.00, 6.00),
            "grok-3": (2.00, 6.00),
        },
        "default_price": (2.00, 6.00),
    },
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "env_key": "GROQ_API_KEY",
        "env_base": "GROQ_BASE_URL",
        "env_model": "GROQ_MODEL",
        "thinking": False,
        "models": ["llama-4-scout", "llama-4-maverick", "llama-3.3-70b-versatile"],
        "prices": {},
        "default_price": (0.20, 0.80),
    },
    "mistral": {
        "label": "Mistral",
        "base_url": "https://api.mistral.ai/v1",
        "env_key": "MISTRAL_API_KEY",
        "env_base": "MISTRAL_BASE_URL",
        "env_model": "MISTRAL_MODEL",
        "thinking": False,
        "models": ["mistral-large-latest", "mistral-small-latest", "codestral-latest"],
        "prices": {
            "mistral-large-latest": (2.00, 6.00),
            "mistral-small-latest": (0.20, 0.60),
        },
        "default_price": (2.00, 6.00),
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
        "env_base": "OPENROUTER_BASE_URL",
        "env_model": "OPENROUTER_MODEL",
        "thinking": False,
        "models": ["openrouter/auto", "deepseek/deepseek-v4-flash",
                   "openai/gpt-5.6-luna", "anthropic/claude-sonnet-5"],
        "prices": {},
        "default_price": (0.20, 1.20),
    },
    "custom": {
        "label": "Custom (any OpenAI-compatible API)",
        "base_url": "",
        "env_key": "CUSTOM_LLM_API_KEY",
        "env_base": "CUSTOM_LLM_BASE_URL",
        "env_model": "CUSTOM_LLM_MODEL",
        "thinking": False,
        "models": [],
        "prices": {},
        "default_price": (0.14, 0.28),
    },
}

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
    """Atomic write: temp file + rename so a crash mid-write can't corrupt
    the live settings (which hold API keys)."""
    tmp = SETTINGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp, SETTINGS_FILE)


def get_provider() -> str:
    name = (os.environ.get("EPUB_PROVIDER", "").strip()
            or str(load_settings().get("provider", "")).strip()
            or DEFAULT_PROVIDER)
    return name if name in PROVIDER_PRESETS else DEFAULT_PROVIDER


def get_provider_info(name: str | None = None) -> dict:
    return PROVIDER_PRESETS.get(name or get_provider(), PROVIDER_PRESETS[DEFAULT_PROVIDER])


def get_api_key() -> str:
    info = get_provider_info()
    key = os.environ.get(info["env_key"], "").strip()
    if not key:
        key = str(load_settings().get("provider_keys", {}).get(get_provider(), "")).strip()
    if not key:
        # Legacy single-slot fallback — only when it belongs to the active
        # provider (tag written by set_api_key; historical files default to
        # deepseek, the only provider before the multi-provider refactor).
        settings = load_settings()
        owner = str(settings.get("api_key_provider", "deepseek")).strip()
        if owner == get_provider():
            key = str(settings.get("api_key", "")).strip()
    return key


def set_api_key(key: str, provider: str | None = None) -> None:
    provider = provider or get_provider()
    settings = load_settings()
    settings.setdefault("provider_keys", {})[provider] = key.strip()
    settings["api_key"] = key.strip()  # legacy single-slot fallback
    settings["api_key_provider"] = provider  # tag which provider owns it
    save_settings(settings)


def validate_ready() -> list[str]:
    """Fail-fast checks before any API call. Returns a list of problems
    (empty = ready to run). Guards the custom-provider footgun: an empty
    base URL or model would otherwise surface as an opaque httpx error."""
    problems = []
    if not get_api_key():
        info = get_provider_info()
        problems.append(
            f"No API key configured for provider '{get_provider()}'. "
            f"Set it in Settings or the {info['env_key']} environment variable."
        )
    if not get_base_url():
        problems.append(
            f"Provider '{get_provider()}' has no API base URL. "
            "Set one in Settings → Change base URL (or the env var "
            f"{get_provider_info()['env_base']})."
        )
    if not get_model():
        problems.append(
            f"Provider '{get_provider()}' has no model configured. "
            "Set one in Settings → Change model (or the env var "
            f"{get_provider_info()['env_model']})."
        )
    return problems


def get_model() -> str:
    info = get_provider_info()
    return (os.environ.get(info["env_model"], "").strip()
            or str(load_settings().get("model", "")).strip()
            or (info["models"][0] if info["models"] else ""))


def get_base_url() -> str:
    info = get_provider_info()
    return (os.environ.get(info["env_base"], "").strip()
            or str(load_settings().get("base_url", "")).strip()
            or info["base_url"])


def get_concurrency() -> int:
    try:
        value = int(load_settings().get("concurrency", CONCURRENCY_DEFAULT))
    except (TypeError, ValueError):
        value = CONCURRENCY_DEFAULT
    return max(1, min(CONCURRENCY_MAX, value))


def get_max_group_tokens() -> int:
    try:
        return int(load_settings().get("max_group_tokens", MAX_GROUP_TOKENS_DEFAULT))
    except (TypeError, ValueError):
        return MAX_GROUP_TOKENS_DEFAULT


def get_thinking() -> str:
    thinking = (os.environ.get("EPUB_THINKING", "").strip()
                or str(load_settings().get("thinking", "disabled")).strip().lower())
    return thinking if thinking in ("adaptive", "enabled", "disabled") else "disabled"


def get_fill_thinking() -> str:
    thinking = (os.environ.get("EPUB_FILL_THINKING", "").strip()
                or str(load_settings().get("fill_thinking", "")).strip().lower())
    return thinking if thinking in ("adaptive", "enabled", "disabled") else FILL_THINKING_DEFAULT


def _thinking_extra_body(thinking: str) -> dict:
    if thinking == "adaptive":
        return {"thinking": {"type": "adaptive"}}
    return {"thinking": {"type": "enabled" if thinking == "enabled" else "disabled"}}


def get_extra_body() -> dict:
    """Extra body for the translation LLM. DeepSeek-family providers take a
    `thinking` param; everyone else gets nothing extra (OpenAI/Anthropic/Gemini
    reject unknown request fields)."""
    if get_provider_info()["thinking"]:
        return _thinking_extra_body(get_thinking())
    return {}


def get_fill_extra_body() -> dict:
    if get_provider_info()["thinking"]:
        return _thinking_extra_body(get_fill_thinking())
    return {}


def get_token_budget(source_name: str) -> int:
    stem = source_name.rsplit(".", 1)[0]
    is_test = stem.startswith("Test_") or stem.lower().endswith("_test")
    key = "token_budget_test" if is_test else "token_budget"
    default = TOKEN_BUDGET_TEST_DEFAULT if is_test else TOKEN_BUDGET_DEFAULT
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


def get_prices() -> tuple[float, float]:
    info = get_provider_info()
    model = get_model()
    return info["prices"].get(model, info["default_price"])


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    input_per_m, output_per_m = get_prices()
    return (input_tokens / 1_000_000 * input_per_m
            + output_tokens / 1_000_000 * output_per_m)
