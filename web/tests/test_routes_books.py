import io

import core_loader


def test_books_list_and_out(client, sandbox):
    (sandbox["books"] / "a.epub").write_bytes(b"x")
    (sandbox["books"] / "b.epub").write_bytes(b"x")
    (sandbox["out"] / "a.en.epub").write_bytes(b"x")
    books = client.get("/api/books").get_json()
    assert [b["name"] for b in books] == ["a.epub", "b.epub"]
    assert books[0]["key"] == "a"
    assert client.get("/api/out").get_json() == ["a.en.epub"]


def test_upload_book(client, sandbox):
    resp = client.post("/api/books",
                       data={"file": (io.BytesIO(b"content"), "new.epub")},
                       content_type="multipart/form-data")
    assert resp.status_code == 201
    assert (sandbox["books"] / "new.epub").read_bytes() == b"content"


def test_upload_rejects_duplicate_and_non_epub(client, sandbox):
    (sandbox["books"] / "new.epub").write_bytes(b"x")
    assert client.post("/api/books", data={"file": (io.BytesIO(b"c"), "new.epub")},
                       content_type="multipart/form-data").status_code == 409
    assert client.post("/api/books", data={"file": (io.BytesIO(b"c"), "new.txt")},
                       content_type="multipart/form-data").status_code == 400


def test_estimate(client, sandbox, monkeypatch):
    (sandbox["books"] / "a.epub").write_bytes(b"x")
    monkeypatch.setattr(core_loader.translate_book, "estimate",
                        lambda p: {"chapters": 5, "chars": 100, "tokens": 170, "cost": 0.01})
    data = client.get("/api/books/a.epub/estimate").get_json()
    assert data["chapters"] == 5 and data["cost"] == 0.01 and data["name"] == "a.epub"


def test_estimate_404(client, sandbox):
    assert client.get("/api/books/missing.epub/estimate").status_code == 404


def test_download(client, sandbox):
    (sandbox["out"] / "a.en.epub").write_bytes(b"epubdata")
    resp = client.get("/api/download/a.en.epub")
    assert resp.status_code == 200 and resp.data == b"epubdata"
    assert client.get("/api/download/nope.epub").status_code == 404
