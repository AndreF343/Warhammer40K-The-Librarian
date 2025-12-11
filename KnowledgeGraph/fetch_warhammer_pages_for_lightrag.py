#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Fetch Warhammer 40K Fandom pages by page_id, politely, and write one Markdown file per page
for LightRAG ingestion.

v2 changes:
- Removed dependency on `markdownify` to avoid "name 'unicode' is not defined" issues in some envs.
- Implemented a lightweight HTML→Markdown converter using BeautifulSoup only.
- Still strips TOC/edit/navboxes/footnote superscripts; keeps headings, lists, paragraphs.
- Flattens links to anchor text.

Requirements:
  pip install requests beautifulsoup4 lxml pandas python-slugify

Etiquette:
  https://www.mediawiki.org/wiki/API:Etiquette
  https://www.mediawiki.org/wiki/API:Action_API
"""

import os
import re
import time
import json
import random
import pandas as pd
import requests
from bs4 import BeautifulSoup, NavigableString, Tag
from slugify import slugify

# -------------------------
# CONFIG
# -------------------------
CSV_PATH = "pages_kg.csv"                  # your 7k canonical pages CSV (page_id,title,url)
OUT_DIR = "out/pages"                       # output directory for .md files
FAIL_LOG = "out/failures.csv"               # csv of failures
API_ENDPOINT = "https://warhammer40k.fandom.com/api.php"
USER_AGENT = "WH40K-KG-Ingest/1.0 (+your_email_or_site_here)"
REQUESTS_PER_SEC = 1.0                      # ~1 rps is polite
MAX_RETRIES = 6
INITIAL_BACKOFF = 2.0                       # seconds
TIMEOUT = 30                                # seconds

USE_PARSE = True  # use action=parse (HTML); alternative is wikitext via action=query

# -------------------------
# HELPERS
# -------------------------

def sleep_politely(last_time, rps=1.0):
    min_gap = 1.0 / max(rps, 0.01)
    elapsed = time.time() - last_time
    if elapsed < min_gap:
        time.sleep(min_gap - elapsed)
    return time.time()

def flatten_links(soup: BeautifulSoup):
    """Replace <a> tags with their anchor text only."""
    for a in soup.find_all("a"):
        a.replace_with(a.get_text(" ", strip=True))

def remove_noise(soup: BeautifulSoup):
    """Remove typical wiki noise elements."""
    for sel in [
        ".toc", ".mw-editsection", ".navbox", ".vertical-navbox", ".catlinks",
        "sup.reference", ".metadata"
    ]:
        for el in soup.select(sel):
            el.decompose()
    for bad in soup(["script", "style"]):
        bad.decompose()

def node_to_markdown(node) -> str:
    """Recursively convert an element to Markdown-ish text."""
    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag):
        return ""

    name = node.name.lower()

    # Headings
    if name in ["h1", "h2", "h3", "h4", "h5", "h6"]:
        level = int(name[1])
        prefix = "#" * level
        text = "".join(node_to_markdown(c) for c in node.children).strip()
        return f"{prefix} {text}\n\n" if text else ""

    # Paragraphs
    if name == "p":
        text = "".join(node_to_markdown(c) for c in node.children).strip()
        return f"{text}\n\n" if text else ""

    # Line breaks
    if name == "br":
        return "\n"

    # Lists
    if name in ["ul", "ol"]:
        items = []
        for i, li in enumerate(node.find_all("li", recursive=False), start=1):
            item_md = "".join(node_to_markdown(c) for c in li.children).strip()
            if not item_md:
                continue
            if name == "ul":
                items.append(f"- {item_md}")
            else:
                items.append(f"{i}. {item_md}")
        return "\n".join(items) + "\n\n" if items else ""

    # List items handled above; if encountered alone, just render text
    if name == "li":
        return "".join(node_to_markdown(c) for c in node.children)

    # Tables: render as plain text rows (simple fallback)
    if name == "table":
        rows = []
        for tr in node.find_all("tr", recursive=False):
            cols = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"], recursive=False)]
            if cols:
                rows.append(" | ".join(cols))
        if rows:
            return "\n".join(rows) + "\n\n"
        return ""

    # Blockquotes
    if name == "blockquote":
        text = "".join(node_to_markdown(c) for c in node.children).strip()
        if not text:
            return ""
        quoted = "\n".join([f"> {line}" if line.strip() else ">" for line in text.splitlines()])
        return f"{quoted}\n\n"

    # Images: keep alt text only
    if name == "img":
        alt = node.get("alt", "").strip()
        return alt

    # Default: recurse into children
    return "".join(node_to_markdown(c) for c in node.children)

def html_to_markdown(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    remove_noise(soup)
    flatten_links(soup)

    # Convert top-level flow content
    parts = []
    for child in soup.body.children if soup.body else soup.children:
        md = node_to_markdown(child)
        parts.append(md)
    md = "".join(parts)

    # Tidy whitespace
    lines = [ln.rstrip() for ln in md.splitlines()]
    md = "\n".join(lines).strip() + "\n"
    return md

def write_markdown(page_id: int, title: str, url: str, categories, sections, body_md: str):
    os.makedirs(OUT_DIR, exist_ok=True)
    slug = slugify(title or f"page-{page_id}")[:100]
    path = os.path.join(OUT_DIR, f"{page_id}--{slug}.md")

    # Normalize categories to list[str]
    cats = categories or []
    if isinstance(cats, dict) and "categories" in cats:
        cats = cats["categories"]
    if isinstance(cats, list):
        cats_text = ", ".join(sorted({ (c.get('*') if isinstance(c, dict) and '*' in c else str(c)).strip() for c in cats if c }))
    else:
        cats_text = str(cats).strip() if cats else ""

    header = {
        "page_id": int(page_id),
        "title": title or "",
        "url": url or "",
        "categories": [c.get("*") if isinstance(c, dict) and "*" in c else c for c in (cats or [])],
        "sections": sections or []
    }
    frontmatter = "---\n" + json.dumps(header, ensure_ascii=False, indent=2) + "\n---\n\n"

    # Prelude lines per your preference
    body_prelude = []
    if title:
        body_prelude.append(f"**Title:** {title}")
    if cats_text:
        body_prelude.append(f"**Categories:** {cats_text}")
    if body_prelude:
        body_prelude.append("")
    prelude = "\n".join(body_prelude)

    with open(path, "w", encoding="utf-8") as f:
        f.write(frontmatter)
        f.write(f"# {title}\n\n" if title else "")
        f.write(prelude)
        f.write(body_md)

    return path

def fetch_with_backoff(session, params):
    backoff = INITIAL_BACKOFF
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(API_ENDPOINT, params=params, timeout=TIMEOUT)
            if r.status_code == 429 or 500 <= r.status_code < 600:
                time.sleep(backoff); backoff *= 2; continue

            data = r.json()
            if isinstance(data, dict) and "error" in data and data["error"].get("code") == "maxlag":
                time.sleep(backoff + random.uniform(0, 1.0))
                backoff = min(backoff * 2, 60)
                continue

            return data
        except requests.RequestException:
            time.sleep(backoff); backoff = min(backoff * 2, 60)
        except Exception:
            time.sleep(backoff); backoff = min(backoff * 2, 60)
            if attempt == MAX_RETRIES:
                raise
    raise RuntimeError("Max retries exceeded")

def coerce_int(s):
    try:
        return int(str(s).strip().strip('"').strip("'"))
    except Exception:
        m = re.search(r'\d+', str(s))
        if m:
            return int(m.group(0))
        raise

# -------------------------
# MAIN
# -------------------------

def main():
    os.makedirs(os.path.dirname(FAIL_LOG), exist_ok=True)
    fail_rows = []

    raw = pd.read_csv(CSV_PATH, header=None)
    if raw.shape[1] >= 3:
        raw = raw.iloc[:, :3]
        raw.columns = ["page_id", "title", "url"]
    else:
        raise ValueError("Expected CSV with at least 3 columns: page_id,title,url")

    # Trim whitespace/quotes
    for c in raw.columns:
        if raw[c].dtype == object:
            raw[c] = raw[c].astype(str).str.strip().str.strip('"').str.strip("'")

    raw["page_id"] = raw["page_id"].apply(coerce_int)
    df = raw.drop_duplicates(subset=["page_id"]).reset_index(drop=True)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    last_req_time = 0.0

    for idx, row in df.iterrows():
        page_id = int(row["page_id"])
        title = row.get("title", "")
        url = row.get("url", "")

        last_req_time = sleep_politely(last_req_time, rps=REQUESTS_PER_SEC)

        try:
            if USE_PARSE:
                params = {
                    "action": "parse",
                    "format": "json",
                    "formatversion": "2",
                    "pageid": page_id,
                    "prop": "text|sections|categories",
                    "disablelimitreport": "1",
                    "maxlag": "5",
                }
                data = fetch_with_backoff(session, params)

                if "error" in data:
                    raise RuntimeError(f"API error: {data['error']}")

                parsed = data.get("parse") or {}
                html = (parsed.get("text") or "")
                sections = parsed.get("sections") or []
                categories = parsed.get("categories") or []

                if not html:
                    raise RuntimeError("Empty HTML content")

                body_md = html_to_markdown(html)
                out_path = write_markdown(page_id, title, url, categories, sections, body_md)

            else:
                params = {
                    "action": "query",
                    "format": "json",
                    "formatversion": "2",
                    "prop": "revisions|categories",
                    "pageids": page_id,
                    "rvslots": "main",
                    "rvprop": "content",
                    "maxlag": "5",
                }
                data = fetch_with_backoff(session, params)
                if "error" in data:
                    raise RuntimeError(f"API error: {data['error']}")

                pages = (data.get("query") or {}).get("pages") or []
                if not pages or "revisions" not in pages[0]:
                    raise RuntimeError("No revisions/content found")

                rev = pages[0]["revisions"][0]
                wikitext = rev.get("slots", {}).get("main", {}).get("content", "")
                categories = pages[0].get("categories", [])
                body_md = (wikitext or "").strip() + "\n"
                out_path = write_markdown(page_id, title, url, categories, [], body_md)

            print(f"[{idx+1}/{len(df)}] wrote {out_path}")

        except Exception as e:
            print(f"[{idx+1}/{len(df)}] FAIL page_id={page_id}: {e}")
            fail_rows.append(
                {"page_id": page_id, "title": title, "url": url, "error": str(e)}
            )

    if fail_rows:
        pd.DataFrame(fail_rows).to_csv(FAIL_LOG, index=False)
        print(f"Wrote failures to {FAIL_LOG}")

if __name__ == "__main__":
    main()
