import argparse
import json
import shutil
from pathlib import Path

from bs4 import BeautifulSoup
from ebooklib import epub
from epub_translator import LLM, SubmitKind, language, translate

import normalize
from config import (
    CACHE_DIR,
    OUT_DIR,
    TOKEN_ENCODING,
    estimate_cost,
    get_api_key,
    get_base_url,
    get_concurrency,
    get_extra_body,
    get_fill_extra_body,
    get_fill_thinking,
    get_max_group_tokens,
    get_model,
)
from glossary import book_key, build_translation_prompt, merge_glossaries


def prepare_epub(source_path: Path, work_dir: Path) -> Path:
    book = epub.read_epub(source_path)
    changed = False
    for item in book.get_items():
        if not isinstance(item, epub.EpubHtml):
            continue
        if item.get_name().lower().endswith(("nav.xhtml", "toc.xhtml")):
            continue
        normalized = normalize.normalize_html(item.get_content())
        if normalized != item.get_content():
            item.set_content(normalized)
            changed = True
    if not any(isinstance(item, epub.EpubNav) for item in book.get_items()):
        book.add_item(epub.EpubNav())
        book.spine = ["nav"] + [ref for ref, _ in book.spine]
        changed = True
    if not changed:
        return source_path
    work_dir.mkdir(parents=True, exist_ok=True)
    prepared = work_dir / f"{source_path.stem}.prep.epub"
    epub.write_epub(prepared, book)
    return prepared


def estimate(source_path: Path) -> dict:
    book = epub.read_epub(source_path)
    chapters = 0
    total_chars = 0
    for item in book.get_items():
        if not isinstance(item, epub.EpubHtml):
            continue
        chapters += 1
        soup = BeautifulSoup(item.get_content(), "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        total_chars += len(soup.get_text())
    tokens = int(total_chars * 1.2) + chapters * 50
    cost = estimate_cost(tokens, int(tokens * 0.6))
    return {"chapters": chapters, "chars": total_chars, "tokens": tokens, "cost": cost}


def _cache_config() -> dict:
    return {
        "thinking": get_extra_body()["thinking"]["type"],
        "fill_thinking": get_fill_thinking(),
        "model": get_model(),
    }


def _ensure_cache_matches_config(cache_path: Path, config: dict) -> bool:
    marker = cache_path / "config.json"
    try:
        saved = json.loads(marker.read_text(encoding="utf-8"))
        return saved == config
    except (OSError, ValueError):
        return False


def run_translation(source_path: Path, on_progress=None) -> dict:
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError("No API key configured. Set it in Settings or OPENCODE_GO_API_KEY.")
    key = book_key(source_path.name)
    glossary = merge_glossaries(key)
    prompt = build_translation_prompt(glossary)
    target_path = OUT_DIR / f"{source_path.stem}.en.epub"
    cache_path = CACHE_DIR / key
    source_for_translation = prepare_epub(source_path, CACHE_DIR / "prep")

    config = _cache_config()
    cache_cleared = False
    if cache_path.exists():
        if not _ensure_cache_matches_config(cache_path, config):
            shutil.rmtree(cache_path, ignore_errors=True)
            cache_cleared = True
    cache_path.mkdir(parents=True, exist_ok=True)
    (cache_path / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )

    def make_llm(extra_body: dict) -> LLM:
        return LLM(
            key=api_key,
            url=get_base_url(),
            model=get_model(),
            token_encoding=TOKEN_ENCODING,
            cache_path=str(cache_path),
            retry_times=5,
            retry_interval_seconds=6.0,
            temperature=0.4,
            extra_body=extra_body,
        )

    translation_llm = make_llm(get_extra_body())
    fill_llm = make_llm(get_fill_extra_body())

    translate(
        source_path=str(source_for_translation),
        target_path=str(target_path),
        target_language=language.ENGLISH,
        submit=SubmitKind.REPLACE,
        user_prompt=prompt,
        translation_llm=translation_llm,
        fill_llm=fill_llm,
        concurrency=get_concurrency(),
        max_group_tokens=get_max_group_tokens(),
        on_progress=on_progress,
    )

    input_tokens = translation_llm.input_tokens + fill_llm.input_tokens
    output_tokens = translation_llm.output_tokens + fill_llm.output_tokens
    return {
        "target": target_path,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost": estimate_cost(input_tokens, output_tokens),
        "cache_cleared": cache_cleared,
    }


def main():
    parser = argparse.ArgumentParser(description="Translate an EPUB from Chinese to English.")
    parser.add_argument("epub", help="path to a .epub file in the 'books' folder")
    args = parser.parse_args()

    def _print_progress(frac: float) -> None:
        print(f"\rProgress: {frac * 100:5.1f}%", end="", flush=True)

    result = run_translation(Path(args.epub), on_progress=_print_progress)
    print()
    if result.get("cache_cleared"):
        print("Note: translation cache was cleared (thinking/fill mode changed).")
    print(f"Saved to {result['target']}")
    print(f"Tokens: {result['input_tokens']:,} in / {result['output_tokens']:,} out   "
          f"Est. cost: ${result['cost']:.2f}")


if __name__ == "__main__":
    main()
