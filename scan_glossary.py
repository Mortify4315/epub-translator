import argparse
import json
import re
from pathlib import Path

import jieba
from bs4 import BeautifulSoup
from ebooklib import epub
from openai import OpenAI

from config import get_api_key, get_base_url, get_model
from glossary import GLOBAL_NAME, add_terms, book_key, merge_glossaries

CJK = re.compile(r"^[\u4e00-\u9fff]+$")

STOPWORDS = {
    "一个", "是", "不", "了", "有", "这", "那", "我", "你", "他", "她", "它",
    "我们", "你们", "他们", "她们", "它们", "都", "又", "很", "也", "就", "在",
    "着", "过", "吧", "吗", "啊", "呢", "去", "来", "上", "中", "下", "要", "会",
    "能", "可", "把", "被", "对", "从", "向", "为", "以", "于", "和", "自己",
    "没", "别", "同", "与", "等", "地", "得", "然后", "现在", "时候", "什么",
    "怎么", "这样", "那样", "这个", "那个", "已经", "没有", "看见", "知道",
    "觉得", "说道", "一下", "起来", "出来", "只是", "但是", "因为", "所以",
    "如果", "虽然", "可是", "不过", "而且", "或者", "还有", "一个", "一个",
    "之后", "之前", "以及", "甚至", "于是", "否则", "一边", "一直", "一起",
    "突然", "直接", "开始", "真的", "一点", "一样", "怎么", "什么", "大概",
}


def extract_chapter_texts(epub_path: Path) -> list:
    book = epub.read_epub(epub_path)
    texts = []
    for item in book.get_items_of_type(epub.EpubHtml):
        soup = BeautifulSoup(item.get_content(), "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        texts.append(soup.get_text("\n"))
    return texts


def candidate_terms(epub_path: Path, min_count: int = 5, max_terms: int = 120) -> dict:
    counts = {}
    for text in extract_chapter_texts(epub_path):
        for token in jieba.lcut(text):
            token = token.strip()
            if len(token) < 2 or len(token) > 12:
                continue
            if not CJK.match(token):
                continue
            if token in STOPWORDS:
                continue
            counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return {term: count for term, count in ranked[:max_terms] if count >= min_count}


def propose_translations(terms: list, api_key: str, base_url: str, model: str, batch: int = 50) -> dict:
    client = OpenAI(api_key=api_key, base_url=base_url)
    results = {}
    for i in range(0, len(terms), batch):
        batch_terms = terms[i : i + batch]
        prompt = (
            "You are building a translation glossary for a Chinese web novel being translated into English.\n"
            "For each Chinese term below (character names, skills, places, cultivation terms, titles), "
            "give the most natural English translation or romanization as used in translated web novels.\n"
            "Format each as one line: <chinese term> => <English>. Output only those lines, nothing else.\n"
            "Terms:\n" + "\n".join(batch_terms)
        )
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        content = (resp.choices[0].message.content or "").strip()
        if content.startswith("{"):
            try:
                data = json.loads(content)
                for src, dst in data.items():
                    if src in batch_terms and isinstance(dst, str) and dst.strip():
                        results[src] = dst.strip()
                continue
            except json.JSONDecodeError:
                pass
        for line in content.splitlines():
            if "=>" in line:
                src, dst = line.split("=>", 1)
                src = src.strip().strip('"').strip()
                dst = dst.strip().strip('"').strip()
                if src in batch_terms and dst:
                    results[src] = dst
    return results


def main():
    parser = argparse.ArgumentParser(description="Scan a Chinese EPUB and propose glossary terms.")
    parser.add_argument("epub", help="path to a .epub file in the 'books' folder")
    parser.add_argument("--min-count", type=int, default=5)
    parser.add_argument("--max-terms", type=int, default=120)
    parser.add_argument("--scope", choices=["book", "global"], default="book")
    args = parser.parse_args()

    api_key = get_api_key()
    if not api_key:
        print("No API key configured. Set OPENCODE_GO_API_KEY or use the app Settings.")
        return

    path = Path(args.epub)
    candidates = candidate_terms(path, min_count=args.min_count, max_terms=args.max_terms)
    if not candidates:
        print("No candidate terms found.")
        return
    print(f"Found {len(candidates)} candidate terms. Asking the model for translations...")
    proposed = propose_translations(list(candidates.keys()), api_key, get_base_url(), get_model())
    if not proposed:
        print("The model returned no usable suggestions.")
        return

    scope = GLOBAL_NAME if args.scope == "global" else book_key(path.name)
    existing = merge_glossaries(scope)
    fresh = {src: dst for src, dst in proposed.items() if src not in existing}
    n = add_terms(scope, fresh)
    print(f"Added {n} new term(s) to the '{scope}' glossary:")
    for src, dst in fresh.items():
        print(f"  {src} -> {dst}")


if __name__ == "__main__":
    main()
