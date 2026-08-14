<p align="center">
  <h1 align="center">🥢 Hawker Finder</h1>
  <p align="center">Every Singapore hawker centre, one map. Find the stall worth queuing for.</p>
</p>

---

**Hawker Finder** is a web app that helps you discover Singapore's hawker centres and their stalls — from Michelin Bib Gourmand picks to SFA hygiene grades, halal-certified and vegetarian options, live rainfall (so you know if the open-air centre is dry), and an AI "craving" search that turns *"I feel like laksa"* into actual stalls nearby.

Live at **[hawkerfinder.com](https://hawkerfinder.com)**.

## ✨ Features

- **129 hawker centres** from the NEA dataset, browseable on an interactive map
- **Stall-level detail** — individual stalls with cuisine, prices, and photos
- **Michelin Bib Gourmand** badges on award-winning stalls and centres
- **SFA hygiene grades** (A/B/C) for every stall from official track records
- **🌱 Vegetarian & 🟢 Halal** filters across centres and stalls
- **Near-me sorting** — pan the map and sort by distance; no search box needed
- **Live rainfall** overlays (nearest NEA rain station) so you know before you go
- **AI Craving search** — describe a craving in plain language, get stall matches
- **"Timeout picks"** — trending hawker articles from Timeout Singapore
- **Google Maps ratings** per centre

## 🧭 Browse, don't type

The UI is deliberately minimal: pick a district filter, tap **Near Me**, and pan the map. Sorting by distance updates as you move — no text search field, no dead dropdowns. Every filter you see is backed by data that actually returns results.

## 🏗️ Architecture

```
Flask (app.py)                      # routes + rendering
├── PostgreSQL (hawker_finder DB)   # stalls, SFA records, halal, ratings
├── cache.py                        # SQLite enrichment cache + PG access
├── llm_enricher.py                 # AI stall/photo enrichment (OpenCode Zen)
└── data/
    ├── hawker_centres.geojson      # NEA centres (source of truth)
    ├── restaurants.json            # curated Bib Gourmand restaurants
    └── michelin_bib_gourmand.json  # Michelin award data
```

### Scrapers (run standalone)

| Script | Purpose |
|---|---|
| `scrape_michelin.py` | Michelin Bib Gourmand + Best-of Hawker from guide.michelin.com |
| `sfa_scraper.py` | SFA Track Records (hygiene grades) per centre → PG |
| `halal_scraper.py` | MUIS halal-certified stalls from halalboleh.com → PG |
| `google_rating.py` | Google Maps ratings from search results |
| `whyq_enrich.py` | Menu-item enrichment from WhyQ sitemap (for AI Craving) |
| `timeout_scraper.py` | Trending articles from Timeout Singapore |
| `stall_scraper.py` | Best-article matching for stalls |
| `daily_enrich.py` | Daily cron: LLM-enrich centres + photos |

## 🔑 Configuration

All secrets come from environment variables (see `db_config.py`) — never hardcoded:

| Variable | Purpose |
|---|---|
| `PG_PASSWORD` | PostgreSQL password (required) |
| `PG_HOST` / `PG_PORT` / `PG_DB` / `PG_USER` | DB connection (defaults: `nas:54321/hawker_finder/postgres`) |
| `OPENCODE_ZEN_API_KEY` / `OPENCODE_GO_API_KEY` | LLM enrichment (OpenCode Zen) |
| `FIRECRAWL_API_KEY` | Web scraping fallback |
| `BRAVE_API_KEY` | (legacy) search |

Copy `.env.example` → `.env` and fill in your keys. `.env` is gitignored.

## 🚀 Run

```bash
# 1. Create a virtualenv + install deps
python3 -m venv .venv && source .venv/bin/activate
pip install flask requests beautifulsoup4 psycopg2-binary

# 2. Set your PostgreSQL password
export PG_PASSWORD="..."   # or put it in .env

# 3. Launch
python3 app.py
# → http://localhost:5004
```

The app serves on port **5004** by default.

## 🗺️ Data sources

- **NEA Hawker Centres** (GeoJSON, refreshed 4-hourly via data.gov.sg)
- **Michelin Guide Singapore** (Bib Gourmand & Best-of Hawker)
- **SFA Track Records** (food-hygiene grades)
- **MUIS** (halal certification, via halalboleh.com)
- **Google Maps** (ratings)
- **WhyQ** (menu items)
- **Timeout Singapore** (trending articles)

## 📄 License

MIT — see [LICENSE](LICENSE).

---

*Made with 🥢 for Singapore. Support the project → [Buy me a coffee](https://buymeacoffee.com/siewsphone).*
