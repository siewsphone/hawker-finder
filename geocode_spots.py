"""Geocode NON-hawker eataries (restaurants, coffee-shop stalls not in a
registered hawker centre) and add them as map-markable spots.

Source: Michelin Bib Gourmand + full Bib Gourmand list (97) — those entries
with no matching hawker centre live outside NEA centres. Geocode them with
OSM Nominatim (free, no key) and store in PG `food_spots` for map markers.

Run:  python3 geocode_spots.py --dry
      python3 geocode_spots.py          (write)
"""
import sys, os, json, time, argparse
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')
import requests

HEADERS = {"User-Agent": "HawkerFinderEnrichment/1.0 (support@hawkerfinder.com)"}

def get_db():
    import psycopg2
    from db_config import PG_DSN
    return psycopg2.connect(PG_DSN)

def ensure_table(conn):
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS non_hawker_spots (
        id serial PRIMARY KEY,
        name text,
        cuisine text,
        category text,
        lat double precision,
        lng double precision,
        address text,
        price text,
        source text,
        detail text,
        created_at timestamptz DEFAULT now()
    )""")
    conn.commit()
    cur.close()

def geocode(name, timeout=12):
    """Return (lat, lng, display) or (None, None, None)."""
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search",
                         params={"q": f"{name} Singapore", "format": "json", "limit": 1},
                         headers=HEADERS, timeout=timeout)
        if r.status_code == 200 and r.json():
            j = r.json()[0]
            return float(j.get("lat")), float(j.get("lon")), j.get("display_name", "")
    except Exception:
        pass
    return None, None, None

def load_bibster():
    d = json.load(open("michelin_bib_gourmand.json"))
    spots = []
    for s in d.get("stalls", []):
        if s.get("centre"):           # already in a hawker centre -> skip (map shows centre)
            continue
        nm = (s.get("name") or "").strip()
        if not nm:
            continue
        spots.append({
            "name": nm, "source": "michelin_bib",
            "price": "Under $45",
        })
    return spots

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    conn = get_db()
    ensure_table(conn)
    spots = load_bibster()
    if args.limit:
        spots = spots[:args.limit]
    print(f"[Spots] {len(spots)} non-hawker eateries to geocode")

    placed = 0
    for i, sp in enumerate(spots, 1):
        lat, lng, addr = geocode(sp["name"])
        if lat is None:
            print(f"  [{i}] ✗ {sp['name']} (no fix)")
            time.sleep(1.2)
            continue
        print(f"  [{i}] ✓ {sp['name']} -> {lat:.5f},{lng:.5f}")
        placed += 1
        if not args.dry:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO non_hawker_spots (name, cuisine, category, lat, lng, source, address, price) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (sp["name"], sp.get("cuisine",""), "Michelin Bib Gourmand", lat, lng,
                 sp["source"], addr, sp.get("price","")))
            conn.commit()
            cur.close()
        time.sleep(1.2)   # Nominatim = 1 req/sec
    conn.close()
    print(f"\n[Spots] done. {placed} places geocoded into non_hawker_spots")

if __name__ == "__main__":
    main()