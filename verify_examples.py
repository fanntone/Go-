#!/usr/bin/env python3
"""
抽出 content/*.md 裡所有 ```go 圍欄中「完整可獨立編譯」的範例（以 package main 開頭），
逐一丟給 go vet 檢查。

會自動跳過：
  - 需要非標準函式庫相依的範例
  - 使用 cgo（import "C"）的範例
  - 刻意示範編譯錯誤的片段（以 SKIP_MARKERS 判斷）
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
CONTENT = ROOT / "content"

FENCE_OPEN = re.compile(r"^(?P<indent>[ \t]*)(?P<ticks>`{3,})[ \t]*(?P<lang>[\w+#-]*)[ \t]*$")
IMPORT_LINE = re.compile(r'^\s*(?:[\w.]+\s+)?"([^"]+)"', re.MULTILINE)


def go_blocks(text: str) -> list[str]:
    """取出所有 ```go 區塊，包含縮排在 admonition 裡的（會依縮排量 dedent）。"""
    lines = text.split("\n")
    out: list[str] = []
    i = 0

    while i < len(lines):
        m = FENCE_OPEN.match(lines[i])
        if not m:
            i += 1
            continue

        indent, ticks, lang = m.group("indent"), m.group("ticks"), m.group("lang")

        j = i + 1
        while j < len(lines):
            s = lines[j].strip()
            if s and set(s) == {"`"} and len(s) >= len(ticks):
                break
            j += 1

        if j < len(lines) and lang == "go":
            out.append(
                "\n".join(
                    ln[len(indent):] if ln.startswith(indent) else ln.lstrip()
                    for ln in lines[i + 1:j]
                )
            )
        i = j + 1

    return out

# 這些範例需要外部環境（C 工具鏈、被嵌入的實體檔案），不列入檢查
SKIP_MARKERS = ('import "C"', "//go:build ignore", "//go:embed")

STDLIB_PREFIXES = None  # 由 `go list std` 動態取得


def stdlib_set() -> set[str]:
    out = subprocess.run(
        ["go", "list", "std"], capture_output=True, text=True, check=True
    ).stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


def is_stdlib_only(src: str, std: set[str]) -> tuple[bool, str]:
    block = re.search(r"^import\s*\((.*?)^\)", src, re.DOTALL | re.MULTILINE)
    paths = []
    if block:
        paths = IMPORT_LINE.findall(block.group(1))
    else:
        for m in re.finditer(r'^import\s+(?:[\w.]+\s+)?"([^"]+)"', src, re.MULTILINE):
            paths.append(m.group(1))

    for p in paths:
        if p not in std:
            return False, p
    return True, ""


def go_minor() -> str:
    """本機 go 版本的 major.minor，例如 '1.22'。"""
    out = subprocess.run(["go", "version"], capture_output=True, text=True, check=True).stdout
    m = re.search(r"go(\d+)\.(\d+)", out)
    return f"{m.group(1)}.{m.group(2)}" if m else "1.21"


def main() -> int:
    std = stdlib_set()
    ver = go_minor()
    print(f"以本機工具鏈驗證：go {ver}\n")

    workdir = Path(tempfile.mkdtemp(prefix="goverify-"))
    (workdir / "go.mod").write_text(f"module verify\n\ngo {ver}\n", encoding="utf-8")

    checked = skipped = failed = 0
    failures: list[str] = []

    try:
        for md in sorted(CONTENT.glob("*.md")):
            text = md.read_text(encoding="utf-8")
            for i, src in enumerate(go_blocks(text), 1):
                label = f"{md.name}#go{i}"

                if not src.lstrip().startswith("package main"):
                    continue
                if any(mark in src for mark in SKIP_MARKERS):
                    skipped += 1
                    continue

                ok, bad = is_stdlib_only(src, std)
                if not ok:
                    skipped += 1
                    print(f"  skip {label}  (需要 {bad})")
                    continue

                d = workdir / f"ex{checked + failed:03d}"
                d.mkdir(exist_ok=True)
                (d / "main.go").write_text(src, encoding="utf-8")

                r = subprocess.run(
                    ["go", "vet", "./" + d.name],
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                )
                if r.returncode == 0:
                    checked += 1
                else:
                    failed += 1
                    failures.append(f"--- {label}\n{r.stderr.strip()}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    print(f"通過 {checked}　跳過 {skipped}　失敗 {failed}")
    if failures:
        print()
        print("\n\n".join(failures))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
