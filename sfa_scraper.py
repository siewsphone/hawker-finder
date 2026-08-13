#!/usr/bin/env python3
"""Scrape SFA Track Records for every hawker centre → PostgreSQL.

Strategy per centre (multi-fallback):
  1. Search by ADDRESSPOSTALCODE → filter stalls whose address ends with that postal
  2. Search by ADDRESSBUILDINGNAME → filter by postal OR building-name containment
  3. Search by ADDRESSSTREETNAME → filter by postal
  4. Search by NAME tokens → keep stalls whose address contains a long-enough token
Records with 0 stalls are recorded too (so daily refresh knows it's covered).

Output table: sfa_track_records (in hawker_finder DB)
  centre_id   TEXT   (NEA OBJECTID)
  centre_name TEXT
  postal      TEXT
  sfa_data    JSONB  (full array of stall records)
  fetched_at  TIMESTAMPTZ
  PRIMARY KEY (centre_id)
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import psycopg2
import psycopg2.extras

from db_config import PG_DSN
API = "https://www.sfa.gov.sg/api/TrackRecord/GetTrackRecord"
GEOJSON = Path(__file__).parent / "hawker_centres.geojson"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.sfa.gov.sg/tools-and-resources/track-records",
    "Accept": "*/*",
}
DELAY = 0.4  # polite rate limit (SFA allows this easily)


def sfa_search(address="", postal=""):
    params = {
        "postalCode": postal,
        "establishmentAddress": address,
        "licenceNumber": "",
        "businessName": "",
        "licenseeName": "",
        "typeOfFoodBussiness": "",
        "isShowLicenceSuspended": "false",
        "grades": "",
    }
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def ends_with_postal(addr, postal):
    return addr.rstrip().endswith("Singapore " + postal)


def tokenize(s):
    return {t for t in s.replace("-", " ").upper().split() if len(t) >= 4}


def fetch_centre(centre):
    """Return list of stall records for one centre (may be empty)."""
    postal = centre["postal"]
    building = centre["building"]
    street = centre["street"]
    name = centre["name"]

    strategies = []
    if postal:
        strategies.append(("postal", lambda: sfa_search(postal=postal)))
    if building:
        strategies.append(("building", lambda: sfa_search(address=building)))
    if street:
        strategies.append(("street", lambda: sfa_search(address=street)))
    if name:
        strategies.append(("name", lambda: sfa_search(address=name)))

    best = []
    used = "none"
    for label, fn in strategies:
        try:
            data = fn().get("data", [])
        except Exception as e:
            print(f"    ⚠️ {label} search failed: {e}", flush=True)
            continue
        time.sleep(DELAY)

        if label == "postal":
            # postal search: results may span multiple postal codes → keep exact match
            matched = [d for d in data if ends_with_postal(d["establishmentAddress"], postal)]
            if matched:
                best, used = matched, "postal"
                break
        elif label in ("building", "street", "name"):
            # filter by postal first
            matched = [d for d in data if ends_with_postal(d["establishmentAddress"], postal)]
            if matched:
                best, used = matched, f"{label}(postal)"
                break
            # else: filter by building/name token containment (>=2 tokens)
            if label == "building" and building:
                btoks = tokenize(building)
                matched = [d for d in data
                           if len(btoks & tokenize(d["establishmentAddress"].split(",")[-1].split("Singapore")[0])) >= 2
                           or len(btoks & tokenize(d["establishmentAddress"])) >= 2]
                if matched:
                    best, used = matched, "building(tokens)"
                    break
            if label == "name" and name:
                ntoks = tokenize(name)
                matched = [d for d in data if len(ntoks & tokenize(d["establishmentAddress"])) >= 2]
                if matched:
                    best, used = matched, "name(tokens)"
                    break

    # dedupe by licenceNumber
    seen, uniq = set(), []
    for d in best:
        key = d.get("licenceNumber") or d.get("refNo")
        if key and key not in seen:
            seen.add(key)
            uniq.append(d)
    return uniq, used


def main():
    limit = None
    if len(sys.argv) > 2 and sys.argv[1] == "--limit":
        limit = int(sys.argv[2])
    elif len(sys.argv) > 1 and sys.argv[1] == "--centre":
        # e.g. --centre "TIONG BAHRU"
        only_name = sys.argv[2].upper()

    d = json.loads(GEOJSON.read_text())
    centres = []
    for f in d["features"]:
        p = f["properties"]
        centres.append({
            "objectid": str(p.get("OBJECTID", "")),
            "name": p.get("NAME", "") or "",
            "building": p.get("ADDRESSBUILDINGNAME", "") or "",
            "street": p.get("ADDRESSSTREETNAME", "") or "",
            "block": p.get("ADDRESSBLOCKHOUSENUMBER", "") or "",
            "postal": str(p.get("ADDRESSPOSTALCODE", "") or ""),
        })
    centres.sort(key=lambda c: c["name"])

    if limit:
        centres = centres[:limit]
    if "only_name" in dir() and only_name:
        centres = [c for c in centres if only_name in c["name"].upper() or only_name in c["building"].upper()]

    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sfa_track_records (
            centre_id   TEXT PRIMARY KEY,
            centre_name TEXT NOT NULL,
            postal      TEXT,
            stall_count INTEGER NOT NULL DEFAULT 0,
            sfa_data    JSONB NOT NULL DEFAULT '[]',
            fetch_strategy TEXT,
            fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    total = 0
    print(f"📡 Scraping SFA Track Records for {len(centres)} centres...\n", flush=True)
    for i, c in enumerate(centres, 1):
        stalls, used = fetch_centre(c)
        cur.execute("""
            INSERT INTO sfa_track_records (centre_id, centre_name, postal, stall_count, sfa_data, fetch_strategy, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (centre_id) DO UPDATE SET
                centre_name = EXCLUDED.centre_name,
                postal = EXCLUDED.postal,
                stall_count = EXCLUDED.stall_count,
                sfa_data = EXCLUDED.sfa_data,
                fetch_strategy = EXCLUDED.fetch_strategy,
                fetched_at = now()
        """, (c["objectid"], c["name"], c["postal"], len(stalls),
              json.dumps(stalls, ensure_ascii=False), used))
        total += len(stalls)
        print(f"[{i}/{len(centres)}] {c['name'][:45]:47} {len(stalls):4} stalls ({used})", flush=True)

    cur.close()
    conn.close()
    print(f"\n✅ Done. {total} stall records across {len(centres)} centres.")


if __name__ == "__main__":
    main()
