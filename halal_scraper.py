#!/usr/bin/env python3
"""Scrape MUIS halal-certified hawker stalls from halalboleh.com into PG.

halalboleh.com mirrors the official MUIS halal registry
(halal.muis.gov.sg/halal/establishments). We scrape only Type=Hawker
rows (906+ stalls), extract unit numbers, and store them for matching
against SFA stall data in Hawker Finder.

Table: halal_stalls (in hawker_finder DB)
  id serial PK
  muis_id int UNIQUE        -- halalboleh.com/muis-certified/<id>
  name text
  type text                 -- 'Hawker'
  scheme text               -- Eating Establishment / Food Preparation Area
  address text
  postal text               -- 6-digit postal extracted from address
  unit text                 -- e.g. '#01-110' extracted from address
  status text               -- Active
  url text
  fetched_at float
"""
import re
import sys
import time
import json
import psycopg2
import requests

BASE = "https://halalboleh.com/muis-certified"
from db_config import DSN

POSTAL_RE = re.compile(r"\b(\d{6})\b")
UNIT_RE = re.compile(r"(#?\d{2}-\d{2,4}[A-Z]?|STALL\s*\d+|Stall\s*\d+)", re.IGNORECASE)


def clean(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def extract_rows(html):
    """Return list of dicts from the establishments table."""
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL)
    out = []
    for row in rows:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        if len(tds) < 5:
            continue
        # td[0]=Status, td[1]=Establishment name (link), td[2]=Type, td[3]=Scheme, td[4]=Address, td[5]=Valid Until
        name_link = re.search(r'href="(https://halalboleh.com/muis-certified/(\d+))"[^>]*>(.*?)</a>', tds[1], re.DOTALL)
        muis_id = int(name_link.group(2)) if name_link else None
        url = name_link.group(1) if name_link else ""
        name = clean(name_link.group(3)) if name_link else clean(tds[1])
        typ = clean(tds[2])
        scheme = clean(tds[3])
        address = clean(tds[4])
        status = clean(tds[0])
        valid_until = clean(tds[5]) if len(tds) > 5 else ""
        # Valid until often empty ("—") — the 5th column in table is "Valid Until"
        if not muis_id:
            continue
        postal_m = POSTAL_RE.search(address)
        unit_m = UNIT_RE.search(address)
        out.append({
            "muis_id": muis_id,
            "name": name,
            "type": typ,
            "scheme": scheme,
            "address": address,
            "postal": postal_m.group(1) if postal_m else None,
            "unit": (unit_m.group(1).upper() if unit_m else None),
            "status": status,
            "url": url,
        })
    return out


def main():
    conn = psycopg2.connect(**DSN)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS halal_stalls (
            id SERIAL PRIMARY KEY,
            muis_id INT UNIQUE,
            name TEXT,
            type TEXT,
            scheme TEXT,
            address TEXT,
            postal TEXT,
            unit TEXT,
            status TEXT,
            url TEXT,
            fetched_at FLOAT
        )
    """)
    conn.commit()

    total = 0
    page = 1
    while True:
        url = f"{BASE}?type=Hawker&page={page}"
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"})
        except Exception as e:
            print(f"page {page} error: {e}", flush=True)
            break
        if r.status_code != 200:
            print(f"page {page} HTTP {r.status_code}", flush=True)
            break
        rows = extract_rows(r.text)
        if not rows:
            print(f"page {page}: no rows — stopping", flush=True)
            break
        for row in rows:
            cur.execute("""
                INSERT INTO halal_stalls (muis_id, name, type, scheme, address, postal, unit, status, url, fetched_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (muis_id) DO UPDATE SET
                  name=EXCLUDED.name, type=EXCLUDED.type, scheme=EXCLUDED.scheme,
                  address=EXCLUDED.address, postal=EXCLUDED.postal, unit=EXCLUDED.unit,
                  status=EXCLUDED.status, fetched_at=EXCLUDED.fetched_at
            """, (row["muis_id"], row["name"], row["type"], row["scheme"], row["address"],
                  row["postal"], row["unit"], row["status"], row["url"], time.time()))
        total += len(rows)
        conn.commit()
        print(f"page {page}: {len(rows)} rows (total {total})", flush=True)
        # Stop at last page: check "Page X of N"
        m = re.search(r"Page\s+\d+\s+of\s+(\d+)", r.text)
        if m:
            last = int(m.group(1))
            if page >= last:
                print(f"reached last page {last}", flush=True)
                break
        page += 1
        time.sleep(0.4)

    cur.close()
    conn.close()
    print(f"DONE: {total} halal stalls stored")


if __name__ == "__main__":
    main()
