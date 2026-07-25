#!/usr/bin/env python3
"""
Go 底層原理筆記 — 靜態網站產生器

用法:
    python build.py            # 產生 site/
    python build.py --serve    # 產生後在 http://localhost:8080 起本機伺服器

相依套件: markdown, pygments, pyyaml
    pip install -r requirements.txt
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import markdown
import yaml

ROOT = Path(__file__).parent
CONTENT = ROOT / "content"
THEME = ROOT / "theme"
OUT = ROOT / "site"

FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
FENCE_RE = re.compile(r"^```(\w*)\s*$", re.MULTILINE)


def slugify(value: str, separator: str = "-") -> str:
    """保留 CJK 的 slugify。

    python-markdown 內建的版本會把中文字元整個丟掉，讓純中文標題退化成
    _1、_2 這種流水號 —— 插入一個標題就會讓所有既有錨點位移。
    """
    value = unicodedata.normalize("NFKC", str(value)).strip().lower()
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)   # \w 在 Unicode 模式下含 CJK
    value = re.sub(r"[\s_" + re.escape(separator) + r"]+", separator, value, flags=re.UNICODE)
    return value.strip(separator)


def fence_langs(src: str) -> list[str]:
    """依序取出 markdown 中每個 ``` 圍欄區塊的語言（開頭的那一行）。"""
    langs, opened = [], False
    for m in FENCE_RE.finditer(src):
        if not opened:
            langs.append(m.group(1))
        opened = not opened
    return langs


def tag_code_langs(body: str, langs: list[str]) -> str:
    """把語言標到 codehilite 產生的 <div class="hl"> 上，供前端顯示語言標籤。"""
    it = iter(langs)

    def sub(_m):
        return f'<div class="hl" data-lang="{next(it, "")}">'

    return re.sub(r'<div class="hl">', sub, body)


@dataclass
class Page:
    slug: str
    title: str
    part: str
    order: float
    number: str
    summary: str
    body: str
    toc: list = field(default_factory=list)
    updated: str = ""

    @property
    def url(self) -> str:
        return f"{self.slug}.html"

    @property
    def label(self) -> str:
        return f"{self.number} {self.title}" if self.number else self.title


def read_pages() -> tuple[list[Page], dict]:
    book = yaml.safe_load((CONTENT / "_book.yml").read_text(encoding="utf-8"))
    md = markdown.Markdown(
        extensions=[
            "fenced_code",
            "codehilite",
            "tables",
            "toc",
            "attr_list",
            "def_list",
            "sane_lists",
            "admonition",
            "footnotes",
        ],
        extension_configs={
            "codehilite": {"guess_lang": False, "css_class": "hl", "linenums": False},
            "toc": {"permalink": "#", "toc_depth": "2-3", "slugify": slugify},
        },
    )

    pages: list[Page] = []
    for path in sorted(CONTENT.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        m = FRONTMATTER.match(raw)
        if not m:
            raise SystemExit(f"缺少 frontmatter: {path.name}")
        meta = yaml.safe_load(m.group(1)) or {}
        src = raw[m.end():]
        md.reset()
        body = tag_code_langs(md.convert(src), fence_langs(src))
        pages.append(
            Page(
                slug=meta.get("slug", path.stem),
                title=meta["title"],
                part=meta["part"],
                order=float(meta["order"]),
                number=str(meta.get("number", "")),
                summary=meta.get("summary", ""),
                body=body,
                toc=getattr(md, "toc_tokens", []),
                updated=str(meta.get("updated", "")),
            )
        )

    pages.sort(key=lambda p: p.order)
    return pages, book


def render_sidebar(pages: list[Page], book: dict, current: str | None) -> str:
    parts: dict[str, list[Page]] = {}
    for p in pages:
        parts.setdefault(p.part, []).append(p)

    out = ['<nav class="booknav" aria-label="全書目錄">']
    for part in book["parts"]:
        key = part["id"]
        items = parts.get(key, [])
        if not items:
            continue
        open_attr = " open" if any(p.slug == current for p in items) or current is None else ""
        out.append(f"<details class='part'{open_attr}>")
        out.append(
            f"<summary><span class='part-kicker'>{html.escape(part['kicker'])}</span>"
            f"<span class='part-title'>{html.escape(part['title'])}</span></summary>"
        )
        out.append("<ul>")
        for p in items:
            cls = " class='active'" if p.slug == current else ""
            out.append(
                f"<li{cls}><a href='{p.url}'>"
                f"<span class='num'>{html.escape(p.number)}</span>"
                f"<span class='txt'>{html.escape(p.title)}</span></a></li>"
            )
        out.append("</ul></details>")
    out.append("</nav>")
    return "\n".join(out)


def render_toc(tokens: list) -> str:
    if not tokens:
        return ""
    def walk(items, depth=0):
        if not items or depth > 1:
            return ""
        buf = ["<ul>"]
        for it in items:
            buf.append(
                f"<li><a href='#{it['id']}'>{html.escape(TAG_RE.sub('', it['name']))}</a>"
                + walk(it.get("children", []), depth + 1)
                + "</li>"
            )
        buf.append("</ul>")
        return "".join(buf)

    return f"<nav class='pagetoc' aria-label='本頁目錄'><p class='pagetoc-h'>本頁章節</p>{walk(tokens)}</nav>"


def plain_text(html_body: str) -> str:
    text = re.sub(r"<(script|style|svg)[\s\S]*?</\1>", " ", html_body)
    text = TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def build_search_index(pages: list[Page]) -> str:
    index = []
    for p in pages:
        index.append(
            {
                "u": p.url,
                "t": p.label,
                "s": p.summary,
                "b": plain_text(p.body)[:6000],
                "h": [TAG_RE.sub("", t["name"]) for t in p.toc],
            }
        )
    return json.dumps(index, ensure_ascii=False, separators=(",", ":"))


def build():
    pages, book = read_pages()
    tpl = (THEME / "base.html").read_text(encoding="utf-8")

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    for asset in ("style.css", "app.js"):
        shutil.copy(THEME / asset, OUT / asset)

    (OUT / "search-index.json").write_text(build_search_index(pages), encoding="utf-8")

    # ---- 章節頁 ----
    for i, page in enumerate(pages):
        prev_p = pages[i - 1] if i > 0 else None
        next_p = pages[i + 1] if i < len(pages) - 1 else None
        pager = ["<nav class='pager'>"]
        if prev_p:
            pager.append(
                f"<a class='prev' href='{prev_p.url}'><span>上一節</span>"
                f"<strong>{html.escape(prev_p.label)}</strong></a>"
            )
        else:
            pager.append("<span></span>")
        if next_p:
            pager.append(
                f"<a class='next' href='{next_p.url}'><span>下一節</span>"
                f"<strong>{html.escape(next_p.label)}</strong></a>"
            )
        pager.append("</nav>")

        part_title = next(p["title"] for p in book["parts"] if p["id"] == page.part)
        header = (
            f"<header class='page-head'>"
            f"<p class='crumb'><a href='index.html'>{html.escape(book['title'])}</a>"
            f"<span>/</span>{html.escape(part_title)}</p>"
            f"<h1><span class='h1num'>{html.escape(page.number)}</span>{html.escape(page.title)}</h1>"
            + (f"<p class='lede'>{html.escape(page.summary)}</p>" if page.summary else "")
            + (f"<p class='verified'>對照版本 · Go {html.escape(page.updated)}</p>" if page.updated else "")
            + "</header>"
        )

        out_html = (
            tpl.replace("{{TITLE}}", html.escape(f"{page.label} — {book['title']}"))
            .replace("{{DESCRIPTION}}", html.escape(page.summary))
            .replace("{{SIDEBAR}}", render_sidebar(pages, book, page.slug))
            .replace("{{PAGETOC}}", render_toc(page.toc))
            .replace("{{CONTENT}}", header + "<article class='prose'>" + page.body + "</article>" + "".join(pager))
            .replace("{{BOOKTITLE}}", html.escape(book["title"]))
            .replace("{{BODYCLASS}}", "chapter")
        )
        (OUT / page.url).write_text(out_html, encoding="utf-8")

    # ---- 首頁 ----
    (OUT / "index.html").write_text(render_home(pages, book, tpl), encoding="utf-8")

    (OUT / ".nojekyll").touch()

    try:
        print(f"✓ 已產生 {len(pages) + 1} 頁 → {OUT}")
    except UnicodeEncodeError:
        print(f"[OK] 已產生 {len(pages) + 1} 頁 → {OUT}")


def render_home(pages: list[Page], book: dict, tpl: str) -> str:
    parts: dict[str, list[Page]] = {}
    for p in pages:
        parts.setdefault(p.part, []).append(p)

    cards = []
    for part in book["parts"]:
        items = parts.get(part["id"], [])
        if not items:
            continue
        rows = "".join(
            f"<li><a href='{p.url}'><span class='num'>{html.escape(p.number)}</span>"
            f"<span class='txt'><strong>{html.escape(p.title)}</strong>"
            f"<em>{html.escape(p.summary)}</em></span></a></li>"
            for p in items
        )
        cards.append(
            f"<section class='partcard'>"
            f"<div class='partcard-h'><p class='kicker'>{html.escape(part['kicker'])}</p>"
            f"<h2>{html.escape(part['title'])}</h2>"
            f"<p class='blurb'>{html.escape(part.get('blurb', ''))}</p></div>"
            f"<ol class='partlist'>{rows}</ol></section>"
        )

    hero = f"""
<header class="hero">
  <p class="hero-kicker">{html.escape(book['kicker'])}</p>
  <h1>{html.escape(book['title'])}</h1>
  <p class="hero-sub">{html.escape(book['subtitle'])}</p>
  <div class="hero-meta">
    <span class="pill pill-go">Go {html.escape(book['go_version'])}</span>
    <span class="pill">{len(pages)} 個章節</span>
    <span class="pill">繁體中文 · 台灣用語</span>
  </div>
  <div class="hero-actions">
    <a class="btn btn-primary" href="{pages[0].url}">從頭開始讀 →</a>
    <a class="btn" href="#toc">直接看目錄</a>
  </div>
</header>
<section class="notes">{''.join(f"<div class='note'><h3>{html.escape(n['h'])}</h3><p>{html.escape(n['p'])}</p></div>" for n in book['notes'])}</section>
<h2 id="toc" class="toc-heading">目錄</h2>
"""
    return (
        tpl.replace("{{TITLE}}", html.escape(f"{book['title']} — {book['subtitle']}"))
        .replace("{{DESCRIPTION}}", html.escape(book["subtitle"]))
        .replace("{{SIDEBAR}}", render_sidebar(pages, book, None))
        .replace("{{PAGETOC}}", "")
        .replace("{{CONTENT}}", hero + "".join(cards))
        .replace("{{BOOKTITLE}}", html.escape(book["title"]))
        .replace("{{BODYCLASS}}", "home")
    )


def serve():
    import functools
    import http.server
    import socketserver

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(OUT))
    with socketserver.TCPServer(("127.0.0.1", 8080), handler) as httpd:
        print("→ http://localhost:8080  (Ctrl+C 結束)")
        httpd.serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true", help="產生後啟動本機伺服器")
    args = ap.parse_args()
    build()
    if args.serve:
        serve()
