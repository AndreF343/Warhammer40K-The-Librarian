#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Convert pages.csv (page_id, title, fullurl, categories, pagecontent)
into one Markdown file per row for LightRAG ingestion.

No CLI args, everything hardcoded.
"""

import os
import re
import json
import pandas as pd

IN_CSV = "pages_kg_pc.csv"               # <-- your CSV with: page_id,title,fullurl,categories,pagecontent
OUT_DIR = "lightrag_docs"          # output directory
LIMIT = 0                          # 0 = all rows; set >0 to test on first N rows


def slugify(text, maxlen=96):
    text = str(text or "").lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text[:maxlen] or "page"


def parse_categories(raw):
    if raw is None:
        return []
    raw = str(raw).strip()
    if not raw:
        return []
    # try JSON first (e.g. '["C","Badab War"]')
    try:
        val = json.loads(raw)
        if isinstance(val, list):
            return [str(x) for x in val]
    except Exception:
        pass
    # fallback: simple comma split
    raw = raw.strip("[]")
    return [p.strip().strip('"').strip("'") for p in raw.split(",") if p.strip()]


def yaml_header(meta: dict) -> str:
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {item}")
        else:
            lines.append(f'{k}: "{v}"')
    lines.append("---\n")
    return "\n".join(lines)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    df = pd.read_csv(IN_CSV)

    required = ["page_id", "title", "fullurl", "categories", "pagecontent"]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"CSV missing required column: {col}")

    if LIMIT > 0:
        df = df.head(LIMIT)

    total = len(df)
    written = 0

    for idx, row in df.iterrows():
        page_id = int(row["page_id"])
        title = str(row["title"]).strip()
        url = str(row["fullurl"]).strip()
        content = str(row["pagecontent"])  # already cleaned, no extra editing
        cats = parse_categories(row["categories"])

        meta = {
            "title": title,
            "categories": cats,
        }

        # filename: 12345-roboute-guilliman.md
        fname = f"{page_id}-{slugify(title)}.md"
        out_path = os.path.join(OUT_DIR, fname)

        with open(out_path, "w", encoding="utf-8") as f:
            f.write(yaml_header(meta))
            f.write(f"# {title}\n\n")
            f.write(content.rstrip() + "\n")

        written += 1
        print(f"[{idx+1}/{total}] wrote {out_path}")

    print(f"Done. Wrote {written} markdown files to {OUT_DIR}.")


if __name__ == "__main__":
    main()
