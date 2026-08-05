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
        job = jobs.manager.start("translate", book_name)
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
        job = jobs.manager.start("scan", book_name)
    except RuntimeError as exc:
        abort(409, str(exc))
    return jsonify(job.to_dict())


@bp.get("/api/jobs/<job_id>")
def get_job(job_id):
    job = jobs.manager.get(job_id)
    if not job:
        abort(404, "Job not found.")
    return jsonify(job.to_dict())


@bp.post("/api/jobs/<job_id>/stop")
def stop_job(job_id):
    try:
        jobs.manager.stop(job_id)
    except KeyError:
        abort(404, "Job not found.")
    except RuntimeError as exc:
        abort(409, str(exc))
    job = jobs.manager.get(job_id)
    return jsonify(job.to_dict())


@bp.get("/api/jobs/<job_id>/log")
def job_log(job_id):
    job = jobs.manager.get(job_id)
    if not job:
        abort(404, "Job not found.")
    try:
        after = int(request.args.get("after", "0"))
    except ValueError:
        abort(400, "after must be an integer.")
    entries = job.log[after:]
    return jsonify({"total": len(job.log), "entries": entries})
