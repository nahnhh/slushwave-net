### Things to do
[x] Create a Python script to scrape source data from the list: artist, album, year, image -> albums.json
[x] Use `color-thief-py` to get dominant color of album -> artworks.json
[ ] Add other album urls in description/credits to URL scraping queue
[ ] Change `artworks.jsonl` to `{img_hash, art_id {album1:[art_id], album2:[.]}, ...}`
[ ] Assign color to node, display all nodes around a color wheel (OKLCH)
[ ] HTML & CSS to create the site (neocities)

### Info to scrape from artist list --> Compile `source.json` file
From the bandcamp artist's *Music* site, the scraper can find info for:
- artist(s)
- alias
- album
- url: url of album
- image: image filename
However, still have to iterate through each *album* site for:
- release year
- tracklist
- runtime

### Log
#### 13/06/26: New workflow for `parse_album_page` - `main()`
1. URL Discovery: URL -> soup -> url, schema, tralbum -> pass to `extract_alt_album_urls(schema)` -> `queue.put_nowait(alt_url)` -> store to `parsed_pages[url] = {schema, album}
2. Pass to `scrape_album_page(schema, tralbum)` to check skips and get album data
3. Pass to `scrape_many(art_ids)` to get artwork 

URL discovery and album parsing happen together. Track parsing happens after.
Queue
 ↓
album URL

worker:
    fetch album page
    parse schema/tralbum
    discover alt URLs
    enqueue alt URLs
    scrape_album_page(schema,tralbum)
    return album_data

main:
    gather album_data
    collect art_ids

after crawl:
    scrape_many(art_ids)


#### 12/06/26: `parse_album_page.py` is done
- Workflow of `parse_album_page`: Skip no tracks -> Skip non slushwave -> Skip no updates -> Append result -> Scrape alt album urls from label if any
  + Skip no tracks -> Find all unique urls that has '/album/', in schema['description'] or schema['creditText'] -> Scrape those urls (?)
  + If `album_scraper.load_cache()`, `json.dump` writes only new updated albums -> Create a master `albums.json`, merge updates
- Work on `parse_music_page` -> get all album urls and aliases, should be fast :D
- The slushwave artist list is too extensive, only add data from releases that has slushwave in keywords`.lower()`
- Future: JSON - Function to look up art_id from `albums.json` in `artworks.jsonl` -> return only existing art_ids in order of tracks(?)
- Future: JSON - Filter to remove artists whose slushwave releases make up < 10% of their catalog -> get no. slushwave releases / total releases
  1. Scrape music page.
  2. Collect album URLs (`f"page+{.get('href')}"`) + alias (`"artist-override"`) in `select_all("music-grid-item")`
  3. Fetch album pages with `parse_album_page.py`, BUT:
  4. Extract tags = `keywords.lower()` for every release.
  5. Only do full metadata extraction for releases tagged with slushwave.
  6. Calculate slushwave releases / total releases percentage.

- Todo: `artwork.jsonl`: `{img_hash, art_id: [], dom_color, palette, date_fetched}`
-> List of art_id allows for instances where the same image gets uploaded in different albums
-> This will also include same track art uploaded across multiple tracks

#### 11/06/26:
  + `ALBUMS_CACHE_JSONL` stores {url, mod_date} to compare processed albums to see if there needs updating -> Add fallback to `main()` to stop if there is 0 new albums
  + `ARTWORKS_JSONL` stores {art_id, dom_color, palette, date_fetched}
  + Need to fix artwork dedup: because unique track art_id are created for every track art uploaded, and since fetching artworks happen concurrently, the first unique art_id is random. -> the list `track_art_id` is random -> need to store hash as well(?)

#### 10/06/26: Completed, metadata now looks like this:
```json
  {
    "url": "https://giftsfromhome.bandcamp.com/album/-",
    "album": "スター",
    "artist": "Quà từ Nhà",
    "total_time": "0:15:35",
    "num_tracks": "2",
    "keywords": [
      "Ambient",
      "Slushwave",
      "Vietnam"
    ],
    "new_date": "21 Apr 2025 15:32:54 GMT",
    "publish_date": "08 May 2025 07:14:52 GMT",
    "release_date": "08 May 2025 00:00:00 GMT",
    "mod_date": "25 Dec 2025 17:25:56 GMT",
    "album_art_id": 658787489,
    "track_art_id": [
      "0658787489"
    ]
  },
```
- Points to note:
	+ Slushwave tag should be filtered with `lower()`
	+ Publish date and Release date will only differ if the album is published in private first, then released to public later.
	+ Release date only store dates.
	+ Order of dates is usually: `new_date -> publish_date -> release_date -> mod_date`
	+ `album_art_id` is sometimes the same as `track_art_id` without the leading 0, but the link still works anyway.

- Todo next 11/06:
	+ Create `artwork.json`: {art_id, dom_color, palette(8), date_fetched} with `color-thief-py`, BytesIO to wrap link: `f"https://f4.bcbits.com/img/a{art_id}_3"`
  + Read artwork cache to avoid processing existing art_id.
	+ `parse_music_page.py` to collect alias & album urls.
  + Try fetching albums with no tracks
  + Workflow: AlbumScraper -> albums.json -> collect unique art_ids -> ArtworkScraper -> artwork.jsonl
  + Log.info: Saved how many new artworks to `artwork.jsonl` out of X artworks.


#### 07/06/26 AI explains how to get dominant color:
Get color of image via `color-thief-py`
Extract a palette of 5–8 colors.
Convert them to OKLCH.
Discard very dark/light colors.
Choose the color with the highest chroma.

```
palette = ct.get_palette(color_count=8)

best = max(
    palette,
    key=lambda rgb: oklch_chroma(rgb)
)
```