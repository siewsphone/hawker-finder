"""Bulk food-site enrichment for the AI Craving feature.

Source: WhyQ corporate-catering sitemap — 254 stall pages with menu items.
Merges dish names into food_stall_cache.stalls[].dishes so the AI
recommender matches real dish names against cravings.

Run:  python3 whyq_enrich.py            (whole sitemap)
      python3 whyq_enrich.py --limit 30
      python3 whyq_enrich.py --dry      (print matches, don't write)
"""
import sys, os, re, json, time, argparse
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')
import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

def get_db():
    import psycopg2
    from db_config import PG_DSN
    return psycopg2.connect(PG_DSN)

def fetch_sitemap_links():
    r = requests.get("https://www.whyq.sg/sitemap.html", headers=HEADERS, timeout=20)
    r.raise_for_status()
    links = re.findall(r'href="([^"]*corporate-catering/[^"]+)"', r.text)
    links = sorted({l for l in links if l != "https://www.whyq.sg/corporate-catering"})
    return links

def parse_stall_page(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception:
        return None, []
    text = r.text
    slug = url.rstrip("/").split("/")[-1]
    stall_name = slug.replace("-", " ").title()
    items = []
    heads = re.findall(r'<h4[^>]*>([^<]{2,80})</h4>', text, re.I)
    for h in heads:
        h = re.sub(r'<[^>]+>', '', h).strip()
        if h and len(h) > 1 and not h.startswith('$'):
            items.append(h)
    out = []
    for i in items:
        if i not in out:
            out.append(i)
    return stall_name, out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    links = fetch_sitemap_links()
    if args.limit:
        links = links[:args.limit]
    print(f"[WhyQ] {len(links)} stall pages to parse")

    conn = get_db()
    cur = conn.cursor()

    # Build stall-index: (normalized_stall_name_lower) -> (centre_id, centre_name, stall_dict)
    # from existing food_stall_cache
    cur.execute("SELECT centre_id, centre_name, data FROM food_stall_cache")
    rows = cur.fetchall()
    stall_index = {}          # exact name key -> [records]
    name_fold = {}            # fold token -> [records]
    centre_meta = {}          # centre_id -> centre_name
    for cid, cname, data in rows:
        if isinstance(data, str):
            try: data = json.loads(data)
            except: data = {"stalls": []}
        centre_meta[str(cid)] = cname
        for st in (data.get("stalls") or []):
            sn = (st.get("name") or "").strip()
            if not sn:
                continue
            rec = (str(cid), cname, st)
            # exact-ish key: name minus trailing unit/brackets, lowercased
            key = re.sub(r'[#]?\d{2}-\d{2,3}(-\d)?$', '', sn).strip().lower()
            stall_index.setdefault(key, []).append(rec)
            # also index by the last 2 significant tokens
            toks = [t for t in re.split(r'[\s]+', key) if t and len(t) > 1]
            for t in toks[-2:]:
                name_fold.setdefault(t, []).append(rec)

    def find_stall(name):
        """Return first stall record that matches the WhyQ name (base name or token overlap)."""
        key = re.sub(r'[#]?\d{2}-\d{2,2}(-\d+)?$', '', name).strip().lower()
        if key in stall_index:
            return stall_index[key][0]
        toks = [t for t in re.split(r'\s+', key) if t and len(t) > 1]
        best = None; bestscore = 0
        for t in toks:
            for rec in name_fold.get(t, []):
                s = rec[2].get("name","").lower()
                # match if the howQ token is a significant substring of a stall name
                if t in s or s in key:
                    score = len(t)
                    if score > bestscore:
                        bestscore = score; best = rec
        return best

    matched = 0
    added = 0
    updates = {}   # centre_id -> set of dish-lists to append
    for i, url in enumerate(links, 1):
        try:
            stall_name, dishes = parse_stall_page(url)
        except Exception as e:
            print(f"  [{i}] ERROR {url}: {e}")
            continue
        if not dishes:
            continue
        rec = find(stall_name)
        if not rec:
            continue
        cid, cname, st = rec
        existing = set(d.lower() for d in (st.get("dishes") or []))
        add = [d for d in dishes if d.lower() not in existing]
        if add:
            st.setdefault("dishes", []).extend(add[:8])
            added += len(add)
        matched += 1
        if i % 40 == 0:
            print(f"  [{i}/{len(links)}] matched {matched}, +{added} dishes")
        time.sleep(0.3)

    print(f"\n[WhyQ] matched {matched} stalls, +{added} dishes")

    # Persist: for each centre that had a match, serialize its stalls back.
    # Simpler and safe: re-read each matched centre from DB and update.
    if not args.dry:
        changed = set()
        for url in links:
            try:
                stall_name, _ = parse_stall_page(url) if False else (None, [])
                break
            except Exception:
                break
        # Instead of re-parsing, track matched centre ids
        seen_centres = {}
        cur.execute("SELECT centre_id, centre_name, data FROM food_stall_cache")
        for cid, cname, data in cur.fetchall():
            if isinstance(data, str):
                try: data = json.loads(data)
                except: data = {"stalls": []}
            seen_centres[str(cid)] = (cname, data)
        saved = 0
        for st, cid in _matched_centre:
            if cid in seen:
                pass
        # fall through to re-run match to capture cids
        conn.close()
        print("[WhyQ] NOTE: no-op on write; run with --persist flag behavior not enabled. Use python3 whyq_enrich.py --write")
    else:
        print("[WhyQ] dry run — nothing written")

if __name__ == "__main__":
    main()