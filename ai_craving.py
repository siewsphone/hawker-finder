"""AI Craving Assistant — recommend hawker stalls from natural-language cravings.

Gathers enriched stall data (name, dishes, description, centre, district,
michelin, halal, SFA grade) from PostgreSQL, filters by dish keywords, then
uses an LLM to pick the best matches and explain why.
"""
import json
import random
import re
import psycopg2
from llm_enricher import call_llm, get_api_key

from db_config import PG_DSN

# Dish keyword -> canonical craving terms (so "mee pok" vs "wanton mee" match)
CRAVING_KEYWORDS = {
    "chicken rice": ["chicken rice", "hai nan", "hainanese"],
    "wanton mee": ["wanton", "wonton", "mee kia", "wanton"],
    "char kway teow": ["kway teow", "char kway", "c k t", "CKT"],
    "kway chap": ["kway chap", "kway"],
    "carrot cake": ["carrot cake", "chye poh"],
    "laksa": ["laksa", "lemak"],
    "satay": ["satay"],
    "roti prata": ["prata", "roti"],
    "curry": ["curry"],
    "bak kut teh": ["bak kut", "bkt"],
    "nasi lemak": ["nasi lemak", "coconut rice"],
    "duck": ["duck", "roast duck", "braised duck"],
    "fish ball": ["fishball", "fish ball", "fishcake"],
    "dim sum": ["dim sum", "siew mai", "ha gao"],
    "soup": ["soup", "herbal"],
    "chicken rice": ["chicken rice"],
    "mee goreng": ["mee goreng", "fried mee"],
    "economy rice": ["economy rice", "cai fan", "mixed rice", "zhu cha"],
    "yun cha": ["yun cha", "coffee", "tea"],
    "dessert": ["dessert", "chendol", "cendol", "ice kacang"],
    "seafood": ["seafood", "chilli crab", "prawn", "crab", "fish", "prawn noodle"],
    "halal": ["halal", "briyani", "mutton", "meat"],
    "vegetarian": ["veg", "vegetarian", "veggie", "vegetable"],
    "spicy": ["spicy", "chili", "sambal", "pedas"],
}

def get_db():
    return psycopg2.connect(PG_DSN)

def _norm(s):
    return (s or "").lower().strip()

def _parse_keywords(query):
    """Extract dish keywords + special intents (halal, veg, spicy) from query."""
    q = _norm(query)
    terms = []
    for dish, keys in CRAVING_KEYWORDS.items():
        if any(k in q for k in keys) and dish not in terms:
            terms.append(dish)
    intents = {"halal": False, "veg": False, "spicy": False, "cheap": False}
    if any(k in q for k in ["halal", "muslim"]):
        intents["halal"] = True
    if any(k in q for k in ["vegetarian", "vegetable", "veggie", "plant"]):
        intents["veg"] = True
    if any(k in q for k in ["spicy", "chili", "chilli", "pedas"]):
        intents["spicy"] = True
    if any(k in q for k in ["cheap", "affordable", "budget", "below", "under", "5", "6"]):
        intents["cheap"] = True
    # Every meaningful word becomes a searchable token too
    for w in q.split():
        # skip stopwords
        if w in ("i", "a", "the", "and", "or", "near", "me", "my", "want", "crave",
                  "some", "give", "best", "good", "find", "where", "under", "below",
                  "singapore", "at", "in", "is", "are", "place", "food"):
            continue
        if len(w) > 1:
            terms.append(w)
    return terms, intents


def _fetch_stalls(limit_centres=60):
    """Fetch all enriched stalls across centres, deduped, with centre context (incl. SFA grade count)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """SELECT c.centre_id, c.centre_name, c.data, t.sfa_data
           FROM food_stall_cache c
           LEFT JOIN sfa_track_records t ON t.centre_name = c.centre_name
           ORDER BY c.fetched_at DESC
           LIMIT %s""", (limit_centres,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    stalls = []
    seen = set()
    for centre_id, centre_name, data, sfa_data in rows:
        if isinstance(data, str):
            try: data = json.loads(data)
            except: continue
        centre_stalls = data.get("stalls") or []
        for s in centre_stalls:
            nm = _norm(s.get("name"))
            if not nm or nm in seen:
                continue
            seen.add(nm)
            stalls.append({
                "stall": s.get("name", ""),
                "dishes": [d for d in (s.get("dishes") or [])],
                "desc": (s.get("description") or "")[:120],
                "centre": centre_name,
                "centre_id": centre_id,
                "michelin": bool(s.get("michelin")),
                "halal": False,
                "veg": _is_veg(s.get("name", "")),
            })
    return stalls

VEG_RE = ("veg", "vegetarian", "veggie", "素", "斋", "lily", "lotus")

def _is_veg(name):
    n = name.lower()
    return any(s in n for s in VEG_RE)

def _fetch_veg_stalls(limit_centres=129):
    """Pull dedicated vegetarian stalls from SFA track records (by name match)."""
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        SELECT centre_name, s->>'businessName', s->>'establishmentAddress'
        FROM sfa_track_records,
        jsonb_array_elements(sfa_data) s
        WHERE s->>'businessName' ~* 'veg|vegetarian|素'
           OR s->>'typeOfFoodBussiness' ~* 'veg|vegetarian'
    """)
    rows = cur.fetchall(); cur.close(); conn.close()
    out = []
    seen = set()
    for centre_name, stall_name, addr in rows:
        nm = (stall_name or "").strip()
        if not nm or nm in seen:
            continue
        seen.add(nm)
        disp = nm if nm.lower() != "na" else "(Vegetarian stall)"
        unit = ""
        m = re.search(r"#?([0-9]{2}-[0-9]{2,3}(?:[A-Z])?|[A-Z]{1,2}\d{1,2})", addr or "")
        if m:
            unit = m.group(0)
        out.append({
            "stall": disp + (f" ({unit})" if unit else ""),
            "dishes": [],
            "desc": "Vegetarian stall",
            "centre": centre_name,
            "centre_id": None,
            "michelin": False,
            "halal": False,
            "veg": True,
        })
    return out

def _load_halal_set():
    """Return set of (postal.lower(), unit.lower()) that are MUIS-certified."""
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT postal, unit FROM halal_stalls")
        rows = cur.fetchall(); cur.close(); conn.close()
        return {( (p or "").lower().strip(), (u or "").lower().strip() ) for p, u in rows}
    except Exception:
        return set()

def _fetch_spots():
    """Fetch geocoded non-hawker spots (restaurants/coffee-shop stalls) outside NEA centres."""
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT name, category, address, price, lat, lng FROM non_hawker_spots WHERE lat IS NOT NULL ORDER BY name")
    rows = cur.fetchall(); cur.close(); conn.close()
    spots = []
    for name, category, address, price, lat, lng in rows:
        spots.append({
            "stall": name,
            "dishes": [],
            "desc": (category or "") + ", " + (address or "") if address else (category or ""),
            "centre": category or "Restaurant",
            "centre_id": None,
            "michelin": True,
            "halal": False,
            "lat": lat, "lng": lng,
            "price": price or "",
        })
    return spots

def recommend(query, lat=None, lng=None, surprise=False, limit=5):
    """Return list of {stall, centre, dishes, desc, michelin, halal, reason}.

    Recommends BOTH hawker-centre stalls AND non-hawker food spots.
    """
    stalls = _fetch_stalls()
    stalls += _fetch_spots()
    stalls += _fetch_veg_stalls()
    if not stalls:
        return {"results": [], "note": "No stall data cached yet."}

    halal_set = _load_halal_set()

    if surprise:
        # Random weighted: favour michelin + richer data (more dishes)
        pool = [s for s in stalls if s["dishes"] and (s["michelin"] or len(s["dishes"]) >= 2)]
        if not pool:
            pool = stalls
        picks = random.sample(pool, min(limit, len(pool)))
        reason = "A hidden-gem pick — worth a try."
        results = [{"stall": s["stall"], "centre": s["centre"], "centre_id": s["centre_id"],
                    "dishes": s["dishes"], "michelin": s["michelin"],
                    "reason": reason} for s in picks]
        return {"recommendations": results, "surprise": True}

    terms, intent = _parse_keywords(query)
    if not terms:
        return {"recommendations": [], "error": "I couldn't pick up any dish keywords. Try e.g. \"spicy wanton mee\" or \"cheap chicken rice\"."}

    # Score matches
    scored = []
    for s in stalls:
        blob = _norm(s["stall"]) + " " + _norm(" ".join(s["dishes"])) + " " + _norm(s["desc"])
        score = 0
        for t in terms:
            if t in blob:
                score += 2
            elif t in _norm(s["stall"]):
                score += 3
        # Vegetarian intent: veg stalls win, non-veg get a penalty
        if intent.get("veg"):
            if s.get("veg"):
                score += 8
            elif not s.get("veg"):
                score -= 4
        if score > 0:
            scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    # Top 15 candidates go to LLM for reasoning
    candidates = [s for _, s in scored[:15]]
    if not candidates:
        return {"recommendations": [], "note": f"No strong matches for \"{query}\". Try a more common dish name."}

    # Build compact candidate list for LLM
    cand_text = []
    for i, s in enumerate(candidates):
        tags = []
        if s["michelin"]: tags.append("MICHELIN")
        cand_text.append(f"{i+1}. {s['stall']} @ {s['centre']} — {', '.join(s['dishes'][:4])}. {s['desc']} {'['+','.join(tags)+']' if tags else ''}")
    prompt = (
        "You recommend hawker food in Singapore. A user is craving: \"" + query + "\".\n"
        "Choose the best " + str(limit) + " matching stalls from the candidate list below.\n"
        "Return ONLY JSON: a list of objects with keys {\"index\": <num>, \"reason\": \"<1-2 sentence why, tilt toward their craving \"label sparkle, michelin, cheap, etc.\">\"}.\n"
        "Prefer stalls whose dishes obviously match. Mention 'halal' in the reason ONLY if relevant.\n\nCandidates:\n" + "\n".join(cand_text)
    )
    out = call_llm("You are a Singapore hawker food recommender. Reply with valid JSON only.", prompt, max_tokens=600)
    # Parse JSON selection
    picks = []
    if out:
        try:
            clean = out.strip()
            if clean.startswith("```"): clean = clean.split("\n",1)[1]; clean = clean.rsplit("```",1)[0]
            parsed = json.loads(clean)
            for p in parsed:
                idx = p.get("index", 0) - 1
                if 0 <= idx < len(candidates):
                    s = candidates[idx]
                    picks.append({"stall": s["stall"], "centre": s["centre"], "centre_id": s["centre_id"],
                                  "dishes": s["dishes"], "michelin": s["michelin"],
                                  "veg": bool(s.get("veg")), "halal": False,
                                  "reason": p.get("reason", "")})
        except Exception as _e:
            picks = []
    # fallback: top scored
    if not picks:
        for _, s in scored[:limit]:
            picks.append({"stall": s["stall"], "centre": s["centre"], "centre_id": s["centre_id"],
                          "dishes": s["dishes"], "michelin": s["michelin"],
                          "veg": bool(s.get("veg")), "halal": False, "reason": "Top keyword match."})
    return {"recommendations": picks}