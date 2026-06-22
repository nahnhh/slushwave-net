This is a web of all releases on bandcamp tagged with slushwave coming from all artists in `slushwave-bandcamp-links.txt` -- a list compiled in the Slushwave Social Club. Come join in! https://discord.gg/slushwave 

# Documentation
*(Explaining the project for future self)*

## The files
* [good_profiles.json](good_profiles.json) is derived from [firefox_profiles.py](firefox_profiles.py) (`FINGERPRINTS`) after testing all client identifiers to remove ones that raise Client Challenge.
* [slushwave-bandcamp-link.txt](slushwave-bandcamp-link.txt): a list of all artists who has at least 1 release tagged with ["slush", "nature", "ambient", "dreamtone", "signal", "transmission", "mallsoft"] on Bandcamp. It was originally just "slush", but I decided to expand on my search (since slushwave or not, the album page soup has already been fetched to memory, I didn't want it to go to waste).
* Data files generated after running AlbumScraper:
    - [albums.jsonl](albums.jsonl): Data from all releases (tagged with the target genres). Answers: *What is the info about this release?*
    - [all_slushwave_artists.jsonl](all_slushwave_artists.jsonl): Used for `skipmode='historical'` (AlbumScraper cache). Supposed to store {artist_url, slushwave: [], not_slushwave: []} for cache, but I guess async wrote the file incomplete. Doesn't really matter though.
	- [album_mod_dates.jsonl](album_mod_dates.jsonl): Used for `skipmode='stale'` (AlbumScraper cache). Check for last modified date of a release and update changes if any. As this is one big dictionary, it reflects an accurate number of all slushwave releases up to date.
* Data files generated after running `ArtworkScraper`:
	- [art_release_date.jsonl](art_release_date.jsonl): Used for ArtworkScraper cache. Store {release_id, url, mod_date, artworks: []}` -> `artworks: {hashA: {art_id: [], track_num: []}}. Lists all the releases whose art_ids have been fetched. If an album's artwork is updated, rescan and append new record. Answers: *What are the unique artworks in this release?*
    ```json
	{
		"release_id": 123,
		"url": "...",
		"mod_date": "...",
		"artworks": {
			"hashA": {
				"art_id": [111],
				"track_num": [0,1,2,3] # 0 = release art
			},
			"hashB": {
				"art_id": [444],
				"track_num": [4]
			},
		}
	}
	```
    - [artworks.jsonl](artworks.jsonl): Stores all artwork data {img_hash, dom_color: {l,c,h}, palette: [8 colors in hex], in_release: [release ids]}. Answers: *Where else is this artwork used?*
	```json
	{
		"img_hash": "hashA",
		"dom_color": "...",
		"palette": [...],
		"in_release": ["release_id1","release_id2",...],
		"date_fetched": "11 Jun 2026 12:22:14 VNT"
	}
	```
* Backup files:
    - [bc_scraper_study.ipynb](bc_scraper_study.ipynb): The first file I made to figure out where and how to fetch the elements inside the Bandcamp music/album page.
    - [parse_album_page old.py](parse_album_page old.py): The first python file I made, compiled from the notebook. Works minimally: `AlbumScraper` handles both release data + track art urls fetching, no cache files implemented, could only fetch from album urls. Great start for a fork.
    - [Notes.md](Notes.md): A checklist & log of what I've done with the project :D

## The code: `parse_music_page.py`

### `AsyncSession`
The whole html fetching runs on `AsyncSession` (from `async_tls_client`) and `BeautifulSoup`
- `get_ok_clients(skip=True)`: Test client identifiers for possible Client Challenge. Returns `ok_clients` and dumps them to `good_profiles.json`
- `class BrowserSession`:
    - `sem`: limits number of coroutines.
    - `requests_per_sec`: used for global rate limiting -> must wait at least (1/rps) seconds between requests -> likely the reason artwork scraping multiple tracks in a release takes long.
    - `get()`: Get response from url, handles rate limiting with 3 attempts.
    - `fetch()`: Fetch soup from url response, handles redirects, 404, and Client Challenge.
- Helper functions:
    - `split_to_batches()`: Split the list to batches (to perform scraping)
    - `nozero(text)`: Removes zero-width characters from text.
    - `pick_dominant_color(palette)`: Picks dominant color from a palette except for ones that has lightness < 0.15 (near-black) or > 0.90 (near-white).

### `ArtworkScraper`
This runs after `AlbumScraper` and reads release data from `albums.jsonl`. Returns `art_release_ids.jsonl` and `artworks.jsonl` - both are read upon class initiation.
- `_get_art_id_from_url()`: Track url -> soup -> `datatr-album` -> art id
- `_fetch_artwork_data()`: Art id url -> hash -> color palette
- `_scrape_unique_artworks()`: Extract album + track art ids from 1 release record in albums.jsonl.

### `AlbumScraper`
This runs first, fast and pretty solid! Artist urls (Music pages) list -> Music page soup -> Album urls -> Album soup -> schema, tralbum -> Album data + Track urls + Other album urls in credit/description
- `get_all_release_urls()`: Get all album urls from a .txt file with all links listed.
	- Music (artist) page url -> music soup -> extract album urls
	- Album page url -> keep directly
- `_categorize_release`: Categorize release to either slushwave or not slushwave in all_slushwave_artists.jsonl
- `_scrape_album_page()`: Fetch an album page, checks all skips and returns required album metadata. Skip stale albums -> Skips not slushwave -> Get alt album urls -> Skip no tracks
- `scrape_all_albums()`: Scrape albums from a list of urls & discover more album urls on the run.