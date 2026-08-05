def test_glossary_list_global_then_books(client, sandbox):
    (sandbox["books"] / "a.epub").write_bytes(b"x")
    (sandbox["glossaries"] / "global.json").write_text('{"道": "Dao"}', encoding="utf-8")
    (sandbox["glossaries"] / "a.json").write_text('{"丹田": "dantian"}', encoding="utf-8")
    scopes = client.get("/api/glossary").get_json()
    assert [s["key"] for s in scopes] == ["global", "a"]
    assert scopes[0]["terms"] == {"道": "Dao"}
    assert scopes[1]["terms"] == {"丹田": "dantian"}


def test_glossary_add_edit_delete(client, sandbox):
    sc = "global"
    assert client.post(f"/api/glossary/{sc}/term",
                       json={"src": "丹田", "dst": "dantian"}).get_json()["added"] == 1
    assert client.post(f"/api/glossary/{sc}/term",
                       json={"src": "丹田", "dst": "again"}).get_json()["added"] == 0
    assert client.put(f"/api/glossary/{sc}/term/%E4%B8%B9%E7%94%B0",
                      json={"src": "丹田", "dst": "Dantian"}).status_code == 200
    terms = client.get("/api/glossary").get_json()[0]["terms"]
    assert terms["丹田"] == "Dantian"
    assert client.delete(f"/api/glossary/{sc}/term/%E4%B8%B9%E7%94%B0").status_code == 200
    assert client.get("/api/glossary").get_json()[0]["terms"] == {}


def test_glossary_edit_missing_404(client, sandbox):
    assert client.put("/api/glossary/global/term/nope",
                      json={"src": "nope", "dst": "x"}).status_code == 404


def test_glossary_term_validation(client, sandbox):
    assert client.post("/api/glossary/global/term", json={"src": "", "dst": "x"}).status_code == 400
