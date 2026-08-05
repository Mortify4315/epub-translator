import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as server


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    books = tmp_path / "books"
    out = tmp_path / "out"
    glos = tmp_path / "glossaries"
    for d in (books, out, glos):
        d.mkdir()
    monkeypatch.setattr(server.core.config, "BOOKS_DIR", books)
    monkeypatch.setattr(server.core.config, "OUT_DIR", out)
    monkeypatch.setattr(server.core.glossary, "GLOSSARY_DIR", glos)
    monkeypatch.setattr(server.core.config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(server.core.config, "get_api_key", lambda: "")
    return {"books": books, "out": out, "glossaries": glos}


def wait_until(pred, timeout=5.0):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False
