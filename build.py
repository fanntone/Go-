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
from markdown.extensions import Extension
from markdown.preprocessors import Preprocessor
from pygments import highlight as pyg_highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound

ROOT = Path(__file__).parent
CONTENT = ROOT / "content"
THEME = ROOT / "theme"
OUT = ROOT / "site"

FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")


FENCE_OPEN = re.compile(r"^(?P<indent>[ \t]*)(?P<ticks>`{3,}|~{3,})[ \t]*(?P<lang>[\w+#-]*)[ \t]*$")

_FORMATTER = HtmlFormatter(cssclass="hl", nowrap=False)
_DIV_OPEN = re.compile(r'^<div class="hl">')


class IndentedFencePreprocessor(Preprocessor):
    """支援縮排的程式碼圍欄。

    python-markdown 內建的 fenced_code 是 preprocessor，正則錨在行首且不允許
    前置空白，所以 admonition（!!! 區塊）內縮排 4 格的圍欄完全不會被辨識 ——
    ``` 會原樣印出來、程式碼被擠成一個段落。

    這個版本以「行」為單位掃描，接受任意縮排：把內容依開頭圍欄的縮排量 dedent、
    交給 Pygments 上色，再把結果存進 htmlStash，並在原縮排位置放回 placeholder，
    讓 admonition / list 等 block processor 仍能正確把它收進自己的範圍內。
    """

    def run(self, lines: list[str]) -> list[str]:
        out: list[str] = []
        i = 0

        while i < len(lines):
            m = FENCE_OPEN.match(lines[i])
            if not m:
                out.append(lines[i])
                i += 1
                continue

            indent, ticks, lang = m.group("indent"), m.group("ticks"), m.group("lang")

            # 找對應的結束圍欄（同種符號、長度不短於開頭，縮排寬鬆比對）
            j = i + 1
            while j < len(lines):
                s = lines[j].strip()
                if s.startswith(ticks[0] * len(ticks)) and set(s) == {ticks[0]}:
                    break
                j += 1

            if j >= len(lines):          # 沒有結束圍欄，原樣保留
                out.append(lines[i])
                i += 1
                continue

            body = "\n".join(
                ln[len(indent):] if ln.startswith(indent) else ln.lstrip()
                for ln in lines[i + 1:j]
            )

            out.append(indent + self.md.htmlStash.store(render_code(body, lang)))
            i = j + 1

        return out


class IndentedFenceExtension(Extension):
    def extendMarkdown(self, md):
        # 25 是內建 fenced_code 的優先度；normalize_whitespace 是 30，要排在它後面
        md.preprocessors.register(IndentedFencePreprocessor(md), "indented_fence", 26)


def render_code(body: str, lang: str) -> str:
    """用 Pygments 上色，並把語言標到外層 div 供前端顯示標籤。"""
    try:
        lexer = get_lexer_by_name(lang) if lang else TextLexer()
    except ClassNotFound:
        lexer = TextLexer()

    out = pyg_highlight(body, lexer, _FORMATTER)
    attr = f'<div class="hl" data-lang="{html.escape(lang, quote=True)}">'
    return _DIV_OPEN.sub(lambda _: attr, out, count=1)


def slugify(value: str, separator: str = "-") -> str:
    """保留 CJK 的 slugify。

    python-markdown 內建的版本會把中文字元整個丟掉，讓純中文標題退化成
    _1、_2 這種流水號 —— 插入一個標題就會讓所有既有錨點位移。
    """
    value = unicodedata.normalize("NFKC", str(value)).strip().lower()
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)   # \w 在 Unicode 模式下含 CJK
    value = re.sub(r"[\s_" + re.escape(separator) + r"]+", separator, value, flags=re.UNICODE)
    return value.strip(separator)


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
            IndentedFenceExtension(),
            "tables",
            "toc",
            "attr_list",
            "def_list",
            "sane_lists",
            "admonition",
            "footnotes",
        ],
        extension_configs={
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
        body = md.convert(src)
        # 防護：圍欄若沒被解析，``` 會原樣出現在輸出裡（縮排圍欄曾經整批失效過）
        stray = body.count("```")
        if stray:
            raise SystemExit(
                f"{path.name}: 輸出中殘留 {stray} 個未解析的 ``` 圍欄標記，"
                f"請檢查該檔的程式碼區塊。"
            )

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
