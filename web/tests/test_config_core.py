"""Direct tests for the core config module (provider registry, key
precedence, budget naming, readiness validation). These cover the code
paths that route-level tests cannot reach. All writes go to a tmp
settings file — never the real one.
"""
import json

import pytest

import core_loader

config = core_loader.config


def _fresh_settings(tmp_path, monkeypatch, data=None):
    settings_file = tmp_path / "settings.json"
    if data is not None:
        settings_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config, "SETTINGS_FILE", settings_file)
    for k in list(__import__("os").environ):
        if k.startswith("EPUB_") or k.endswith("_API_KEY") or k.endswith("_MODEL") or k.endswith("_BASE_URL"):
            monkeypatch.delenv(k, raising=False)
    return settings_file


def test_provider_resolves_from_settings(tmp_path, monkeypatch):
    _fresh_settings(tmp_path, monkeypatch, {"provider": "opencode-go"})
    assert config.get_provider() == "opencode-go"
    assert config.get_provider_info()["label"] == "OpenCode Go"


def test_unknown_provider_falls_back_to_default(tmp_path, monkeypatch):
    _fresh_settings(tmp_path, monkeypatch, {"provider": "nope"})
    assert config.get_provider() == "deepseek"


def test_env_provider_beats_settings(tmp_path, monkeypatch):
    _fresh_settings(tmp_path, monkeypatch, {"provider": "deepseek"})
    monkeypatch.setenv("EPUB_PROVIDER", "openai")
    assert config.get_provider() == "openai"


def test_key_precedence_env_slot_legacy(tmp_path, monkeypatch):
    _fresh_settings(tmp_path, monkeypatch, {
        "provider": "deepseek",
        "provider_keys": {"deepseek": "sk-slot"},
        "api_key": "sk-legacy",
        "api_key_provider": "deepseek",
    })
    assert config.get_api_key() == "sk-slot"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    assert config.get_api_key() == "sk-env"


def test_legacy_fallback_only_for_owning_provider(tmp_path, monkeypatch):
    # Regression F4: a stale legacy key must NOT leak to another provider.
    _fresh_settings(tmp_path, monkeypatch, {
        "provider": "openai",
        "provider_keys": {},
        "api_key": "sk-legacy",
        "api_key_provider": "deepseek",
    })
    assert config.get_api_key() == ""


def test_legacy_fallback_when_untagged_defaults_to_deepseek(tmp_path, monkeypatch):
    # Historical files (pre-refactor) have no api_key_provider tag; the key
    # belonged to DeepSeek, the only provider back then.
    _fresh_settings(tmp_path, monkeypatch, {
        "provider": "deepseek", "provider_keys": {}, "api_key": "sk-old"})
    assert config.get_api_key() == "sk-old"


def test_set_api_key_writes_provider_slot_and_tag(tmp_path, monkeypatch):
    _fresh_settings(tmp_path, monkeypatch, {"provider": "deepseek"})
    config.set_api_key("sk-new", "opencode-go")
    data = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
    assert data["provider_keys"]["opencode-go"] == "sk-new"
    assert data["api_key"] == "sk-new"
    assert data["api_key_provider"] == "opencode-go"


def test_extra_body_gated_by_provider(tmp_path, monkeypatch):
    _fresh_settings(tmp_path, monkeypatch, {"provider": "deepseek", "thinking": "disabled"})
    assert config.get_extra_body() == {"thinking": {"type": "disabled"}}
    _fresh_settings(tmp_path, monkeypatch, {"provider": "openai"})
    assert config.get_extra_body() == {}


def test_token_budget_naming(tmp_path, monkeypatch):
    _fresh_settings(tmp_path, monkeypatch, {
        "token_budget": 1500000, "token_budget_test": 500000})
    assert config.get_token_budget("book.epub") == 1500000
    assert config.get_token_budget("Test_book.epub") == 500000
    # Regression F5: _test suffix must get the test budget too.
    assert config.get_token_budget("赤心巡天_test.epub") == 500000


def test_chapter_limit(tmp_path, monkeypatch):
    _fresh_settings(tmp_path, monkeypatch, {})
    assert config.get_chapter_limit() == 0  # default: unlimited
    _fresh_settings(tmp_path, monkeypatch, {"chapter_limit": 403})
    assert config.get_chapter_limit() == 403
    _fresh_settings(tmp_path, monkeypatch, {"chapter_limit": -5})
    assert config.get_chapter_limit() == 0
    _fresh_settings(tmp_path, monkeypatch, {"chapter_limit": "bogus"})
    assert config.get_chapter_limit() == 0


def test_pipeline_defaults_to_two_pass_and_normalizes_invalid_values(tmp_path, monkeypatch):
    _fresh_settings(tmp_path, monkeypatch, {})
    assert config.get_pipeline() == "two-pass"
    _fresh_settings(tmp_path, monkeypatch, {"pipeline": "one-pass"})
    assert config.get_pipeline() == "one-pass"
    _fresh_settings(tmp_path, monkeypatch, {"pipeline": "experimental"})
    assert config.get_pipeline() == "two-pass"


def test_strict_one_pass_requires_a_real_boolean(tmp_path, monkeypatch):
    _fresh_settings(tmp_path, monkeypatch, {"strict_one_pass": True})
    assert config.get_strict_one_pass() is True
    _fresh_settings(tmp_path, monkeypatch, {"strict_one_pass": "true"})
    assert config.get_strict_one_pass() is False


def test_opencode_go_flash_cost_accounts_for_cached_input(tmp_path, monkeypatch):
    _fresh_settings(tmp_path, monkeypatch, {
        "provider": "opencode-go", "model": "deepseek-v4-flash"})
    assert config.get_prices() == (0.14, 0.28)
    assert config.estimate_cost(1_000_000, 1_000_000,
                                cached_input_tokens=1_000_000) == pytest.approx(0.2828)


def test_concurrency_clamped(tmp_path, monkeypatch):
    _fresh_settings(tmp_path, monkeypatch, {"concurrency": 32})
    assert config.get_concurrency() == 32
    _fresh_settings(tmp_path, monkeypatch, {"concurrency": 999})
    assert config.get_concurrency() == 64
    _fresh_settings(tmp_path, monkeypatch, {"concurrency": 0})
    assert config.get_concurrency() == 1
    _fresh_settings(tmp_path, monkeypatch, {"concurrency": "bogus"})
    assert config.get_concurrency() == 8


def test_validate_ready_ok(tmp_path, monkeypatch):
    _fresh_settings(tmp_path, monkeypatch, {
        "provider": "deepseek",
        "provider_keys": {"deepseek": "sk-x"},
        "model": "deepseek-v4-flash",
    })
    assert config.validate_ready() == []


def test_validate_ready_custom_without_base_url(tmp_path, monkeypatch):
    # Regression F2: the custom-provider footgun must be caught up front.
    _fresh_settings(tmp_path, monkeypatch, {
        "provider": "custom",
        "provider_keys": {"custom": "sk-x"},
        "model": "some-model",
    })
    problems = config.validate_ready()
    assert any("base URL" in p for p in problems)


def test_validate_ready_missing_model(tmp_path, monkeypatch):
    _fresh_settings(tmp_path, monkeypatch, {
        "provider": "custom",
        "provider_keys": {"custom": "sk-x"},
        "base_url": "https://relay.example/v1",
        "model": "",
    })
    problems = config.validate_ready()
    assert any("model" in p for p in problems)
