import argparse
import json
import shutil
import time
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
    get_chapter_limit,
    get_concurrency,
    get_extra_body,
    get_fill_extra_body,
    get_fill_thinking,
    get_max_group_tokens,
    get_max_retries,
    get_model,
    get_provider,
    get_provider_info,
    get_retry_times,
    get_token_budget,
    validate_ready,
)
from glossary import book_key, build_translation_prompt, merge_glossaries


class BudgetExceeded(RuntimeError):
    def __init__(self, budget: int, used: int) -> None:
        super().__init__(f"Budget exceeded: used {used:,} of {budget:,} tokens.")
        self.budget = budget
        self.used = used


class ChapterLimitReached(RuntimeError):
    """Raised by the progress callback when the configured chapter limit is
    reached. Unlike BudgetExceeded the partial output is KEPT (finalized by
    the engine's Zip context on clean exception exit), so each batch yields a
    readable epub with the chapters translated so far."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"Chapter limit reached: {limit} chapters translated.")
        self.limit = limit


def count_headers(prepared_epub: Path) -> int:
    """Number of pre-chapter on_progress calls the engine makes (TOC + metadata)."""
    book = epub.read_epub(prepared_epub)
    headers = 0
    if any(isinstance(item, epub.EpubNav) for item in book.get_items()):
        headers += 1
    if book.metadata:
        headers += 1
    return headers


def make_chapter_progress(total_chapters: int, headers: int, chapter_limit: int = 0,
                          on_progress=None, on_chapter=None):
    """Engine on_progress adapter that counts completed chapters and raises
    ChapterLimitReached once chapter_limit chapters are done (0 = unlimited).
    on_chapter(done, frac, total) is invoked per chapter completion."""
    state = {"n": 0, "total": total_chapters, "last": time.monotonic()}

    def callback(frac: float) -> None:
        state["n"] += 1
        done = max(0, min(state["total"], state["n"] - headers))
        if frac >= 0.999:
            state["total"] = done
        if on_chapter:
            on_chapter(done, frac, state["total"])
        if on_progress:
            on_progress(frac)
        if chapter_limit and done >= chapter_limit:
            raise ChapterLimitReached(chapter_limit)
        state["last"] = time.monotonic()

    def elapsed_since_last() -> float:
        return time.monotonic() - state["last"]

    return callback, elapsed_since_last


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
    """Token/cost estimate, calibrated against real runs (2026-08-11).

    Measured economics (live, deepseek-v4-flash via opencode-go):
      - 5-chapter run (group 10000): 251,722 tok / 20,215 chars = 12.45 tok/char
      - 13-chapter run (group 5000):  404,141 tok / 28,325 chars = 14.27 tok/char
      - blended ≈ 13.5 tokens per source char (translate + fill passes combined)
      - in/out split ≈ 46% / 54% of total tokens
    The old chars*1.2 model understated real cost ~10x.
    """
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
    tokens = int(total_chars * 13.5) + chapters * 50
    input_tokens = int(tokens * 0.46)
    output_tokens = int(tokens * 0.54)
    cost = estimate_cost(input_tokens, output_tokens)
    return {"chapters": chapters, "chars": total_chars, "tokens": tokens, "cost": cost}


def _cache_config() -> dict:
    return {
        "provider": get_provider(),
        "base_url": get_base_url(),
        "thinking": get_extra_body().get("thinking", {}).get("type", "none"),
        "fill_thinking": get_fill_thinking(),
        "model": get_model(),
        "max_group_tokens": get_max_group_tokens(),
    }


def _ensure_cache_matches_config(cache_path: Path, config: dict) -> bool:
    marker = cache_path / "config.json"
    try:
        saved = json.loads(marker.read_text(encoding="utf-8"))
        return saved == config
    except (OSError, ValueError):
        return False


def run_translation(source_path: Path, on_progress=None) -> dict:
    problems = validate_ready()
    if problems:
        raise RuntimeError("; ".join(problems))
    api_key = get_api_key()
    key = book_key(source_path.name)
    glossary = merge_glossaries(key)
    prompt = build_translation_prompt(glossary)
    target_path = OUT_DIR / f"{source_path.stem}.en.epub"
    cache_path = CACHE_DIR / key
    source_for_translation = prepare_epub(source_path, CACHE_DIR / "prep")
    budget = get_token_budget(source_path.name)

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
            retry_times=get_retry_times(),
            retry_interval_seconds=6.0,
            temperature=0.4,
            extra_body=extra_body,
        )

    translation_llm = make_llm(get_extra_body())
    fill_llm = make_llm(get_fill_extra_body())

    est = estimate(source_path)
    headers = count_headers(source_for_translation)
    chapter_limit = get_chapter_limit()
    budget = get_token_budget(source_path.name)

    def budget_aware_progress(frac: float) -> None:
        used = translation_llm.total_tokens + fill_llm.total_tokens
        if used > budget:
            raise BudgetExceeded(budget, used)
        if on_progress:
            on_progress(frac)

    on_chapter_progress, _elapsed = make_chapter_progress(
        est["chapters"], headers, chapter_limit, on_progress=budget_aware_progress)

    chapter_limited = False
    try:
        translate(
            source_path=str(source_for_translation),
            target_path=str(target_path),
            target_language=language.ENGLISH,
            submit=SubmitKind.REPLACE,
            user_prompt=prompt,
            max_retries=get_max_retries(),
            translation_llm=translation_llm,
            fill_llm=fill_llm,
            concurrency=get_concurrency(),
            max_group_tokens=get_max_group_tokens(),
            on_progress=on_chapter_progress,
        )
    except BudgetExceeded:
        target_path.unlink(missing_ok=True)
        raise
    except ChapterLimitReached:
        # Partial output is intentional (batch mode): keep the finalized epub.
        chapter_limited = True

    input_tokens = translation_llm.input_tokens + fill_llm.input_tokens
    output_tokens = translation_llm.output_tokens + fill_llm.output_tokens
    return {
        "target": target_path,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost": estimate_cost(input_tokens, output_tokens),
        "cache_cleared": cache_cleared,
        "chapter_limited": chapter_limited,
    }


def main():
    parser = argparse.ArgumentParser(description="Translate an EPUB from Chinese to English.")
    parser.add_argument("epub", help="path to a .epub file in the 'books' folder")
    args = parser.parse_args()

    def _print_progress(frac: float) -> None:
        print(f"\rProgress: {frac * 100:5.1f}%", end="", flush=True)

    try:
        result = run_translation(Path(args.epub), on_progress=_print_progress)
    except BudgetExceeded as err:
        print()
        print(f"Budget exceeded: used {err.used:,} of {err.budget:,} tokens — stopped.")
        print("Cache kept. Re-run to resume from where it stopped.")
        return
    print()
    if result.get("cache_cleared"):
        print("Note: translation cache was cleared (provider/base URL/model or mode changed).")
    if result.get("chapter_limited"):
        print("Note: stopped at the configured chapter limit — output epub contains the "
              "chapters translated so far. Re-run (or raise chapter_limit) to continue.")
    print(f"Saved to {result['target']}")
    print(f"Tokens: {result['input_tokens']:,} in / {result['output_tokens']:,} out   "
          f"Est. cost: ${result['cost']:.2f}")


if __name__ == "__main__":
    main()
