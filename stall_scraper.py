import requests
from bs4 import BeautifulSoup
import re

SKIP_HEADINGS = re.compile(
    r'^(location|address|opening|contact|how to|menu|about|tips|'
    r'highlights|must-try|popular|recommended|conclusion|related|nearby|'
    r'map|directions|parking|price|share|comments|faq|review|'
    r'video|photos|gallery|overview|introduction|what to|where to|'
    r'more|best of|top|bestseller|signature|final|latest|also|'
    r'other|index|table of contents|back to|stalls? by|all stalls|'
    r'open now|operating hours)', re.IGNORECASE
)

# Skip headings that are cuisine categories like "Chinese(22)", "Malay(15)"
CUISINE_CATEGORY = re.compile(r'^[A-Za-z]+\(\d+\)$')

def scrape_stall_names(article_url):
    """Scrape numbered stall names from a food review article"""
    try:
        r = requests.get(article_url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }, timeout=10)
        if r.status_code != 200:
            return None
        
        soup = BeautifulSoup(r.text, 'html.parser')
        all_h = soup.find_all(['h2', 'h3', 'h4'])
        
        # Strategy 1: Numbered headings like "1. Stall Name"
        numbered = []
        seen_nums = set()
        for tag in all_h:
            txt = tag.get_text(strip=True)
            m = re.match(r'^[#]?(\d+)[\.\)]\s*(.+)$', txt)
            if m:
                name = re.sub(r'\s*[|–—].*$', '', m.group(2).strip())
                if name and len(name) < 100 and not CUISINE_CATEGORY.match(name):
                    if name not in seen_nums:
                        seen_nums.add(name)
                        numbered.append({'num': int(m.group(1)), 'name': name})
        
        if 3 <= len(numbered) <= 50:
            numbered.sort(key=lambda x: x['num'])
            return numbered
        
        # Strategy 2: Unnumbered h2/h3 headings that look like stall names
        stalls = []
        seen = set()
        for tag in all_h:
            txt = tag.get_text(strip=True)
            if not (3 < len(txt) < 80):
                continue
            if SKIP_HEADINGS.match(txt):
                continue
            if CUISINE_CATEGORY.match(txt):
                continue
            if re.match(r'^[\d\s\-–—]+$', txt) or re.match(r'^[^a-zA-Z]+$', txt):
                continue
            parent = tag.parent
            pcls = ' '.join(parent.get('class', [])) if parent and parent.get('class') else ''
            if any(x in pcls for x in ['sidebar', 'widget', 'menu', 'nav', 'footer', 'header']):
                continue
            if txt not in seen:
                seen.add(txt)
                stalls.append(txt)
        
        if 4 <= len(stalls) <= 50:
            return [{'num': i+1, 'name': s} for i, s in enumerate(stalls)]
        
        return None
    except:
        return None


def find_best_article(centre_name):
    """Find the best food review article with stall names"""
    try:
        query = centre_name + " Singapore best stalls guide"
        r = requests.post("https://html.duckduckgo.com/html/", data={"q": query}, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }, timeout=10)
        if r.status_code != 200:
            return None
        
        soup = BeautifulSoup(r.text, 'html.parser')
        preferred = [
            'sethlui.com', 'misstamchiak.com', 'danielfooddiary.com',
            'eatbook.sg', 'ladyironchef.com', 'honeycombers.com',
            'thesmartlocal.com', 'timeout.com', 'guidelinemas.com',
            'singaporehawkercentres.com', 'hawkerpedia.com.sg',
            'hawker.guide'
        ]
        
        scored = []
        for res in soup.select('.result'):
            a = res.select_one('.result__title a')
            if not a: continue
            url = a.get('href', '')
            title = a.get_text(" ", strip=True)
            snippet_el = res.select_one('.result__snippet')
            snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ''
            
            score = 0
            if re.search(r'\d+\s+(stalls?|best|must-try|food)', title + snippet, re.IGNORECASE):
                score += 5
            for i, d in enumerate(preferred):
                if d in url:
                    score += len(preferred) - i
                    break
            
            scored.append({'url': url, 'title': title, 'score': score})
        
        if not scored:
            return None
        
        scored.sort(key=lambda x: -x['score'])
        
        for candidate in scored[:3]:
            stalls = scrape_stall_names(candidate['url'])
            if stalls:
                return {
                    'article_title': candidate['title'],
                    'article_url': candidate['url'],
                    'stalls': stalls
                }
        return None
    except:
        return None
