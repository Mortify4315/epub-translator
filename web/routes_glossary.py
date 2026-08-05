from flask import Blueprint, abort, jsonify, request

import core_loader as core

bp = Blueprint("glossary", __name__)


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
