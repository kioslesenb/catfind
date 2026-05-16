import argparse
import hashlib
import logging
import random
import time
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client
from dotenv import load_dotenv
import os

# Load .env
env_paths = [Path("../backend/.env"), Path("backend/.env"), Path(".env")]
for p in env_paths:
    if p.exists():
        load_dotenv(p)
        print(f"✅ Loaded .env from: {p}")
        break

BASE_URL = "https://www.amivedi.nl/vermist/zoeken"
DETAIL_BASE = "https://www.amivedi.nl"

CAT_KEYWORDS = {"kat", "kitten", "poes", "katje", "maine coon"}

BUCKET_NAME = os.getenv("SUPABASE_BUCKET", "cat-images")
FOLDER_PATH = "missing_cat"

supabase: Client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

TEMP_IMAGES = Path("temp_images")
TEMP_IMAGES.mkdir(exist_ok=True)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def is_cat(text: str) -> bool:
    return any(kw in (text or "").lower() for kw in CAT_KEYWORDS)

def safe_request(url: str, timeout=15):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.text
    except:
        return None

def upload_to_supabase(local_path: Path, storage_path: str) -> str | None:
    try:
        with open(local_path, "rb") as f:
            supabase.storage.from_(BUCKET_NAME).upload(storage_path, f, {"content-type": "image/jpeg", "upsert": "true"})
        return supabase.storage.from_(BUCKET_NAME).get_public_url(storage_path)
    except Exception as e:
        if "duplicate" not in str(e).lower():
            logger.warning(f"Upload failed: {e}")
        return None

# ====================== MAIN ======================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--max-imports", type=int, default=30)
    args = parser.parse_args()

    logger.info("🚀 DEBUG VERSION - Checking image detection")

    imported = 0
    page = 0

    while page < args.max_pages and imported < args.max_imports:
        list_url = f"{BASE_URL}?start={page * 12}"
        logger.info(f"PAGE {page+1}/{args.max_pages}")

        html = safe_request(list_url)
        if not html:
            break

        detail_urls = []
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            if "/detail/?meldingid=" in a["href"]:
                full_url = DETAIL_BASE + a["href"] if not a["href"].startswith("http") else a["href"]
                detail_urls.append(full_url)
        detail_urls = list(dict.fromkeys(detail_urls))

        logger.info(f"Found {len(detail_urls)} listings")

        with ThreadPoolExecutor(max_workers=3) as executor:
            for future in as_completed([executor.submit(scrape_detail, url) for url in detail_urls]):
                result = future.result()
                if result and is_cat(result.get("title", "")):
                    img_count = len(result.get("images", []))
                    if img_count > 0:
                        insert_to_supabase(result)
                        imported += 1
                    logger.info(f"→ {result.get('title', 'No title')[:60]:<60} | Images found: {img_count}")

        page += 1
        time.sleep(random.uniform(2, 4))

    logger.info(f"Finished! Imported {imported} cats with images.")

def scrape_detail(url):
    try:
        html = safe_request(url)
        if not html: return None

        soup = BeautifulSoup(html, "html.parser")
        meldingid = url.split("meldingid=")[-1].split("&")[0]

        data = {"meldingid": meldingid, "title": "", "images": []}

        if h1 := soup.find("h1"):
            data["title"] = h1.get_text(strip=True)

        # Aggressive image search
        image_set = set()
        for img in soup.find_all("img"):
            for attr in ["src", "data-src", "data-lazy", "data-original", "srcset"]:
                src = img.get(attr)
                if src and any(e in str(src).lower() for e in [".jpg", ".jpeg", ".png", ".webp"]):
                    full_url = src if str(src).startswith("http") else DETAIL_BASE + str(src)
                    image_set.add(full_url)

        for full_url in list(image_set)[:5]:   # limit to max 5 per cat
            filename = f"{meldingid}_{hashlib.md5(full_url.encode()).hexdigest()[:12]}.jpg"
            storage_path = f"{FOLDER_PATH}/{filename}"

            try:
                resp = requests.get(full_url, headers=HEADERS, timeout=20)
                if resp.status_code == 200:
                    local_path = TEMP_IMAGES / filename
                    local_path.write_bytes(resp.content)
                    public_url = upload_to_supabase(local_path, storage_path)
                    if public_url:
                        data["images"].append(public_url)
                    local_path.unlink(missing_ok=True)
            except:
                continue

        return data

    except Exception as e:
        logger.error(f"Error {url}: {e}")
        return None

def insert_to_supabase(animal: dict):
    try:
        supabase.table("lost_animals").upsert(animal, on_conflict="meldingid").execute()
    except:
        pass

if __name__ == "__main__":
    main()