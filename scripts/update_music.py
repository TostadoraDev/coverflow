"""Refresh the album gallery between the markers in README.md.

Uses Apple's public iTunes Search API. No API token, external dependencies,
or stored album-cover images are required.
"""

from __future__ import annotations

import html
import json
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
START = "<!-- MUSIC-GALLERY:START -->"
END = "<!-- MUSIC-GALLERY:END -->"
SEARCH_API = "https://itunes.apple.com/search"


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold()
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def artwork_600(url: str) -> str:
    """Use the same artwork CDN URL at higher resolution when possible."""
    return re.sub(r"/100x100(?:bb|-\d+)?\.(jpe?g|png)(?=$|\?)", r"/600x600bb.\1", url)


def find_album(artist: str, album: str) -> dict:
    query = urlencode(
        {"term": f"{artist} {album}", "media": "music", "entity": "album", "country": "US", "limit": 30}
    )
    request = Request(
        f"{SEARCH_API}?{query}",
        headers={"User-Agent": "GitHubMusicGallery/1.0 (personal readme generator)"},
    )
    with urlopen(request, timeout=20) as response:
        data = json.load(response)

    expected_artist = normalize(artist)
    expected_album = normalize(album)
    candidates = []
    for item in data.get("results", []):
        if not item.get("artworkUrl100") or not item.get("collectionViewUrl"):
            continue
        item_artist = normalize(item.get("artistName", ""))
        item_album = normalize(item.get("collectionName", ""))
        # Prevent matches with the wrong artist or a misleading album title.
        if item_artist != expected_artist:
            continue
        if item_album == expected_album:
            score = 3.0
        elif item_album.startswith(expected_album + " "):
            # Handles 'Deluxe', 'Bonus Tracks', etc. when no standard match exists.
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
            "Try the official album name or a different artist spelling."
        )
    return max(candidates, key=lambda pair: pair[0])[1]


def render_cell(config: dict, result: dict) -> str:
    artist = html.escape(config["artist"])
    album = html.escape(config["album"])
    year = html.escape(result.get("releaseDate", "")[:4])
    cover = html.escape(artwork_600(result["artworkUrl100"]), quote=True)
    link = html.escape(config.get("url") or result["collectionViewUrl"], quote=True)
    return (
        '    <td align="center" valign="top" width="175">\n'
        f'      <a href="{link}"><img src="{cover}" width="145" alt="{album} album cover" /></a><br/>\n'
        f'      <strong>{album}</strong><br/>\n'
        f'      <sub>{artist}{" · " + year if year else ""}</sub>\n'
        '    </td>'
    )


def generate_gallery(albums: list) -> str:
    if not albums:
        return "<p align=\"center\">More albums coming soon 🎧</p>"
    rows = []
    for i in range(0, len(albums), 4):
        cells = []
        for config in albums[i : i + 4]:
            if not all(isinstance(config.get(k), str) and config[k].strip() for k in ("artist", "album")):
                raise ValueError(f"Every album must have nonempty 'artist' and 'album': {config!r}")
            result = find_album(config["artist"], config["album"])
            print(f"Found: {config['album']} — {config['artist']} (iTunes #{result.get('collectionId', '?')})")
            cells.append(render_cell(config, result))
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
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
