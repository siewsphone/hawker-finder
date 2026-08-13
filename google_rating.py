"""Scrape Google Maps ratings from Google Search results"""
import requests
import re
import json
import time
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

def get_google_rating(name):
    """Search Google for a hawker centre and extract rating from rich snippets"""
    try:
        query = f"{name} Singapore"
        url = f"https://www.google.com/search?q={requests.utils.quote(query)}&hl=en"
        
        r = requests.get(url, headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        }, timeout=12)
        
        if r.status_code != 200:
            return None
        
        soup = BeautifulSoup(r.text, "html.parser")
        text = r.text
        
        # Method 1: JSON-LD script tags (most reliable)
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
                if isinstance(data, list):
                    for item in data:
                        result = _extract_rating(item)
                        if result:
                            return result
                else:
                    result = _extract_rating(data)
                    if result:
                        return result
            except:
                continue
        
        # Method 2: Regex search for ratingValue/ratingCount in JSON blobs
        rt = re.search(r'"ratingValue"\s*:\s*([\d.]+)', text)
        rc = re.search(r'"ratingCount"\s*:\s*(\d+)', text)
        if rt:
            return {
                "rating": float(rt.group(1)),
                "reviews": int(rc.group(1)) if rc else 0,
                "source": "google"
            }
        
        return None
    except:
        return None

def _extract_rating(data):
    """Extract rating from a JSON-LD node"""
    if not isinstance(data, dict):
        return None
    
    # Direct rating
    for key in ("ratingValue", "rating"):
        if key in data:
            try:
                rating = float(data[key])
                reviews = 0
                for rk in ("ratingCount", "reviewCount", "bestRating"):
                    if rk in data:
                        try:
                            reviews = int(data[rk])
                        except:
                            pass
                        break
                return {"rating": rating, "reviews": reviews, "source": "google"}
            except:
                pass
    
    # aggregateRating wrapper
    if "aggregateRating" in data and isinstance(data["aggregateRating"], dict):
        ar = data["aggregateRating"]
        if "ratingValue" in ar:
            return {
                "rating": float(ar["ratingValue"]),
                "reviews": int(ar.get("ratingCount", 0)),
                "source": "google"
            }
    
    # Recurse into sub-items
    for val in data.values():
        if isinstance(val, dict):
            result = _extract_rating(val)
            if result:
                return result
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    result = _extract_rating(item)
                    if result:
                        return result
    
    return None

if __name__ == "__main__":
    centres = ["Commonwealth Crescent Market", "Tiong Bahru Market", 
               "Newton Food Centre", "Berseh Food Centre", "Old Airport Road Food Centre"]
    for c in centres:
        print(f"\n{c}:")
        result = get_google_rating(c)
        if result:
            print(f"  ⭐ {result['rating']}/5 ({result['reviews']} reviews)")
        else:
            print(f"  — no rating found")
        time.sleep(1)
