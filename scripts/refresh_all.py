#!/usr/bin/env python3
"""
Daily refresh: clear cache + re-enrich all hawker centres.
Loads centres live from GEOJSON (no drift), scrapes fresh Michelin data.
Run standalone — no Flask app needed.
"""
import sys, os, json, time, subprocess

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from cache import init_cache, clear_all_for_centre, clear_expired
from llm_enricher import enrich_centre as llm_enrich_centre
from scrape_michelin import main as scrape_michelin_main


def load_centres():
    """Load centre list from live GEOJSON — stays in sync with app.py."""
    geojson_path = os.path.join(BASE, "hawker_centres.geojson")
    with open(geojson_path) as f:
        raw = json.load(f)

    centres = []
    for feat in raw["features"]:
        p = feat["properties"]
        centres.append({
            "id": p["OBJECTID"],
            "name": p.get("NAME", "Unknown"),
        })
    return centres


def main():
    init_cache()
    clear_expired()

    # Step 1: Scrape Michelin Bib Gourmand data (live)
    print("[DailyRefresh] Scraping Michelin Bib Gourmand list...")
    try:
        scrape_script = os.path.join(BASE, "scrape_michelin.py")
        subprocess.run([sys.executable, scrape_script], cwd=BASE, timeout=120)
    except Exception as e:
        print(f"[DailyRefresh] Michelin scrape error (non-fatal): {e}")

    # Step 2: Load all centres from GEOJSON
    centres = load_centres()
    print(f"[DailyRefresh] Starting refresh for {len(centres)} centres")
    print(f"[DailyRefresh] Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    results = {"ok": 0, "failed": 0}

    for c in centres:
        cid = c["id"]
        name = c["name"]
        try:
            # Check if cache exists and is still fresh (7-day TTL)
            from cache import get_cached as check_cached
            existing = check_cached(cid)
            if existing:
                results["ok"] += 1
                stalls = len(existing.get("stalls", []))
                photos = len(existing.get("photos", []))
                print(f"  ⏩ {name}: {stalls} stalls, {photos} photos (cached, skipped)")
                continue

            # Don't clear existing cache — only enrich if missing/expired
            enriched = llm_enrich_centre(name, cid)

            if enriched and enriched.get("stalls"):
                results["ok"] += 1
                stalls = len(enriched["stalls"])
                photos = len(enriched.get("photos", []))
                print(f"  ✅ {name}: {stalls} stalls, {photos} photos")
            else:
                results["failed"] += 1
                print(f"  ⚠️ {name}: enrichment returned no stalls")

            time.sleep(2)  # Rate-limit between centres

        except Exception as e:
            results["failed"] += 1
            print(f"  ❌ {name}: {e}")

    print(f"\n[DailyRefresh] Complete: {results['ok']} OK, {results['failed']} failed")
    if results["failed"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
