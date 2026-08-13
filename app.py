import json, math, os, re, requests, time
from flask import Flask, render_template, request, jsonify
from bs4 import BeautifulSoup
from stall_scraper import find_best_article
from cache import init_cache, get_cached, set_cached, clear_expired, get_rating_cached, set_rating_cached, get_photos, set_photos, get_db, get_photo_data, clear_all_for_centre
from llm_enricher import enrich_centre as llm_enrich_centre
from urllib.request import urlopen
from timeout_scraper import fetch_timeout_feed

# ─── SFA Track Records (PostgreSQL) ─────────────────────
import psycopg2 as _pg2
from db_config import PG_DSN as _SFA_DSN
_SFA_CACHE = {}
_SFA_CACHE_TTL = 3600  # 1 hour

def _norm_unit(u):
    """Normalize stall unit for matching: '#01-97' -> '1-97', '01-097' -> '1-97'."""
    if not u:
        return None
    u = u.upper().replace('#', '').strip()
    m = re.match(r'(\d+)[- ](\d+[A-Z]?)', u)
    if m:
        n2 = m.group(2)
        return f'{int(m.group(1))}-{int(n2)}' if n2.isdigit() else f'{int(m.group(1))}-{n2}'
    return u


_VEG_EXCLUDE_RE = re.compile(r"supplier|provision|trading|enterprise|frozen|import", re.IGNORECASE)

_VEG_STALL_RE = re.compile(
    r"\b(?:veg(?:etarian|e)?|vegetable)\b|素|斋|纯素|菜饭|mixed veg|veggie",
    re.IGNORECASE)

def _is_veg_stall(text):
    """True if a stall name/business type indicates vegetarian (excl. suppliers)."""
    if not text:
        return False
    if _VEG_EXCLUDE_RE.search(text):
        return False
    return bool(_VEG_STALL_RE.search(text))


def get_halal_units(postal):
    """Return dict {normalized_unit: halal_name} for a centre's postal code
    from the MUIS halal registry (halal_stalls table, scraped from halalboleh.com)."""
    if not postal:
        return {}
    try:
        conn = _pg2.connect(_SFA_DSN)
        cur = conn.cursor()
        cur.execute("SELECT name, unit FROM halal_stalls WHERE postal = %s AND unit IS NOT NULL", (postal,))
        rows = cur.fetchall()
        cur.close(); conn.close()
        out = {}
        for name, unit in rows:
            nu = _norm_unit(unit)
            if nu:
                out[nu] = name
        return out
    except Exception as e:
        print(f"[Halal] Error: {e}")
        return {}


def get_sfa_centre_data(centre_name_or_id):
    """Return SFA stall records for a hawker centre.
    Lookup by centre_id (int) or centre_name (str) from PG sfa_track_records.
    Returns dict: {sfa_stalls: [...], grade_counts: {A: n, B: n, ...}, total: n}
    Cached in-memory for 1 hour (SFA data updates at most daily).
    """
    cache_key = str(centre_name_or_id)
    now = time.time()
    if cache_key in _SFA_CACHE and now - _SFA_CACHE[cache_key]["ts"] < _SFA_CACHE_TTL:
        return _SFA_CACHE[cache_key]["data"]
    try:
        conn = _pg2.connect(_SFA_DSN)
        cur = conn.cursor()
        if isinstance(centre_name_or_id, int):
            cur.execute("SELECT sfa_data, centre_name FROM sfa_track_records WHERE centre_id = %s", (str(centre_name_or_id),))
        else:
            cur.execute("SELECT sfa_data, centre_name FROM sfa_track_records WHERE centre_name = %s", (centre_name_or_id,))
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row:
            return {"sfa_stalls": [], "grade_counts": {}, "total": 0}
        sfa_data, _ = row
        if not sfa_data:
            return {"sfa_stalls": [], "grade_counts": {}, "total": 0}
        stalls = []
        grades = {}
        # Map SFA grades: A/B/C/New/Silver/Gold/Bronze (SAFE)
        grade_order = {"Gold": 0, "Silver": 1, "Bronze": 2, "A": 3, "B": 4, "New": 5, "C": 6}
        for rec in sfa_data:
            g = (rec.get("grades") or "").strip()
            bn = (rec.get("businessName") or "").strip()
            addr = (rec.get("establishmentAddress") or "").strip()
            uf = (rec.get("typeOfFoodBussiness") or "").strip()
            # Extract unit number from address
            unit = ""
            import re
            m = re.search(r"#(\d{2}-\d+)", addr)
            if m:
                unit = "#" + m.group(1)
            stalls.append({
                "name": bn if bn != "NA" else "",
                "licensee": (rec.get("licenseeName") or "").strip() if bn == "NA" else "",
                "grade": g,
                "unit": unit,
                "type": uf,
                "addr": addr,
                "veg": _is_veg_stall(bn + " " + uf),
            })
            grades[g] = grades.get(g, 0) + 1
        # Sort by grade (best first), then by name
        stalls.sort(key=lambda s: (grade_order.get(s["grade"], 99), s["name"] or s["licensee"]))
        # Tag halal stalls (MUIS registry, matched by postal + unit)
        postal = None
        for rec in sfa_data:
            m = re.search(r"\b(\d{6})\b", (rec.get("establishmentAddress") or ""))
            if m:
                postal = m.group(1)
                break
        halal_map = get_halal_units(postal) if postal else {}
        if halal_map:
            for s in stalls:
                nu = _norm_unit(s["unit"])
                if nu and nu in halal_map:
                    s["halal"] = True
                    s["halal_name"] = halal_map[nu]
                    # SFA sometimes has no business name ("NA") — use the MUIS certified name
                    if not s["name"]:
                        s["name"] = halal_map[nu]
        return {"sfa_stalls": stalls, "grade_counts": grades, "total": len(stalls)}
    except Exception as e:
        print(f"[SFA] Error: {e}")
        return {"sfa_stalls": [], "grade_counts": {}, "total": 0}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)

# Initialise SQLite cache
init_cache()
clear_expired()

# Load NEA Hawker Centres GEOJSON
with open(os.path.join(BASE_DIR, "hawker_centres.geojson")) as f:
    raw = json.load(f)

# Load Michelin Bib Gourmand data
MICHELIN_DATA = {}
michelin_raw_path = os.path.join(BASE_DIR, "michelin_bib_gourmand.json")
if os.path.exists(michelin_raw_path):
    with open(michelin_raw_path) as f:
        michelin_raw = json.load(f)
    for entry in michelin_raw["stalls"]:
        centre_name = (entry.get("centre") or "").strip().lower()
        if centre_name not in MICHELIN_DATA:
            MICHELIN_DATA[centre_name] = []
        MICHELIN_DATA[centre_name].append(entry)
all_michelin_names_list = [s for entries in MICHELIN_DATA.values() for s in entries]

# Transform to our format
HAWKER_CENTRES = []
for feat in raw["features"]:
    p = feat["properties"]
    geom = feat["geometry"]
    lng, lat = geom["coordinates"] if geom else (103.8, 1.35)
    
    stalls = p.get("NUMBER_OF_COOKED_FOOD_STALLS")
    if stalls:
        try: stalls = int(stalls)
        except: stalls = None
    
    addr = p.get("ADDRESS_MYENV") or ""
    if not addr:
        parts = []
        if p.get("ADDRESSBLOCKHOUSENUMBER"): parts.append(str(p["ADDRESSBLOCKHOUSENUMBER"]))
        if p.get("ADDRESSSTREETNAME"): parts.append(p["ADDRESSSTREETNAME"])
        if p.get("ADDRESSPOSTALCODE"): parts.append(f"Singapore {p['ADDRESSPOSTALCODE']}")
        addr = ", ".join(parts) if parts else p.get("ADDRESSBUILDINGNAME") or p.get("NAME", "Singapore")
    
    district = "Other"
    addr_lower = addr.lower()
    for d in ["Ang Mo Kio", "Bedok", "Bishan", "Bukit Batok", "Bukit Panjang",
              "Bukit Timah", "Changi", "Choa Chu Kang", "Clementi", "Geylang",
              "Hougang", "Jurong East", "Jurong West", "Kallang", "Katong",
              "Marina Bay", "Novena", "Orchard", "Pasir Panjang", "Pasir Ris",
              "Paya Lebar", "Punggol", "Queenstown", "Sembawang", "Sengkang",
              "Serangoon", "Tampines", "Tiong Bahru", "Toa Payoh", "Woodlands", "Yishun",
              "Commonwealth", "Newton", "Dunman", "Telok Blangah", "Redhill",
              "Alexandra", "Holland", "Whampoa", "Balestier", "Jalan Besar",
              "Lavender", "MacPherson", "Potong Pasir", "River Valley",
              "Rochor", "Tanjong Pagar", "Tanglin", "Marine Parade",
              "East Coast", "Bugis", "Beach Road", "South Buona Vista",
              "Bukit Merah", "Old Airport", "Clemenceau", "New Market",
              "Maxwell", "China Street", "Telok Ayer", "Amoy", "Shenton Way",
              "People's Park", "Chinatown", "Raffles", "City Hall",
              "Clarke Quay", "Selegie", "Lorong", "Sims", "Aljunied",
              "Eunos", "Kembangan", "Joo Chiat", "Upper Serangoon",
              "Little India", "Farrer Park", "Boon Keng", "Geylang Bahru",
              "Kallang", "Mountbatten", "Dakota"]:
        if d.lower() in addr_lower:
            district = d
            break
    
# Check Michelin Bib Gourmand stalls — match on centre name
    name_lower = p.get("NAME", "").strip().lower()
    name_clean = re.sub(r'[()]', '', name_lower)
    centre_id = p["OBJECTID"]
    michelin_stalls = []
    # First: try matching by centre name in MICHELIN_DATA
    for michelin_name, michelin_entry in MICHELIN_DATA.items():
        if not michelin_name:
            continue
        # Normalize: US spelling, ampersand, punctuation
        michelin_clean = re.sub(r'[()]', '', michelin_name)
        michelin_clean = re.sub(r'\bfood center\b', 'food centre', michelin_clean)
        # Verify with significant word overlap to avoid false positives
        mn_words = set(michelin_clean.replace(',', '').replace('&', '').split())
        gn_words = set(name_clean.replace(',', '').replace('&', '').split())
        stopwords = {'food', 'centre', 'market', 'the', 'and', 'blk', 'road', 'street', 'lorong', 'avenue', 'singapore', 'pasar', 'shopping', 'mall', 'blk', 'for', '&'}
        common = (mn_words & gn_words) - stopwords
        # Substring match: lenient (1 word). No substring: strict (3+ words).
        if michelin_clean in name_clean or name_clean in michelin_clean:
            if len(common) >= 1:
                michelin_stalls = michelin_entry
                break
        elif len(common) >= 3:
            michelin_stalls = michelin_entry
            break
    # Fallback: known centre→Michelin mapping (by centre ID)
    if not michelin_stalls:
        _id_names = {s["name"].strip().lower(): s for s in all_michelin_names_list}
        _known_centre_stalls = {
            119372: ["lao fu zi fried kway teow", "nam sing hokkien fried mee", "nam sing hokkien mee", "to-ricos kway chap", "to-ricos guo shi"],
            119428: [], 119419: [], 119431: [], 119412: [], 119483: [],
        }
        centre_stalls = _known_centre_stalls.get(centre_id, [])
        for sn in centre_stalls:
            entry = _id_names.get(sn)
            if entry:
                michelin_stalls.append(entry)
    
    HAWKER_CENTRES.append({
        "id": p["OBJECTID"],
        "name": p.get("NAME", "Unknown"),
        "address": addr,
        "lat": lat,
        "lng": lng,
        "district": district,
        "status": p.get("STATUS") or "Existing",
        "stalls": stalls or 0,
        "photo_url": p.get("PHOTOURL") or "",
        "description": p.get("DESCRIPTION") or "",
        "building": p.get("ADDRESSBUILDINGNAME") or "",
        "street": p.get("ADDRESSSTREETNAME") or "",
        "postal": str(p.get("ADDRESSPOSTALCODE") or ""),
        "completion_year": p.get("EST_ORIGINAL_COMPLETION_DATE"),
        "michelin_stalls": michelin_stalls,
        "michelin_count": len(michelin_stalls),
    })

with_photos = sum(1 for c in HAWKER_CENTRES if c.get("photo_url"))
total = len(HAWKER_CENTRES)

FOOD_TYPES = [
    "Chicken Rice", "Laksa", "Char Kway Teow", "Satay", "Nasi Lemak",
    "Roti Prata", "Hokkien Mee", "Bak Kut Teh", "Chilli Crab", "Oyster Omelette",
    "Fishball Noodles", "Wan Tan Mee", "Curry Mee", "Mee Siam", "Yong Tau Foo",
    "Popiah", "Kaya Toast", "Nasi Padang", "Biryani", "Dessert", "Seafood"
]

DISTRICTS = sorted(set(hc["district"] for hc in HAWKER_CENTRES))
STATUSES = sorted(set(hc["status"] for hc in HAWKER_CENTRES))

def haversine(lat1, lng1, lat2, lng2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

# ── Rainfall cache (refreshed every 5 minutes) ──
RAINFALL_CACHE = {"data": None, "fetched": 0}
RAINFALL_TTL = 120  # 2 minutes — check NEA frequently
RAINFALL_URL = "https://api-open.data.gov.sg/v2/real-time/api/rainfall"

def fetch_rainfall():
    """Fetch rainfall data from NEA API with caching."""
    now = time.time()
    if RAINFALL_CACHE["data"] and (now - RAINFALL_CACHE["fetched"]) < RAINFALL_TTL:
        return RAINFALL_CACHE["data"]
    try:
        headers = {"User-Agent": "HawkerFinder/1.0"}
        resp = requests.get(RAINFALL_URL, headers=headers, timeout=10)
        data = resp.json()
        if data.get("code") == 0 and "data" in data:
            RAINFALL_CACHE["data"] = data["data"]
            RAINFALL_CACHE["fetched"] = now
            return data["data"]
    except Exception as e:
        print(f"[Rainfall] Fetch error: {e}")
    # Return stale cache if fetch fails
    return RAINFALL_CACHE["data"]

def find_nearest_rainfall(lat, lng):
    """Find the nearest rain station and its latest reading."""
    data = fetch_rainfall()
    if not data:
        return None
    stations = {s["id"]: s for s in data["stations"]}
    readings = data.get("readings", [])
    if not readings:
        return None
    latest = readings[-1]  # Most recent reading
    best = None
    best_dist = float("inf")
    for r in latest.get("data", []):
        sid = r["stationId"]
        station = stations.get(sid)
        if not station:
            continue
        loc = station.get("location")
        if not loc:
            continue
        d = haversine(lat, lng, loc["latitude"], loc["longitude"])
        if d < best_dist:
            best_dist = d
            best = {
                "station_name": station["name"],
                "station_id": sid,
                "distance_km": round(d, 2),
                "rainfall_mm": r["value"],
                "timestamp": latest["timestamp"]
            }
    return best

def filter_centres(query="", food="", district="", status_val="", min_stalls=0, 
                   lat=None, lng=None, sort="relevance"):
    results = list(HAWKER_CENTRES)
    if query:
        q = query.lower()
        results = [c for c in results if q in c["name"].lower() or q in c["address"].lower()]
    if district:
        results = [c for c in results if c["district"].lower() == district.lower()]
    if status_val:
        results = [c for c in results if c["status"].lower() == status_val.lower()]
    if min_stalls > 0:
        results = [c for c in results if c["stalls"] >= min_stalls]
    if lat and lng:
        for c in results:
            c["_dist"] = round(haversine(lat, lng, c["lat"], c["lng"]), 1)
        if sort == "distance":
            results.sort(key=lambda x: x["_dist"])
    elif sort == "stalls":
        results.sort(key=lambda x: -x["stalls"])
    elif sort == "name":
        results.sort(key=lambda x: x["name"])
    return results

def search_food_stalls(centre_name):
    """Search DuckDuckGo for food stall reviews of a hawker centre"""
    try:
        query = centre_name + " Singapore food stalls reviews"
        url = "https://html.duckduckgo.com/html/"
        r = requests.post(url, data={"q": query}, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=10)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for res in soup.select(".result")[:8]:
            title_el = res.select_one(".result__title a")
            snippet_el = res.select_one(".result__snippet")
            if not title_el:
                continue
            # Fix truncated text from <br> tags joining words without spaces
            snippet = ""
            if snippet_el:
                snippet = snippet_el.get_text(" ", strip=True)
            results.append({
                "title": title_el.get_text(" ", strip=True),
                "url": title_el.get("href", ""),
                "snippet": snippet,
                "source": res.select_one(".result__url") and res.select_one(".result__url").get_text(" ", strip=True) or ""
            })
        return results
    except:
        return []

@app.route("/")
def index():
    # Compute dynamic status counts
    status_counts = {}
    for c in HAWKER_CENTRES:
        s = c["status"]
        status_counts[s] = status_counts.get(s, 0) + 1
    
    return render_template("index.html", 
                         food_types=FOOD_TYPES,
                         districts=DISTRICTS,
                         statuses=STATUSES,
                         status_counts=status_counts,
                         total=total,
                         with_photos=with_photos)

@app.route("/api/centres")
def api_centres():
    query = request.args.get("q", "")
    food = request.args.get("food", "")
    district = request.args.get("district", "")
    status_val = request.args.get("status", "")
    min_stalls = request.args.get("min_stalls", 0, type=int)
    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)
    sort = request.args.get("sort", "relevance")
    veg = request.args.get("veg", "0") == "1"
    results = filter_centres(query, food, district, status_val, min_stalls, lat, lng, sort)
    # Enrich michelin_count from cached stall data
    try:
        import cache as _cm
        _cm.init_cache()
        _conn = _cm.get_db()
        _cur = _conn.cursor()
        for c in results:
            _cur.execute("SELECT data FROM food_stall_cache WHERE centre_id = %s", (str(c["id"]),))
            _row = _cur.fetchone()
            if _row:
                _stalls = json.loads(_row["data"]).get("stalls", [])
                _michelin = [s for s in _stalls if s.get("michelin")]
                if _michelin:
                    c["michelin_count"] = len(_michelin)
        _cur.close()
        _conn.close()
    except Exception:
        pass
    # Enrich veg_count from SFA track records (by name match)
    try:
        import cache as _cm2
        _conn2 = _cm2.get_db()
        _cur2 = _conn2.cursor()
        _cur2.execute("SELECT centre_name, COUNT(*) FROM sfa_track_records, jsonb_array_elements(sfa_data) s WHERE s->>'businessName' ~* 'veg|vegetarian|vegetable|素|斋' GROUP BY centre_name")
        _veg_map = {r[0]: r[1] for r in _cur2.fetchall()}
        _cur2.close(); _conn2.close()
        for c in results:
            c["veg_count"] = _veg_map.get(c["name"], 0)
    except Exception:
        for c in results:
            c["veg_count"] = 0
    # Known mapping fallback (used when no cached data yet)
    _km = {119372: 3}
    for c in results:
        if c.get("michelin_count", 0) == 0 and c["id"] in _km:
            c["michelin_count"] = _km[c["id"]]
    return jsonify([{
        "id": c["id"], "name": c["name"], "address": c["address"],
        "lat": c["lat"], "lng": c["lng"], "district": c["district"],
        "status": c["status"], "stalls": c["stalls"],
        "photo_url": c["photo_url"], "_dist": c.get("_dist"),
        "michelin_count": c.get("michelin_count", 0),
        "veg_count": c.get("veg_count", 0),
    } for c in results if not veg or c.get("veg_count", 0) > 0])

@app.route("/api/centre/<int:centre_id>")
def api_centre_detail(centre_id):
    c = next((c for c in HAWKER_CENTRES if c["id"] == centre_id), None)
    if not c:
        return jsonify({"error": "Not found"}), 404
    data = dict(c)
    data["rainfall"] = find_nearest_rainfall(c["lat"], c["lng"])
    return jsonify(data)

@app.route("/api/centre/<int:centre_id>/food-stalls")
def api_food_stalls(centre_id):
    """Search for food stall reviews with SQLite caching (60 min TTL)"""
    c = next((c for c in HAWKER_CENTRES if c["id"] == centre_id), None)
    if not c:
        return jsonify({"error": "Not found"}), 404
    
    # Check cache first
    cached = get_cached(centre_id)
    if cached:
        # Update the centre name in case it changed
        cached["centre"] = c["name"]
        # Tag stalls with Michelin status (only THIS centre's michelin stalls)
        this_centre_michelin = [s["name"].strip().lower() for s in c.get("michelin_stalls", [])]
        if cached.get("stalls") and this_centre_michelin:
            for stall in cached["stalls"]:
                if "michelin" not in stall:
                    stall["michelin"] = stall.get("name", "").strip().lower() in this_centre_michelin
        print(f"[Cache] HIT for {c['name']} (ID {centre_id})")
        return jsonify(cached)
    
    print(f"[Cache] MISS for {c['name']} (ID {centre_id}) — fetching fresh data")
    
    # Clear old photos — any re-fetch should repopulate with clean data
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM photo_cache WHERE centre_id = %s", (str(centre_id),))
    db.commit()
    cur.close()
    db.close()
    
    # Get review article links (best-effort, don't block SFA)
    try:
        reviews = search_food_stalls(c["name"])
    except Exception as e:
        print(f"[Enrich] Review search failed: {e}")
        reviews = []
    
    # Try to scrape actual stall names (best-effort)
    try:
        stalls_info = find_best_article(c["name"])
    except Exception as e:
        print(f"[Enrich] Stall extraction failed: {e}")
        stalls_info = None
    
    # Load SFA data FIRST — always returns, even if enrichment fails
    sfa = get_sfa_centre_data(c["name"])
    
    # Tag halal stalls (MUIS registry, matched by centre postal + unit)
    centre_postal = str(c.get("postal") or "")
    halal_map = get_halal_units(centre_postal) if centre_postal else {}
    if halal_map:
        for s in sfa["sfa_stalls"]:
            nu = _norm_unit(s.get("unit", ""))
            if nu and nu in halal_map:
                s["halal"] = True
                s["halal_name"] = halal_map[nu]
                # SFA sometimes has no business name ("NA") — use the MUIS certified name
                if not s.get("name"):
                    s["name"] = halal_map[nu]
    
    result = {
        "centre": c["name"],
        "reviews": reviews,
        "stalls": stalls_info["stalls"] if stalls_info else None,
        "stall_article": {
            "title": stalls_info["article_title"],
            "url": stalls_info["article_url"]
        } if stalls_info else None,
        "michelin_stalls": c.get("michelin_stalls", []),
        "cache_version": 4,  # bump to invalidate old cached data
        "sfa_stalls": sfa["sfa_stalls"],
        "sfa_grade_counts": sfa["grade_counts"],
        "sfa_total": sfa["total"]
    }
    
    # Match SFA grades to scraped stalls (name-based)
    if result["stalls"] and sfa["sfa_stalls"]:
        # Build lookup by cleaned name
        import re as _re
        def _clean_n(n):
            return _re.sub(r'[^\w\s]', '', (n or '').lower()).strip()
        sfa_lookup = {}
        for sr in sfa["sfa_stalls"]:
            if sr["name"]:
                sfa_lookup[_clean_n(sr["name"])] = sr
        for stall in result["stalls"]:
            cn = _clean_n(stall.get("name", ""))
            if cn in sfa_lookup:
                stall["sfa_grade"] = sfa_lookup[cn]["grade"]
    
    # Tag each scraped stall as Michelin if matched (only THIS centre's michelin stalls)
    this_centre_michelin = [s["name"].strip().lower() for s in c.get("michelin_stalls", [])]
    if result["stalls"] and this_centre_michelin:
        for stall in result["stalls"]:
            stall["michelin"] = stall["name"].strip().lower() in this_centre_michelin
    
    # Store in cache (even if None — next scrape will overwrite)
    set_cached(centre_id, c["name"], result)
    
    # Try LLM enrichment in background for richer stall data + photos
    # Runs when BS4 found < 3 stalls, OR no photos cached yet (first LLM pass)
    photos_cached = get_photos(centre_id)
    if (not result.get("stalls") or len(result.get("stalls", [])) < 3) or not photos_cached:
        try:
            print(f"[Enrich] Running LLM enrichment for {c['name']}")
            enriched = llm_enrich_centre(c["name"], c["id"])
            if enriched and enriched.get("stalls"):
                result["stalls"] = enriched["stalls"]
                result["llm_enriched"] = True
                if enriched.get("article"):
                    result["stall_article"] = enriched["article"]
                if enriched.get("photos"):
                    set_photos(centre_id, enriched["photos"])
                    result["photos"] = enriched["photos"]
                # Update cache
                set_cached(centre_id, c["name"], result)
                print(f"[Enrich] LLM enrichment complete: {len(enriched['stalls'])} stalls")
        except Exception as e:
            print(f"[Enrich] LLM enrichment error: {e}")
    
    return jsonify(result)

@app.route("/api/rating/<int:centre_id>")
def api_rating(centre_id):
    """Get Google Maps rating for a hawker centre"""
    c = next((c for c in HAWKER_CENTRES if c["id"] == centre_id), None)
    if not c:
        return jsonify({"error": "Not found"}), 404
    
    cached = get_rating_cached(centre_id)
    if cached:
        return jsonify(cached)
    
    return jsonify({"rating": None, "reviews": None})

@app.route("/api/rating/set", methods=["POST"])
def api_set_rating():
    """Assistant endpoint to batch-set ratings from Google Maps scraping"""
    data = request.get_json()
    if not data or "centre_id" not in data:
        return jsonify({"error": "Missing centre_id"}), 400
    c = next((c for c in HAWKER_CENTRES if c["id"] == data["centre_id"]), None)
    if not c:
        return jsonify({"error": "Centre not found"}), 404
    set_rating_cached(
        data["centre_id"],
        c["name"],
        data.get("rating"),
        data.get("reviews", 0)
    )
    return jsonify({"ok": True})

@app.route("/photo/<int:centre_id>/<int:idx>")
def serve_photo(centre_id, idx):
    """Serve a photo from the SQLite BLOB cache"""
    photo = get_photo_data(centre_id, idx)
    if not photo:
        return "", 404
    from flask import Response
    return Response(photo["data"], mimetype=photo.get("content_type", "image/jpeg"))

@app.route("/centre/<int:centre_id>")
def detail(centre_id):
    centre = next((c for c in HAWKER_CENTRES if c["id"] == centre_id), None)
    if not centre:
        return render_template("404.html"), 404
    
    rainfall = find_nearest_rainfall(centre["lat"], centre["lng"])
    sfa = get_sfa_centre_data(centre_id)
    
    nearby = []
    for c in HAWKER_CENTRES:
        if c["id"] != centre_id:
            dist = round(haversine(centre["lat"], centre["lng"], c["lat"], c["lng"]), 1)
            if dist <= 3:
                nearby.append((c, dist))
    nearby.sort(key=lambda x: x[1])
    
    return render_template("detail.html", centre=centre, nearby=nearby[:8], rainfall=rainfall, sfa=sfa)

@app.route("/privacy")
def privacy():
    return render_template("privacy.html", total=total, with_photos=with_photos)

def search_all_stalls(query):
    """Search stall names and dishes across all cached centres."""
    if not query or len(query.strip()) < 2:
        return []
    q = query.strip().lower()
    try:
        import cache as _cm
        _cm.init_cache()
        conn = _cm.get_db()
        cur = conn.cursor()
        cur.execute("SELECT centre_id, centre_name, data FROM food_stall_cache")
        rows = cur.fetchall()
        results = []
        for row in rows:
            try:
                data = json.loads(row["data"])
            except:
                continue
            stalls = data.get("stalls", [])
            if not stalls:
                continue
            for stall in stalls:
                sname = (stall.get("name", "") or "").strip().lower()
                dishes = stall.get("food", [])
                dish_text = " ".join(d.lower() for d in dishes if d)
                if q in sname or q in dish_text or any(q in (d or "").lower() for d in dishes):
                    # Find centre info for district
                    centre = next((c for c in HAWKER_CENTRES if c["id"] == row["centre_id"]), None)
                    results.append({
                        "stall_name": stall.get("name", ""),
                        "food": dishes,
                        "description": (stall.get("description", "") or "")[:120],
                        "centre_id": row["centre_id"],
                        "centre_name": row["centre_name"],
                        "district": centre["district"] if centre else "",
                        "michelin": stall.get("michelin", False),
                    })
                    if len(results) >= 50:
                        break
            if len(results) >= 50:
                break
        conn.close()
        return results
    except Exception as e:
        print(f"[StallSearch] Error: {e}")
        return []

@app.route("/api/search-stalls")
def api_search_stalls():
    query = request.args.get("q", "")
    results = search_all_stalls(query)
    return jsonify(results)

# ── Non-Hawker Food Spots (map markers) ─────────────
@app.route("/api/spots")
def api_spots():
    """Return geocoded non-hawker eateries (Michelin Bib Gourmand etc.) as map markers."""
    try:
        conn = _pg2.connect(_SFA_DSN)
        cur = conn.cursor()
        cur.execute("""
            SELECT name, cuisine, category, lat, lng, source, address, price
            FROM non_hawker_spots
            WHERE lat IS NOT NULL AND lng IS NOT NULL
            ORDER BY name
        """)
        rows = cur.fetchall()
        cur.close(); conn.close()
        spots = [{
            "name": r[0], "cuisine": r[1] or "", "category": r[2] or "",
            "lat": r[3], "lng": r[4], "source": r[5] or "",
            "address": r[6] or "", "price": r[7] or ""
        } for r in rows]
        return jsonify(spots)
    except Exception as e:
        print(f"[Spots] Error: {e}")
        return jsonify([])

# ── Rainfall API ──

@app.route("/api/rainfall")
def api_rainfall():
    """Return all rainfall stations with latest readings."""
    data = fetch_rainfall()
    if not data:
        return jsonify({"error": "No rainfall data available"}), 503
    return jsonify(data)

@app.route("/api/rainfall/nearest")
def api_rainfall_nearest():
    """Return nearest rainfall reading for given coordinates."""
    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)
    if not lat or not lng:
        return jsonify({"error": "lat and lng query params required"}), 400
    result = find_nearest_rainfall(lat, lng)
    if not result:
        return jsonify({"error": "No rainfall data available"}), 503
    return jsonify(result)

@app.route("/api/rainfall/centre/<int:centre_id>")
def api_rainfall_centre(centre_id):
    """Return nearest rainfall reading for a specific hawker centre."""
    c = next((c for c in HAWKER_CENTRES if c["id"] == centre_id), None)
    if not c:
        return jsonify({"error": "Not found"}), 404
    result = find_nearest_rainfall(c["lat"], c["lng"])
    if not result:
        return jsonify({"error": "No rainfall data available"}), 503
    return jsonify(result)

# ── Timeout Singapore API ──

@app.route("/api/timeout/picks")
def api_timeout_picks():
    """Return trending food & drink articles from Timeout Singapore."""
    articles = fetch_timeout_feed()
    return jsonify(articles)

# ── AI Craving Assistant ─────────────────────────────
from ai_craving import recommend as ai_recommend

@app.route("/api/craving", methods=["POST"])
def api_craving():
    """AI food recommender: rank stalls by a natural-language craving."""
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    surprise = bool(data.get("surprise"))
    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)
    if not surprise and not query:
        return jsonify({"error": "Tell me what you're craving (e.g. spicey wanton mee) or pick Surprise Me."}), 400
    try:
        r = ai_recommend(query, lat=lat, lng=lng, surprise=surprise, limit=5)
        return jsonify(r)
    except Exception as e:
        print(f"[Craving] Error: {e}")
        return jsonify({"error": "Something went wrong. Try again in a moment."}), 500


# ── Restaurants (Michelin Bib Gourmand, non-hawker) ──
RESTAURANTS = json.load(open(os.path.join(os.path.dirname(__file__), "data", "restaurants.json")))

@app.route("/api/restaurants")
def api_restaurants():
    """Return curated restaurants with Bib Gourmand badges."""
    return jsonify(RESTAURANTS)


if __name__ == "__main__":
    import sys
    p = 5004
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            p = int(sys.argv[i + 1])
    app.run(host="0.0.0.0", port=p, debug=False)
