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

### Teaching myself how to scrape data
1. **Headers list** to rotate, `tls_client` to fetch url instead of BeautifulSoup's session
2. **Functions to scrape data** from source: `parse_movie_details(url, soup)` -> output to pandas DataFrame.
- Very important that the scrape functions only uses `soup` and not `session`. The `session` to get `soup` from will be in the async function below.
3. **Async functions**: async def
- `scrape_async(url, session, header)` to both fetch URL and pass parsing function
- `scrape_batch(url, session, header)` to call `scrape_async()`
- `process_batch_with_error(urls, session, header, batch_num)`: process one batch with error handling
- `process_all_batches(url, start_at=0)`: process all batches

### Teaching myself soup again

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