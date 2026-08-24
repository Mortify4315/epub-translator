import json
import os

from flask import Blueprint, abort, jsonify, request

import core_loader as core

bp = Blueprint("settings", __name__)


def settings_payload():
    key = core.config.get_api_key()
    if not key:
        key = str(core.config.load_settings().get("api_key", "")).strip()
    masked = (key[:6] + "…" + key[-4:]) if len(key) > 12 else "(not set)"
    provider = core.config.get_provider()
    info = core.config.get_provider_info(provider)
    optional_key = bool(info.get("api_key_optional"))
    configured_key = bool(key and key != "local-router")
    return {
        "provider": provider,
        "providers": [
            {
                "name": name,
                "label": info2["label"],
                "base_url": info2["base_url"],
                "models": info2["models"],
                "api_key_optional": bool(info2.get("api_key_optional")),
            }
            for name, info2 in core.config.PROVIDER_PRESETS.items()
        ],
        "provider_label": info["label"],
        "models": info["models"],
        "thinking_supported": info["thinking"],
        "env_key": info["env_key"],
        "api_key_set": bool(key) or optional_key,
        "api_key_optional": optional_key,
        "api_key_configured": configured_key,
        "api_key_masked": masked if configured_key else "(not required)" if optional_key else "(not set)",
        "model": core.config.get_model(),
        "base_url": core.config.get_base_url(),
        "concurrency": core.config.get_concurrency(),
        "max_group_tokens": core.config.get_max_group_tokens(),
        "chapter_limit": core.config.get_chapter_limit(),
        "thinking": core.config.get_thinking(),
        "fill_thinking": core.config.get_fill_thinking(),
        "pipeline": core.config.get_pipeline(),
        "strict_one_pass": core.config.get_strict_one_pass(),
    }


@bp.get("/api/settings")
def get_settings():
    return jsonify(settings_payload())


@bp.post("/api/settings")
def update_settings():
    data = request.get_json(force=True)
    s = core.config.load_settings()

    if "provider" in data:
        provider = str(data["provider"]).strip()
        if provider not in core.config.PROVIDER_PRESETS:
            abort(400, f"Unknown provider '{provider}'.")
        previous_provider = str(s.get("provider") or core.config.DEFAULT_PROVIDER).strip()
        if provider == "custom":
            # The custom provider has no default base URL — refusing to save a
            # state that would only fail at request time with an opaque error.
            # (An env-var override is also a valid source.)
            base = (str(data.get("base_url") or "").strip()
                    or (str(s.get("base_url") or "").strip()
                        if previous_provider == "custom" else "")
                    or os.environ.get("CUSTOM_LLM_BASE_URL", "").strip())
            if not base:
                abort(400, "provider 'custom' requires a base_url (any OpenAI-compatible endpoint).")
        s["provider"] = provider
        if provider != previous_provider:
            preset = core.config.get_provider_info(provider)
            if "base_url" not in data:
                s["base_url"] = preset["base_url"]
            if "model" not in data:
                s["model"] = preset["models"][0] if preset["models"] else ""

    if "api_key" in data and str(data["api_key"]).strip():
        # Write to the slot for the provider being saved (or the active one).
        provider = str(data.get("provider") or s.get("provider") or "").strip()
        core.config.set_api_key(str(data["api_key"]).strip(),
                                provider if provider in core.config.PROVIDER_PRESETS else None)
        s = core.config.load_settings()

    if "model" in data:
        model = str(data["model"]).strip()
        if not model:
            abort(400, "model must not be empty.")
        s["model"] = model

    if "base_url" in data:
        base_url = str(data["base_url"]).strip()
        if base_url and not base_url.startswith(("http://", "https://")):
            abort(400, "base_url must start with http:// or https://.")
        s["base_url"] = base_url

    if "concurrency" in data:
        try:
            s["concurrency"] = max(1, min(core.config.CONCURRENCY_MAX, int(data["concurrency"])))
        except (TypeError, ValueError):
            abort(400, f"concurrency must be an integer 1-{core.config.CONCURRENCY_MAX}.")
    if "max_group_tokens" in data:
        try:
            s["max_group_tokens"] = int(data["max_group_tokens"])
        except (TypeError, ValueError):
            abort(400, "max_group_tokens must be an integer.")
    if "chapter_limit" in data:
        try:
            s["chapter_limit"] = max(0, int(data["chapter_limit"]))
        except (TypeError, ValueError):
            abort(400, "chapter_limit must be a non-negative integer (0 = unlimited).")
    if "pipeline" in data:
        pipeline = str(data["pipeline"]).strip().lower()
        if pipeline not in core.config.PIPELINES:
            abort(400, "pipeline must be one of: two-pass, one-pass.")
        s["pipeline"] = pipeline
    if "strict_one_pass" in data:
        if type(data["strict_one_pass"]) is not bool:
            abort(400, "strict_one_pass must be a boolean.")
        s["strict_one_pass"] = data["strict_one_pass"]
    if data.get("thinking") in ("enabled", "disabled"):
        s["thinking"] = data["thinking"]
    if data.get("fill_thinking") in ("adaptive", "enabled", "disabled"):
        s["fill_thinking"] = data["fill_thinking"]
    core.config.save_settings(s)
    return jsonify(settings_payload())


@bp.post("/api/qa")
def run_qa():
    data = request.get_json(force=True)
    source = core.config.BOOKS_DIR / (data.get("source") or "")
    target = core.config.OUT_DIR / (data.get("target") or "")
    if not source.is_file() or not target.is_file():
        abort(404, "Source or target book not found.")
    issues = core.qa_check.check(source, target)
    return jsonify({"count": len(issues), "issues": issues})


@bp.post("/api/scan/accept")
def accept_scan():
    data = request.get_json(force=True)
    scope = data.get("scope")
    terms = data.get("terms")
    if not scope or not isinstance(terms, dict) or not terms:
        abort(400, "scope and terms are required.")
    added = core.glossary.add_terms(scope, {str(s): str(d) for s, d in terms.items()})
    return jsonify({"added": added})
