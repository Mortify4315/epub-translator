"""Merge a chapter-limited partial epub back onto the full source epub.

The chapter_limit run finalizes a zip containing ONLY the processed files
(translated chapters, nav, metadata). This script produces the canonical
output: every source entry, with translated entries (from the partial)
replacing their source counterparts. Result: chapters 1..N in English,
the rest still in Chinese, all styles/images/structure intact.

Usage: python merge_partial_epub.py <source.epub> <partial.en.epub> <output.epub>
"""
import shutil
import sys
import zipfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: merge_partial_epub.py <source.epub> <partial.en.epub> <output.epub>")
        return 2
    source_path, partial_path, out_path = (Path(a) for a in sys.argv[1:])

    with zipfile.ZipFile(source_path) as src, zipfile.ZipFile(partial_path) as part:
        partial_names = set(part.namelist())
        tmp = out_path.with_suffix(".epub.tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
            for info in src.infolist():
                name = info.filename
                if name.endswith("/"):
                    continue
                if name in partial_names:
                    data = part.read(name)
                    compress = zipfile.ZIP_DEFLATED
                    # keep mimetype stored+first (epub spec)
                    if name == "mimetype":
                        compress = zipfile.ZIP_STORED
                    out.writestr(name, data, compress_type=compress)
                else:
                    out.writestr(name, src.read(name), compress_type=info.compress_type)
        tmp.replace(out_path)
    print(f"merged -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
