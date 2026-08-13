"""Retry unresolved non-hawker spots with smarter geocoding.

Strategy per miss:
1. Try Nominatim with the cleaned base name (parentheticals stripped).
2. If still no fix, web-search (DDG) for the eatery + address, geocode the
   found address via Nominatim.

Run:  python3 geocode_missed.py [--limit N] [--dry]
"""
import sys, os, re, json, time, argparse, html
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')
import requests

HDR = {"User-Agent": "HawkerFinder/1.0 (support@hawkerfinder.com)"}

def get_db():
    import psycopg2
    from db_config import PG_DSN
    return psycopg2.connect(PG_DSN)

def geocode(q):
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search",
                         params={"q": q, "format": "json", "limit": 1, "countrycodes": "sg"},
                         headers=HDR, timeout=15)
        if r.status_code == 200 and r.json():
            j = r.json()[0]
            return float(j["lat"]), float(j["lon"]), j.get("display_name", "")
    except Exception:
        pass
    return None

def clean_name(nm):
    n = re.sub(r"\(.*?\)", "", nm).strip()   # drop (Beach Road) etc
    n = re.sub(r"\s{2,}", " ", n).strip()
    return n

def ddg_address(nm):
    """Search DDG for the eatery's address."""
    try:
        r = requests.post("https://html.duckduckgo.com/html/",
                          data={"q": f"{nm} Singapore address food restaurant"},
                          headers=HDR, timeout=18)
        if r.status_code != 200:
            return None
        # find a street-looking token (blk/road/street/lane) in results
        m = re.search(r"(?:[A-Z/a-z0-9 ]{3,50})\b(?:Blk|Block|Road|Street|Lane|Avenue|Terrace|View|Place|Park|Loop)\b[^<]{0,40}", r.text)
        if m:
            return re.sub(r"<[^>]+>", "", m.group(0)).strip().replace("&", "&")
    except Exception:
        pass
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT name FROM non_hawker_spots")
    have = {r[0] for r in cur.fetchall()}
    d = json.load(open("michelin_bib_gourmand.json"))
    missing = [s["name"] for s in d["stalls"]
               if not s.get("centre") and s["name"] not in have]
    if args.limit:
        missing = missing[:args.limit]
    print(f"[Missed] {len(missing)} unresolved spots")

    placed = 0
    for i, nm in enumerate(missing, 1):
        base = clean_name(nm)
        # pass 1: cleaned name
        hit = geocode(f"{base} Singapore")
        if not hit and base != nm:
            hit = geocode(f"{nm} Singapore")
        addr = ""
        # pass 2: DDG address search -> geocode address
        if not hit:
            addr = ddg_address(nm)
            if addr:
                hit = geocode(addr)
            # pass 3: relaxed base name without 'Singapore' suffix hint
            if not hit:
                hit = geocode(base)
        if not hit:
            print(f"  [{i}] ✗ {nm}")
            time.sleep(1.2); continue
        lat, lng, disp = hit
        print(f"  [{i}] ✓ {nm} -> [{lat:.5f},{lng:.5f}]")
        if not args.dry:
            c2 = conn.cursor()
            c2.execute(
                "INSERT INTO non_hawker_spots (name, cuisine, category, lat, lng, source, address, price) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (nm, "", "Michelin Bib Gourmand", lat, lng, "michelin_bib",
                 addr or disp, "Under $45"))
            conn.commit(); c2.close()
        placed += 1
        time.sleep(1.2)
    conn.close()
    print(f"\n[Missed] resolved {placed} of {len(missing)}")

if __name__ == "__main__":
    main()