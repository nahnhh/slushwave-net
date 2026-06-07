#!/usr/bin/env python3
"""Scrape Bandcamp album pages and extract image links for tracks/albums.

This script reads artist home page URLs from `slushwave-bandcamp-links.txt`,
fetches each artist page, finds album and track release URLs, then scrapes every release page for image URLs.

Install dependencies with:
    pip install requests beautifulsoup4
"""

import csv
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Iterable, List, Optional, Set
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent
LINKS_FILE = BASE_DIR / "slushwave-bandcamp-links.txt"
OUTPUT_FILE = BASE_DIR / "bc-albums-image-links.csv"
IMAGES_DIR = BASE_DIR / "images"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
REQUEST_DELAY_SECONDS = 1
IMAGE_URL_RE = re.compile(r"https?://[^\s'\"<>]*\.(?:png|jpe?g|gif|webp)", re.IGNORECASE)


def get_soup(url: str, session: requests.Session) -> BeautifulSoup:
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def resolve_url(base: str, link: str) -> str:
    return urljoin(base, link.strip())


def extract_release_urls(artist_url: str, soup: BeautifulSoup) -> Set[str]:
    urls: Set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href:
            continue

        absolute = resolve_url(artist_url, href)
        parsed = urlparse(absolute)
        if "bandcamp.com" not in parsed.netloc:
            continue

        if "/album/" in parsed.path or "/track/" in parsed.path:
            urls.add(absolute.rstrip("/"))

    return urls


def extract_image_urls_from_text(text: str, base_url: str) -> Set[str]:
    urls: Set[str] = set()
    for match in IMAGE_URL_RE.findall(text):
        urls.add(resolve_url(base_url, match))
    return urls


def extract_image_urls_from_soup(soup: BeautifulSoup, page_url: str) -> Set[str]:
    images: Set[str] = set()

    for img in soup.find_all("img", src=True):
        images.add(resolve_url(page_url, img["src"]))

    for meta in soup.select('meta[property="og:image"], meta[name="twitter:image"], link[rel="image_src"]'):
        image_url = meta.get("content") or meta.get("href")
        if image_url:
            images.add(resolve_url(page_url, image_url))

    for tag in soup.find_all(attrs={"style": True}):
        style = tag["style"]
        for match in IMAGE_URL_RE.findall(style):
            images.add(resolve_url(page_url, match))

    for script in soup.find_all("script"):
        if script.string:
            images.update(extract_image_urls_from_text(script.string, page_url))

    return images


def filter_bandcamp_images(urls: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    output: List[str] = []
    for url in urls:
        clean = url.strip()
        if not clean:
            continue
        if clean in seen:
            continue
        seen.add(clean)
        output.append(clean)
    return output


def download_image(url: str, session: requests.Session, images_dir: Path) -> Optional[str]:
    """Download an image from URL and save to images_dir.
    
    Args:
        url: The image URL to download
        session: The requests session to use
        images_dir: Directory to save the image to
    
    Returns:
        Relative path to the downloaded file, or None if download failed
    """
    try:
        images_dir.mkdir(parents=True, exist_ok=True)
        
        # Create filename from URL hash + extension
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        ext_match = re.search(r'\.(png|jpe?g|gif|webp)(?:[?#]|$)', url, re.IGNORECASE)
        ext = ext_match.group(1).lower() if ext_match else "jpg"
        filename = f"{url_hash}.{ext}"
        filepath = images_dir / filename
        
        # Skip if already downloaded
        if filepath.exists():
            return str(filepath.relative_to(BASE_DIR))
        
        # Download the image
        response = session.get(url, timeout=30)
        response.raise_for_status()
        
        # Save to file
        with filepath.open("wb") as f:
            f.write(response.content)
        
        return str(filepath.relative_to(BASE_DIR))
    
    except Exception as exc:
        print(f"      Failed to download image: {exc}")
        return None


def read_artist_links(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"Artist list not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip() and not line.strip().startswith("#")]


def scrape_artist_releases(artist_url: str, session: requests.Session) -> Set[str]:
    soup = get_soup(artist_url, session)
    return extract_release_urls(artist_url, soup)


def scrape_release_images(release_url: str, session: requests.Session) -> Set[str]:
    soup = get_soup(release_url, session)
    return extract_image_urls_from_soup(soup, release_url)


def write_results(rows: List[List[str]], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["artist_url", "release_url", "image_url", "local_file_path"])
        writer.writerows(rows)


def main() -> None:
    artist_urls = read_artist_links(LINKS_FILE)
    if not artist_urls:
        print(f"No artist URLs found in {LINKS_FILE}")
        return

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    rows: List[List[str]] = []
    for index, artist_url in enumerate(artist_urls, start=1):
        print(f"[{index}/{len(artist_urls)}] Fetching artist page: {artist_url}")
        try:
            release_urls = scrape_artist_releases(artist_url, session)
        except Exception as exc:
            print(f"  Skipped artist page due to error: {exc}")
            continue

        if not release_urls:
            print("  No album or track release URLs found.")
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        for release_url in sorted(release_urls):
            print(f"    Scraping release: {release_url}")
            try:
                image_urls = scrape_release_images(release_url, session)
            except Exception as exc:
                print(f"      Skipped release due to error: {exc}")
                continue

            image_urls = filter_bandcamp_images(image_urls)
            for image_url in image_urls:
                local_path = download_image(image_url, session, IMAGES_DIR)
                rows.append([artist_url, release_url, image_url, local_path or ""])

            if not image_urls:
                print("      No image URLs found on this release page.")

            time.sleep(REQUEST_DELAY_SECONDS)

    if rows:
        write_results(rows, OUTPUT_FILE)
        print(f"Saved {len(rows)} image links to {OUTPUT_FILE}")
    else:
        print("No image links were found.")


if __name__ == "__main__":
    main()
