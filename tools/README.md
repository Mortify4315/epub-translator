# Batch-translation utilities

Long books can be translated in batches via the `chapter_limit` setting
(0 = unlimited; set in `cli/settings.json` or `POST /api/settings`). Each run
stops cleanly after N spine items (~N-6 real chapters — the offset is the
cover/front/intro/volume pages) and the engine finalizes a *readable* partial
epub. That partial only contains processed files (no container.xml, styles,
or untranslated chapters), so two post-processing steps produce the file you
read:

## Workflow per batch

```bash
# 1. set the batch boundary, then run the translation (web job or CLI)
#    cli/settings.json: "chapter_limit": 800

# 2. merge: partial epub + full source -> complete epub (translated chapters
#    replace their source counterparts, untranslated chapters stay Chinese,
#    all styles/images intact)
python tools/merge_partial_epub.py \
    "cli/books/Chi Xin Xun Tian.epub" \
    <partial.en.epub> \
    "cli/out/<book>.en.epub"

# 3. trim (optional): copy of the merged file with ONLY the English chapters
#    (front matter kept, untranslated Chinese remainder dropped)
python tools/trim_batch_epub.py \
    "cli/out/<book>.en.epub" \
    "cli/out/<book> 1-<N>.epub"
```

## Notes

- Cache: per-request, survives kills/limits. Re-running the same book serves
  finished chapters from disk (no re-billing); only in-flight groups re-bill.
- The `chapter_limit` run raises `ChapterLimitReached` internally — the
  partial output is intentionally kept (unlike `BudgetExceeded`, which
  deletes it).
- Glossary changes between batches invalidate the cache for previously
  translated chapters (the prompt is part of the cache key) — keep the
  glossary stable between batches to avoid re-billing.
- Both scripts need the `cli/.venv` (ebooklib + beautifulsoup4).
