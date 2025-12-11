#!/usr/bin/env python3
"""
Fetch all page URLs from a MediaWiki site (Warhammer 40K).
Defaults to Fandom: https://warhammer40k.fandom.com/api.php
Optionally switch to Lexicanum: https://wh40k.lexicanum.com/mediawiki/api.php

Usage:
  python get_wh40k_urls.py --out urls.csv
  python get_wh40k_urls.py --lexicanum --out urls_lex.csv
  python get_wh40k_urls.py --namespace 0 --out mainspace.csv
  python get_wh40k_urls.py --limit 500 --out fast.csv
"""

import csv
import time
import argparse
from typing import Dict, Any, Iterator, List

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

FANDOM_API = "https://warhammer40k.fandom.com/api.php"
LEXICANUM_API = "https://wh40k.lexicanum.com/mediawiki/api.php"

def make_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"])
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.headers.update({
        "User-Agent": "WH40K-URL-Crawler/1.0 (contact: example@example.com)"
    })
    return s

def page_generator(api: str, gaplimit: int, namespace: int) -> Iterator[Dict[str, Any]]:
    """
    Yields pages with info including fullurl.
    """
    session = make_session()
    params: Dict[str, Any] = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "generator": "allpages",
        "gaplimit": gaplimit,
        "prop": "info",
        "inprop": "url",           # <- includes fullurl in the response
        "gapnamespace": namespace, # 0 = main namespace
    }

    cont: Dict[str, Any] = {}
    while True:
        try:
            resp = session.get(api, params={**params, **cont}, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            # simple backoff and retry next loop
            print(f"[warn] request failed: {e}; sleeping then continuing...")
            time.sleep(1.5)
            continue

        if "query" in data and "pages" in data["query"]:
            for p in data["query"]["pages"]:
                # p includes: pageid, ns, title, contentmodel, pagelanguage, touched, lastrevid, length, fullurl, ...
                yield p

        if "continue" in data:
            cont = data["continue"]
            # be gentle—Fandom/Lexicanum have rate limits
            time.sleep(0.1)
        else:
            break

def collect_all_urls(api: str, gaplimit: int, namespace: int) -> List[Dict[str, Any]]:
    rows = []
    for p in page_generator(api, gaplimit, namespace):
        rows.append({
            "pageid": p.get("pageid"),
            "ns": p.get("ns"),
            "title": p.get("title"),
            "fullurl": p.get("fullurl"),
            "lastrevid": p.get("lastrevid"),
            "length": p.get("length"),
        })
    return rows

def main():
    ap = argparse.ArgumentParser(description="Fetch all page URLs from a MediaWiki site (Warhammer 40K).")
    ap.add_argument("--lexicanum", action="store_true",
                    help="Use Lexicanum (wh40k.lexicanum.com) instead of Fandom.")
    ap.add_argument("--api", type=str, default=None,
                    help="Override the API endpoint entirely (advanced).")
    ap.add_argument("--out", type=str, default="wh40k_urls.csv",
                    help="Output CSV filename (default: wh40k_urls.csv).")
    ap.add_argument("--limit", type=int, default=200,
                    help="Per-request page batch size (gaplimit). Typical max is 200 for non-bot clients.")
    ap.add_argument("--namespace", type=int, default=0,
                    help="Namespace filter (0=main articles, 14=Category, etc.).")
    args = ap.parse_args()

    api = args.api or (LEXICANUM_API if args.lexicanum else FANDOM_API)
    print(f"[info] using API: {api}")
    print(f"[info] namespace: {args.namespace} | gaplimit: {args.limit}")

    rows = collect_all_urls(api=api, gaplimit=args.limit, namespace=args.namespace)
    print(f"[info] collected {len(rows)} pages; writing CSV: {args.out}")

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["pageid", "ns", "title", "fullurl", "lastrevid", "length"])
        writer.writeheader()
        writer.writerows(rows)

    print("[done]")

if __name__ == "__main__":
    main()
