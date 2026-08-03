import argparse
from pathlib import Path

from bs4 import BeautifulSoup
from ebooklib import epub
from epub_translator import LLM, SubmitKind, language, translate

from config import (
    CACHE_DIR,
    OUT_DIR,
    TOKEN_ENCODING,
    estimate_cost,
    get_api_key,
    get_base_url,
    get_concurrency,
    get_model,
)
from glossary import book_key, build_translation_prompt, merge_glossaries


def estimate(source_path: Path) -> dict:
    book = epub.read_epub(source_path)
    chapters = 0
    total_chars = 0
    for item in book.get_items_of_type(epub.EpubHtml):
        chapters += 1
        soup = BeautifulSoup(item.get_content(), "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        total_chars += len(soup.get_text())
    tokens = int(total_chars * 1.2) + chapters * 50
    cost = estimate_cost(tokens, int(tokens * 0.6))
    return {"chapters": chapters, "chars": total_chars, "tokens": tokens, "cost": cost}


def run_translation(source_path: Path, on_progress=None) -> dict:
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError("No API key configured. Set it in Settings or OPENCODE_GO_API_KEY.")
    key = book_key(source_path.name)
    glossary = merge_glossaries(key)
    prompt = build_translation_prompt(glossary)
    target_path = OUT_DIR / f"{source_path.stem}.en.epub"
    cache_path = CACHE_DIR / key

    llm = LLM(
        key=api_key,
        url=get_base_url(),
        model=get_model(),
        token_encoding=TOKEN_ENCODING,
        cache_path=str(cache_path),
        retry_times=5,
        retry_interval_seconds=6.0,
        temperature=0.4,
    )

    translate(
        source_path=str(source_path),
        target_path=str(target_path),
        target_language=language.ENGLISH,
        submit=SubmitKind.REPLACE,
        user_prompt=prompt,
        llm=llm,
        concurrency=get_concurrency(),
        on_progress=on_progress,
    )

    return {
        "target": target_path,
        "input_tokens": llm.input_tokens,
        "output_tokens": llm.output_tokens,
        "cost": estimate_cost(llm.input_tokens, llm.output_tokens),
    }


def main():
    parser = argparse.ArgumentParser(description="Translate an EPUB from Chinese to English.")
    parser.add_argument("epub", help="path to a .epub file in the 'books' folder")
    args = parser.parse_args()
    result = run_translation(Path(args.epub))
    print(f"Saved to {result['target']}")
    print(f"Tokens: {result['input_tokens']:,} in / {result['output_tokens']:,} out   "
          f"Est. cost: ${result['cost']:.2f}")


if __name__ == "__main__":
    main()
