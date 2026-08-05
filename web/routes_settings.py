import json

from flask import Blueprint, abort, jsonify, request

import core_loader as core

bp = Blueprint("settings", __name__)


def settings_payload():
    key = core.config.get_api_key()
    if not key:
        key = str(core.config.load_settings().get("api_key", "")).strip()
    masked = (key[:6] + "…" + key[-4:]) if len(key) > 12 else "(not set)"
    return {
        "api_key_set": bool(key),
        "api_key_masked": masked,
        "model": core.config.get_model(),
        "base_url": core.config.get_base_url(),
        "concurrency": core.config.get_concurrency(),
        "max_group_tokens": core.config.get_max_group_tokens(),
        "thinking": core.config.get_extra_body()["thinking"]["type"],
        "fill_thinking": core.config.get_fill_thinking(),
    }


@bp.get("/api/settings")
def get_settings():
    return jsonify(settings_payload())


@bp.post("/api/settings")
def update_settings():
    data = request.get_json(force=True)
    if "api_key" in data and str(data["api_key"]).strip():
        core.config.set_api_key(str(data["api_key"]).strip())
    s = core.config.load_settings()
    if data.get("model") in ("deepseek-v4-flash", "deepseek-v4-pro"):
        s["model"] = data["model"]
    if "concurrency" in data:
        try:
            s["concurrency"] = max(1, min(16, int(data["concurrency"])))
        except (TypeError, ValueError):
            abort(400, "concurrency must be an integer 1-16.")
    if "max_group_tokens" in data:
        try:
            s["max_group_tokens"] = int(data["max_group_tokens"])
        except (TypeError, ValueError):
            abort(400, "max_group_tokens must be an integer.")
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
