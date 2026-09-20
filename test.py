#!/usr/bin/env python3

"""
Wikipedia Image Fetcher

Usage:
    python wikipedia_image_fetcher.py "Deepika Padukone"

The program:
1. Searches Wikipedia for the requested entity.
2. Finds the best matching article.
3. Retrieves the article's main image.
4. Downloads the image from upload.wikimedia.org.
5. Saves the image and metadata locally.

It deliberately does NOT call commons.wikimedia.org.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import requests


# ============================================================
# CONFIG
# ============================================================

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"

OUTPUT_ROOT = Path("./wikipedia_images")

USER_AGENT = (
    "BollywoodKoko/1.0 "
    "(Wikipedia image fetcher; contact: your-email@example.com)"
)

TIMEOUT = 30

# Minimum image dimensions we will accept.
MIN_WIDTH = 300
MIN_HEIGHT = 300


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Connection": "close",
    }
)


# ============================================================
# HELPERS
# ============================================================

def slugify(text):
    """
    Convert an entity name into a safe directory/file name.
    """

    text = text.strip().lower()

    text = re.sub(r"[^a-z0-9]+", "_", text)

    text = text.strip("_")

    return text or "unknown"


def clean(text):
    if not text:
        return ""

    return re.sub(r"\s+", " ", str(text)).strip()


def safe_filename(text):
    """
    Make a safe filename while retaining readability.
    """

    text = clean(text)

    text = re.sub(r'[<>:"/\\|?*]', "_", text)

    text = text.strip(". ")

    return text or "image"


# ============================================================
# WIKIPEDIA SEARCH
# ============================================================

def search_wikipedia(query):
    """
    Search English Wikipedia and return candidate articles.
    """

    print()
    print("=" * 70)
    print("WIKIPEDIA SEARCH")
    print("=" * 70)
    print("Query:", query)

    params = {
        "action": "query",
        "format": "json",

        "list": "search",
        "srsearch": query,
        "srnamespace": 0,
        "srlimit": 10,

        "utf8": 1,
    }

    try:

        response = session.get(
            WIKIPEDIA_API,
            params=params,
            timeout=TIMEOUT,
        )

        print("HTTP status:", response.status_code)

        response.raise_for_status()

        data = response.json()

    except Exception as exc:

        print()
        print("[ERROR] Wikipedia search failed:")
        print(repr(exc))

        return []

    results = data.get("query", {}).get("search", [])

    candidates = []

    for item in results:

        title = clean(item.get("title"))

        if not title:
            continue

        candidates.append(
            {
                "pageid": item.get("pageid"),
                "title": title,
                "snippet": clean(
                    re.sub(
                        r"<.*?>",
                        "",
                        item.get("snippet", ""),
                    )
                ),
            }
        )

    print("Results:", len(candidates))

    for index, candidate in enumerate(candidates, start=1):

        print(
            f"{index:2}. "
            f"{candidate['title']} "
            f"(pageid={candidate['pageid']})"
        )

    return candidates


# ============================================================
# FIND BEST ARTICLE
# ============================================================

def choose_article(query, candidates):
    """
    Prefer exact title match.
    Otherwise use the first Wikipedia search result.
    """

    if not candidates:
        return None

    normalized_query = clean(query).casefold()

    # Exact title match.
    for candidate in candidates:

        if candidate["title"].casefold() == normalized_query:

            print()
            print("[INFO] Exact article match:")
            print(candidate["title"])

            return candidate

    # Otherwise first result.
    candidate = candidates[0]

    print()
    print("[INFO] Using top Wikipedia result:")
    print(candidate["title"])

    return candidate


# ============================================================
# GET ARTICLE IMAGE
# ============================================================

def get_article_image(pageid):
    """
    Get the original image associated with the Wikipedia article.
    """

    params = {
        "action": "query",
        "format": "json",

        "pageids": pageid,

        "prop": "pageimages",
        "piprop": "original",

        "pilicense": "any",
    }

    print()
    print("=" * 70)
    print("GETTING ARTICLE IMAGE")
    print("=" * 70)

    try:

        response = session.get(
            WIKIPEDIA_API,
            params=params,
            timeout=TIMEOUT,
        )

        print("HTTP status:", response.status_code)

        response.raise_for_status()

        data = response.json()

    except Exception as exc:

        print()
        print("[ERROR] Failed to get article image:")
        print(repr(exc))

        return None

    pages = data.get("query", {}).get("pages", {})

    page = pages.get(str(pageid))

    if not page:

        print("[WARN] Article page not returned.")

        return None

    original = page.get("original")

    if not original:

        print("[WARN] Wikipedia article has no original image.")

        return None

    result = {
        "pageid": page.get("pageid"),
        "title": page.get("title"),
        "image_url": original.get("source"),
        "width": original.get("width"),
        "height": original.get("height"),
    }

    print("Title :", result["title"])
    print("Image :", result["image_url"])
    print(
        "Size  :",
        f"{result['width']} x {result['height']}",
    )

    return result


# ============================================================
# IMAGE VALIDATION
# ============================================================

def validate_image_info(image_info):

    width = image_info.get("width") or 0
    height = image_info.get("height") or 0

    if width < MIN_WIDTH or height < MIN_HEIGHT:

        print()
        print(
            f"[WARN] Image too small: "
            f"{width} x {height}"
        )

        return False

    return True


# ============================================================
# DOWNLOAD IMAGE
# ============================================================

def download_image(image_info, output_dir):

    image_url = image_info["image_url"]

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Try to preserve the original extension.
    parsed = urlparse(image_url)

    filename = Path(
        unquote(parsed.path)
    ).name

    filename = safe_filename(filename)

    if not filename:
        filename = "image"

    # Remove Wikimedia query-derived oddities.
    filename = filename.split("?")[0]

    output_path = output_dir / filename

    print()
    print("=" * 70)
    print("DOWNLOADING IMAGE")
    print("=" * 70)

    print("URL :", image_url)
    print("File:", output_path)

    headers = {
        "User-Agent": USER_AGENT,
        "Connection": "close",
    }

    try:

        response = session.get(
            image_url,
            headers=headers,
            timeout=60,
            stream=True,
        )

        print("HTTP status:", response.status_code)
        print("Content-Type:", response.headers.get("Content-Type"))

        response.raise_for_status()

        total = 0

        with open(output_path, "wb") as file:

            for chunk in response.iter_content(
                chunk_size=1024 * 64
            ):

                if not chunk:
                    continue

                file.write(chunk)

                total += len(chunk)

        print()
        print("[SUCCESS] Image downloaded.")
        print("Path :", output_path.resolve())
        print("Size :", f"{total:,} bytes")

        return output_path

    except Exception as exc:

        print()
        print("[ERROR] Image download failed:")
        print(repr(exc))

        if output_path.exists():

            try:
                output_path.unlink()
            except Exception:
                pass

        return None


# ============================================================
# SAVE METADATA
# ============================================================

def save_metadata(
    entity,
    article,
    image_info,
    image_path,
    output_dir,
):

    metadata = {
        "query": entity,

        "wikipedia": {
            "api": WIKIPEDIA_API,
            "pageid": article.get("pageid"),
            "title": article.get("title"),
            "url": (
                "https://en.wikipedia.org/wiki/"
                + quote(
                    article["title"].replace(" ", "_"),
                    safe="_()",
                )
            ),
        },

        "image": {
            "source": image_info.get("image_url"),
            "local_file": str(
                image_path.resolve()
            ),
            "width": image_info.get("width"),
            "height": image_info.get("height"),
        },

        "source_domain": "upload.wikimedia.org",

        "license_verification": {
            "status": "NOT_VERIFIED",
            "note": (
                "The Wikipedia page image was successfully "
                "retrieved. This program does not assume that "
                "the image license is acceptable for publication. "
                "Verify the associated Wikimedia Commons file "
                "license before commercial/public use."
            ),
        },
    }

    metadata_path = output_dir / "metadata.json"

    metadata_path.write_text(
        json.dumps(
            metadata,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print()
    print("Metadata:")
    print(metadata_path.resolve())

    return metadata_path


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description="Download the Wikipedia article image for an entity."
    )

    parser.add_argument(
        "query",
        help='Entity to search, e.g. "Deepika Padukone"',
    )

    parser.add_argument(
        "--output",
        default=str(OUTPUT_ROOT),
        help="Output directory",
    )

    args = parser.parse_args()

    entity = clean(args.query)

    if not entity:

        print("[ERROR] Query cannot be empty.")
        sys.exit(1)

    output_root = Path(args.output)

    entity_dir = output_root / slugify(entity)

    print()
    print("=" * 70)
    print("WIKIPEDIA IMAGE FETCHER")
    print("=" * 70)
    print("Entity :", entity)
    print("Output :", entity_dir.resolve())

    # --------------------------------------------------------
    # 1. Search Wikipedia
    # --------------------------------------------------------

    candidates = search_wikipedia(entity)

    if not candidates:

        print()
        print("[RESULT] No Wikipedia article found.")

        sys.exit(2)

    # --------------------------------------------------------
    # 2. Select article
    # --------------------------------------------------------

    article = choose_article(
        entity,
        candidates,
    )

    if not article:

        print("[RESULT] Could not select article.")

        sys.exit(3)

    # --------------------------------------------------------
    # 3. Get image
    # --------------------------------------------------------

    image_info = get_article_image(
        article["pageid"]
    )

    if not image_info:

        print()
        print("[RESULT] Article has no usable image.")

        sys.exit(4)

    # --------------------------------------------------------
    # 4. Validate dimensions
    # --------------------------------------------------------

    if not validate_image_info(image_info):

        sys.exit(5)

    # --------------------------------------------------------
    # 5. Download
    # --------------------------------------------------------

    image_path = download_image(
        image_info,
        entity_dir,
    )

    if not image_path:

        sys.exit(6)

    # --------------------------------------------------------
    # 6. Metadata
    # --------------------------------------------------------

    save_metadata(
        entity,
        article,
        image_info,
        image_path,
        entity_dir,
    )

    # --------------------------------------------------------
    # DONE
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("COMPLETED")
    print("=" * 70)

    print("Entity :", entity)
    print("Article:", article["title"])
    print("Image  :", image_path.resolve())


if __name__ == "__main__":
    main()