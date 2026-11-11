"""Build the CareFlow documentation as one PDF.

    python scripts/build_pdf.py                       # -> CareFlow-Documentation.pdf in the repository root
    python scripts/build_pdf.py --out C:/tmp/docs.pdf
    python scripts/build_pdf.py --exclude manual-test-plan.md   # leave a page out (e.g. before sharing outside)

How it works (all free tools):
  1. Refreshes the generated reference pages (scripts/gen_docs.py).
  2. Collects every page in mkdocs.yml's navigation that has real content, in the same order. Placeholder pages
     (still to be written) are skipped, so the PDF grows as the documentation is completed. The README becomes
     the opening "Overview" chapter.
  3. Builds them into one printable page with MkDocs Material and mkdocs-print-site-plugin (cover page, contents,
     numbered chapters, rendered Mermaid diagrams).
  4. Prints that page to PDF with Microsoft Edge or Google Chrome in headless mode, then adds page numbers.

Requires: pip install -r docs/requirements.txt  (mkdocs-print-site-plugin, pypdf, reportlab are listed there)
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import functools
import http.server
import io
import re
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
PLACEHOLDER = '!!! note "Placeholder"'
BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/microsoft-edge",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


class _Loader(yaml.SafeLoader):
    """Reads mkdocs.yml without resolving its !ENV and !!python/name tags."""


_Loader.add_multi_constructor("!", lambda loader, suffix, node: None)
_Loader.add_multi_constructor("tag:yaml.org,2002:python/name:", lambda loader, suffix, node: None)


def is_content(path: Path) -> bool:
    return path.exists() and PLACEHOLDER not in path.read_text(encoding="utf-8")


def filter_nav(nav: list, exclude: set[str]) -> tuple[list, list[str], list[str]]:
    """Keep only pages with real content. Returns (nav, included pages, skipped placeholders)."""
    out, included, skipped = [], [], []
    for item in nav:
        (title, value), = item.items()
        if isinstance(value, list):
            sub, inc, skp = filter_nav(value, exclude)
            included += inc
            skipped += skp
            if sub:
                out.append({title: sub})
        elif value in exclude or value == "index.md":
            continue  # the site home page describes the website; the README overview opens the PDF instead
        elif is_content(DOCS / value):
            out.append({title: value})
            included.append(value)
        else:
            skipped.append(value)
    return out, included, skipped


def order_for_reading(nav: list) -> list:
    """Readers want the guides before the generated reference tables; the website order puts reference first."""
    def rename(item: dict) -> dict:
        (title, value), = item.items()
        if isinstance(value, list):
            return {("Guides" if title == "Current guides" else title): [rename(v) for v in value]}
        return {title.replace(" (current)", ""): value}

    reference = [i for i in nav if "Reference" in i]
    rest = [i for i in nav if "Reference" not in i]
    return [rename(i) for i in rest + reference]


def readme_as_overview() -> str:
    """The README, with links rewritten for a page that lives inside docs/."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    text = re.sub(r"\]\(docs/([^)]+)\)", r"](\1)", text)  # docs/x.md -> x.md

    def outside(m: re.Match) -> str:  # links to repository files outside docs/ can't work in a PDF
        label, target = m.group(1), m.group(2)
        return f"{label} (`{target}`)" if label.strip("`") != target else f"`{target}`"

    text = re.sub(r"\[([^\]]+)\]\(((?:backend|web|evals|deploy|scripts)/[^)]*|render\.yaml|docker-compose[^)]*)\)", outside, text)
    return text


def build_site(work: Path, nav: list, base: dict) -> Path:
    shutil.copytree(DOCS, work / "docs")
    (work / "docs" / "overview.md").write_text(readme_as_overview(), encoding="utf-8")
    # The plugin prints its own "Page N" in the corner; add_page_numbers() stamps "Page N of M" instead (none on the cover).
    (work / "docs" / "pdf.css").write_text("@page { size: A4; @bottom-right { content: none; } }\n", encoding="utf-8")
    # A diagram of 20+ tables can't be read at A4 width; the PDF keeps the column tables and points to the website.
    schema = work / "docs" / "reference" / "generated" / "database-schema.md"
    if schema.exists():
        text = schema.read_text(encoding="utf-8")
        note = ('!!! note "Diagram"\n    The relationship diagram of all tables is too large for a printed page. View it on the '
                "documentation website (Reference > Database schema). The tables below list every column and reference.\n\n")
        schema.write_text(re.sub(r"## Diagram\n.*?(?=## Tables)", lambda m: note, text, flags=re.S), encoding="utf-8")
    theme = dict(base.get("theme", {}))
    theme["palette"] = {"scheme": "default", "primary": "teal", "accent": "teal"}  # print in the light theme
    theme["features"] = [f for f in theme.get("features", []) if not f.startswith("navigation.instant")]
    stamp = dt.date.today().isoformat()
    config = {
        "site_name": "CareFlow documentation",
        "site_description": "Administrative discharge clearance for hospitals",
        "copyright": f"Generated on {stamp} from the CareFlow repository. Not a certified medical device.",
        "docs_dir": "docs",
        "site_dir": "site",
        "theme": theme,
        "markdown_extensions": base.get("markdown_extensions", []),
        "extra_css": ["pdf.css"],
        "plugins": [{"print-site": {
            "add_to_navigation": False, "print_page_title": "CareFlow documentation", "add_print_site_banner": False,
            "add_table_of_contents": True, "toc_title": "Contents", "toc_depth": 2, "add_full_urls": False,
            "enumerate_headings": True, "enumerate_headings_depth": 2, "enumerate_figures": True, "add_cover_page": True,
        }}],
        "nav": [{"Overview": "overview.md"}] + order_for_reading(nav),
    }
    # mkdocs.yml uses a !!python/name tag for the Mermaid fence; write it back as that tag.
    text = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
    text = text.replace("format: null", "format: !!python/name:pymdownx.superfences.fence_code_format")
    (work / "mkdocs.yml").write_text(text, encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "mkdocs", "build", "--quiet"], cwd=work, capture_output=True, text=True,
                            env={**__import__("os").environ, "NO_MKDOCS_2_WARNING": "1"})
    if result.returncode != 0:
        sys.exit(f"mkdocs build failed:\n{result.stdout}\n{result.stderr}")
    page = work / "site" / "print_page" / "index.html"
    if not page.exists():
        sys.exit("The print page wasn't produced; is mkdocs-print-site-plugin installed?")
    return work / "site"


@contextlib.contextmanager
def serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    handler.log_message = lambda *a, **k: None
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            yield f"http://127.0.0.1:{httpd.server_address[1]}"
        finally:
            httpd.shutdown()


def print_pdf(url: str, out: Path, work: Path) -> None:
    browser = next((b for b in BROWSERS if Path(b).exists()), None) or shutil.which("msedge") or shutil.which("google-chrome")
    if not browser:
        sys.exit("No Edge or Chrome found to print the PDF.")
    raw = work / "raw.pdf"
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run", f"--user-data-dir={work / 'browser'}",
           "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw", "--virtual-time-budget=45000",
           f"--print-to-pdf={raw}", url]
    subprocess.run(cmd, capture_output=True, timeout=300)
    if not raw.exists() or raw.stat().st_size < 10_000:
        sys.exit("The browser didn't produce a PDF.")
    add_page_numbers(raw, out)


def add_page_numbers(src: Path, out: Path) -> None:
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.colors import Color
    from reportlab.pdfgen import canvas

    writer = PdfWriter(clone_from=str(src))  # pages must belong to the writer before stamping (pypdf 7)
    total = len(writer.pages)
    for i, page in enumerate(writer.pages, start=1):
        w, h = float(page.mediabox.width), float(page.mediabox.height)
        if i > 1:  # no number on the cover
            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=(w, h))
            c.setFont("Helvetica", 8)
            c.setFillColor(Color(0.42, 0.45, 0.5))
            c.drawRightString(w - 36, 20, f"Page {i} of {total}")
            c.drawString(36, 20, "CareFlow documentation")
            c.save()
            buf.seek(0)
            page.merge_page(PdfReader(buf).pages[0])
    writer.add_metadata({"/Title": "CareFlow documentation", "/Subject": "Administrative discharge clearance for hospitals",
                         "/Creator": "scripts/build_pdf.py (MkDocs Material + print-site)"})
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        writer.write(f)


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the CareFlow documentation as one PDF.")
    ap.add_argument("--out", type=Path, default=ROOT / "CareFlow-Documentation.pdf")
    ap.add_argument("--exclude", action="append", default=[], help="a docs page to leave out, e.g. manual-test-plan.md")
    ap.add_argument("--skip-generate", action="store_true", help="don't refresh the generated reference pages first")
    args = ap.parse_args()

    if not args.skip_generate:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_docs.py")], check=True)
    base = yaml.load((ROOT / "mkdocs.yml").read_text(encoding="utf-8"), Loader=_Loader)
    nav, included, skipped = filter_nav(base["nav"], set(args.exclude))
    print(f"Including {len(included) + 1} pages (README overview + {len(included)}); skipping {len(skipped)} placeholders.")
    with tempfile.TemporaryDirectory(prefix="careflow-pdf-") as tmp:
        work = Path(tmp)
        site = build_site(work, nav, base)
        with serve(site) as url:
            print_pdf(f"{url}/print_page/", args.out, work)
    from pypdf import PdfReader

    print(f"Wrote {args.out} ({len(PdfReader(str(args.out)).pages)} pages, {args.out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
