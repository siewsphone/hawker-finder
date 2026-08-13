#!/usr/bin/env python3
"""
Scrape Michelin Bib Gourmand Singapore + Best-of Hawker Centres.
Only from guide.michelin.com. Store detail page URLs for photo extraction.
"""
import os, sys, json, re, time
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "michelin_bib_gourmand.json")

def fetch_html(url):
    """Fetch a URL and return HTML text."""
    try:
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=30)
        if r.status_code == 200:
            return r.text
        print(f"[Michelin] HTTP error {r.status_code} for {url}")
    except Exception as e:
        print(f"[Michelin] Request error: {e}")
    return ""

def html_to_markdown(html):
    """Convert HTML to simple markdown text for parsing."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)

def parse_restaurants(markdown):
    """Extract restaurant names and URLs from markdown H3 links"""
    items = re.findall(r'### \[([^\]]+)\]\(([^)]+)\)', markdown)
    return items


def parse_best_of_with_centres(markdown):
    """Extract {centre_name, stall_name, url} from Best-of page.
    
    The Best-of page structure:
        # **1. Centre Name**
        ...
        ##### [Stall Name](url)
        ...
        # **2. Centre Name**
        ...
    """
    results = []
    current_centre = None
    in_hawker_section = False

    for line in markdown.split('\n'):
        stripped = line.strip()

        # Detect centre heading: "# **N\. Centre Name**" (might have escaped period)
        centre_match = re.match(r'^#\s+\*\*\d+\\?\.\s+(.+?)\*\*$', stripped)
        if centre_match:
            current_centre = centre_match.group(1).strip()
            in_hawker_section = True
            continue

        # Detect end of hawker centres (Find our other best-of guides)
        if stripped.startswith('## Find our other best-of guides'):
            in_hawker_section = False
            continue

        # Detect stall: "##### [Stall Name](url)"
        if in_hawker_section and current_centre:
            stall_match = re.match(r'^#####\s+\[([^\]]+)\]\(([^)]+)\)', stripped)
            if stall_match:
                name = stall_match.group(1).strip()
                link = stall_match.group(2).strip()
                if not link.startswith('http'):
                    link = f'https://guide.michelin.com{link}'
                results.append({
                    'name': name,
                    'url': link,
                    'centre': current_centre,
                    'source': 'best_of'
                })

    return results

def scrape_bib_gourmand():
    """Scrape the Bib Gourmand listing (2 pages)."""
    results = []
    for page in [1, 2]:
        url = f"https://guide.michelin.com/sg/en/singapore-region/singapore/restaurants/bib-gourmand/page/{page}"
        print(f"[Michelin] Bib Gourmand page {page}...")
        html = fetch_html(url)
        md = html_to_markdown(html) if html else ""
        if md:
            items = parse_restaurants(md)
            print(f"[Michelin]  Found {len(items)}")
            for name, link in items:
                if not link.startswith("http"):
                    link = f"https://guide.michelin.com{link}"
                results.append({"name": name.strip(), "url": link, "source": "bib_gourmand"})
            time.sleep(1)
    return results

def scrape_best_of():
    """Scrape the Best-of Hawker Centres page with centre→stall context."""
    url = "https://guide.michelin.com/sg/en/best-of/the-best-hawker-centers-in-singapore-and-what-to-order"
    print(f"[Michelin] Best-of page...")
    html = fetch_html(url)
    md = html_to_markdown(html) if html else ""
    if md:
        results = parse_best_of_with_centres(md)
        print(f"[Michelin]  Found {len(results)} stalls across centres")
        centres_found = set(r['centre'] for r in results)
        for c in sorted(centres_found):
            stalls = [r['name'] for r in results if r['centre'] == c]
            print(f"    {c}: {len(stalls)} stalls")
        return results
    return []

def main():
    print(f"[Michelin] Scraping guide.michelin.com...\n")
    
    # 1. Bib Gourmand (primary source)
    bib = scrape_bib_gourmand()
    
    # 2. Best-of Hawker Centres (cross-reference)
    best_of = scrape_best_of()
    
    # Merge: Best-of entries with centre information enrich Bib Gourmand entries
    merged = {}
    # First pass: add Bib Gourmand entries
    for r in bib:
        key = r["name"].lower().strip()
        merged[key] = r  # No centre field from Bib Gourmand
    # Second pass: Best-of entries either add (new) or merge centre into existing
    for r in best_of:
        key = r["name"].lower().strip()
        if key in merged:
            # Merge: carry centre from best_of into bib entry
            if r.get("centre"):
                merged[key]["centre"] = r["centre"]
        else:
            merged[key] = r
    merged = list(merged.values())
    
    print(f"\n[Michelin] Total unique: {len(merged)} (Bib Gourmand: {len(bib)}, Best-of: {len(best_of)})")
    
    # Preserve centre mappings from OLD file if it exists, for entries without centre
    old_centre_map = {}
    if os.path.exists(OUTPUT):
        try:
            with open(OUTPUT) as f:
                old = json.load(f)
            for s in old.get("stalls", []):
                if s.get("centre"):
                    key = s["name"].lower().strip()
                    if key not in old_centre_map:
                        old_centre_map[key] = s["centre"]
        except Exception:
            pass
    
    # Build output with centre field preserved from best_of or old file
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    output = {
        "banner": "Michelin Bib Gourmand — affordable Michelin-recognised food under S$45\nAlso includes Best-of Hawker Centres recommendations",
        "stalls": [],
        "total": 0,
        "scraped_at": now,
        "sources": [
            "guide.michelin.com/sg/en/singapore-region/singapore/restaurants/bib-gourmand",
            "guide.michelin.com/sg/en/best-of/the-best-hawker-centers-in-singapore-and-what-to-order"
        ]
    }
    
    for r in merged:
        entry = {
            "name": r["name"],
            "url": r["url"],
            "detail_url": r["url"],
            "source": r["source"],
            "scraped_at": now,
            "centre": r.get("centre", ""),
        }
        # Fallback: if no centre from scrape, try old file
        if not entry["centre"]:
            key = r["name"].lower().strip()
            entry["centre"] = old_centre_map.get(key, "")
        output["stalls"].append(entry)
    
    output["total"] = len(output["stalls"])
    
    with open(OUTPUT, "w") as f:
        json.dump(output, f, indent=2)
    
    print(f"[Michelin] Saved {len(merged)} to {OUTPUT}")
    for r in merged[:8]:
        print(f"  [{r['source'][:10]}] {r['name']}")
    
    return merged

if __name__ == "__main__":
    main()
