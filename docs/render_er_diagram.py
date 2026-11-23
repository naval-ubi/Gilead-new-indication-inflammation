"""Renders the Mermaid diagrams in ER_DIAGRAM.md exactly as written into docs/ER_Diagram.pdf (one domain per page;
A3 landscape for wide diagrams, A3 portrait for tall ones; vector output, so zooming stays sharp).

Uses Mermaid's own renderer (mermaid.js) inside headless Google Chrome, so the output matches what GitHub and other
Mermaid viewers draw. Rendering is local: only the mermaid.js library file is downloaded (once, pinned version).

Usage (from the repo root; needs Google Chrome or Chromium):
    python docs/render_er_diagram.py                     # -> docs/ER_Diagram.pdf
    python docs/render_er_diagram.py --png               # also docs/ER_Diagram.png (all diagrams on one image)
    CHROME=/path/to/chrome python docs/render_er_diagram.py
"""
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from datetime import date
from pathlib import Path

MERMAID_VERSION = "11.4.1"
MERMAID_URL = f"https://cdn.jsdelivr.net/npm/mermaid@{MERMAID_VERSION}/dist/mermaid.min.js"
ROOT = Path(__file__).resolve().parent.parent
SRC, OUT_PDF, OUT_PNG = ROOT / "ER_DIAGRAM.md", ROOT / "docs" / "ER_Diagram.pdf", ROOT / "docs" / "ER_Diagram.png"
CHROME_CANDIDATES = [os.environ.get("CHROME", ""), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                     "/Applications/Chromium.app/Contents/MacOS/Chromium", "google-chrome", "chromium", "chromium-browser",
                     r"C:\Program Files\Google\Chrome\Application\chrome.exe"]


def chrome() -> str:
    for c in CHROME_CANDIDATES:
        if c and (Path(c).exists() or shutil.which(c)):
            return c
    sys.exit("Google Chrome / Chromium not found. Set CHROME=/path/to/chrome.")


def mermaid_js() -> Path:
    cache = Path(tempfile.gettempdir()) / f"mermaid-{MERMAID_VERSION}.min.js"
    if not cache.exists():
        print(f"downloading mermaid {MERMAID_VERSION} ...")
        urllib.request.urlretrieve(MERMAID_URL, cache)
    return cache


def diagrams(md: str) -> list[tuple[str, str]]:
    """(section title, mermaid source) for every ```mermaid block, titled by the nearest preceding '## ' heading."""
    out, title = [], "ER diagram"
    for m in re.finditer(r"^## ([^\n]+)$|^```mermaid\n(.*?)^```", md, re.M | re.S):
        if m.group(1):
            title = m.group(1).strip()
        else:
            out.append((title, m.group(2)))
    return out


def page(blocks, js: Path, png: bool) -> str:
    sections = "\n".join(
        f'<section><h2>{html.escape(t)}</h2><pre class="mermaid">{html.escape(src)}</pre></section>' for t, src in blocks)
    paging = "" if png else "section{break-after:page;page-break-after:always}section:last-child{break-after:auto}"
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>ER Diagram</title>
<style>
@page {{ size: A3 landscape; margin: 12mm; }}
@page wide {{ size: A3 landscape; margin: 12mm; }}
@page tall {{ size: A3 portrait; margin: 12mm; }}
body {{ font-family: Helvetica, Arial, sans-serif; margin: 0; color: #1d1e21; background: #fff; }}
header {{ border-top: 5px solid #c5203f; padding: 8px 0 4px; page: wide; }}
header h1 {{ font-size: 20px; margin: 6px 0 2px; }} header p {{ font-size: 11px; color: #6e7076; margin: 0; }}
h2 {{ font-size: 15px; color: #203662; margin: 14px 0 6px; }}
section {{ padding-bottom: 8px; page: wide; }} section.tall {{ page: tall; }} {paging}
pre.mermaid {{ margin: 0; text-align: center; }}
pre.mermaid svg {{ max-width: none !important; }}
</style></head><body>
<header><h1>Database ER Diagram</h1><p>New Indication Intelligence Platform · Backend v0.4 · {date.today():%d %b %Y}<br>
Keys: PK primary key, FK foreign key, UK unique. A solid line (||) means the parent is required; a circle (o|) means it is optional.</p></header>
{sections}
<script src="{js.as_uri()}"></script>
<script>
mermaid.initialize({{ startOnLoad: false, theme: "default", securityLevel: "strict",
  er: {{ useMaxWidth: true, layoutDirection: "TB", minEntityWidth: 110, entityPadding: 12, fontSize: 13 }} }});
// After rendering, print tall diagrams on a portrait page and wide ones on a landscape page.
mermaid.run().then(() => document.querySelectorAll("section").forEach((sec, i) => {{
  const svg = sec.querySelector("svg"), vb = svg?.viewBox?.baseVal;
  if (!vb) return;
  const tall = vb.height > vb.width * 0.9, MM = 96 / 25.4;      // CSS px per mm
  if (tall) sec.classList.add("tall");
  // printable area of an A3 page (12 mm margins), minus the section title and, on page 1, the document header
  const availW = ({'1e9' if png else '(tall ? 273 : 396)'}) * MM;
  const availH = ({'1e9' if png else '(tall ? 396 : 273) - 16 - (i === 0 ? 24 : 0)'}) * MM;
  const k = Math.min(1, availW / vb.width, availH / vb.height); // natural size; only ever scaled down
  svg.removeAttribute("width"); svg.removeAttribute("height");
  svg.style.width = (vb.width * k) + "px"; svg.style.height = (vb.height * k) + "px";
}})).then(() => {{ document.body.dataset.height = Math.ceil(document.documentElement.scrollHeight); }});
</script>
</body></html>"""


def main():
    want_png = "--png" in sys.argv
    blocks = diagrams(SRC.read_text())
    if not blocks:
        sys.exit("No ```mermaid blocks found in ER_DIAGRAM.md")
    js, exe = mermaid_js(), chrome()
    with tempfile.TemporaryDirectory() as tmp:
        pdf_html = Path(tmp) / "er.html"
        pdf_html.write_text(page(blocks, js, png=False))
        flags = ["--headless=new", "--disable-gpu", "--no-first-run", "--allow-file-access-from-files",
                 "--virtual-time-budget=20000"]
        subprocess.run([exe, *flags, "--no-pdf-header-footer", f"--print-to-pdf={OUT_PDF}", pdf_html.as_uri()],
                       check=True, capture_output=True)
        print("ok", OUT_PDF)
        if want_png:
            png_html = Path(tmp) / "er_png.html"
            png_html.write_text(page(blocks, js, png=True))
            # pass 1: measure the rendered height; pass 2: screenshot at exactly that size (no blank tail)
            dom = subprocess.run([exe, *flags, "--window-size=1800,1000", "--dump-dom", png_html.as_uri()],
                                 check=True, capture_output=True, text=True).stdout
            m = re.search(r'data-height="(\d+)"', dom)
            height = int(m.group(1)) + 24 if m else 6000
            subprocess.run([exe, *flags, "--hide-scrollbars", f"--window-size=1800,{height}", f"--screenshot={OUT_PNG}",
                            png_html.as_uri()], check=True, capture_output=True)
            print("ok", OUT_PNG)


if __name__ == "__main__":
    main()
