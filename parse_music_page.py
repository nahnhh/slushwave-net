import asyncio
import aiofiles
from io import BytesIO
import json
import os
import random
import re
import hashlib
import time
from firefox_profiles import FINGERPRINTS
from collections import defaultdict
from tqdm import tqdm
from datetime import timedelta, datetime
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
LINKS_FILE_COPY = Path("slushwave-bandcamp-links copy.txt")
ARTISTS_SLUSHWAVE_JSONL = Path("all_slushwave_artists.jsonl")
ALBUM_MOD_DATES_JSON = Path("album_mod_dates.json")
ALBUMS_JSONL = Path("albums.jsonl")
ART_IDS_JSONL = Path("art_release_ids.jsonl")
ART_IDS_JSONL = Path("art_release_ids.jsonl")
ARTWORKS_JSONL =  Path("artworks.jsonl")
TEST_URL = "https://giftsfromhome.bandcamp.com/album/-"
ARTWORK_URL = "https://f4.bcbits.com/img/a{art_id}_3"
ALBUM_URL = re.compile(r"https://[a-zA-Z0-9-]+\.bandcamp\.com/album/\S+")
SINGLE_URL = re.compile(r"https://[a-zA-Z0-9-]+\.bandcamp\.com/track/\S+")

# --- STARTUP TEST PROFILES ---
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
class TooManyRequests(Exception):
	pass
class IsntHere(Exception):
	pass
class ClientChallenge(Exception):
	pass

class BrowserSession:
	def __init__(self, ok_clients: list, sem=50, requests_per_sec=2):
		self.ok_clients = ok_clients
		self.requests_made = 0
		self.sem = asyncio.Semaphore(sem)
		self.new_session()
		self.rate_lock = asyncio.Lock()
		self.last_request = 0.0
		self.min_interval = 1 / requests_per_sec

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

	async def _wait_for_rate_limit(self):
		async with self.rate_lock:
			now = time.monotonic()
			wait = self.min_interval - (now - self.last_request)
			if wait > 0:
				await asyncio.sleep(wait)
			self.last_request = time.monotonic()

	async def get(self, url, **kwargs):
		for attempt in range(3):
			try:
				async with self.sem:
					await self._wait_for_rate_limit()
					r = await self.session.get(url, **kwargs)
				if r.status_code == 429:
					backoff = random.uniform(
						7 * (attempt + 1),
						14 * (attempt + 1)
					)
					log.warning(
						f"429 ({attempt+1}/3), sleeping {backoff:.1f} seconds"
					)
					await asyncio.sleep(backoff)
					continue
				return r
			except Exception:
				if attempt < 2:
					await asyncio.sleep(2 * (attempt + 1))
					continue
				raise

		raise TooManyRequests
	
	async def _check_response(self, url):
		"""Checks response for 404 or Client Challenge"""
		r = await self.get(url)
		if r.status_code == 404:
			log.info(f"SKIP: Page isn't here {url}")
			return None
		soup = BeautifulSoup(r.text or "", "lxml")
		title = soup.title.get_text(strip=True) if soup.title else ""
		if title == "Client Challenge":
			raise ClientChallenge
		return soup	

	async def fetch(self, url):
		"""Fetch soup from URL."""
		try:
			soup = await self._check_response(url)
			if soup:
				a = soup.select_one("body a[href]")
				if (a and soup.body and 
					soup.body.get_text().startswith("You are being redirected")):
					redirect_url = a["href"]
					log.info(f"Redirect {url} -> {redirect_url}")
					soup = await self._check_response(redirect_url)
				return soup
		except ClientChallenge:
			log.warning(f"Client Challenge: {url}")
			return None
		except TooManyRequests:
			log.warning(f"Too many requests after retries: {url}")
			return None
		except Exception:
			log.exception(f"Failed: {url}")
			return None


# --- FUNCTIONS TO USE ---
def split_to_batches(items, batch_size=8):
	for i in range(0, len(items), batch_size):
		yield items[i:i + batch_size]

def nozero(text) -> str:
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


# --- PHASE (2) BUILD ART_IDS.JSONL + (3) ARTWORKS.JSONL ---
# --- Release results from AlbumScraper -> hash, dominant color, palette ---
class ArtworkScraper:
	def __init__(self, session_or_clients, sem=300, use_cache=True):
		if isinstance(session_or_clients, BrowserSession):
			self.s = session_or_clients
		else:
			self.s = BrowserSession(session_or_clients, sem)
		self.use_cache = use_cache
		self.release_ids = {}
		self.release_ids = {}
		self.artworks = {}		# artworks metadata created
		self._load_cache()

	def _load_cache(self):
		"""Load image hashes to dedup later."""
		if not self.use_cache or not ARTWORKS_JSONL.exists():
			return
		with open(ART_IDS_JSONL, "r", encoding="utf-8") as f:
			for line in f:
				try:
					record = json.loads(line)
					release_id = record["release_id"]
					self.release_ids[release_id] = record
				except Exception:
					continue
		with open(ART_IDS_JSONL, "r", encoding="utf-8") as f:
			for line in f:
				try:
					record = json.loads(line)
					release_id = record["release_id"]
					self.release_ids[release_id] = record
				except Exception:
					continue
		with open(ARTWORKS_JSONL, "r", encoding="utf-8") as f:
			for line in f:
				try:
					record = json.loads(line)
					h = record["img_hash"]
					self.artworks[h] = record
				except Exception:
					continue

	def _load_release_data(self, file_or_list=ALBUMS_JSONL):
		"""
		Return a list of release records created by AlbumScraper.
		Accepts:
			- albums.jsonl path
			- list returned from scrape_all_albums()
		"""
		if isinstance(file_or_list, list):
			return file_or_list
		with open(file_or_list, "r", encoding="utf-8") as f:
			return [json.loads(line) for line in f if line.strip()]

	async def _get_art_id_from_url(self, url):
		"""Get (track) art id from data-tralbum."""
		soup = await self.s.fetch(url)
		try:
			tralbum = json.loads(soup.select_one("[data-tralbum]").get("data-tralbum", {})) # type: ignore
			art_id = tralbum.get('art_id')
			return art_id
		except Exception:
			log.exception(f"Couldn't fetch data-tralbum from {url}")
			return None
	
	async def _fetch_artwork_data(self, art_id):
		"""Get artwork, hash, color palette and dominant color from art id."""
		# --- Get artwork & hash ---
		r = await self.s.get(ARTWORK_URL.format(art_id=art_id))
		img = r.content
		img_hash = hashlib.blake2b(img,digest_size=8).hexdigest() # type: ignore
		if img_hash in self.artworks:
			return {
				"img_hash": img_hash,
				"art_id": art_id
			}
		# --- Get palette & dominant color ---
		ct = ColorThief(BytesIO(img)) # type: ignore
		palette = ct.get_palette(color_count=9, quality=10)
		dom_color = pick_dominant_oklch(palette)

		return {
			"img_hash": img_hash,
			"art_id": art_id,
			"dom_color": dom_color,
			"palette": [f"#{r:02X}{g:02X}{b:02X}" for r, g, b in palette],
			"date_fetched": datetime.now().strftime("%d %b %Y %H:%M:%S VNT")
		}

	async def _scrape_unique_artworks(self, release):
		"""
		Extract album + track art ids from 1 Release record in albums.jsonl.
		Adds 1 record to self.artworks -> artworks.jsonl.
		Returns 1 record for art_ids.jsonl.
		"""
		# --- Fetch track art ids ---
		url = release["url"]
		track_num_by_art_id = defaultdict(list)
		if ALBUM_URL.match(url):
			base_url = url.rsplit("/album/",1)[0]
			track_urls = [t if t.startswith("http") else base_url + t
							for t in release.get("track_urls", [])]
			track_art_ids = []
			for batch in split_to_batches(track_urls, 5):
				ids = await asyncio.gather(
					*(self._get_art_id_from_url(u) for u in batch)
				)
				track_art_ids.extend(ids)
				await asyncio.sleep(random.uniform(0.5, 1.5))
			track_art_ids = []
			for batch in split_to_batches(track_urls, 5):
				ids = await asyncio.gather(
					*(self._get_art_id_from_url(u) for u in batch)
				)
				track_art_ids.extend(ids)
				await asyncio.sleep(random.uniform(0.5, 1.5))
			# --- Get track numbers ---
			release_art_id = release["album_art_id"]
			for track_num, art_id in enumerate(track_art_ids,start=1):
				if art_id:
					track_num_by_art_id[art_id].append(track_num)
			# --- Fetch unique artworks from art ids ---
			unique_art_ids = {release_art_id, *filter(None, track_art_ids)}
			artworks = await asyncio.gather(
				*(self._fetch_artwork_data(art_id)
					for art_id in unique_art_ids)
			)
		else: # SINGLE URL
			release_art_id = await self._get_art_id_from_url(url)
			artworks = [await self._fetch_artwork_data(release_art_id)]

		# --- Build artworks.jsonl: Lookup artwork data via hash ---
		art_id_to_hash = {}
		for art in artworks:
			if not art:
				continue
			h = art["img_hash"]
			art_id_to_hash[art["art_id"]] = h

			if "palette" in art and h not in self.artworks:
				self.artworks[h] = {
					"img_hash": h,
					"dom_color": art["dom_color"],
					"palette": art["palette"],
					"in_release": [],
					"date_fetched": art["date_fetched"]
				}

		# --- Build art_ids.jsonl: Lookup all artworks in a release ---
		release_id = release["album_id"]
		if self.use_cache and release_id in self.release_ids:
			return self.release_ids[release_id]
		for h in set(art_id_to_hash.values()):
			if release_id not in self.artworks[h]["in_release"]:
				self.artworks[h]["in_release"].append(release_id)
		
		artworks = {}
		release_art_hash = art_id_to_hash[release_art_id]
		artworks[release_art_hash] = {
			"art_id": [release_art_id],
			"track_num": [0] # release/album art = track 0
		}
		for art_id, track_nums in track_num_by_art_id.items():
			if art_id == release_art_id:
				continue
			h = art_id_to_hash[art_id]
			if h not in artworks:
				artworks[h] = {
					"art_id": [],
					"track_num": []
				}
			artworks[h]["art_id"].append(art_id)
			artworks[h]["track_num"].extend(track_nums)
		record = {
			"release_id": release["album_id"],
			"artworks": artworks,
		}
		self.release_ids[release_id] = record
		return record
	
	async def scrape_all_artworks(self, file_or_list):
		releases = self._load_release_data(file_or_list)
		results = []
		tasks = [self._scrape_unique_artworks(release)
				 for release in releases]
		with tqdm(total=len(releases), desc="Artworks", unit="album") as pbar:
			for future in asyncio.as_completed(tasks):
				try:
					result = await future
					if result:
						results.append(result)
				except Exception:
					log.exception("Artwork scrape failed")

		tasks = [self._scrape_unique_artworks(release)
				 for release in releases]
		with tqdm(total=len(releases), desc="Artworks", unit="album") as pbar:
			for future in asyncio.as_completed(tasks):
				try:
					result = await future
					if result:
						results.append(result)
				except Exception:
					log.exception("Artwork scrape failed")

				pbar.update(1)
				pbar.set_postfix(
					processed=len(results),
					artworks=len(self.artworks),
				)

		return results

	def save_results(self, results):
		# update release cache
		for record in results:
			self.release_ids[record["release_id"]] = record
		# write release cache
		with open(ART_IDS_JSONL, "w", encoding="utf-8") as f:
			for record in self.release_ids.values():
				f.write(json.dumps(record, ensure_ascii=False) + "\n")
		# write artwork cache
		with open(ARTWORKS_JSONL, "w", encoding="utf-8") as f:
			for record in self.artworks.values():
				f.write(json.dumps(record, ensure_ascii=False) + "\n")
		log.info(
			f"Added {len(results)} release_id records "
			f"and saved {len(self.artworks)} artwork records"
		)
		
# --- PHASE (0) URL DISCOVERY + (1) PARSE ALBUM DATA ---
# --- Artist urls (Music pages) list -> Music page soup -> Album urls -> Album soup -> schema, tralbum -> Album data + Track urls ---
class AlbumScraper:
	def __init__(self, session_or_clients, sem=150, use_cache=True, skip_mode="historical"): # "historical" | "stale" 
		if isinstance(session_or_clients, BrowserSession):
			self.s = session_or_clients
		else:
			self.s = BrowserSession(session_or_clients, sem)
		self.use_cache = use_cache
		self.skip_mode = skip_mode
		self.mod_dates = {}
		self.artists_slushwave = {}
		self.albums = {}
		self.album_urls = set()
		self._load_cache()

	def _load_cache(self):
		if not self.use_cache:
			return
		if ARTISTS_SLUSHWAVE_JSONL.exists():
			with open(ARTISTS_SLUSHWAVE_JSONL, "r", encoding="utf-8") as f:
				for line in f:
					try:
						record = json.loads(line)
						artist_url = record["artist_url"].rstrip("/")
						slushwave = record.get("slushwave", [])
						not_slushwave = record.get("not_slushwave", [])
						self.artists_slushwave[artist_url] = {
							"artist_url": artist_url,
							"slushwave": slushwave,
							"not_slushwave": not_slushwave,
						}
					except Exception:
						continue
		if ALBUMS_JSONL.exists():
			with open(ALBUMS_JSONL, "r", encoding="utf-8") as f:
				for line in f:
					try:
						album = json.loads(line)
						self.albums[album["album_id"]] = album
					except Exception:
						continue
		if ALBUM_MOD_DATES_JSON.exists():
			with open(ALBUM_MOD_DATES_JSON, "r", encoding="utf-8") as f:
				self.mod_dates = json.load(f)

	# --- 0. Read from artist list -> music page soup -> album urls ---
	def _should_skip_release(self, artist_url: str, rel_url: str) -> bool:
		cached = self.artists_slushwave.get(artist_url, {})
		if self.skip_mode == "historical":
			return (
				rel_url in cached.get("slushwave", [])
				or rel_url in cached.get("not_slushwave", [])
			)
		if self.skip_mode == "stale":
			return rel_url in cached.get("not_slushwave", [])
		return False

	async def _fetch_releases_from_artist(self, artist_url) -> set[str]:
		"""Get response & soup from Music page -> extract album urls"""
		artist_root = artist_url.removesuffix("/music")
		await asyncio.sleep(random.uniform(0.5,1))
		soup = await self.s.fetch(artist_url)
		if not soup:
			return set()
		links = (
			soup.select("li.music-grid-item a[href]")
			or soup.select("div.ipCellImage a[href]")
		)
		release_urls = {
			a["href"] for a in links
			if not self._should_skip_release(artist_root, a["href"])
		}
		if not release_urls:
			log.info(f"SKIP: No new releases by {artist_root}")
			return set()

		album_urls = {rel if rel.startswith("http") 
					else artist_root + rel 
					for rel in release_urls}
		return album_urls

	async def get_all_release_urls(self, file_or_list):
		"""
		Get all album urls from a .txt file with all links listed.
			+) Music (artist) page url -> music soup -> extract album urls
			+) Album page url -> keep directly
			+) Music (artist) page url -> music soup -> extract album urls
			+) Album page url -> keep directly
		"""
		if isinstance(file_or_list, (list, set, tuple)):
			urls = set(file_or_list)
		else:
			with open(Path(file_or_list), "r", encoding="utf-8") as f:
				urls = {line.strip() for line in f if line.strip()}
		album_urls = {
			url for url in urls
			if ALBUM_URL.match(url) or SINGLE_URL.match(url)
		}
		artist_urls = urls - album_urls
		url_lists = await asyncio.gather(
			*(self._fetch_releases_from_artist(url.rstrip("/") + "/music") for url in artist_urls),
			return_exceptions=True
		)
		for result in url_lists:
			if isinstance(result, Exception):
				log.exception(
					f"Artist page fetch failed for {result}",
					exc_info=result
				)
				continue
			if result:
				album_urls.update(result) # type: ignore
		self.album_urls.update(album_urls)

	# --- Read album urls -> album page soup -> alt album urls + album data + track urls ---
	def _get_alt_album_urls(self, album_schema, url=None):
		"""Get other album urls in description/credits -> Update to {album_urls}"""
		if not url:
			url = album_schema.get('@id')
		text = (
			(album_schema.get("description") or "") + " " +
			(album_schema.get("creditText") or "")
		)
		alt_urls = set(ALBUM_URL.findall(text))
		if alt_urls:
			log.info(f"{len(alt_urls)} other album url found in {url}")
		self.album_urls.update(alt_urls)

	def _is_genre(self, keywords, genres=("slush","nature","ambient","dreamtone","signal","transmission","mallsoft")):
		kw = {k.lower() for k in (keywords or [])}
		for keyword in kw:
			if any(genre in keyword for genre in genres):
				return True
		return False

	def _record_release(self, artist_url, url, is_target):
		rel = url[len(artist_url):] if url.startswith(artist_url) else url
		record = self.artists_slushwave.setdefault(
			artist_url,
			{
				"artist_url": artist_url,
				"slushwave": [],
				"not_slushwave": []
			}
		)
		key = "slushwave" if is_target else "not_slushwave"
		if rel not in record[key]:
			record[key].append(rel)

	async def _scrape_album_page(self, url) -> dict:
		"""
		Fetch an album page, checks all skips and returns required album metadata.
		Skips not slushwave -> Get alt album urls -> Skip no tracks -> Skip stale albums.
		"""
		try:
			soup = await self.s.fetch(url)
			if not soup:
				return {}
			tralbum = json.loads(soup.select_one("[data-tralbum]").get("data-tralbum","{}")) # type: ignore
			current = tralbum.get('current')

			# Skip stale albums with no updates
			mod_date = current.get("mod_date") or ""
			if mod_date == self.mod_dates.get(url):
				log.info(f"SKIP: No updates for {url}")
				return {}

			schema = json.loads(soup.select_one("script[type='application/ld+json']").get_text(strip=True)) # type: ignore

			# Skip non slushwave releases
			keywords = schema.get('keywords',[])
			artist_url = (url.split("/album/")[0] if "/album/" in url else url.split("/track/")[0])
			is_genre = self._is_genre(keywords)
			self._record_release(artist_url, url, is_genre)

			if not is_genre:
				log.info(f"SKIP: Not target genre {url}")
				return {}

			self._get_alt_album_urls(schema, url)

			# Skip no tracks
			num_tracks = schema.get('numTracks') or schema.get('inAlbum',{}).get('numTracks') or 0
			if int(num_tracks) == 0:
				log.info(f"SKIP: No tracks in {url}")
				return {}

			# Get album metadata (finally)
			release = nozero((schema['name'] or current['title'] or ""))
			artist_name = nozero((schema['byArtist']['name'] or current['artist'] or ""))
			track_info = tralbum.get('trackinfo')
			track_urls = [t.get('title_link') for t in track_info]
			runtime = timedelta(seconds=int(sum(t.get('duration', 0) for t in track_info)))

			self.mod_dates[url] = mod_date
			return {
					"album_id": tralbum.get('id'),
					"url": url,
					"release": release,
					"artist": artist_name,
					"runtime": str(runtime),
					"num_tracks": int(num_tracks),
					"tags": keywords,
					"new_date": current.get('new_date') or "",
					"publish_date": current.get('publish_date',""),
					"release_date": current.get('release_date',""),				
					"mod_date": mod_date,
					"album_art_id": current.get('art_id'),
					"track_urls": track_urls
			}
		except Exception:
			log.exception(f"Failed to parse {url}")
			return {}
	
	async def scrape_all_albums(self, seed_urls=None) -> list:
		"""Scrape albums & discover more albums on the run."""
		if seed_urls:
			self.album_urls.update(seed_urls)
		pbar = tqdm(total=len(self.album_urls), desc="Albums", unit="album")
		results = []
		processed_urls = set()
		try:
			while True:
				urls_to_process = list(self.album_urls - processed_urls)
				if not urls_to_process:
					break
				fetched = await asyncio.gather(
					*(self._scrape_album_page(url) for url in urls_to_process)
				)
				processed_urls.update(urls_to_process)
				results.extend(item for item in fetched if item)

				pbar.update(len(urls_to_process))
				if len(self.album_urls) > (pbar.total or 0):
					pbar.total = len(self.album_urls)
					pbar.refresh()
				pbar.set_postfix(
					known=len(self.album_urls),
					processed=len(processed_urls),
					results=len(results),
				)
		finally:
			pbar.close()
			if not results:
				log.info("No new or updated albums found.")
			log.info(
				f"Finished fetching urls: "
				f"{len(processed_urls)} URLs -> {len(results)} albums"
			)
		return results
	
	def save_results(self, results):
		if self.use_cache:
			for album in results:
				self.albums[album["album_id"]] = album
		else:
			self.albums = {album["album_id"]: album for album in results}
		with open(ALBUMS_JSONL, "w", encoding="utf-8") as f:
			for album in self.albums.values():
				f.write(json.dumps(album, ensure_ascii=False) + "\n")
		with open(ARTISTS_SLUSHWAVE_JSONL, "w", encoding="utf-8") as f:
			for record in self.artists_slushwave.values():
				f.write(json.dumps(record, ensure_ascii=False) + "\n")
		with open(ALBUM_MOD_DATES_JSON, "w", encoding="utf-8") as f:
			json.dump(self.mod_dates, f, ensure_ascii=False, indent=2)

# ====================================
# OKAY!!! LET'S GET THIS THING RUNNING
# ====================================

async def main():
	start_time = time.time()
	ok_clients = await get_ok_clients(skip=True)
	random.seed(42)
	s = BrowserSession(ok_clients=ok_clients)

	# # ---- SCRAPING ALBUMS ----
	log.info(f"Fetching album urls...")
	with open("slushwave-bandcamp-links copy.txt", "r", encoding="utf-8") as f:
		urls = [line.strip() for line in f if line.strip()]
	# urls = [
	# 	'https://giftsfromhome.bandcamp.com/album/-',
	# ]
	album_scraper = AlbumScraper(s, sem=8, use_cache=True, skip_mode="historical")

	for batch_num, batch_urls in enumerate(split_to_batches(urls, 8), start=1):
		start_time_batch = time.time()
		log.info(f"Processing batch {batch_num} ({len(batch_urls)} urls)")
		# Clear discovered URLs from previous batch
		album_scraper.album_urls.clear()
		await album_scraper.get_all_release_urls(batch_urls) # type: ignore
		album_scraper.album_urls.difference_update(album_scraper.mod_dates.keys())
		results = await album_scraper.scrape_all_albums()
		if results:	
			album_scraper.save_results(results)
			log.info(f"Batch {batch_num}: saved {len(results)} albums in {time.time() - start_time_batch:.2f} seconds")
		else:
			log.info("No new or updated albums found.")
		await asyncio.sleep(2)

	# ---- SCRAPING ARTWORKS ----
	# log.info(f"Fetching artworks...")
	# with open(ALBUMS_JSONL, "r", encoding="utf-8") as f:
	# 	releases = [json.loads(line) for line in f if line.strip()]
	# artwork_scraper = ArtworkScraper(s, sem=8, use_cache=True)
	# processed_ids = set(artwork_scraper.release_ids.keys())
	# releases = [release for release in releases
	# 	if release["album_id"] not in processed_ids
	# ]
	# log.info(f"Skipping {len(processed_ids)} already processed releases, "
	# 		 f"{len(releases)} releases to process")
	# for batch in split_to_batches(releases, batch_size=4):
	# 	results = await artwork_scraper.scrape_all_artworks(batch)
	# 	artwork_scraper.save_results(results)
	# with open(ALBUMS_JSONL, "r", encoding="utf-8") as f:
	# 	releases = [json.loads(line) for line in f if line.strip()]
	# artwork_scraper = ArtworkScraper(s, sem=8, use_cache=True)
	# processed_ids = set(artwork_scraper.release_ids.keys())
	# releases = [
	# 	release
	# 	for release in releases
	# 	if release["album_id"] not in processed_ids
	# ]
	# log.info(f"Skipping {len(processed_ids)} already processed releases, "
	# 		 f"{len(releases)} releases to process")
	# for batch in split_to_batches(releases, batch_size=4):
	# 	results = await artwork_scraper.scrape_all_artworks(batch)
	# 	artwork_scraper.save_results(results)

	log.info(f"Total time: {time.time() - start_time:.4f} seconds")

if __name__ == "__main__":
	asyncio.run(main())