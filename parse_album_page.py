import asyncio
import aiofiles
from io import BytesIO
import json
import os
import random
import re
import hashlib
import time
from tqdm import tqdm
from typing import Any, Dict, List, Optional
import pandas as pd
from datetime import timedelta, datetime
from firefox_profiles import FINGERPRINTS
from bs4 import BeautifulSoup
from async_tls_client import AsyncSession
from colorthief import ColorThief
from coloraide import Color

import warnings
warnings.filterwarnings('ignore')
from pathlib import Path
import logging
from rich.logging import RichHandler

FORMAT = '%(message)s'
logging.basicConfig(
		level="INFO", format=FORMAT, datefmt="[%X]", handlers=[RichHandler()]
)
log = logging.getLogger('rich')

GOOD_PROFILES = Path("good_profiles.json")
LINKS_FILE = Path.cwd() / 'slushwave-bandcamp-links.txt'
ALBUM_MOD_DATES_JSON = Path("album_mod_dates.json")
ARTWORKS_JSONL =  Path("artworks.jsonl")
ARTWORK_URL = "https://f4.bcbits.com/img/a{art_id}_3"
TEST_URL = "https://giftsfromhome.bandcamp.com/album/-"

#STARTUP TEST PROFILES
async def _test_profile(profile):
	s = AsyncSession(client_identifier=profile)
	try:
		r = await s.get(TEST_URL)
		soup = BeautifulSoup(r.text, "lxml") # type: ignore
		challenged = (soup.title and soup.title.get_text(strip=True) == "Client Challenge")
		return profile, challenged

	except Exception:
		return profile, True
	
async def get_good_profiles():
	tasks = [_test_profile(profile) for profile in FINGERPRINTS]
	results = await asyncio.gather(*tasks, return_exceptions=False)
	failed = []
	good = []
	for profile, challenged in results:
		if challenged:
			failed.append(profile)
		else:
			good.append(profile)
	return good, failed

class BrowserSession:
	def __init__(self, ok_clients: list):
		self.ok_clients = ok_clients
		self.requests_made = 0
		self.new_session()
		self.retire_after = random.randint(40, 100)

	def rotate_client(self):
		self.client_identifier = random.choice(self.ok_clients)

	def new_session(self):
		self.rotate_client()

		self.session = AsyncSession(
			client_identifier=self.client_identifier,
			random_tls_extension_order=True
		)

		self.session.proxies.update({
			"http": os.getenv("mobileproxyuk"),
			"https": os.getenv("mobileproxyuk"),
		})

		self.session.headers.update({
			"Referer": "https://bandcamp.com/",
			"Accept-Language": "en-US,en;q=0.9",
		})

	async def get(self, url, **kwargs):
		if self.requests_made >= self.retire_after:
			self.new_session()
			self.requests_made = 0
			self.retire_after = random.randint(40, 100)

		resp = await self.session.get(url,**kwargs)
		self.requests_made += 1
		return resp


# FUNCTIONS TO USE
def nozero(text: Any) -> str:
		if text is None:
				return ""
		text = str(text)
		return re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)

def rgb_to_oklch(rgb):
	r, g, b = rgb
	c = Color("srgb", [r / 255, g / 255, b / 255]).convert("oklch")
	l, chroma, hue = c.coords()

	return {
		"l": round(l, 4),
		"c": round(chroma, 4),
		"h": round(hue or 0, 2)
	}

def pick_dominant_oklch(palette):
	candidates = []

	for rgb in palette:
		oklch = rgb_to_oklch(rgb)
		# discard near-black / near-white colors
		if oklch["l"] < 0.15 or oklch["l"] > 0.90:
			continue
		candidates.append(oklch)

	if not candidates:
		candidates = [rgb_to_oklch(rgb) for rgb in palette]

	return max(candidates, key=lambda c: c["c"])

class ArtworkScraper:
	def __init__(self, session_or_clients, sem=150):
		if isinstance(session_or_clients, BrowserSession):
			self.s = session_or_clients
		else:
			self.s = BrowserSession(session_or_clients)
		self.sem = asyncio.Semaphore(sem)
		self.lock = asyncio.Lock()
		self.seen_hash = set()

	def load_cache(self):
		if not ARTWORKS_JSONL.exists():
			return

		with open(ARTWORKS_JSONL, "r", encoding="utf-8") as f:
			for line in f:
				try:
					self.seen_hash.add(json.loads(line)["img_hash"])
				except (json.JSONDecodeError, KeyError):
					continue

	async def fetch_artwork(self, art_id):
		async with self.sem:
			r = await (self.s.get(ARTWORK_URL.format(art_id=art_id)))
			img = r.content
			img_hash = hashlib.blake2b(img,digest_size=8).hexdigest()

			async with self.lock:
				# Check if img_hash is already in artwork.jsonl
				if img_hash in self.seen_hash:
					return None
				self.seen_hash.add(img_hash)

			ct = ColorThief(BytesIO(img)) # type: ignore
			palette = ct.get_palette(color_count=9, quality=10)
			dom_color = pick_dominant_oklch(palette)

			record = {
				"art_id": str(art_id),
				"img_hash": img_hash,
				"dom_color": dom_color,
				"palette": [f"#{r:02X}{g:02X}{b:02X}" for r, g, b in palette],
				"date_fetched": datetime.now().strftime("%d %b %Y %H:%M:%S VNT")
			}

			async with self.lock:
				async with aiofiles.open(ARTWORKS_JSONL,"a",encoding="utf-8") as f:
					await f.write(json.dumps(record,ensure_ascii=False) + "\n")

			return record

	async def scrape_many(self, art_ids):
		tasks = [self.fetch_artwork(art_id) for art_id in art_ids]
		results = await asyncio.gather(*tasks, return_exceptions=True)

		return results
	

class AlbumScraper:
	def __init__(self, session_or_clients, sem=150):
		if isinstance(session_or_clients, BrowserSession):
			self.s = session_or_clients
		else:
			self.s = BrowserSession(session_or_clients)
		self.sem = asyncio.Semaphore(sem)
		self.mod_dates = {}
		self.seen_art_id = set()
		self.seen_hash = set()
		self.lock = asyncio.Lock()
	
	# Check mod date to see if album has been updated
	def load_cache(self):
		if not ALBUM_MOD_DATES_JSON.exists():
			return	

		with open(ALBUM_MOD_DATES_JSON, "r", encoding="utf-8") as f:
			self.mod_dates = json.load(f)

	async def get_release_data(self, url):
		async with self.sem:
			r = await self.s.get(url)
			soup = BeautifulSoup(r.text or "", "lxml")

			try:
				t_tralbum = json.loads(soup.select_one("[data-tralbum]").get("data-tralbum", "{}"))
			except Exception:
				log.exception(f"Couldn't fetch data-tralbum from {url}")
				return None

			# --- art id checks ---
			t_art_id = str(t_tralbum.get('art_id')).zfill(10)

			async with self.lock:
				# Check for cached art ids
				if t_art_id in self.seen_art_id:
					return None
				self.seen_art_id.add(t_art_id)

			return t_art_id

	async def scrape_album_page(self, soup) -> Dict[str, Any]:
		"""Fetch album page and returns all required metadata."""
		# soup_title = nozero(soup.title.get_text(strip=True))
		schema = json.loads(soup.select("script[type='application/ld+json']")[0].get_text(strip=True))
		tralbum = json.loads(soup.select_one("[data-tralbum]").get("data-tralbum", "{}"))
		current = tralbum.get('current')

		# Skip stale albums with no update
		url = schema.get('@id')
		mod_date = current.get("mod_date") or ""
		if mod_date == self.mod_dates.get(url):
			log.info(f"Skipping: no updates for {url}")
			return None
		self.mod_dates[url] = mod_date

		# LD+JSON: album name, artist, number of tracks, keywords/tags
		album_name = nozero((schema['name'] or current['title'] or ""))
		artist_name = nozero((schema['byArtist']['name'] or current['artist'] or ""))
		keywords = schema.get("keywords",[]) or []
		track_art_id = []

		# Total time: Check for number of tracks before computing
		total_time = timedelta(0)
		num_tracks = schema.get("numTracks") or current.get("track_count") or 0
		if int(num_tracks) > 0:
			track_info = tralbum.get('trackinfo')
			track_durs = [t['duration'] for t in track_info]
			total_time = timedelta(seconds=int(sum(track_durs)))

			# All track data
			track_urls = [t['item']['@id'] for t in schema["track"]["itemListElement"]]
			for coro in asyncio.as_completed(self.get_release_data(url) for url in track_urls):
				result = await coro
				if not result:
					continue
				t_art_id = result
				track_art_id.append(t_art_id)

		result = {
				"url": url,
				"album": album_name,
				"artist": artist_name,
				"total_time": str(total_time),
				"num_tracks": int(num_tracks),
				"keywords": keywords,
				"new_date": current.get("new_date") or "",
				"publish_date": current.get("publish_date") or "",
				"release_date": current.get("release_date") or "",				
				"mod_date": mod_date,
				"album_art_id": str(current.get("art_id")).zfill(10),
				"track_art_id": track_art_id,
		}

		return result

# ====================================
# OKAY!!! LET'S GET THIS THING RUNNING
# ====================================

async def get_ok_clients(skip=True):
	if GOOD_PROFILES.exists():
		use_cache = skip or (
			input("Retest client identifiers? [y/N]: ").strip().lower() != "y"
		)
		if use_cache:
			with open(GOOD_PROFILES) as f:
				return json.load(f)

	log.info("Testing client identifiers...")
	ok_clients, _ = await get_good_profiles()
	with open(GOOD_PROFILES, "w") as f:
		json.dump(ok_clients, f)
	return ok_clients

async def main():
	ok_clients = await get_ok_clients(skip=True)

	urls = [
		'https://giftsfromhome.bandcamp.com/album/-',
		'https://daysofblue.bandcamp.com/album/--12',
		'https://noproblematapes.bandcamp.com/album/--89',
		'https://geometriclullaby.bandcamp.com/album/geo-c07',
		'https://desertsand.bandcamp.com/album/vja-tal-qalb',
		'https://blackmoon00.bandcamp.com/album/-',
		'https://desertsand.bandcamp.com/album/perli-tal-passat',
		'https://delusivemystery.bandcamp.com/album/--12'
	]

	random.seed(42)
	s = BrowserSession(ok_clients=ok_clients)

	# ---- SCRAPING ALBUMS ----
	album_scraper = AlbumScraper(s, sem=150)
	artwork_scraper = ArtworkScraper(s, sem=150)
	album_scraper.load_cache()
	artwork_scraper.load_cache()
	failed_urls = set()
	soups = []
	albums = []
	art_ids = set()

	async def fetch(url):
		r = await s.get(url)
		soup = BeautifulSoup(r.text or "", "lxml")
		return url, soup

	start_time = time.time()
	for task in asyncio.as_completed(fetch(url) for url in urls):
		try:
			url, soup = await task
			if soup.title and soup.title.get_text(strip=True) == "Client Challenge": # type: ignore
				failed_urls.add(url)
				log.warning(f"Client Challenge with {s.client_identifier} - Couldn't fetch {url}")
				s.new_session()
				continue
			soups.append(soup)
		except Exception:
			log.exception("Fetch failed for whatever reason :(")

	log.info(f"{len(soups)} albums fetched in {time.time() - start_time:.4f} seconds")
	start_time = time.time()

	for soup in tqdm(soups, desc="Fetching albums"):
		album_data = await album_scraper.scrape_album_page(soup)
		if not album_data:
			continue
		albums.append(album_data)
		art_ids.add(album_data["album_art_id"])
		art_ids.update(album_data["track_art_id"])
	
	if not albums:
		log.info("No new or updated albums found. Exiting.")
		return

	timestamp = datetime.now().strftime("%y%m%dT%H%M")
	results_file = Path(f"albums_{timestamp}.json")
	with open(results_file, "w", encoding="utf-8") as f:
			json.dump(albums, f, indent=2, ensure_ascii=False)
	with open(ALBUM_MOD_DATES_JSON, "w", encoding="utf-8") as f:
		json.dump(album_scraper.mod_dates,f,ensure_ascii=False,indent=2)
	log.info(f"Albums data saved to: {results_file}")

	# ---- SCRAPING ARTWORKS ----
	artworks = await artwork_scraper.scrape_many(art_ids)

	new_saved_count = sum(
		1 for r in artworks
		if r is not None and not isinstance(r, Exception)
	)
	skipped_count = len(art_ids) - new_saved_count

	if ARTWORKS_JSONL.exists():
		log.info(f"Artworks data saved to: {ARTWORKS_JSONL}")
		log.info(f"{new_saved_count} new arts saved, "
						 f"{skipped_count} skipped "
						 f"in {time.time() - start_time:.4f} seconds")

if __name__ == "__main__":
	asyncio.run(main())