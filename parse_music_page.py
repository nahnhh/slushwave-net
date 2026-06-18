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
LINKS_FILE = "slushwave-bandcamp-links.txt"
ALBUM_MOD_DATES_JSON = Path("album_mod_dates.json")
ALBUMS_JSONL = Path("albums.jsonl")
ART_IDS_JSONL = Path("art_ids.jsonl")
ARTWORKS_JSONL =  Path("artworks.jsonl")
TEST_URL = "https://giftsfromhome.bandcamp.com/album/-"
ARTWORK_URL = "https://f4.bcbits.com/img/a{art_id}_3"
ALBUM_URL = re.compile(r"https://[a-zA-Z0-9-]+\.bandcamp\.com/album/\S+")
SINGLE_URL = re.compile(r"https://[a-zA-Z0-9-]+\.bandcamp\.com/track/\S+")

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
		self.artworks = {}		# artworks metadata created
		self._load_cache()

	def _load_cache(self):
		"""Load image hashes to dedup later."""
		if not self.use_cache or not ARTWORKS_JSONL.exists():
			return
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
			track_art_ids = await asyncio.gather(
				*(self._get_art_id_from_url(u) for u in track_urls)
			)
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
		return {
			"release_id": release["album_id"],
			"artworks": artworks,
		}
	
	async def scrape_all_artworks(self, file_or_list):
		releases = self._load_release_data(file_or_list)
		results = []
		async def worker(release, pbar):
			try:
				result = await self._scrape_unique_artworks(release)
				results.append(result)
			except Exception:
				log.exception(f"Artwork scrape failed for {release['url']}")
			finally:
				pbar.update(1)

		with tqdm(total=len(releases), desc="Artworks", unit="album") as pbar:
			async with asyncio.TaskGroup() as tg:
				for release in releases:
					tg.create_task(worker(release, pbar))

		return results

	def save_results(self, results):
		art_ids_content = "\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n"
		artworks_content = "\n".join(json.dumps(r, ensure_ascii=False) for r in self.artworks.values()) + "\n"
		with open(ART_IDS_JSONL, "w", encoding="utf-8") as f:
			f.write(art_ids_content)
		with open(ARTWORKS_JSONL, "w", encoding="utf-8") as f:
			f.write(artworks_content)
		log.info(
			f"Saved {len(results)} art_id records "
			f"and {len(self.artworks)} artwork records"
		)
		
# --- PHASE (0) URL DISCOVERY + (1) PARSE ALBUM DATA ---
# --- Artist urls (Music pages) list -> Music page soup -> Album urls -> Album soup -> schema, tralbum -> Album data + Track urls ---
class AlbumScraper:
	def __init__(self, session_or_clients, sem=150, use_cache=False):
		if isinstance(session_or_clients, BrowserSession):
			self.s = session_or_clients
		else:
			self.s = BrowserSession(session_or_clients, sem)
		self.mod_dates = {}
		self.album_urls = set()
		self.albums = {}
		self.use_cache = use_cache
		self._load_cache()

	def _load_cache(self):
		if not self.use_cache:
			return
		if ALBUM_MOD_DATES_JSON.exists():
			with open(ALBUM_MOD_DATES_JSON, "r", encoding="utf-8") as f:
				self.mod_dates = json.load(f)
		if ALBUMS_JSONL.exists():
			with open(ALBUMS_JSONL, "r", encoding="utf-8") as f:
				for line in f:
					try:
						album = json.loads(line)
						self.albums[album["album_id"]] = album
					except Exception:
						continue

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
		album_urls = {url for url in urls if ALBUM_URL.match(url) or SINGLE_URL.match(url)}
		artist_urls = urls - album_urls
		url_lists = await asyncio.gather(
			*(self._fetch_albums_from_artist(url) for url in artist_urls),
			return_exceptions=False
		)
		album_urls.update(album_url for urls in url_lists for album_url in urls)
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
			num_tracks = schema.get('numTracks') or schema.get('inAlbum',{}).get('numTracks') or 0
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
				f"Finished fetching urls in {time.time() - start_time:.4f} seconds: "
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

	# ---- SCRAPING ALBUMS ----
	album_scraper = AlbumScraper(s, sem=50, use_cache=False)
	log.info(f"Fetching album urls...")
	# urls = [
	# 	'https://giftsfromhome.bandcamp.com/album/-',
	# ]
	urls = 'slushwave-bandcamp-links.txt'
	await album_scraper.get_all_album_urls(urls)  # type: ignore
	results = await album_scraper.scrape_all_albums()
	if not results:
		log.info("No new or updated albums found. Exiting.")
		return
	album_scraper.save_results(results)

	# ---- SCRAPING ARTWORKS ----
	# log.info(f"Fetching artworks...")
	# artwork_scraper = ArtworkScraper(s, sem=50, use_cache=True)
	# artworks = await artwork_scraper.scrape_all_artworks(results)
	# artwork_scraper.save_results(artworks)

	# log.info(f"Total time: {time.time() - start_time:.4f} seconds")

if __name__ == "__main__":
	asyncio.run(main())