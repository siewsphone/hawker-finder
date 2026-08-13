"""
Timeout Singapore scraper — fetches trending food & drink articles
and maps them to hawker centres where possible.
"""
import requests, json, re, time
from bs4 import BeautifulSoup

TIMEOUT_FOOD_URL = "https://www.timeout.com/singapore/food-drink"
CACHE_TTL = 3600  # 1 hour
_cache = {"data": None, "fetched": 0}

def fetch_timeout_feed():
    """Scrape the Timeout SG food & drink landing page for article cards."""
    now = time.time()
    if _cache["data"] and (now - _cache["fetched"]) < CACHE_TTL:
        return _cache["data"]

    try:
        r = requests.get(TIMEOUT_FOOD_URL, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=15)
        if r.status_code != 200:
            return _cache.get("data") or []
        
        soup = BeautifulSoup(r.text, "html.parser")
        articles = []

        # Find article cards — Timeout uses <article> tags
        for article in soup.find_all("article"):
            # Get the main link
            a = article.find("a", href=True)
            if not a:
                continue
            
            href = a.get("href", "")
            # Skip non-article links
            if not href.startswith("/singapore/"):
                continue
            # Skip non-content paths
            if any(skip in href for skip in ["/newsletter", "/search", "/about", "/contact"]):
                continue
            
            url = "https://www.timeout.com" + href
            
            # Title from heading inside the article
            h = article.find(["h3", "h2", "h4"])
            title = h.get_text(strip=True) if h else ""
            if not title or len(title) < 10:
                continue

            # Category tag (Restaurants, Bars and pubs, etc.)
            cat_el = article.find("h5") or article.find(class_=re.compile("category|section"))
            category = cat_el.get_text(strip=True) if cat_el else ""
            
            # Description / excerpt
            desc_el = article.find("p")
            description = desc_el.get_text(strip=True) if desc_el else ""

            # Image
            img = article.find("img")
            img_url = ""
            if img:
                img_url = img.get("src") or img.get("data-src") or ""
                if img_url and img_url.startswith("//"):
                    img_url = "https:" + img_url

            # Rating (if present — Timeout uses "X out of 5 stars")
            rating = None
            rating_text = article.get_text()
            rm = re.search(r'(\d+(?:\.\d+)?)\s*out of\s*5\s*stars?', rating_text)
            if rm:
                rating = float(rm.group(1))

            # Check if hawker-related
            is_hawker = any(kw in (title + " " + description).lower() 
                          for kw in ["hawker", "kopitiam", "food centre", "food court", 
                                     "market", "stall", "bak kut teh", "chicken rice",
                                     "laksa", "char kway teow", "satay", "nasi lemak",
                                     "roti prata", "hokkien mee", "prata"])

            articles.append({
                "title": title,
                "url": url,
                "category": category,
                "description": description,
                "image": img_url,
                "rating": rating,
                "is_hawker": is_hawker,
            })

        # Deduplicate by URL
        seen = set()
        deduped = []
        for a in articles:
            if a["url"] not in seen:
                seen.add(a["url"])
                deduped.append(a)

        _cache["data"] = deduped
        _cache["fetched"] = now
        return deduped

    except Exception as e:
        print(f"[Timeout] Fetch error: {e}")
        return _cache.get("data") or []


def get_article_detail(url):
    """Fetch full article text to find mentioned hawker centres or food spots."""
    try:
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=10)
        if r.status_code != 200:
            return None
        
        soup = BeautifulSoup(r.text, "html.parser")
        
        # Remove unwanted elements
        for tag in soup.find_all(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        
        # Get article body text
        body = soup.find("article") or soup.find("main") or soup
        text = body.get_text(separator=" ", strip=True)
        
        # Truncate
        if len(text) > 5000:
            text = text[:5000]
        
        return text
    except Exception as e:
        print(f"[Timeout] Detail error for {url}: {e}")
        return None


if __name__ == "__main__":
    articles = fetch_timeout_feed()
    print(f"Found {len(articles)} articles:")
    for a in articles[:10]:
        flag = "🍜" if a["is_hawker"] else "  "
        rating = f" {'⭐' * int(a['rating'] or 0)}" if a.get("rating") else ""
        print(f"  {flag} {a['title']}{rating}")
        print(f"       {a['category']} — {a['url']}")
