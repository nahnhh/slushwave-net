import asyncio
import json
import os
import random
import re
import hashlib
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
		soup = BeautifulSoup(r.text, "lxml")
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

def nozero(text: Any) -> str:
		if text is None:
				return ""
		text = str(text)
		return re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)

class Scraper:
	def __init__(self, session_or_clients):
		if isinstance(session_or_clients, BrowserSession):
			self.s = session_or_clients
		else:
			self.s = BrowserSession(session_or_clients)
	
	async def get_art_id(self, url):
		r = await self.s.get(url)
		soup = BeautifulSoup(r.text or "", "lxml")

		icon_url = soup.select_one('link[rel="shortcut icon"]')['href']

		img = await self.s.get(icon_url)
		img_hash = hashlib.sha256(img.content).hexdigest()

		art_id = re.search(r'a(\d+)_', icon_url).group(1)

		return art_id, img_hash

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

		# All track urls & total time
		track_url_df = pd.DataFrame([
			{
				"url": t['item']['@id']
			}
			for t in schema["track"]["itemListElement"]
		])
		track_dur_df = pd.DataFrame([
			{
				"position": t['track_num'],
				"duration": t['duration'],
			}
			for t in track_info
		])
		total_time = timedelta(seconds=int(track_dur_df["duration"].sum()))

		# All track images
		results = await asyncio.gather(
		*(self.get_art_id(url) for url in track_url_df["url"])
		)

		track_art_df = pd.DataFrame(results, columns=["art_id", "img_hash"]).drop_duplicates(subset=["img_hash"])
		log.info(f"{soup_title} - Unique image hashes: {track_art_df['img_hash'].nunique()}")
		

		result = {
				"url": url,
				"album": album_name,
				"artist": artist_name,
				"num_tracks": num_tracks,
				"keywords": keywords,
				"total_time": str(total_time),
				"release_date": current.get("release_date") or "",
				"publish_date": current.get("publish_date") or "",
				"new_date": current.get("new_date") or "",
				"mod_date": current.get("mod_date") or "",
				"album_art_id": current.get("art_id"),
				"track_art_id": track_art_df.get("art_id").tolist()
		}

		return result

# =======
# OKAY!!!
# =======

async def get_ok_clients():
	#STARTUP TEST CLIENTS
	force = input("Retest client identifiers? [y/N]:").strip().lower() in ('y', 'yes')
	cache_file = Path("good_profiles.json")
	if not force and cache_file.exists():
		with open(cache_file, "r") as f:
				ok_clients = json.load(f)
		print("Good Profiles (old cache):")
	else:
		if not cache_file.exists():
			print("CACHE_FILE doesn't exist. Starting test...")
		ok_clients, _ = await _get_good_profiles()
		with open(cache_file, "w") as f:
				json.dump(ok_clients, f)
		print("Good Profiles (new cache):")
	print(ok_clients)

async def main():
	OK_CLIENTS = await get_ok_clients()

	url1 = 'https://daysofblue.bandcamp.com/album/--12'
	url2 = 'https://noproblematapes.bandcamp.com/album/--89'
	url3 = 'https://geometriclullaby.bandcamp.com/album/geo-c07'
	urls = [url1, url2, url3]

	random.seed(42)
	s = BrowserSession(OK_CLIENTS)
	scraper = Scraper(s)
	failed = []
	soups = []
	results = []

	for url in urls:
		r = await s.get(url)
		soup = BeautifulSoup(r.text, 'lxml')
		if soup.title and soup.title.get_text(strip=True) == "Client Challenge":
			failed.append({
				"url": url,
				"profile": s.client_identifier,
				})
			log.warning(f"Client Challenge with {s.client_identifier} for {url}")
			s.new_session()
			continue
		soups.append(soup)

	print("Albums fetched:")
	print([nozero(soup.title.get_text(strip=True)) for soup in soups])

	for soup in tqdm(soups, desc="Extracting metadata from albums..."):
		result = await scraper.scrape_album_page(soup)
		results.append(result)

	timestamp = datetime.now().strftime("%y%m%dT%H%M")
	results_file = Path(f"results_{timestamp}.json")
	with open(results_file, "w", encoding="utf-8") as f:
			json.dump(results, f, indent=2, ensure_ascii=False)
	log.info(f"Saved to {results_file}")

if __name__ == "__main__":
	asyncio.run(main())