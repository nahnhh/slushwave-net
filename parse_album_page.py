import asyncio
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

BASE_DIR = Path.cwd()
LINKS_FILE = BASE_DIR / 'slushwave-bandcamp-links.txt'
OUTPUT_FILE = BASE_DIR / 'bc-albums-image-links.csv'
IMAGES_DIR = BASE_DIR / 'images'

#STARTUP TEST PROFILES
TEST_URL = "https://giftsfromhome.bandcamp.com/album/-"

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

class BrowserSession:
	def __init__(self, ok_clients: list):
		self.ok_clients = ok_clients
		self.new_session()
		self.requests_made = 0
		self.retire_after = random.randint(40, 120)
		self.lock = asyncio.Lock()

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
			async with self.lock:
				self.new_session()
			self.requests_made = 0
			self.retire_after = random.randint(40, 120)

		resp = await self.session.get(url,**kwargs)
		self.requests_made += 1
		return resp

def nozero(text: Any) -> str:
		if text is None:
				return ""
		text = str(text)
		return re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)

class Scraper:
	def __init__(self, session_or_clients, sem=150):
		if isinstance(session_or_clients, BrowserSession):
			self.s = session_or_clients
		else:
			self.s = BrowserSession(session_or_clients)
		self.sem = asyncio.Semaphore(sem)
		self.seen_icon = set()
		self.seen_hash = set()
		self.lock = asyncio.Lock()

	async def get_art_id(self, url):
		async with self.sem:
			r = await self.s.get(url)
			soup = BeautifulSoup(r.text or "", "lxml")

			icon_url = soup.select_one('link[rel="shortcut icon"]')['href'] # type: ignore
			# check for cached art urls
			async with self.lock:
				if icon_url in self.seen_icon:
					return None
				self.seen_icon.add(icon_url)

			img = await self.s.get(icon_url)
			img_hash = hashlib.sha256(img.content).hexdigest() # type: ignore

			async with self.lock:
				# check for hash dedupes
				if img_hash in self.seen_hash:
					return None
				self.seen_hash.add(img_hash)

			art_id = re.search(r'a(\d+)_', icon_url).group(1) # type: ignore

			return art_id

	async def scrape_album_page(self, soup) -> Dict[str, Any]:
		"""Fetch album page and returns all required metadata."""
		soup_title = nozero(soup.title.get_text(strip=True))
		schema = json.loads(soup.select("script[type='application/ld+json']")[0].get_text(strip=True))
		tralbum_tag = soup.select_one("[data-tralbum]")
		current = json.loads(tralbum_tag["data-tralbum"])['current']
		track_info = json.loads(tralbum_tag["data-tralbum"])['trackinfo']

		# LD+JSON: album name, artist, number of tracks, keywords/tags
		url = nozero(schema['@id'])
		album_name = nozero((schema['name'] or current['title'] or ""))
		artist_name = nozero((schema['byArtist']['name'] or current['artist'] or ""))
		num_tracks = nozero((schema['numTracks'] or current['track_count'] or ""))
		keywords = schema.get("keywords") if isinstance(schema, dict) else []

		# Total time
		track_durs = [t['duration'] for t in track_info]
		total_time = timedelta(seconds=int(sum(track_durs)))

		# All track images
		track_urls = [t['item']['@id'] for t in schema["track"]["itemListElement"]]
		track_art_id = []
		for coro in asyncio.as_completed(self.get_art_id(url) for url in track_urls):
			result = await coro
			if result:
				track_art_id.append(result)

		# Results in JSON format
		result = {
				"url": url,
				"album": album_name,
				"artist": artist_name,
				"total_time": str(total_time),
				"num_tracks": num_tracks,
				"keywords": keywords,
				"new_date": current.get("new_date") or "",
				"publish_date": current.get("publish_date") or "",
				"release_date": current.get("release_date") or "",
				"mod_date": current.get("mod_date") or "",
				"album_art_id": str(current.get("art_id")),
				"track_art_id": track_art_id
		}

		return result

# ====================================
# OKAY!!! LET'S GET THIS THING RUNNING
# ====================================

async def get_ok_clients():
	#STARTUP TEST CLIENTS
	force = input("Retest client identifiers? [y/N]:")
	cache_file = Path("good_profiles.json")
	if not force and cache_file.exists():
		with open(cache_file, "r") as f:
				ok_clients = json.load(f)
		log.info("Using good profiles (old cache)...")
	else:
		if not cache_file.exists():
			log.info("CACHE_FILE doesn't exist. Starting test...")
		ok_clients, _ = await _get_good_profiles()
		with open(cache_file, "w") as f:
				json.dump(ok_clients, f)
		log.info("Using good profiles (new cache)...")
	return ok_clients

async def main():
	ok_clients = await get_ok_clients()

	urls = [
		'https://giftsfromhome.bandcamp.com/album/-',
		'https://daysofblue.bandcamp.com/album/--12',
		'https://noproblematapes.bandcamp.com/album/--89',
		'https://geometriclullaby.bandcamp.com/album/geo-c07',
		'https://desertsand.bandcamp.com/album/vja-tal-qalb',
		'https://blackmoon00.bandcamp.com/album/-'
	]

	random.seed(42)
	s = BrowserSession(ok_clients=ok_clients)
	scraper = Scraper(s, sem=150)
	failed = []
	soups = []
	results = []

	start_time = time.time()

	async def fetch(url):
		r = await s.get(url)
		soup = BeautifulSoup(r.text or "", "lxml")
		return url, r, soup

	for coro in asyncio.as_completed(fetch(url) for url in urls):
		url, _, soup = await coro

		if soup.title and soup.title.get_text(strip=True) == "Client Challenge":
			failed.append({"url": url, "profile": s.client_identifier})
			log.warning(f"Client Challenge with {s.client_identifier}")
			s.new_session()
			continue

		soups.append(soup)

	# print([nozero(soup.title.get_text(strip=True)) for soup in soups])
	log.info(f"{len(soups)} albums fetched in {time.time() - start_time:.4f} seconds ")
	start_time = time.time()

	for soup in tqdm(soups, desc="Getting data"):
		result = await scraper.scrape_album_page(soup)
		results.append(result)

	timestamp = datetime.now().strftime("%y%m%dT%H%M")
	results_file = Path(f"source_{timestamp}.json")
	with open(results_file, "w", encoding="utf-8") as f:
			json.dump(results, f, indent=2, ensure_ascii=False)
	log.info(f"Saved to: {results_file}")
	log.info("--- %s seconds ---" % (time.time() - start_time))

if __name__ == "__main__":
	asyncio.run(main())