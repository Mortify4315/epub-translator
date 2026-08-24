import json

from flask import Blueprint, Response, abort, jsonify, request

import core_loader as core

bp = Blueprint("glossary", __name__)


def _known_scope(scope: str) -> bool:
    if scope == core.glossary.GLOBAL_NAME:
        return True
    return any(core.glossary.book_key(book.name) == scope
               for book in core.config.BOOKS_DIR.glob("*.epub"))


@bp.get("/api/glossary")
def list_glossaries():
    scopes = [{
        "key": core.glossary.GLOBAL_NAME,
        "label": "Shared (all books)",
        "terms": core.glossary.load_glossary(core.glossary.GLOBAL_NAME),
    }]
    for book in sorted(core.config.BOOKS_DIR.glob("*.epub")):
        key = core.glossary.book_key(book.name)
        scopes.append({"key": key, "label": book.name,
                       "terms": core.glossary.load_glossary(key)})
    return jsonify(scopes)


@bp.get("/api/glossary/<scope>/export")
def export_glossary(scope):
    if not _known_scope(scope):
        abort(404, "Glossary not found.")
    payload = json.dumps(
        core.glossary.load_glossary(scope), ensure_ascii=False, indent=2
    ) + "\n"
    safe_name = "".join(ch for ch in scope if ch.isalnum() or ch in "-_") or "glossary"
    return Response(
        payload,
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}-glossary.json"'},
    )


@bp.post("/api/glossary/<scope>/import")
def import_glossary(scope):
    if not _known_scope(scope):
        abort(404, "Glossary not found.")
    upload = request.files.get("file")
    if not upload or not upload.filename:
        abort(400, "Choose a JSON glossary file.")
    raw = upload.read(2_000_001)
    if len(raw) > 2_000_000:
        abort(413, "Glossary file must be 2 MB or smaller.")
    try:
        parsed = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        abort(400, "Glossary must be a UTF-8 JSON object.")
    if not isinstance(parsed, dict):
        abort(400, "Glossary JSON must map source terms to translations.")
    normalized = {
        str(source).strip(): str(target).strip()
        for source, target in parsed.items()
        if str(source).strip() and str(target).strip()
    }
    if len(normalized) > 10_000:
        abort(413, "Glossary may contain at most 10,000 terms.")
    added = core.glossary.add_terms(scope, normalized)
    return jsonify({"added": added, "skipped": len(normalized) - added})


@bp.post("/api/glossary/<scope>/term")
def add_term(scope):
    data = request.get_json(force=True)
    src = str(data.get("src", "")).strip()
    dst = str(data.get("dst", "")).strip()
    if not src or not dst:
        abort(400, "src and dst are required.")
    added = core.glossary.add_terms(scope, {src: dst})
    return jsonify({"added": added})


@bp.put("/api/glossary/<scope>/term/<old_src>")
def edit_term(scope, old_src):
    data = request.get_json(force=True)
    src = str(data.get("src", "")).strip()
    dst = str(data.get("dst", "")).strip()
    if not old_src or not src or not dst:
        abort(400, "old_src, src, dst are required.")
    terms = core.glossary.load_glossary(scope)
    if old_src not in terms:
        abort(404, "Term not found.")
    del terms[old_src]
    terms[src] = dst
    core.glossary.save_glossary(scope, terms)
    return jsonify({"ok": True})


@bp.delete("/api/glossary/<scope>/term/<src>")
def delete_term(scope, src):
    terms = core.glossary.load_glossary(scope)
    if src not in terms:
        abort(404, "Term not found.")
    del terms[src]
    core.glossary.save_glossary(scope, terms)
    return jsonify({"ok": True})
