#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Debug-hardened version for: "name 'unicode' is not defined" issues.
- No markdownify import anywhere.
- Prints script path, Python version, and key package versions at start.
- Captures full tracebacks into failures.csv.
- Adds a --head N option to test the first N rows.
"""

import os, sys, re, time, json, random, argparse, traceback, importlib
import pandas as pd
import requests
from bs4 import BeautifulSoup, NavigableString, Tag
from slugify import slugify

CSV_PATH = "pages_kg.csv"
OUT_DIR = "out/pages"
FAIL_LOG = "out/failures.csv"
API_ENDPOINT = "https://warhammer40k.fandom.com/api.php"
USER_AGENT = "WH40K-KG-Ingest/1.0 (+your_email_or_site_here)"
REQUESTS_PER_SEC = 1.0
MAX_RETRIES = 6
INITIAL_BACKOFF = 2.0
TIMEOUT = 30
USE_PARSE = True

def env_banner():
    def ver(pkg):
        try:
            m = importlib.import_module(pkg)
            return getattr(m, "__version__", "unknown")
        except Exception:
            return "not installed"
    banner = {
        "script": os.path.abspath(__file__),
        "python": sys.version,
        "platform": sys.platform,
        "versions": {
            "requests": ver("requests"),
            "bs4": ver("bs4"),
            "lxml": ver("lxml"),
            "pandas": ver("pandas"),
            "python-slugify": ver("slugify"),
            "markdownify_present": ver("markdownify")  # just to show if it exists
        }
    }
    print(json.dumps(banner, indent=2))

def sleep_politely(last_time, rps=1.0):
    min_gap = 1.0 / max(rps, 0.01)
    elapsed = time.time() - last_time
    if elapsed < min_gap:
        time.sleep(min_gap - elapsed)
    return time.time()

def flatten_links(soup: BeautifulSoup):
    for a in soup.find_all("a"):
        a.replace_with(a.get_text(" ", strip=True))

def remove_noise(soup: BeautifulSoup):
    for sel in [".toc", ".mw-editsection", ".navbox", ".vertical-navbox",
                ".catlinks", "sup.reference", ".metadata"]:
        for el in soup.select(sel):
            el.decompose()
    for bad in soup(["script", "style"]):
        bad.decompose()

def node_to_markdown(node) -> str:
    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag):
        return ""
    name = node.name.lower()
    if name in ["h1","h2","h3","h4","h5","h6"]:
        level = int(name[1])
        text = "".join(node_to_markdown(c) for c in node.children).strip()
        return f'{"#"*level} {text}\n\n' if text else ""
    if name == "p":
        text = "".join(node_to_markdown(c) for c in node.children).strip()
        return f"{text}\n\n" if text else ""
    if name in ["ul","ol"]:
        items=[]
        for i, li in enumerate(node.find_all("li", recursive=False), start=1):
            t="".join(node_to_markdown(c) for c in li.children).strip()
            if not t: continue
            items.append(f"- {t}" if name=="ul" else f"{i}. {t}")
        return "\n".join(items) + "\n\n" if items else ""
    if name == "table":
        rows=[]
        for tr in node.find_all("tr", recursive=False):
            cols=[c.get_text(" ", strip=True) for c in tr.find_all(["th","td"], recursive=False)]
            if cols: rows.append(" | ".join(cols))
        return "\n".join(rows) + "\n\n" if rows else ""
    if name == "blockquote":
        text="".join(node_to_markdown(c) for c in node.children).strip()
        return ("\n".join([f"> {ln}" if ln.strip() else ">" for ln in text.splitlines()]) + "\n\n") if text else ""
    if name == "img":
        return node.get("alt","").strip()
    return "".join(node_to_markdown(c) for c in node.children)

def html_to_markdown(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    remove_noise(soup)
    flatten_links(soup)
    parts=[]
    root = soup.body if soup.body else soup
    for child in getattr(root, "children", []):
        parts.append(node_to_markdown(child))
    md="".join(parts)
    md="\n".join(ln.rstrip() for ln in md.splitlines()).strip() + "\n"
    return md

def write_markdown(page_id: int, title: str, url: str, categories, sections, body_md: str):
    os.makedirs(OUT_DIR, exist_ok=True)
    slug = slugify(title or f"page-{page_id}")[:100]
    path = os.path.join(OUT_DIR, f"{page_id}--{slug}.md")

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

    prelude_lines = []
    if title: prelude_lines.append(f"**Title:** {title}")
    if cats_text: prelude_lines.append(f"**Categories:** {cats_text}")
    if prelude_lines: prelude_lines.append("")
    prelude = "\n".join(prelude_lines)

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
            # quick sanity to force JSON decoding early for debugging
            return r.json()
        except Exception as e:
            # Capture and rethrow with traceback so we see the real origin
            tb = traceback.format_exc()
            raise RuntimeError(f"HTTP/JSON failure on attempt {attempt}: {e}\n{tb}")
    raise RuntimeError("Max retries exceeded")

def coerce_int(s):
    try:
        return int(str(s).strip().strip('"').strip("'"))
    except Exception:
        m = re.search(r'\d+', str(s))
        if m:
            return int(m.group(0))
        raise

def main():
    env_banner()

    parser = argparse.ArgumentParser()
    parser.add_argument("--head", type=int, default=0, help="Only process first N rows (for debugging)")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(FAIL_LOG), exist_ok=True)
    fails = []

    raw = pd.read_csv(CSV_PATH, header=None)
    if raw.shape[1] >= 3:
        raw = raw.iloc[:, :3]
        raw.columns = ["page_id", "title", "url"]
    else:
        raise ValueError("Expected CSV with at least 3 columns: page_id,title,url")

    for c in raw.columns:
        if raw[c].dtype == object:
            raw[c] = raw[c].astype(str).str.strip().str.strip('"').str.strip("'")

    raw["page_id"] = raw["page_id"].apply(coerce_int)
    df = raw.drop_duplicates(subset=["page_id"]).reset_index(drop=True)

    if args.head and args.head > 0:
        df = df.head(args.head).copy()

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
            tb = traceback.format_exc()
            print(f"[{idx+1}/{len(df)}] FAIL page_id={page_id}: {e}")
            fails.append({"page_id": page_id, "title": title, "url": url, "error": str(e), "traceback": tb})

    if fails:
        pd.DataFrame(fails).to_csv(FAIL_LOG, index=False)
        print(f"Wrote failures to {FAIL_LOG}")

if __name__ == "__main__":
    main()
