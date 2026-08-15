import json
from pathlib import Path

from config import GLOSSARY_DIR

GLOBAL_NAME = "global"


def _book_file(book_key_name: str) -> Path:
    return GLOSSARY_DIR / f"{book_key_name}.json"


def _safe_key(name: str) -> str:
    clean = "".join(ch for ch in name if ch.isalnum() or ch in "-_")
    return clean or "untitled"


def book_key(epub_name: str) -> str:
    stem = epub_name.rsplit(".", 1)[0]
    return _safe_key(stem)


def load_glossary(book_key_name: str) -> dict:
    path = _book_file(book_key_name)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_glossary(book_key_name: str, terms: dict) -> None:
    ordered = dict(sorted(terms.items(), key=lambda kv: kv[0]))
    _book_file(book_key_name).write_text(
        json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def merge_glossaries(book_key_name: str) -> dict:
    merged = dict(load_glossary(GLOBAL_NAME))
    merged.update(load_glossary(book_key_name))
    return merged


def add_terms(book_key_name: str, terms: dict) -> int:
    current = load_glossary(book_key_name)
    added = 0
    for src, dst in terms.items():
        src = str(src).strip()
        dst = str(dst).strip()
        if not src or not dst or src in current:
            continue
        current[src] = dst
        added += 1
    if added:
        save_glossary(book_key_name, current)
    return added


def build_translation_prompt(glossary: dict) -> str:
    lines = [
        "Translate the Chinese web novel content below into natural, fluent English.",
        "Match the style of professional translated web novels.",
        "Keep every HTML tag exactly as-is. Never translate tags, attributes, or class names.",
        "Follow this strict glossary for names, skills, places, and terms. Always use these mappings:",
    ]
    if glossary:
        lines.extend(f"- {src} => {dst}" for src, dst in glossary.items())
    else:
        lines.append("- (no glossary terms supplied)")
    lines.append(
        "Character and place names: transliterate personal names into pinyin, "
        "and translate meaningful titles and terms into English "
        "(e.g. 盟主 => \"Alliance Leader\"). Never leave Chinese characters "
        "in the English output."
    )
    lines.append("Output only the English translation.")
    return "\n".join(lines)
