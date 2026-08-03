import importlib
import pkgutil
import threading
import webbrowser
from argparse import ArgumentParser
from pathlib import Path

from flask import Blueprint, Flask, jsonify, send_from_directory
from werkzeug.exceptions import HTTPException

import core_loader as core

app = Flask(__name__, static_folder="static")
app.json.ensure_ascii = False


@app.errorhandler(Exception)
def handle_error(exc):
    if isinstance(exc, HTTPException):
        return jsonify({"error": exc.description}), exc.code
    app.logger.exception("Unhandled error")
    return jsonify({"error": str(exc)}), 500


@app.get("/")
def index():
    if (Path(app.static_folder) / "index.html").is_file():
        return send_from_directory("static", "index.html")
    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        "<title>Web Novel EPUB Translator</title></head><body>"
        "<h1>Web Novel EPUB Translator</h1><p>Frontend not built yet.</p></body></html>"
    )


@app.get("/api/ping")
def ping():
    return jsonify({"ok": True, "core": core.config.BASE_DIR.name})


def register_blueprints():
    here = Path(__file__).resolve().parent
    for info in pkgutil.iter_modules([str(here)]):
        if not info.name.startswith("routes_"):
            continue
        try:
            mod = importlib.import_module(info.name)
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("Skipping route module %s: %s", info.name, exc)
            continue
        for obj in vars(mod).values():
            if isinstance(obj, Blueprint):
                app.register_blueprint(obj)


register_blueprints()


def main():
    parser = ArgumentParser(description="Web GUI for the EPUB translator.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8177)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{args.host}:{args.port}/")).start()
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
