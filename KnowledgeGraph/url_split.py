#!/usr/bin/env python3

import csv
import json
import re
import time
import unicodedata
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import requests

API_ENDPOINT = "https://warhammer40k.fandom.com/api.php"
USER_AGENT = "WH40KIndexer/1.0 (contact: you@example.com)"
REQUEST_DELAY_SECONDS = 1.0


@dataclass
class PageInput:
    page_id: str
    title: str
    fullurl: str
    categories: List[str]


@dataclass
class PageOutput:
    title: str
    fullurl: str
    categories: List[str]
    pagecontent: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe_filename(title: str, taken: set, ext: str = ".md") -> str:
    txt = unicodedata.normalize("NFKD", title)
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    txt = re.sub(r"[^A-Za-z0-9]+", "_", txt).strip("_") or "page"

    base = txt
    candidate = base
    idx = 1
    while candidate in taken:
        idx += 1
        candidate = f"{base}_{idx}"

    taken.add(candidate)
    return candidate + ext


def normalize_categories(raw) -> List[str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except:
            raw = raw.split(",")

    cleaned = []
    for c in raw:
        s = str(c).replace("Category:", "").strip()
        if s:
            cleaned.append(s)
    return list(dict.fromkeys(cleaned))  # dedupe, preserve order


# ---------------------------------------------------------------------------
# HTML cleaner
# (same logic as your n8n Extract Data node)
# ---------------------------------------------------------------------------

def _clean_text(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&nbsp;", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


BANNED_SECTIONS = {"contents", "videos", "sources", "gallery", "bibliography"}
INCLUDE_HEADINGS = True  # same as your JS

def clean_pagecontent(parse: Dict[str, Any]) -> str:
    """
    Port of the n8n Extract Data JS:
    - Unescape text
    - Strip script/style/figure/noscript
    - Keep only <h2> as section markers
    - Skip banned sections (contents, videos, sources, gallery)
    - Optionally prefix sections with "## Heading"
    """
    html = str(parse.get("text") or "")

    # Undo typical escaping
    html = (
        html.replace("\\\\", "\\")   # \\ → \
            .replace('\\"', '"')     # \" → "
            .replace("\\n", "\n")    # \n → newline
    )

    # Remove heavy/noisy blocks
    html = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.I)
    html = re.sub(r"<noscript[\s\S]*?</noscript>", "", html, flags=re.I)
    html = re.sub(r"<style[\s\S]*?</style>", "", html, flags=re.I)
    html = re.sub(r"<figure[\s\S]*?</figure>", "", html, flags=re.I)

    # Keep only <h2> tags as markers
    h_only = re.sub(r"<(?!/?h2\b)[^>]*>", "", html, flags=re.I)

    def clean_text(s: str) -> str:
        s = re.sub(r"<[^>]+>", "", s)               # strip any tags
        s = re.sub(r"&nbsp;", " ", s, flags=re.I)
        s = re.sub(r"[ \t]+", " ", s)
        s = re.sub(r"\n\s*\n\s*\n+", "\n\n", s)     # squash triple+ blank lines
        return s.strip()

    # If no <h2>, just flatten everything
    if not re.search(r"<h2\b", h_only, flags=re.I):
        return clean_text(h_only)

    # Ensure explicit Intro if there is text before the first <h2>
    if not re.match(r"^<h2\b", h_only, flags=re.I) and h_only.strip():
        h_only = "<h2>Intro</h2>" + h_only

    # Find all headers and their positions
    header_re = re.compile(r"<h2[^>]*>(.*?)</h2>", flags=re.I | re.S)
    headers = []
    for m in header_re.finditer(h_only):
        headers.append(
            {
                "title_html": m.group(1),
                "start": m.start(),
                "end": m.end(),
            }
        )

    parts: List[str] = []

    for i, h in enumerate(headers):
        start = h["end"]
        next_start = headers[i + 1]["start"] if i + 1 < len(headers) else len(h_only)

        # Inner title text
        title_html = str(h["title_html"])
        title = re.sub(r"<[^>]+>", "", title_html)
        title = re.sub(r"\[\]$", "", title).strip()
        key = title.lower()

        # Body between this </h2> and next <h2>
        body_html = h_only[start:next_start]
        body = clean_text(body_html)

        if not body:
            continue

        # Ban sections by exact match OR first word (covers "Sources and notes", etc.)
        first_word = key.split()[0] if key else ""
        if key in BANNED_SECTIONS or first_word in BANNED_SECTIONS:
            continue

        # Intro vs normal section formatting
        if INCLUDE_HEADINGS and title and key != "intro":
            parts.append(f"## {title}\n\n{body}")
        else:
            parts.append(body)

    pagecontent = "\n\n".join(parts)
    pagecontent = re.sub(r"\n{3,}", "\n\n", pagecontent).strip()
    return pagecontent


# ---------------------------------------------------------------------------
# Fandom API
# ---------------------------------------------------------------------------

def fetch_parse(title_or_page: str) -> Dict[str, Any]:
    params = {
        "action": "parse",
        "format": "json",
        "formatversion": "2",
        "redirects": "1",
        "prop": "text",
        "page": title_or_page,
    }
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(API_ENDPOINT, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()

    if "error" in data:
        raise RuntimeError(data["error"])

    return data["parse"]


# ---------------------------------------------------------------------------
# Read CSV
# ---------------------------------------------------------------------------

def read_pages(csv_path: Path) -> List[PageInput]:
    out = []

    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f, skipinitialspace=True)

        # Normalize fieldnames: strip whitespace + lowercase
        reader.fieldnames = [fn.strip().lower() for fn in reader.fieldnames]

        for row in reader:
            # Normalize each row: strip whitespace everywhere
            clean_row = {
                (k.strip().lower() if k else ""): (v.strip() if v else "")
                for k, v in row.items()
            }

            page_id = clean_row.get("page_id", "")
            title = clean_row.get("title", "")
            fullurl = clean_row.get("fullurl", "")
            categories_raw = clean_row.get("categories", "")

            categories = normalize_categories(categories_raw)

            out.append(
                PageInput(
                    page_id=page_id,
                    title=title,
                    fullurl=fullurl,
                    categories=categories,
                )
            )

    return out


# ---------------------------------------------------------------------------
# Write LightRAG document
# ---------------------------------------------------------------------------

def write_doc(out_dir: Path, page: PageOutput, taken: set):
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = safe_filename(page.title, taken, ".md")
    path = out_dir / fname

    header = ["---", f'title: "{page.title}"', "categories:"]
    for c in page.categories:
        header.append(f"  - {c}")
    header.append("---")

    content = "\n".join(header) + "\n\n" + page.pagecontent + "\n"

    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def process(csv_path: Path, out_dir: Path):
    pages = read_pages(csv_path)
    taken = set()
    total = len(pages)

    for i, p in enumerate(pages, start=1):
        print(f"[{i}/{total}] Fetching {p.title}")

        try:
            # Fetch wiki HTML
            pagekey = p.title.replace(" ", "_")
            parsed = fetch_parse(pagekey)

            cleaned = clean_pagecontent(parsed)

            out = PageOutput(
                title=p.title.strip(),
                fullurl=p.fullurl.strip(),
                categories=p.categories,
                pagecontent=cleaned,
            )

            out_path = write_doc(out_dir, out, taken)
            print(f"  → wrote {out_path}")

        except Exception as e:
            print(f"  ! ERROR {p.fullurl}: {e}")

        time.sleep(REQUEST_DELAY_SECONDS)


if __name__ == "__main__":
    process(Path("pages_kg.csv"), Path("out/pages"))
