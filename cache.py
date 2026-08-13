import json
import time
import os
import psycopg2
import psycopg2.extras

from db_config import PG_DSN
CACHE_TTL = 7 * 24 * 60 * 60  # 7 days for food stalls
RATING_TTL = 24 * 60 * 60
PHOTO_TTL = 24 * 60 * 60

def get_db():
    """Get a fresh PostgreSQL connection."""
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = True
    return conn

def init_cache():
    # Tables are pre-created in PostgreSQL — just verify connection
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'")
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    print(f"[Cache] PostgreSQL ready (nas:54321/hawker_finder, {count} tables)")

# Food stalls cache
def get_cached(centre_id):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT data FROM food_stall_cache WHERE centre_id = %s AND fetched_at > %s",
            (centre_id, int(time.time()) - CACHE_TTL)
        )
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if row:
        data = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        if data.get("cache_version") != 4:
            return None
        return data
    return None

def set_cached(centre_id, centre_name, data, model_name=None):
    conn = get_db()
    try:
        data["cache_version"] = 4
        if model_name:
            data["enriched_by"] = model_name
        cur = conn.cursor()
        # Delete old then insert — simpler than multi-column ON CONFLICT
        cur.execute("DELETE FROM food_stall_cache WHERE centre_id = %s", (centre_id,))
        cur.execute(
            "INSERT INTO food_stall_cache (centre_id, centre_name, data, fetched_at) VALUES (%s, %s, %s, %s)",
            (centre_id, centre_name, json.dumps(data, ensure_ascii=False), int(time.time()))
        )
        cur.close()
    finally:
        conn.close()

# Photo metadata cache (URLs only)
def get_photos(centre_id):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT url, alt, photo_type FROM photo_cache WHERE centre_id = %s AND fetched_at > %s ORDER BY ctid",
            (centre_id, int(time.time()) - PHOTO_TTL)
        )
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    return [{"url": r[0], "alt": r[1], "type": r[2]} for r in rows] if rows else []

def set_photos(centre_id, photos):
    if not photos:
        return
    conn = get_db()
    try:
        now = int(time.time())
        cur = conn.cursor()
        for p in photos:
            cur.execute("""
                INSERT INTO photo_cache (centre_id, url, alt, photo_type, fetched_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (centre_id, url) DO UPDATE SET alt=%s, photo_type=%s, fetched_at=%s
            """, (centre_id, p["url"], p.get("alt", ""), p.get("type", "article"), now,
                  p.get("alt", ""), p.get("type", "article"), now))
        cur.close()
    finally:
        conn.close()

# Photo BLOB storage
def get_photo_data(centre_id, idx):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT data, alt, content_type FROM photo_data WHERE centre_id = %s AND idx = %s AND fetched_at > %s",
            (centre_id, idx, int(time.time()) - PHOTO_TTL)
        )
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if row and row[0]:
        return {"data": bytes(row[0]), "alt": row[1], "content_type": row[2]}
    return None

def set_photo_data(centre_id, idx, data, alt="", content_type="image/jpeg"):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO photo_data (centre_id, idx, data, alt, content_type, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (centre_id, idx) DO UPDATE SET data=%s, alt=%s, content_type=%s, fetched_at=%s
        """, (centre_id, idx, psycopg2.Binary(data), alt, content_type, int(time.time()),
              psycopg2.Binary(data), alt, content_type, int(time.time())))
        cur.close()
    finally:
        conn.close()

def delete_photo_data(centre_id):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM photo_data WHERE centre_id = %s", (centre_id,))
        cur.close()
    finally:
        conn.close()

# Ratings cache
def get_rating_cached(centre_id):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT rating, reviews FROM ratings_cache WHERE centre_id = %s AND fetched_at > %s",
            (centre_id, int(time.time()) - RATING_TTL)
        )
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if row:
        return {"rating": row[0], "reviews": row[1]}
    return None

def set_rating_cached(centre_id, centre_name, rating, reviews):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM ratings_cache WHERE centre_id = %s", (centre_id,))
        cur.execute(
            "INSERT INTO ratings_cache (centre_id, centre_name, rating, reviews, fetched_at) VALUES (%s, %s, %s, %s, %s)",
            (centre_id, centre_name, rating, reviews or 0, int(time.time()))
        )
        cur.close()
    finally:
        conn.close()

def clear_expired():
    conn = get_db()
    try:
        now = int(time.time())
        cur = conn.cursor()
        cur.execute("DELETE FROM food_stall_cache WHERE fetched_at < %s", (now - CACHE_TTL,))
        cur.execute("DELETE FROM ratings_cache WHERE fetched_at < %s", (now - RATING_TTL,))
        cur.execute("DELETE FROM photo_cache WHERE fetched_at < %s", (now - PHOTO_TTL,))
        cur.execute("DELETE FROM photo_data WHERE fetched_at < %s", (now - PHOTO_TTL,))
        deleted = cur.rowcount
        cur.close()
    finally:
        conn.close()
    if deleted:
        print(f"[Cache] Cleared {deleted} expired entries")

def clear_all_for_centre(centre_id):
    conn = get_db()
    try:
        cur = conn.cursor()
        for t in ['food_stall_cache', 'photo_cache', 'photo_data', 'ratings_cache']:
            cur.execute(f"DELETE FROM {t} WHERE centre_id = %s", (centre_id,))
        cur.close()
    finally:
        conn.close()
