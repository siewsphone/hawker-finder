#!/usr/bin/env python3
"""Daily snapshot refresh: download latest GEOJSON from data.gov.sg and restart Flask"""
import json
import os
import sys
import subprocess
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("refresh-dataset")

GEOJSON_PATH = os.path.join(os.path.dirname(__file__), "hawker_centres.geojson")
DOWNLOAD_URL = "https://api-open.data.gov.sg/v1/public/api/datasets/d_4a086da0a5553be1d89383cd90d07ecd/poll-download"

def download_dataset():
    """Fetch the latest GEOJSON from data.gov.sg"""
    import urllib.request
    
    # Step 1: Get the S3 download URL
    req = urllib.request.Request(DOWNLOAD_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    
    s3_url = data.get("data", {}).get("url")
    if not s3_url:
        raise Exception(f"No download URL in response: {data.get('errorMsg', 'unknown')}")
    
    # Step 2: Download the actual GEOJSON
    req2 = urllib.request.Request(s3_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req2, timeout=60) as resp2:
        raw = resp2.read().decode()
    
    # Validate it's proper GEOJSON
    parsed = json.loads(raw)
    if "features" not in parsed:
        raise Exception("Downloaded file is not valid GEOJSON (no features)")
    
    logger.info(f"Downloaded {len(raw)} bytes, {len(parsed['features'])} features")
    
    # Write atomically
    tmp_path = GEOJSON_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        f.write(raw)
    os.replace(tmp_path, GEOJSON_PATH)
    
    logger.info(f"Written to {GEOJSON_PATH}")
    return len(parsed["features"])

def reload_flask():
    """Trigger Flask's auto-reloader by touching a watched file"""
    try:
        # Touch app.py to trigger Flask's stat-based auto-reload
        os.utime("/opt/data/generated/hawker_finder/app.py")
        logger.info("Touched app.py to trigger Flask reload")
    except Exception as e:
        logger.warning(f"Could not trigger Flask reload: {e}")

def main():
    logger.info("Starting daily dataset refresh...")
    try:
        count = download_dataset()
        reload_flask()
        # Also flush any DB caches
        logger.info(f"✅ Refresh complete — {count} centres synced")
    except Exception as e:
        logger.error(f"❌ Refresh failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
