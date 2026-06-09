# BeautifulSoup Functionalities Used in Bandcamp Scraping

This document outlines all the BeautifulSoup methods and techniques used in `parse_album_page_async()` and `parse_music_page_async()` functions from the Bandcamp scraper notebook.

---

## 1. **Finding Single Elements: `.find()`**

### Purpose
Locates the first matching element in the HTML document.

### Usage in Functions

#### In `parse_album_page_async()`:
- **Finding metadata tags** - Extract release date
  ```python
  meta_date = soup.find("meta", {"itemprop": "datePublished"})
  ```
  
  **HTML Example** from `愛と別れの季節 _ 𝓼𝓸𝓾𝓵𝓶𝓪𝓽𝓮 _ soulmate.htm`:
  ```html
  <meta property="og:title" content="愛と別れの季節, by 𝓼𝓸𝓾𝓵𝓶𝓪𝓽𝓮">
  <meta name="bc-page-properties" content="{...}">
  ```

- **Finding track titles** - Extract title from track element
  ```python
  t = tr.find(class_='title')
  t = tr.find('span', {'itemprop': 'name'})
  ```

- **Finding time/duration** - Extract track duration
  ```python
  time_tag = tr.find(class_='time')
  ```

#### In `parse_music_page_async()`:
- **Finding album grid container**
  ```python
  grid = soup_artist.find("div", {"class": "leftMiddleColumns"})
  ```
  
  **HTML Example** from the structure:
  ```html
  <div class="trackView leftMiddleColumns has-art">
  ```

---

## 2. **Finding Multiple Elements: `.find_all()`**

### Purpose
Returns a list of all matching elements.

### Usage in Functions

#### In `parse_album_page_async()`:
- **Finding all track rows** - Extract tracklist
  ```python
  track_rows = soup.select('table#track_table tr.track_row, ol#track_table li, ...')
  ```
  
  **HTML Example**:
  ```html
  <table class="track_list track_table" id="track_table">
    <tr class="track_row_view linked" rel="tracknum=1">
      <td class="play-col">...</td>
      <td class="track-number-col"><div class="track_number secondaryText">1.</div></td>
      <td class="title-col">
        <div class="title">
          <a href="/track/-"><span class="track-title">君の愛で眩ませ、愚か者にしてくれ</span></a>
          <span class="time secondaryText">15:51</span>
        </div>
      </td>
    </tr>
    <tr class="track_row_view linked" rel="tracknum=2">
      <!-- More tracks... -->
    </tr>
  </table>
  ```

#### In `parse_music_page_async()`:
- **Finding all album items**
  ```python
  items = ol.find_all("li", {"class": "music-grid-item"})
  ```

- **Finding all potential label links**
  ```python
  for a in soup.select(".tralbum-credits a, .credits a, a"):
  ```

---

## 3. **CSS Selectors: `.select()` and `.select_one()`**

### Purpose
Use CSS selector syntax to find elements more flexibly.

### Usage in Functions

#### In `parse_album_page_async()`:
- **Finding track rows with multiple possible structures**
  ```python
  track_rows = soup.select('table#track_table tr.track_row, ol#track_table li, ol.track_list li, li.track')
  ```
  
  This selector matches ANY of these patterns:
  - Table rows: `table#track_table tr.track_row`
  - List items: `ol#track_table li`
  - Track list items: `ol.track_list li`
  - Individual tracks: `li.track`

- **Finding credits or release info** (using `select_one()`)
  ```python
  credits = soup.select_one(".tralbum-credits, .credits, .release-date")
  ```
  
  **HTML Example**:
  ```html
  <h3 class="credits-label">credits</h3>
  <div class="tralbumData tralbum-credits">
    released May 28, 2026
  </div>
  ```

- **Finding tracklist divs** (fallback selector)
  ```python
  track_rows = soup.select('div.trackList > div.track')
  ```

#### In `parse_music_page_async()`:
- **Finding album links within grid**
  ```python
  url = item.find("a")["href"]
  ```

---

## 4. **Extracting Attribute Values: `.get()` and Direct Indexing `["key"]`**

### Purpose
Extract attribute values from HTML tags.

### Usage in Functions

#### In `parse_album_page_async()`:
- **Extracting release date from meta tag**
  ```python
  if meta_date and meta_date.get("content"):
      year = meta_date.get("content")[:4]  # Get content attribute, extract first 4 chars
  ```
  
  **HTML Example**:
  ```html
  <meta property="og:image" content="https://f4.bcbits.com/img/a4043521709_5.jpg">
  ```

#### In `parse_music_page_async()`:
- **Extracting href from anchor tags**
  ```python
  url = item.find("a")["href"]
  ```
  
  **HTML Example**:
  ```html
  <a href="/album/--2"><span class="track-title">Album Name</span></a>
  ```

- **Getting artist name from URL**
  ```python
  "artist": artist_url.split('/')[-1]
  ```

---

## 5. **Extracting Text Content: `.get_text()` and `.text`**

### Purpose
Extract all text from an element, with optional cleanup.

### Usage in Functions

#### In `parse_album_page_async()`:
- **Extracting track title**
  ```python
  title = t.get_text(strip=True)
  ```
  
  **HTML Example**:
  ```html
  <span class="track-title">君の愛で眩ませ、愚か者にしてくれ</span>
  ```
  
  Result: `"君の愛で眩ませ、愚か者にしてくれ"`

- **Extracting duration from text**
  ```python
  dur = time_tag.get_text(strip=True)
  ```
  
  **HTML Example**:
  ```html
  <span class="time secondaryText">15:51</span>
  ```
  
  Result: `"15:51"`

- **Extracting release date from credits section**
  ```python
  m = re.search(r"(19|20)\d{2}", credits.get_text())
  ```

#### In `parse_music_page_async()`:
- **Extracting album name**
  ```python
  album_name = album_element.text.strip()
  ```
  
  **HTML Example**:
  ```html
  <p class="title">
    Album Title
    <span class="artist-override">Alternate Artist</span>
  </p>
  ```

---

## 6. **Finding Elements with Classes: Using `.find()` with `class_` Parameter**

### Purpose
Target elements by their CSS class name.

### Usage in Functions

#### In `parse_album_page_async()`:
- **Finding title elements**
  ```python
  t = tr.find(class_='title')
  ```

- **Finding duration elements**
  ```python
  time_tag = tr.find(class_='time')
  ```

#### In `parse_music_page_async()`:
- **Finding album title paragraph**
  ```python
  album_element = item.find("p", {"class": "title"})
  ```

- **Finding artist override span**
  ```python
  alternate_element := album_element.find("span", {"class": "artist-override"})
  ```

---

## 7. **Navigating Parent-Child Relationships**

### Purpose
Access child elements or parent containers within the DOM tree.

### Usage in Functions

#### In `parse_music_page_async()`:
- **Finding ordered list within a div**
  ```python
  grid = soup_artist.find("div", {"class": "leftMiddleColumns"})
  ol = grid.find("ol", {"id": "music-grid"}) if grid else None
  items = ol.find_all("li", {"class": "music-grid-item"}) if ol else []
  ```
  
  **HTML Structure**:
  ```html
  <div class="leftMiddleColumns">
    <ol id="music-grid">
      <li class="music-grid-item">
        <p class="title">Album 1</p>
        <a href="/album/url">Link</a>
        <picture>...</picture>
      </li>
      <li class="music-grid-item">
        <!-- More items -->
      </li>
    </ol>
  </div>
  ```

---

## 8. **Chaining Methods: Combining `.find()` Operations**

### Purpose
Drill down through nested HTML structure.

### Usage in Functions

#### In `parse_album_page_async()`:
- **Finding picture within album element**
  ```python
  picture = album_element.find("picture")
  source = picture.find("source")
  if source and source.get("srcset"):
      image_url = source.get("srcset").split()[0]
  ```
  
  **HTML Example**:
  ```html
  <li class="music-grid-item">
    <picture>
      <source srcset="https://f4.bcbits.com/img/...avif" type="image/avif" />
      <img src="https://f4.bcbits.com/img/...jpg">
    </picture>
  </li>
  ```

---

## 9. **Using `.get()` with Default Values**

### Purpose
Safely extract attributes that may not exist, with a fallback.

### Usage in Functions

#### In `parse_album_page_async()`:
- **Safely getting href from links**
  ```python
  href = a.get("href", "")  # Returns "" if href doesn't exist
  ```

- **Checking if attribute exists before processing**
  ```python
  if time_tag and time_tag.get("src"):
  ```

---

## 10. **String Processing with Regex Integration**

### Purpose
Extract patterns from text using regular expressions after `.get_text()`.

### Usage in Functions

#### In `parse_album_page_async()`:
- **Extracting year from text using regex**
  ```python
  m = re.search(r"(19|20)\d{2}", credits.get_text())
  if m:
      year = m.group(0)
  ```

- **Extracting time format from text**
  ```python
  m = re.search(r"(\d{1,2}:\d{2}(?::\d{2})?)", tr.get_text())
  if m:
      dur = m.group(1)
  ```

---

## Summary Table

| BeautifulSoup Feature | `parse_album_page_async()` | `parse_music_page_async()` | Purpose |
|-----------------------|---------------------------|--------------------------|---------|
| `.find()` | ✓ | ✓ | Find first matching element |
| `.find_all()` | ✓ | ✓ | Find all matching elements |
| `.select()` | ✓ | | Use CSS selectors (multi-pattern) |
| `.select_one()` | ✓ | | Use CSS selector for single element |
| `.get()` / `["attr"]` | ✓ | ✓ | Extract attribute values |
| `.get_text()` / `.text` | ✓ | ✓ | Extract text content |
| `class_` parameter | ✓ | ✓ | Find by CSS class |
| Parent-child navigation | ✓ | ✓ | Drill down DOM tree |
| Method chaining | ✓ | ✓ | Combine multiple find operations |
| Regex + text extraction | ✓ | | Extract patterns from text |

---

## Key HTML Structures Being Targeted

### Album Page Structure
```
<meta itemprop="datePublished" content="28 May 2026">
<table id="track_table" class="track_list">
  <tr class="track_row_view">
    <td class="title-col"><span class="time">15:51</span></td>
  </tr>
</table>
<div class="tralbum-credits">Released May 28, 2026</div>
```

### Artist Music Grid Structure
```
<div class="leftMiddleColumns">
  <ol id="music-grid">
    <li class="music-grid-item">
      <p class="title">Album Name</p>
      <a href="/album/...">Link</a>
      <picture>
        <source srcset="...avif" type="image/avif" />
      </picture>
    </li>
  </ol>
</div>
```

---

# JSON-LD Scraping with lxml: Structured Data Extraction

This section provides functions to scrape `<script type="application/ld+json">` data from Bandcamp pages using **lxml** for structured data extraction.

## Why JSON-LD?

Bandcamp embeds structured data (Schema.org format) in JSON-LD scripts. This is machine-readable, standardized metadata that's more reliable than parsing HTML selectors.

---

## 1. **Basic JSON-LD Extraction Function**

```python
import json
from lxml import html

def extract_jsonld_data(html_string, schema_type=None):
    """
    Extract all JSON-LD data from HTML.
    
    Args:
        html_string: HTML content as string
        schema_type: Optional - filter by @type (e.g., "MusicAlbum", "MusicRelease")
    
    Returns:
        List of parsed JSON-LD objects
    """
    tree = html.fromstring(html_string)
    script_tags = tree.xpath('//script[@type="application/ld+json"]/text()')
    
    jsonld_data = []
    for script_content in script_tags:
        try:
            data = json.loads(script_content)
            if schema_type:
                # Handle both single object and @graph arrays
                if isinstance(data, list):
                    filtered = [obj for obj in data if obj.get("@type") == schema_type]
                    jsonld_data.extend(filtered)
                elif data.get("@type") == schema_type:
                    jsonld_data.append(data)
            else:
                jsonld_data.append(data)
        except json.JSONDecodeError:
            continue
    
    return jsonld_data
```

---

## 2. **Artist Music Page - Extract Using BeautifulSoup (No JSON-LD Available)**

```python
# NOTE: JSON-LD is NOT available on the artist's music page
# Must use BeautifulSoup/lxml HTML parsing for this step

def extract_music_page_albums_bs(html_string):
    """
    Extract album list from artist's music page using BeautifulSoup.
    
    Returns:
    - artist name(s)
    - alias
    - all album URLs
    - album images
    - album names
    
    This is still ONE request, but requires HTML parsing.
    """
    soup = BeautifulSoup(html_string, 'lxml')
    
    artist_info = {
        "artist": None,
        "alias": None,
        "albums": []
    }
    
    # Extract artist name from page header/meta
    # (varies by Bandcamp layout, typically in h1 or meta tags)
    artist_header = soup.find("h1", {"class": "title"})
    if artist_header:
        artist_info["artist"] = artist_header.get_text(strip=True)
    
    # Find album grid container
    grid = soup.find("div", {"class": "leftMiddleColumns"})
    if grid:
        ol = grid.find("ol", {"id": "music-grid"})
        if ol:
            items = ol.find_all("li", {"class": "music-grid-item"})
            
            for item in items:
                album_name_elem = item.find("p", {"class": "title"})
                album_name = album_name_elem.get_text(strip=True) if album_name_elem else ""
                
                link = item.find("a")
                url = link.get("href", "") if link else ""
                
                # Extract image
                picture = item.find("picture")
                image = ""
                if picture:
                    source = picture.find("source")
                    if source:
                        image = source.get("srcset", "").split()[0]
                
                album_entry = {
                    "album": album_name,
                    "url": url,
                    "image": image
                }
                artist_info["albums"].append(album_entry)
    
    return artist_info
```

**⚠️ Important:** Use this for the artist music page, then use JSON-LD for individual album pages.

---

## 3. **Album Page - Extract Detailed Information**

```python
def extract_album_page_data(html_string):
    """
    Extract album details using JSON-LD MusicAlbum schema.
    
    Returns:
    - artist
    - album name
    - url
    - image
    - release year (from datePublished or dateCreated)
    - tracklist with full details
    - runtime
    """
    schema_data = extract_jsonld_data(html_string, "MusicAlbum")
    
    if not schema_data:
        # Try MusicRelease if MusicAlbum not found
        schema_data = extract_jsonld_data(html_string, "MusicRelease")
    
    album_info = {
        "artist": [],
        "album": None,
        "url": None,
        "image": None,
        "release_year": None,
        "tracklist": [],
        "runtime": None
    }
    
    for album in schema_data:
        album_info["album"] = album.get("name", "")
        album_info["url"] = album.get("url", "")
        album_info["image"] = album.get("image", "")
        
        # Extract year from datePublished
        date_published = album.get("datePublished", "")
        if date_published:
            album_info["release_year"] = date_published[:4]
        
        # Extract artists
        by_artist = album.get("byArtist", [])
        if isinstance(by_artist, list):
            album_info["artist"] = [a.get("name", "") for a in by_artist]
        else:
            album_info["artist"] = [by_artist.get("name", "")]
        
        # Extract tracklist
        track_list = album.get("track", [])
        if isinstance(track_list, dict):
            track_list = [track_list]
        
        for track in track_list:
            track_info = {
                "title": track.get("name", ""),
                "duration": track.get("duration", ""),  # ISO 8601 format (e.g., "PT3M45S")
                "track_number": track.get("position", "")
            }
            album_info["tracklist"].append(track_info)
        
        # Calculate total runtime
        total_duration = album.get("duration", "")
        if total_duration:
            album_info["runtime"] = total_duration
    
    return album_info
```

---

## 4. **Convert ISO 8601 Duration to Seconds/Minutes**

```python
import re

def iso_duration_to_seconds(duration_str):
    """
    Convert ISO 8601 duration format (e.g., "PT3M45S") to seconds.
    
    Args:
        duration_str: ISO 8601 duration string
    
    Returns:
        Total seconds (int)
    """
    pattern = r'P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?'
    match = re.match(pattern, duration_str)
    
    if not match:
        return 0
    
    days, hours, minutes, seconds = match.groups()
    days = int(days) if days else 0
    hours = int(hours) if hours else 0
    minutes = int(minutes) if minutes else 0
    seconds = float(seconds) if seconds else 0
    
    total_seconds = (days * 86400) + (hours * 3600) + (minutes * 60) + seconds
    return int(total_seconds)

def format_duration(seconds):
    """Format seconds to MM:SS or H:MM:SS"""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"
```

---

## 5. **Comparison: JSON-LD vs BeautifulSoup with lxml**

| Aspect | JSON-LD (Album Page) | BeautifulSoup (Artist Page) |
|--------|---------------------|--------------------------|
| **Where Available** | ✅ Album detail pages | ✅ Artist music page (primary) |
| **Speed** | ⚡ Fast JSON parsing | ⚡ Similar HTML DOM traversal |
| **Reliability** | ✅ Standardized Schema.org | ⚠️ Depends on HTML structure changes |
| **Data Completeness** | ✅ Full album metadata | ✅ Album list with basic info |
| **Intuitive?** | ✅ YES - Structured JSON | ⚠️ Selector-based navigation |

---

## 6. **Data Extraction Workflow**

### From Artist's Music Page:

#### BeautifulSoup Approach (REQUIRED - No JSON-LD)
```python
artist_info = extract_music_page_albums_bs(html_content)
# Returns album list:
# - artist name
# - album names
# - album URLs
# - album images
```

**Note:** JSON-LD is NOT available on artist music pages. Must use HTML parsing.

---

### From Individual Album Pages:

#### JSON-LD Approach (PREFERRED)
```python
album_data = extract_album_page_data(html_content)
# Returns immediately:
# - release year ✅
# - tracklist ✅
# - runtime ✅
# - artist, album, url, image ✅
```

#### Alternative: BeautifulSoup Fallback
```python
# If JSON-LD fails, can parse HTML selectors as fallback
# But JSON-LD is available on album pages, so prefer that
```

**Advantage:** JSON-LD on album pages is more reliable than HTML parsing

---

## 7. **Best Practice: Hybrid Approach**

```python
async def scrape_artist_complete(artist_url):
    """
    Optimal workflow:
    1. Artist page: BeautifulSoup (no JSON-LD available)
    2. Per-album page: JSON-LD (preferred, more reliable)
    """
    # Step 1: Get artist page using BeautifulSoup
    artist_html = await fetch_url(artist_url)
    artist_info = extract_music_page_albums_bs(artist_html)
    
    results = []
    
    # Step 2: Iterate each album, extract using JSON-LD
    for album in artist_info["albums"]:
        album_html = await fetch_url(album["url"])
        album_data = extract_album_page_data(album_html)
        
        # Combine artist info with album details
        complete_record = {
            "artist": artist_info["artist"],
            **album_data
        }
        results.append(complete_record)
    
    return results
```

**Workflow Summary:**
- ✅ Artist music page → **BeautifulSoup** (HTML parsing)
- ✅ Album pages → **JSON-LD** (JSON parsing, more reliable)

---

## 8. **When JSON-LD Fails: Fallback to BeautifulSoup**

```python
def scrape_with_fallback(html_string, fallback_parser=None):
    """
    Try JSON-LD first, fall back to HTML parsing if needed.
    """
    try:
        data = extract_album_page_data(html_string)
        if data["album"]:  # Verify we got data
            return data
    except (json.JSONDecodeError, KeyError):
        pass
    
    # Fallback to BeautifulSoup if JSON-LD fails
    if fallback_parser:
        return fallback_parser(html_string)
    
    return None
```

---

## 9. **Summary: When to Use Each Approach**

**JSON-LD (Album Pages Only):**
- ✅ Available on individual album/track detail pages
- ✅ Provides: release year, tracklist, runtime, artist, image
- ✅ More reliable than HTML parsing
- ⚠️ NOT available on artist music page

**BeautifulSoup (Artist Music Page):**
- ✅ Required for artist music page (no JSON-LD available)
- ✅ Extracts: artist name, album list, URLs, images
- ⚠️ Subject to HTML structure changes

**Recommendation:**
1. Use **BeautifulSoup** to get album URLs from artist music page
2. Use **JSON-LD** on each album page for detailed metadata
3. Keep BeautifulSoup selectors as fallback if JSON-LD parsing fails

**Is JSON-LD Better?**

Yes, but **only for album pages**. For artist pages, BeautifulSoup is the only option. The hybrid approach combines both strengths.
