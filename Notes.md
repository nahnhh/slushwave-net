#### Things to do
[ ] Create a Python script to scrape source data from the list: artist, album, year, image -> source.json, compile images in `IMAGE_DIR`
[ ] Use `color-thief-py` to get representative color of album -> colors.json -> merge to source.json
[ ] Assign color to node, use OKLCH to display all nodes around a color wheel
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

10/06/26: Completed, metadata now looks like this:
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

- 11/06:
  + `ALBUMS_CACHE_JSONL` stores {url, mod_date} to compare processed albums to see if there needs updating -> Add fallbacck to `main()` to stop if there is 0 new albums
  + `ARTWORKS_JSONL` stores {art_id, dom_color, palette, date_fetched}
  + Need to fix artwork dedup: because unique track art_id are created for every track art uploaded, and since fetching artworks happen concurrently, the first unique art_id is random. -> the list `track_art_id` is random -> need to store hash as well(?)



### Teaching myself how to scrape data
1. **Headers list** to rotate, `tls_client` to fetch url instead of BeautifulSoup's session
2. **Functions to scrape data** from source: `parse_movie_details(url, soup)` -> output to pandas DataFrame.
- Very important that the scrape functions only uses `soup` and not `session`. The `session` to get `soup` from will be in the async function below.
3. **Async functions**: async def
- `scrape_async(url, session, header)` to both fetch URL and pass parsing function
- `scrape_batch(url, session, header)` to call `scrape_async()`
- `process_batch_with_error(urls, session, header, batch_num)`: process one batch with error handling
- `process_all_batches(url, start_at=0)`: process all batches

### Get color of image via `color-thief-py`
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