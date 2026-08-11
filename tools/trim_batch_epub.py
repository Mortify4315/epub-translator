"""Trim a merged batch epub: keep only English-translated chapters (plus
front matter, styles, images), drop the untranslated Chinese remainder.
Usage: trim_batch_epub.py <merged.en.epub> <output.epub>
"""
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup
from ebooklib import epub


def _is_english_html(content: bytes) -> bool:
    text = BeautifulSoup(content, "html.parser").get_text(" ")
    alpha = sum(1 for c in text if c.isascii() and c.isalpha())
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return alpha > 500 and alpha > cjk


def _chapter_title(content: bytes, fallback: str) -> str:
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup.find_all(["h1", "h2", "h3"]):
        t = tag.get_text(strip=True)
        if t:
            return t[:120]
    return fallback


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: trim_batch_epub.py <merged.en.epub> <output.epub>")
        return 2
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])

    book = epub.read_epub(src)
    new = epub.EpubBook()
    new.set_identifier("batch-trim")
    new.set_title((book.title or "Book") + " (batch)")
    new.set_language(book.language or "en")
    kept_html, chapters = [], []
    for item in book.get_items():
        if not isinstance(item, epub.EpubHtml):
            # images, stylesheets, fonts, ncx — keep everything non-HTML
            new.add_item(item)
            continue
        name = (item.get_name() or "").lower()
        if name.endswith("nav.xhtml"):
            continue  # ebooklib regenerates the nav from book.toc
        if re.search(r"chapter_\d{5}", name):
            if _is_english_html(item.get_content()):
                chapters.append(item)
            continue  # untranslated chapters are dropped
        kept_html.append(item)  # cover/front/intro/volume (English) etc.

    for item in kept_html:
        new.add_item(item)
    chapters.sort(key=lambda i: i.get_name() or "")
    for idx, item in enumerate(chapters, 1):
        new.add_item(item)
        new.toc.append(epub.Link(
            item.get_name(), _chapter_title(item.get_content(), f"Chapter {idx}"), f"ch{idx}"))
    spine = ["nav"] + [i for i in kept_html] + chapters
    new.spine = spine
    new.add_item(epub.EpubNav())
    epub.write_epub(dst, new)
    print(f"trimmed -> {dst} ({len(kept_html)} front-matter + {len(chapters)} English chapters)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
