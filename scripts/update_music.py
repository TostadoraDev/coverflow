"""Generate a GitHub README album gallery without API keys or uploaded images.

Prefer Spotify oEmbed when a Spotify album URL is supplied; otherwise search
Apple's public iTunes catalog. Only the README area between gallery markers
is changed.
"""

from __future__ import annotations

import html
import json
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
START = "<!-- MUSIC-GALLERY:START -->"
END = "<!-- MUSIC-GALLERY:END -->"
ITUNES_SEARCH = "https://itunes.apple.com/search"
SPOTIFY_OEMBED = "https://open.spotify.com/oembed"


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold()
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def fetch_json(url: str) -> dict:
    req = Request(url, headers={"User-Agent": "Coverflow/1.1 (GitHub README generator)"})
    with urlopen(req, timeout=25) as response:
        return json.load(response)


def spotify_album_id(url: str) -> str | None:
    """Return an album ID for Spotify album URLs (including /intl-es/)."""
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        return None
    if parsed.hostname not in ("open.spotify.com", "www.open.spotify.com"):
        return None
    match = re.fullmatch(r"/(?:intl-[a-z]{2}(?:-[a-z]{2})?/)?album/([A-Za-z0-9]{22})/?", parsed.path)
    if not match:
        raise ValueError(f"Spotify URL must link to an album, not a track or playlist: {url}")
    return match.group(1)


def find_spotify_album(artist: str, album: str, album_id: str) -> dict:
    album_url = f"https://open.spotify.com/album/{album_id}"
    payload = fetch_json(f"{SPOTIFY_OEMBED}?{urlencode({'url': album_url})}")
    image = payload.get("thumbnail_url")
    title = payload.get("title", "")
    if not isinstance(image, str) or not image.startswith("https://"):
        raise ValueError(f"Spotify returned no cover for '{album}' by '{artist}'.")
    # Avoid accidentally displaying the wrong cover if someone pasted a wrong URL.
    if normalize(title) != normalize(album):
        if SequenceMatcher(None, normalize(title), normalize(album)).ratio() < 0.82:
            raise ValueError(
                f"Spotify URL for '{album}' by '{artist}' points to '{title}'. "
                "Check the link in albums.json."
            )
    return {"artwork_url": image, "album_url": album_url, "source": "Spotify"}


def artwork_600(url: str) -> str:
    return re.sub(r"/100x100(?:bb|-\d+)?\.(jpe?g|png)(?=$|\?)", r"/600x600bb.\1", url)


def find_itunes_album(artist: str, album: str) -> dict:
    query = urlencode({"term": f"{artist} {album}", "media": "music", "entity": "album", "country": "US", "limit": 30})
    data = fetch_json(f"{ITUNES_SEARCH}?{query}")
    expected_artist, expected_album = normalize(artist), normalize(album)
    candidates = []
    for item in data.get("results", []):
        if not item.get("artworkUrl100") or not item.get("collectionViewUrl"):
            continue
        item_artist = normalize(item.get("artistName", ""))
        item_album = normalize(item.get("collectionName", ""))
        if item_artist != expected_artist:
            continue
        if item_album == expected_album:
            score = 3.0
        elif item_album.startswith(expected_album + " "):
            score = 2.0
        else:
            ratio = SequenceMatcher(None, expected_album, item_album).ratio()
            if ratio < 0.87:
                continue
            score = ratio
        candidates.append((score, item))
    if not candidates:
        raise ValueError(
            f"Could not confidently find '{album}' by '{artist}' on iTunes. "
            "Add its Spotify album URL to the 'url' field in albums.json."
        )
    result = max(candidates, key=lambda pair: pair[0])[1]
    return {
        "artwork_url": artwork_600(result["artworkUrl100"]),
        "album_url": result["collectionViewUrl"],
        "year": result.get("releaseDate", "")[:4],
        "source": "iTunes",
    }


def get_album_info(config: dict) -> dict:
    artist, album = config["artist"], config["album"]
    link = config.get("url")
    if link is not None and (not isinstance(link, str) or not link.strip()):
        raise ValueError(f"The 'url' for '{album}' must be a nonempty string.")
    if link:
        album_id = spotify_album_id(link)
        if album_id:
            result = find_spotify_album(artist, album, album_id)
        else:
            # Non-Spotify URL: retain it as a clickable destination, but fetch from iTunes.
            result = find_itunes_album(artist, album)
        result["album_url"] = link
    else:
        result = find_itunes_album(artist, album)
    if "year" in config:
        year = str(config["year"])
        if not re.fullmatch(r"\d{4}", year):
            raise ValueError(f"The 'year' for '{album}' must contain four digits.")
        result["year"] = year
    return result


def render_cell(config: dict, info: dict) -> str:
    artist = html.escape(config["artist"])
    album = html.escape(config["album"])
    year = html.escape(info.get("year", ""))
    cover = html.escape(info["artwork_url"], quote=True)
    link = html.escape(info["album_url"], quote=True)
    return (
        '    <td align="center" valign="top" width="175">\n'
        f'      <a href="{link}"><img src="{cover}" width="145" alt="{album} album cover" /></a><br/>\n'
        f'      <strong>{album}</strong><br/>\n'
        f'      <sub>{artist}{" · " + year if year else ""}</sub>\n'
        '    </td>'
    )


def generate_gallery(albums: list) -> str:
    if not albums:
        return '<p align="center">More albums coming soon 🎧</p>'
    rows = []
    for i in range(0, len(albums), 4):
        cells = []
        for config in albums[i : i + 4]:
            if not isinstance(config, dict) or not all(
                isinstance(config.get(k), str) and config[k].strip() for k in ("artist", "album")
            ):
                raise ValueError(f"Every album must have nonempty 'artist' and 'album': {config!r}")
            info = get_album_info(config)
            print(f"Found: {config['album']} — {config['artist']} ({info['source']})")
            cells.append(render_cell(config, info))
        rows.append("  <tr>\n" + "\n".join(cells) + "\n  </tr>")
    return '<table align="center">\n' + "\n".join(rows) + "\n</table>"


def update_readme(readme: Path, section: str) -> None:
    current = readme.read_text(encoding="utf-8")
    if current.count(START) != 1 or current.count(END) != 1:
        raise ValueError("README.md needs exactly one pair of MUSIC-GALLERY:START/END markers")
    before, rest = current.split(START, 1)
    _, after = rest.split(END, 1)
    updated = before + START + "\n" + section + "\n" + END + after
    if updated != current:
        readme.write_text(updated, encoding="utf-8")
        print("README.md gallery updated")
    else:
        print("Gallery already up to date")


def main() -> None:
    albums = json.loads((ROOT / "albums.json").read_text(encoding="utf-8"))
    if not isinstance(albums, list):
        raise ValueError("albums.json must contain a JSON array")
    gallery = generate_gallery(albums)
    update_readme(ROOT / "README.md", gallery)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, json.JSONDecodeError, URLError, HTTPError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
