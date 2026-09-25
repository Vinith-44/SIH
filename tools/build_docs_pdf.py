"""Build docs/StoreMind_Technical_Document.pdf from docs/TECHNICAL_DOCUMENT.md.

    storemind/.venv/Scripts/python tools/build_docs_pdf.py
    storemind/.venv/Scripts/python tools/build_docs_pdf.py --keep-html      # also write the HTML beside the PDF

Needs `pip install playwright pypdf markdown-it-py` in the venv. On Windows it prints with the Microsoft Edge that
ships with the system (`channel="msedge"`), so no browser is downloaded; elsewhere pass `--channel chrome` or run
`playwright install chromium` and pass `--channel ""`. The Mermaid diagrams are drawn in the browser by mermaid.js
from the jsDelivr CDN, so the build needs the internet once per run (or `--mermaid-js <local file>`).

How the table of contents gets its page numbers: the PDF is printed with bookmarks (one per chapter and
section). pypdf reads which page each bookmark points to, the numbers are written into the contents page, and
the document is printed again. The contents page keeps the same size between prints, so the pagination does not
move; the script checks that and prints a third time if it did.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import tempfile
from pathlib import Path

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "TECHNICAL_DOCUMENT.md"
OUTPUT = ROOT / "docs" / "StoreMind_Technical_Document.pdf"
MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"

CSS = """
@page { size: A4; margin: 18mm 15mm 17mm 15mm; }
:root { --ink:#1b2430; --muted:#5b6672; --rule:#cfd6dd; --soft:#eef2f5; --accent:#1b5e7a; }
* { box-sizing: border-box; }
html { font-size: 10pt; }
body { margin:0; color:var(--ink); font-family:"Segoe UI", Calibri, "Helvetica Neue", Arial, sans-serif; line-height:1.45; }
.cover { height: 250mm; display:flex; flex-direction:column; justify-content:center; break-after: page; }
.cover .kicker { font-size:10pt; letter-spacing:.14em; text-transform:uppercase; color:var(--muted); margin:0 0 8mm; }
.cover h1.title { font-family:Cambria, Georgia, serif; font-size:30pt; line-height:1.1; margin:0 0 5mm; color:var(--ink);
  border:0; padding:0; break-before:auto; }
.cover .subtitle { font-size:14pt; color:var(--muted); margin:0 0 14mm; max-width:150mm; }
.cover .rule { height:3px; width:60mm; background:var(--accent); margin:0 0 10mm; }
.cover dl { display:grid; grid-template-columns:32mm auto; gap:2mm 6mm; font-size:10.5pt; margin:0; }
.cover dt { color:var(--muted); }
.cover dd { margin:0; }
nav.toc { break-after: page; }
nav.toc h2 { font-family:Cambria, Georgia, serif; font-size:20pt; margin:0 0 6mm; border:0; }
nav.toc ol { list-style:none; margin:0; padding:0; }
nav.toc li { display:flex; align-items:baseline; gap:2mm; margin:0; }
nav.toc li.l1 { font-weight:600; margin-top:2.2mm; }
nav.toc li.l2 { padding-left:7mm; font-size:9.2pt; }
nav.toc li a { color:var(--ink); text-decoration:none; }
nav.toc li .dots { flex:1; border-bottom:1px dotted #9aa5b1; transform:translateY(-1mm); }
nav.toc li .pg { min-width:8mm; text-align:right; font-variant-numeric:tabular-nums; }
h1 { font-family:Cambria, Georgia, serif; font-size:21pt; line-height:1.15; color:var(--ink); margin:0 0 5mm;
  padding-bottom:2mm; border-bottom:2px solid var(--accent); break-before: page; break-after: avoid; }
h2 { font-family:Cambria, Georgia, serif; font-size:14pt; margin:7mm 0 2.5mm; color:var(--ink); break-after: avoid; }
h3 { font-size:11.5pt; margin:5mm 0 2mm; break-after: avoid; }
h4 { font-size:10.5pt; margin:4mm 0 1.5mm; break-after: avoid; }
h2 .status, h3 .status { display:block; font-family:"Segoe UI", Calibri, Arial, sans-serif; font-size:9pt; font-weight:600;
  color:var(--accent); margin-top:1mm; }
p { margin:0 0 2.6mm; orphans:3; widows:3; }
ul, ol { margin:0 0 2.8mm; padding-left:6mm; }
li { margin:0.6mm 0; }
a { color:var(--accent); }
code { font-family:Consolas, "Cascadia Mono", monospace; font-size:8.8pt; background:var(--soft); padding:0 1mm; border-radius:1mm; }
pre.code { font-family:Consolas, "Cascadia Mono", monospace; font-size:8.3pt; line-height:1.4; background:var(--soft);
  padding:2.5mm 3mm; border-radius:1.5mm; white-space:pre-wrap; word-break:break-word; break-inside:avoid; margin:0 0 3mm; }
pre.code code { background:none; padding:0; font-size:inherit; }
table { border-collapse:collapse; width:100%; margin:1mm 0 3.5mm; font-size:8.6pt; line-height:1.35; }
thead { display:table-header-group; }
tr { break-inside: avoid; }
th, td { border:1px solid var(--rule); padding:1.2mm 1.8mm; vertical-align:top; text-align:left; }
th { background:var(--soft); font-weight:600; }
td code, th code { font-size:8pt; }
figure.diagram { margin:2mm 0 1mm; break-inside: avoid; text-align:center; }
figure.diagram svg { max-width:100% !important; height:auto !important; max-height:200mm; }
p.caption { font-size:8.8pt; color:var(--muted); margin:1mm 0 4mm; break-before: avoid; }
blockquote { margin:0 0 3mm; padding:1mm 4mm; border-left:3px solid var(--accent); color:var(--muted); }
strong { font-weight:600; }
"""

SCRIPT = """
window.__mermaidErrors = [];
// useMaxWidth false: every diagram keeps its natural size and CSS only ever shrinks it to the page width,
// so small diagrams are not blown up and wide ones are not stretched first.
mermaid.initialize({ startOnLoad: false, theme: 'neutral', securityLevel: 'loose',
  fontFamily: '"Segoe UI", Calibri, Arial, sans-serif', themeVariables: { fontSize: '15px' },
  flowchart: { useMaxWidth: false, htmlLabels: true, curve: 'basis', nodeSpacing: 35, rankSpacing: 40 },
  sequence: { useMaxWidth: false, mirrorActors: false, actorMargin: 30, width: 130,
              actorFontSize: 14, messageFontSize: 14, noteFontSize: 13 },
  state: { useMaxWidth: false } });
(async () => {
  const blocks = document.querySelectorAll('pre.mermaid');
  for (const [i, block] of blocks.entries()) {
    try {
      const { svg } = await mermaid.render('mmd' + i, block.textContent);
      const figure = document.createElement('figure');
      figure.className = 'diagram';
      figure.innerHTML = svg;
      block.replaceWith(figure);
    } catch (error) {
      window.__mermaidErrors.push('diagram ' + (i + 1) + ': ' + (error && error.message ? error.message : error));
    }
  }
  window.__mermaidDone = true;
})();
"""


def slugify(text: str, seen: dict[str, int]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "section"
    count = seen.get(slug, 0)
    seen[slug] = count + 1
    return slug if count == 0 else f"{slug}-{count}"


def front_matter(text: str) -> tuple[dict[str, str], str]:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not match:
        return {}, text
    meta = dict(line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)
    return {k.strip(): v.strip() for k, v in meta.items()}, text[match.end():]


def render_markdown(body: str) -> tuple[str, list[tuple[int, str, str]]]:
    """Markdown -> HTML.  Returns the HTML and the (level, title, id) of every h1/h2."""
    md = MarkdownIt("commonmark", {"html": True, "typographer": False}).enable("table")
    tokens = md.parse(body)
    seen: dict[str, int] = {}
    headings: list[tuple[int, str, str]] = []
    for i, token in enumerate(tokens):
        if token.type == "heading_open":
            title = tokens[i + 1].content
            anchor = slugify(title, seen)
            token.attrSet("id", anchor)
            level = int(token.tag[1])
            if level <= 2:
                headings.append((level, title, anchor))

    def fence(self, tokens, idx, options, env):
        token = tokens[idx]
        info = token.info.strip().split()[0] if token.info.strip() else ""
        if info == "mermaid":
            return f'<pre class="mermaid">{html.escape(token.content)}</pre>\n'
        return f'<pre class="code"><code>{html.escape(token.content)}</code></pre>\n'

    md.add_render_rule("fence", fence)
    out = md.renderer.render(tokens, md.options, {})
    # "— Status: ..." in a heading becomes a small line under it.
    out = re.sub(r"\s+— Status: (.*?)</h([23])>", r'<span class="status">Status: \1</span></h\2>', out)
    # A paragraph that is only an italic "Figure/Table n: ..." line is a caption.
    out = re.sub(r"<p><em>((?:Figure|Table) [^<]*?(?:<[^>]+>[^<]*?)*?)</em></p>",
                 r'<p class="caption">\1</p>', out)
    return out, headings


def toc_html(headings: list[tuple[int, str, str]], pages: dict[str, int] | None) -> str:
    rows = []
    for level, title, anchor in headings:
        clean = re.sub(r"\s+— Status:.*$", "", title)
        page = str(pages.get(anchor, "")) if pages else "000"
        rows.append(f'<li class="l{level}"><a href="#{anchor}">{html.escape(clean)}</a>'
                    f'<span class="dots"></span><span class="pg">{page}</span></li>')
    return '<nav class="toc"><h2>Contents</h2><ol>' + "".join(rows) + "</ol></nav>"


def page_html(meta: dict[str, str], toc: str, body: str, mermaid_src: str) -> str:
    cover = (
        '<section class="cover">'
        '<p class="kicker">Technical document</p>'
        f'<h1 class="title">{html.escape(meta.get("title", "Technical Document"))}</h1>'
        f'<p class="subtitle">{html.escape(meta.get("subtitle", ""))}</p>'
        '<div class="rule"></div><dl>'
        f'<dt>Version</dt><dd>{html.escape(meta.get("version", ""))}</dd>'
        f'<dt>Date</dt><dd>{html.escape(meta.get("date", ""))}</dd>'
        '<dt>Source</dt><dd>docs/TECHNICAL_DOCUMENT.md, built by tools/build_docs_pdf.py</dd>'
        '</dl></section>'
    )
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(meta.get("title", ""))}'
            f'</title><style>{CSS}</style><script src="{mermaid_src}"></script></head><body>'
            f"{cover}{toc}{body}<script>{SCRIPT}</script></body></html>")


def bookmark_pages(pdf: Path, headings: list[tuple[int, str, str]]) -> dict[str, int]:
    """Page (1-based) of every h1/h2, read from the PDF's bookmarks, matched by title in document order."""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf))
    flat: list[tuple[str, int]] = []

    def walk(items):
        for item in items:
            if isinstance(item, list):
                walk(item)
            else:
                flat.append((item.title.strip(), reader.get_destination_page_number(item) + 1))

    walk(reader.outline)
    pages: dict[str, int] = {}
    cursor = 0
    def clean(text: str) -> str:
        # Chromium's bookmark titles can repeat a wrapped heading and drop the space at the wrap, so compare
        # the first 30 characters with all whitespace removed; headings are matched in document order.
        text = re.sub(r"\s*—?\s*Status:.*$", "", text).replace("`", "")
        return re.sub(r"\s+", "", text)

    for _level, title, anchor in headings:
        norm = clean(title)[:30]
        for j in range(cursor, len(flat)):
            if clean(flat[j][0]).startswith(norm):
                pages[anchor] = flat[j][1]
                cursor = j + 1
                break
    missing = [t for _l, t, a in headings if a not in pages]
    if missing:
        raise SystemExit(f"bookmarks not found for: {missing[:5]}")
    return pages


def print_pdf(html_text: str, out: Path, channel: str, title: str) -> None:
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory() as tmp:
        page_file = Path(tmp) / "doc.html"
        page_file.write_text(html_text, encoding="utf-8")
        with sync_playwright() as p:
            browser = p.chromium.launch(channel=channel or None)
            page = browser.new_page()
            page.goto(page_file.as_uri(), wait_until="load")
            page.wait_for_function("window.__mermaidDone === true", timeout=180_000)
            errors = page.evaluate("window.__mermaidErrors")
            if errors:
                browser.close()
                raise SystemExit("mermaid failed:\n  " + "\n  ".join(errors))
            footer = ('<div style="font-size:7.5pt;color:#6b7580;width:100%;padding:0 15mm;'
                      'display:flex;justify-content:space-between;font-family:Segoe UI,Arial,sans-serif;">'
                      f'<span>{html.escape(title)}</span>'
                      '<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>')
            page.pdf(path=str(out), format="A4", print_background=True, display_header_footer=True,
                     header_template="<div></div>", footer_template=footer,
                     margin={"top": "18mm", "bottom": "17mm", "left": "15mm", "right": "15mm"},
                     outline=True, tagged=True)
            browser.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--channel", default="msedge", help='browser channel: msedge, chrome, or "" for bundled')
    parser.add_argument("--mermaid-js", default=MERMAID_CDN, help="URL or file path of mermaid.min.js")
    parser.add_argument("--keep-html", action="store_true")
    args = parser.parse_args(argv)

    meta, text = front_matter(args.input.read_text(encoding="utf-8"))
    body, headings = render_markdown(text)
    mermaid_src = args.mermaid_js
    if Path(mermaid_src).is_file():
        mermaid_src = Path(mermaid_src).resolve().as_uri()
    title = meta.get("title", "Technical document")

    pages: dict[str, int] | None = None
    for attempt in range(1, 4):
        document = page_html(meta, toc_html(headings, pages), body, mermaid_src)
        print_pdf(document, args.output, args.channel, title)
        found = bookmark_pages(args.output, headings)
        if pages == found:
            break
        pages = found
        print(f"pass {attempt}: {len(found)} headings located, last on page {max(found.values())}")
    else:
        print("warning: page numbers still moving after 3 passes", file=sys.stderr)
    if args.keep_html:
        args.output.with_suffix(".html").write_text(document, encoding="utf-8")

    from pypdf import PdfReader
    print(f"wrote {args.output} ({len(PdfReader(str(args.output)).pages)} pages, "
          f"{args.output.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
