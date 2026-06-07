This is a web of all releases on bandcamp tagged with slushwave coming from all artists in `slushwave-bandcamp-links.txt` -- a list compiled in the Slushwave Social Club. Come join in!

#### Things to do
[ ] Create a Python script to scrape source data from the list: artist, album, year, image ID = UID -> source.json, compile images in `IMAGE_DIR`
[ ] Use `color-thief-py` to get representative color of album -> colors.json -> merge to source.json
[ ] Assign color to node, use OKLCH to display all nodes around a color wheel
[ ] HTML & CSS to create the site (neocities)

### Info to scrape from artist list
- All info about the artist, and for each release by that artist:
- Year of the release
- The release image
- Track names
- Cover of each track (if any)

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