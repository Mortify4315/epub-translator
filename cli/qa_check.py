import argparse
import re
from pathlib import Path

from bs4 import BeautifulSoup
from ebooklib import epub

from config import BOOKS_DIR, OUT_DIR
from glossary import book_key, merge_glossaries


def _chapter_texts(epub_path: Path) -> list:
    book = epub.read_epub(epub_path)
    texts = []
    for item in book.get_items():
        if not isinstance(item, epub.EpubHtml):
            continue
        soup = BeautifulSoup(item.get_content(), "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        texts.append(soup.get_text("\n"))
    return texts


def check(source_path: Path, translated_path: Path) -> list:
    glossary = merge_glossaries(book_key(source_path.name))
    texts = _chapter_texts(translated_path)
    issues = []
    for src, dst in glossary.items():
        canon = dst.lower()
        compact = re.sub(r"\s+", "", canon)
        variants = {
            "compact": compact,
            "hyphen": re.sub(r"\s+", "-", canon),
            "underscore": re.sub(r"\s+", "_", canon),
        }
        for variant in variants.values():
            if not variant or variant == canon:
                continue
            for idx, text in enumerate(texts, 1):
                low = text.lower()
                variant_count = low.count(variant)
                if variant_count and variant_count > low.count(canon):
                    issues.append((src, dst, idx, variant, variant_count))
    issues.sort(key=lambda item: item[2])
    return issues


def main():
    parser = argparse.ArgumentParser(description="Check glossary consistency in a translated EPUB.")
    parser.add_argument("source", nargs="?", help="path to the source .epub")
    parser.add_argument("translated", nargs="?", help="path to the translated .epub")
    args = parser.parse_args()

    if args.source and args.translated:
        source, translated = Path(args.source), Path(args.translated)
    else:
        translated_list = sorted(OUT_DIR.glob("*.epub"))
        if not translated_list:
            print("No translated books in the 'out' folder yet.")
            return
        translated = translated_list[-1]
        source = next(
            (s for s in sorted(BOOKS_DIR.glob("*.epub")) if book_key(s.name) == book_key(translated.name)),
            None,
        )
        if not source:
            print(f"Could not find a matching source book for {translated.name}.")
            return

    issues = check(source, translated)
    if not issues:
        print("No consistency issues found.")
        return
    print(f"{len(issues)} potential issue(s):")
    for src, dst, chapter, variant, count in issues:
        print(f"  Ch {chapter}: '{src}' expected '{dst}' but may appear as '{variant}' (x{count})")


if __name__ == "__main__":
    main()
