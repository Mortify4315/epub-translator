from pathlib import Path

from flask import Blueprint, abort, jsonify, request, send_file

import core_loader as core

bp = Blueprint("books", __name__)


@bp.get("/api/books")
def list_books():
    return jsonify([
        {"name": p.name, "key": core.glossary.book_key(p.name)}
        for p in sorted(core.config.BOOKS_DIR.glob("*.epub"))
    ])


@bp.post("/api/books")
def upload_book():
    f = request.files.get("file")
    if not f or not f.filename or not f.filename.lower().endswith(".epub"):
        abort(400, "Upload an .epub file.")
    dest = core.config.BOOKS_DIR / Path(f.filename).name
    if dest.exists():
        abort(409, "A book with that name already exists.")
    f.save(dest)
    return jsonify({"name": dest.name, "key": core.glossary.book_key(dest.name)}), 201


@bp.get("/api/books/<book>/estimate")
def estimate(book):
    path = core.config.BOOKS_DIR / book
    if not path.is_file():
        abort(404, "Book not found.")
    est = core.translate_book.estimate(path)
    return jsonify({**est, "name": path.name, "model": core.config.get_model()})


@bp.get("/api/out")
def list_out():
    return jsonify([p.name for p in sorted(core.config.OUT_DIR.glob("*.epub"))])


@bp.get("/api/download/<name>")
def download(name):
    path = core.config.OUT_DIR / name
    if not path.is_file():
        abort(404, "File not found.")
    return send_file(path, as_attachment=True)
