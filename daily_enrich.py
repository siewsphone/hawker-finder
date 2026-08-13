#!/usr/bin/env python3
"""Daily cron: enrich hawker centres with LLM-extracted stalls + photos.

PostgreSQL version (fixed 2026-08-07: old file used SQLite ? placeholders
and row_factory against the PG-backed cache.py — silently failed to enrich,
stalling coverage at ~19/129 centres).

Runs daily. Processes centres in batches to manage token costs.
Skips centres enriched within the last 24 hours.
"""
import sys, os, json, time, random

# Ensure we're in the right dir
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')

from cache import get_db, set_cached, set_photos, get_photos, CACHE_TTL
from llm_enricher import enrich_centre

def get_stale_centres(limit=30):
    """Get centres whose food-stall cache is expired or missing.
    Uses a 24h window for cron rotation (not the 7-day CACHE_TTL)."""
    conn = get_db()
    now = int(time.time())
    cutoff = now - 86400  # 24 hours

    # Get all centres from the main dataset
    with open('hawker_centres.geojson') as f:
        raw = json.load(f)

    centres = []
    for feat in raw['features']:
        p = feat['properties']
        cid = p['OBJECTID']
        name = p.get('NAME', 'Unknown')
        centres.append((cid, name))

    # Check which are stale (PG: use cursor, %s placeholder)
    stale = []
    for cid, name in centres:
        cur = conn.cursor()
        cur.execute(
            "SELECT fetched_at FROM food_stall_cache WHERE centre_id = %s", (str(cid),)
        )
        row = cur.fetchone()
        cur.close()
        if not row or row[0] < cutoff:
            stale.append((cid, name))

    conn.close()

    # Shuffle for fairness
    random.shuffle(stale)
    return stale[:limit]

def main():
    print(f"[Cron] Starting daily enrichment at {time.strftime('%Y-%m-%d %H:%M:%S')}")

    limit = int(os.environ.get("DAILY_ENRICH_LIMIT", 20))
    stale = get_stale_centres(limit=limit)
    print(f"[Cron] {len(stale)} centres need refresh (processing up to {limit})")

    success = 0
    errors = 0

    for i, (cid, name) in enumerate(stale, 1):
        print(f"\n[Cron] [{i}/{len(stale)}] Centre {cid}: {name}")
        try:
            result = enrich_centre(name, cid)
            if result and isinstance(result, dict):
                api_result = {
                    "centre": name,
                    "stalls": result.get("stalls", []),
                    "stall_article": result.get("article"),
                    "llm_enriched": True,
                    "reviews": [],
                    "michelin_stalls": [],
                    "photo_article": None
                }
                set_cached(cid, name, api_result)

                raw_photos = result.get("photos", [])
                if raw_photos:
                    if isinstance(raw_photos[0], str):
                        photo_dicts = [{"url": u, "alt": "", "type": "local"} for u in raw_photos]
                    else:
                        photo_dicts = raw_photos
                    set_photos(cid, photo_dicts)
                    print(f"[Cron]  Stored {len(photo_dicts)} photos")

                success += 1
                print(f"[Cron]  ✓ Done")
            else:
                reason = str(result) if result else "empty result"
                print(f"[Cron]  ✗ Invalid result: {reason}")
                errors += 1
        except Exception as e:
            print(f"[Cron]  ✗ Error: {e}")
            errors += 1

        if i < len(stale):
            time.sleep(1)

    print(f"\n[Cron] Complete: {success} succeeded, {errors} failed out of {len(stale)} attempted")
    print(f"[Cron] Next run: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() + 86400))}")

if __name__ == "__main__":
    main()
