#!/usr/bin/env python3
"""檢查 site/ 裡所有內部連結（含 #anchor）是否都指得到東西。"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

SITE = Path(__file__).parent / "site"

HREF = re.compile(r'href=[\'"]([^\'"]+)[\'"]')
ID = re.compile(r'\sid=[\'"]([^\'"]+)[\'"]')


def main() -> int:
    pages = {p.name: p.read_text(encoding="utf-8") for p in SITE.glob("*.html")}
    anchors = {name: set(ID.findall(html)) for name, html in pages.items()}

    bad: list[str] = []
    total = 0

    for name, html in pages.items():
        for href in HREF.findall(html):
            if href.startswith(("http://", "https://", "mailto:", "data:")):
                continue
            total += 1

            target, _, frag = href.partition("#")
            frag = unquote(frag)

            if not target:                      # 同頁 anchor
                target = name
            if target not in pages:
                # 非 HTML 資源（style.css 等）只確認檔案存在
                if not (SITE / target).is_file():
                    bad.append(f"{name}: 找不到檔案 → {href}")
                continue
            if frag and frag not in anchors[target]:
                bad.append(f"{name}: 找不到錨點 → {href}")

    print(f"檢查 {total} 個內部連結，{len(bad)} 個有問題")
    for b in bad:
        print("  ✗", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
