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
LINKS_FILE = "slushwave-bandcamp-links.txt"
ALBUM_MOD_DATES_JSON = Path("album_mod_dates.json")
ALBUMS_JSONL = Path("albums.jsonl")
ARTWORKS_JSONL =  Path("artworks.jsonl")
TEST_URL = "https://giftsfromhome.bandcamp.com/album/-"
ARTWORK_URL = "https://f4.bcbits.com/img/a{art_id}_3"
ALBUM_URL = re.compile(r"https://[a-zA-Z0-9-]+\.bandcamp\.com/album/\S+")

# --- STARTUP TEST PROFILES ---
class ClientChallenge(Exception):
	pass
class IsntHere(Exception):
	pass

async def _test_profile(profile):
	s = AsyncSession(client_identifier=profile)
	try:
		r = await s.get(TEST_URL)
		soup = BeautifulSoup(r.text, "lxml") # type: ignore
		challenged = (soup.title and soup.title.get_text(strip=True) == "Client Challenge")
		return profile, challenged

	except Exception:
		return profile, True
	
async def _get_good_profiles():
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

async def get_ok_clients(skip=True):
	if GOOD_PROFILES.exists():
		use_cache = skip or (
			input("Retest client identifiers? [y/N]: ").strip().lower() != "y"
		)
		if use_cache:
			with open(GOOD_PROFILES) as f:
				return json.load(f)

	log.info("Testing client identifiers...")
	ok_clients, _ = await _get_good_profiles()
	with open(GOOD_PROFILES, "w") as f:
		json.dump(ok_clients, f)
	return ok_clients

# --- TLS CLIENT ASYNC SESSION ---

class BrowserSession:
	def __init__(self, ok_clients: list, sem=150):
		self.ok_clients = ok_clients
		self.requests_made = 0
		self.sem = asyncio.Semaphore(sem)
		self.session_lock = asyncio.Lock()
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
		async with self.sem:
			async with self.session_lock:
				if self.requests_made >= self.retire_after:
					self.new_session()
					self.requests_made = 0
					self.retire_after = random.randint(40, 100)
				session = self.session
				self.requests_made += 1
			return await session.get(url,**kwargs)
	
	async def fetch(self, url):
		"""Fetch soup from url + Client Challenge + Non existent page exception"""
		try:
			r = await self.get(url)
			if r.status_code == 404:
				raise IsntHere
			soup = BeautifulSoup(r.text or "", "lxml")
			title = soup.title.get_text(strip=True) if soup.title else ""
			if title == "Client Challenge":
				raise ClientChallenge
			return soup
		except ClientChallenge:
			log.warning(f"Client Challenge with {self.client_identifier} - Couldn't fetch {url}")
			return None
		except IsntHere:
			log.info(f"SKIP: Page isn't here {url}")
			return None
		except Exception:
			log.exception(f"Failed: {url}")
			return None


# --- FUNCTIONS TO USE ---
def unique(input_list):
	return list(set(input_list))

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
			self.s = BrowserSession(session_or_clients, sem)
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
			img_hash = hashlib.blake2b(img,digest_size=8).hexdigest() # type: ignore

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
	

# --- PHASE (0) URL DISCOVERY + (1) PARSE ALBUM DATA ---
# --- Artist urls (Music pages) list -> Music page soup -> Album urls -> Album soup -> schema, tralbum -> Album data + Track urls ---
class AlbumScraper:
	def __init__(self, session_or_clients, sem=150, load_cache=False):
		if isinstance(session_or_clients, BrowserSession):
			self.s = session_or_clients
		else:
			self.s = BrowserSession(session_or_clients, sem)
		self.mod_dates = {}
		self.album_urls = set()
		self.has_cache = load_cache

	def load_cache(self, skip=False):
		"""Load {url: mod_date} to check for stale albums later."""
		if skip or not ALBUM_MOD_DATES_JSON.exists():
			self.has_cache = False
			return
		with open(ALBUM_MOD_DATES_JSON, "r", encoding="utf-8") as f:
			self.mod_dates = json.load(f)
		self.has_cache = True

	# --- 0. Read from artist list -> music page soup -> album urls ---
	async def _fetch_albums_from_artist(self, artist_url) -> set[str]:
		"""Get response & soup from Music page -> extract album urls"""
		artist_url = artist_url.rstrip("/")
		soup = await self.s.fetch(artist_url)
		if soup is None:
			return set()
		links = (
			soup.select("li.music-grid-item a[href]")
			or soup.select("div.ipCellImage a[href]")
		)
		album_urls = {
			href if href.startswith("http") # type: ignore
			else artist_url + href
			for href in (a["href"] for a in links)
		}
		return album_urls # type: ignore

	async def get_all_album_urls(self, file_or_list=LINKS_FILE):
		"""
		Get all album urls from a .txt file with all links listed.
			+) Music (artist) page url -> music soup -> extract album urls, update to result
			+) Album page url -> skip
		"""
		if isinstance(file_or_list, (list,set,tuple)):
			urls = set(file_or_list)
		else:
			with open(Path(file_or_list), "r", encoding="utf-8") as f:
				urls = {line.strip() for line in f if line.strip()}
		album_urls = {url for url in urls if ALBUM_URL.match(url)}
		artist_urls = urls - album_urls
		url_lists = await asyncio.gather(
			*(self._fetch_albums_from_artist(url) for url in artist_urls),
			return_exceptions=False
		)
		album_urls.update(album_url for urls in url_lists for album_url in urls)
		self.album_urls.update(album_urls)
	
	# --- 1. Read album urls -> album page soup -> alt album urls + album data + track urls ---
	def _get_alt_album_urls(self, album_schema, url=None):
		"""Get other album urls in description/credits -> Update to {album_urls}"""
		if not url:
			url = album_schema.get('@id')
		text = (
			(album_schema.get("description") or "") + " " +
			(album_schema.get("creditText") or "")
		)
		alt_urls = ALBUM_URL.findall(text)
		if alt_urls:
			log.info(f"{len(alt_urls)} other album url found in {url}")
		self.album_urls.update(alt_urls)

	async def _scrape_album_page(self, url) -> dict:
		"""
		Fetch an album page, checks all skips and returns required album metadata.
		Skips not slushwave -> Get alt album urls -> Skip no tracks -> Skip stale albums.
		"""
		try:
			soup = await self.s.fetch(url)
			if not soup:
				return {}
			schema = json.loads(soup.select_one("script[type='application/ld+json']").get_text(strip=True)) # type: ignore

			# Skip non slushwave releases
			keywords = schema.get('keywords',[])
			if not {k.lower() for k in keywords} & {"slushwave"}:
				log.info(f"SKIP: Not slushwave {url}")
				return {}

			self._get_alt_album_urls(schema, url)

			# Skip no tracks
			num_tracks = schema.get('numTracks') or 0
			if int(num_tracks) == 0:
				log.info(f"SKIP: No tracks in {url}")
				return {}
			
			tralbum = json.loads(soup.select_one("[data-tralbum]").get("data-tralbum","{}")) # type: ignore
			current = tralbum.get('current')

			# Skip stale albums with no updates
			mod_date = current.get("mod_date") or ""
			if mod_date == self.mod_dates.get(url):
				log.info(f"SKIP: No updates for {url}")
				return {}
			self.mod_dates[url] = mod_date

			# Get album metadata (finally)
			album_name = nozero((schema['name'] or current['title'] or ""))
			artist_name = nozero((schema['byArtist']['name'] or current['artist'] or ""))
			track_info = tralbum.get('trackinfo')
			track_urls = [t.get('title_link') for t in track_info]
			runtime = timedelta(seconds=int(sum(t.get('duration', 0) for t in track_info)))

			result = {
					"url": url,
					"album": album_name,
					"artist": artist_name,
					"runtime": str(runtime),
					"num_tracks": int(num_tracks),
					"keywords": keywords,
					"new_date": current.get('new_date') or "",
					"publish_date": current.get('publish_date') or "",
					"release_date": current.get('release_date') or "",				
					"mod_date": mod_date,
					"album_id": tralbum.get('id'),
					"album_art_id": current.get('art_id'),
					"track_urls": track_urls,
			}
			return result
		except Exception:
			log.exception(f"Failed to parse {url}")
			return {}
	
	async def scrape_all_albums(self, seed_urls=None) -> list:
		start_time = time.time()
		if seed_urls:
			self.album_urls.update(seed_urls)
		pbar = tqdm(total=len(self.album_urls), desc="Albums", unit="album")
		results = []
		processed_urls = set()
		try:
			while True:
				batch = self.album_urls - processed_urls
				if not batch:
					break
				fetched = await asyncio.gather(
					*(self._scrape_album_page(url) for url in batch))
				processed_urls.update(batch)

				pbar.update(len(batch))
				if len(self.album_urls) > (pbar.total or 0):
					pbar.total = len(self.album_urls)
					pbar.refresh()
				
				results.extend(item for item in fetched if item)

				pbar.set_postfix(
					known=len(self.album_urls),
					processed=len(processed_urls),
					results=len(results),
				)
		finally:
			pbar.close()
			if not results:
				log.info("No new or updated albums found. Exiting.")
			log.info(
				f"Finished in {time.time() - start_time:.4f} seconds: "
				f"{len(processed_urls)} URLs -> {len(results)} albums"
			)
		return results
	
	def save_results(self, results):
		if self.has_cache:
			with open(ALBUMS_JSONL, "a", encoding="utf-8") as f:
				for album in results:
					f.write(json.dumps(album, ensure_ascii=False) + "\n")
			log.info(f"Append: Added {len(results)} to {ALBUMS_JSONL}")
		else:
			with open(ALBUMS_JSONL, "w", encoding="utf-8") as f:
				for album in results:
					f.write(json.dumps(album, ensure_ascii=False) + "\n")
			log.info(f"Override: Saved {len(results)} to {ALBUMS_JSONL}")

		with open(ALBUM_MOD_DATES_JSON, "w", encoding="utf-8") as f:
			json.dump(self.mod_dates,f,ensure_ascii=False,indent=2)

# ====================================
# OKAY!!! LET'S GET THIS THING RUNNING
# ====================================

async def main():
	start_time = time.time()
	ok_clients = await get_ok_clients(skip=True)
	random.seed(42)
	s = BrowserSession(ok_clients=ok_clients)

	# ---- SCRAPING ALBUMS ----
	album_scraper = AlbumScraper(s, sem=150, load_cache=False)

	log.info(f"Fetching album urls...")
	urls = 'seed_urls.txt'
	await album_scraper.get_all_album_urls(urls)
	results = await album_scraper.scrape_all_albums()
	if not results:
		log.info("No new or updated albums found. Exiting.")
		return
	album_scraper.save_results(results)

	# ---- SCRAPING ARTWORKS ----
	# artwork_scraper = ArtworkScraper(s, sem=150)
	# artwork_scraper.load_cache()
	# artworks = await artwork_scraper.scrape_many(art_ids)

	# new_saved_count = sum(
	# 	1 for r in artworks
	# 	if r is not None and not isinstance(r, Exception)
	# )
	# skipped_count = len(art_ids) - new_saved_count

	# if ARTWORKS_JSONL.exists():
	# 	log.info(f"Artworks data saved to: {ARTWORKS_JSONL}")
	# 	log.info(f"{new_saved_count} new arts saved, "
	# 			f"{skipped_count} skipped "
	# 			f"in {time.time() - start_time:.4f} seconds")

	log.info(f"Total time: {time.time() - start_time:.4f} seconds")

if __name__ == "__main__":
	asyncio.run(main())