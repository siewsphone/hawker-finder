"""LLM-powered food stall + photo enrichment using OpenCode Zen API."""
import os
import re
import json
import requests
import traceback
from urllib.parse import quote_plus
from datetime import datetime, timedelta
import html as html_mod

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load API key for OpenCode Zen
def get_api_key():
    key = os.environ.get("OPENCODE_ZEN_API_KEY", "")
    if key:
        return key
    key = os.environ.get("OPENCODE_GO_API_KEY", "")
    if key:
        return key
    env_path = "/opt/data/.env"
    if os.path.exists(env_path):
        for line in open(env_path):
            if line.startswith("OPENCODE_ZEN_API_KEY="):
                return line.strip().split("=", 1)[1]
        for line in open(env_path):
            if line.startswith("OPENCODE_GO_API_KEY="):
                return line.strip().split("=", 1)[1]
    return ""

API_KEY = get_api_key()
API_URL = "https://opencode.ai/zen/go/v1/chat/completions"
MODEL = "deepseek-v4-pro"

def _read_env(name):
    """Read a key from /opt/data/.env (fallback when not in os.environ)."""
    val = os.environ.get(name, "")
    if val:
        return val
    env_path = "/opt/data/.env"
    if os.path.exists(env_path):
        for line in open(env_path):
            if line.startswith(name + "="):
                return line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    return ""

def call_llm(system_prompt, user_prompt, max_tokens=2000):
    if not API_KEY:
        return None
    try:
        r = requests.post(API_URL, json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "thinking": {"type": "disabled"}
        }, headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }, timeout=15)
        if r.status_code == 200:
            content = r.json()["choices"][0]["message"]["content"]
            return content
        print(f"[LLM] API error {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[LLM] API exception: {e}")
    return None


def fetch_article_text(url, timeout=10):
    """Fetch article HTML and extract readable text + raw HTML for image extraction."""
    try:
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=timeout)
        if r.status_code != 200:
            return None, None
        text = r.text
        # Extract visible text
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(text, 'html.parser')
        # Remove script/style
        for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
            tag.decompose()
        body = soup.find('body') or soup
        visible = body.get_text(separator=' ', strip=True)
        # Trim to ~4000 chars
        if len(visible) > 4000:
            visible = visible[:2000] + "\n...\n" + visible[-2000:]
        return visible, text
    except Exception as e:
        print(f"  [fetch] Error: {e}")
        return None, None


def search_burpple(centre_name):
    """Search Burpple for hawker centre articles."""
    results = []
    try:
        query = quote_plus(f"{centre_name} Singapore food")
        url = f"https://www.burpple.com/search?q={query}"
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=10)
        if r.status_code == 200:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.text, 'html.parser')
            for link in soup.select('a[href*="/venues/"]'):
                href = link.get('href', '')
                title = link.get_text(strip=True)
                if title and href:
                    if not href.startswith('http'):
                        href = 'https://www.burpple.com' + href
                    results.append({"title": title, "url": href, "source": "Burpple"})
                    if len(results) >= 3:
                        break
    except Exception as e:
        print(f"  [Burpple] Error: {e}")
    return results


def search_food_stalls(centre_name):
    """Search for food stall review articles about a hawker centre."""
    results = []
    # Try Firecrawl Search first
    try:
        api_key = os.environ.get("FIRECRAWL_API_KEY", "")
        if not api_key:
            api_key = _read_env("FIRECRAWL_API_KEY")
        if api_key:
            query = f"{centre_name} best food stalls review"
            r = requests.post(
                "https://api.firecrawl.dev/v1/search",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"query": query, "limit": 5},
                timeout=15
            )
            if r.status_code == 200:
                data = r.json()
                for item in data.get("data", []):
                    results.append({
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "snippet": item.get("description", ""),
                        "source": "Firecrawl"
                    })
            else:
                print(f"  [Firecrawl] Error: {r.status_code} {r.text[:120]}")
    except Exception as e:
        print(f"  [Firecrawl] Error: {e}")
    
    # Try Burpple as fallback
    if len(results) < 3:
        burpple = search_burpple(centre_name)
        results.extend(burpple)
    
    return results


def extract_stalls_from_article(text, centre_name, raw_html=None):
    """Use LLM to extract stall names + food items from an article."""
    prompt = f"""Extract food stall information from this article about "{centre_name}" hawker centre.

Return a JSON array of objects. Each object has:
- "name": the stall name (required)
- "dishes": array of dish names (optional)
- "description": short description (optional, max 50 chars)

Only include stalls that are clearly mentioned as food stalls at this hawker centre.
Return ONLY the JSON array, nothing else. If no stalls found, return []."""
    
    result = call_llm(prompt, text[:3000], max_tokens=1000)
    if not result:
        return []
    
    # Try to parse JSON
    try:
        # Strip markdown code fences
        clean = result.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]
        stalls = json.loads(clean.strip())
        if isinstance(stalls, list):
            # Validate
            valid = []
            for s in stalls:
                if isinstance(s, dict) and s.get("name"):
                    valid.append(s)
            return valid
    except json.JSONDecodeError:
        print(f"  [LLM] Failed to parse JSON: {clean[:200]}")
    return []


def find_best_article(centre_name):
    """Find the best article for a hawker centre and extract stall names."""
    reviews = search_food_stalls(centre_name)
    if not reviews:
        return None
    
    best_article = None
    best_stalls = []
    
    for review in reviews[:5]:  # Try up to 5 articles
        url = review.get("url", "")
        if not url:
            continue
        print(f"  [LLM] Fetching: {url[:60]}...")
        text, raw_html = fetch_article_text(url, timeout=8)
        if not text:
            continue
        stalls = extract_stalls_from_article(text, centre_name, raw_html)
        if stalls and len(stalls) > len(best_stalls):
            best_stalls = stalls
            best_article = review
            if len(stalls) >= 10:
                break  # Good enough
    
    if not best_stalls:
        # Try Michelin guide as fallback
        michelin_url = f"https://guide.michelin.com/sg/en/singapore-region/singapore/restaurants"
        text, raw_html = fetch_article_text(michelin_url, timeout=8)
        if text:
            stalls = extract_stalls_from_article(text, centre_name, raw_html)
            if stalls:
                best_stalls = stalls
                best_article = {"title": "Michelin Guide Singapore", "url": michelin_url, "source": "Michelin"}
    
    if best_stalls:
        return {
            "stalls": best_stalls,
            "article_title": best_article.get("title", "") if best_article else "",
            "article_url": best_article.get("url", "") if best_article else ""
        }
    return None


def enrich_centre(centre_name, centre_id):
    """Main enrichment function: fetch + LLM-parse articles for a hawker centre."""
    print(f"[Enrich] Finding stalls for: {centre_name}")
    result = find_best_article(centre_name)
    if result and result.get("stalls"):
        print(f"[Enrich] Found {len(result['stalls'])} stalls from {result.get('article_title', '?')}")
    else:
        print(f"[Enrich] No stalls found for {centre_name}")
    return result