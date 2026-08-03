from flask import Blueprint, abort, jsonify, request

import core_loader as core
import jobs

bp = Blueprint("jobs", __name__)


@bp.post("/api/translate")
def start_translate():
    data = request.get_json(force=True)
    book_name = (data.get("book") or "").strip()
    if not book_name:
        abort(400, "book is required.")
    book = core.config.BOOKS_DIR / book_name
    if not book.is_file():
        abort(404, "Book not found.")
    if not core.config.get_api_key():
        abort(400, "No API key configured. Set it in Settings.")
    try:
        job = jobs.manager.start("translate", book_name, lambda j: jobs.run_translate(j, book_name))
    except RuntimeError as exc:
        abort(409, str(exc))
    return jsonify(job.to_dict())


@bp.post("/api/scan")
def start_scan():
    data = request.get_json(force=True)
    book_name = (data.get("book") or "").strip()
    if not book_name:
        abort(400, "book is required.")
    book = core.config.BOOKS_DIR / book_name
    if not book.is_file():
        abort(404, "Book not found.")
    if not core.config.get_api_key():
        abort(400, "No API key configured. Set it in Settings.")
    try:
        job = jobs.manager.start("scan", book_name, lambda j: jobs.run_scan(j, book_name))
    except RuntimeError as exc:
        abort(409, str(exc))
    return jsonify(job.to_dict())


@bp.get("/api/jobs/<job_id>")
def get_job(job_id):
    job = jobs.manager.get(job_id)
    if not job:
        abort(404, "Job not found.")
    return jsonify(job.to_dict())
