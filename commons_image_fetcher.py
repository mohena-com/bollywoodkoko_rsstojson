#!/usr/bin/env python3
"""
BollywoodKoko Wikipedia Image Fetcher

Replaces the previous Wikimedia Commons API fetcher.

Why:
    commons.wikimedia.org is not reachable from the current network,
    while en.wikipedia.org and upload.wikimedia.org are reachable.

Flow:
    slide JSON
        -> extract person/movie candidates
        -> Wikipedia article search
        -> article main image
        -> upload.wikimedia.org download
        -> persistent local cache
        -> update slide JSON open_image

Usage:
    python wikipedia_image_fetcher.py --category news
    python wikipedia_image_fetcher.py --category news --debug
    python wikipedia_image_fetcher.py --category news --force
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import requests
import yaml


DEFAULT_CONFIG = "config.yaml"
DEFAULT_CACHE = "/Volumes/Extreme SSD/webmaster-ai/POJO_PROJECT/data/images"

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"

MIN_WIDTH = 300
MIN_HEIGHT = 300
MAX_CANDIDATES_PER_SLIDE = 3

DEBUG = False


# ---------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------

session = requests.Session()

WIKI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/153.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Connection": "close",
}

IMAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/153.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "image/avif,image/webp,image/apng,"
        "image/svg+xml,image/*,*/*;q=0.8"
    ),
    "Referer": "https://en.wikipedia.org/",
    "Connection": "close",
}


# ---------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------

def log(message, level="INFO"):
    if level == "DEBUG" and not DEBUG:
        return
    print(f"[IMAGE][{level}] {message}")


# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------

def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------
# TEXT HELPERS
# ---------------------------------------------------------------------

def clean(value):
    if not value:
        return ""
    value = re.sub(r"<[^>]+>", " ", str(value))
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def slugify(value):
    value = clean(value).lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = value.strip("_")
    return value[:100] or "unknown"


def normalize_name(value):
    value = clean(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" ,.;:!?-–—")


def looks_like_bad_candidate(value):
    """Reject dates, generic phrases, sentences and obvious noise."""

    value = normalize_name(value)

    if not value:
        return True

    if len(value) > 80:
        return True

    words = value.split()

    if len(words) > 6:
        return True

    # Dates / years.
    if re.fullmatch(r"\d{1,2}(st|nd|rd|th)?\s+\w+\s+\d{4}", value, re.I):
        return True

    if re.fullmatch(r"\d{4}", value):
        return True

    # Generic sentence fragments which appeared in the old extractor.
    bad_phrases = {
        "will release on",
        "will hit theatres on",
        "live with",
        "tickets",
        "around themes of love and family",
        "latest directorial venture",
        "and its creative team",
    }

    if value.casefold() in bad_phrases:
        return True

    # Must contain at least one alphabetic character.
    if not re.search(r"[A-Za-z]", value):
        return True

    return False


def add_candidate(candidates, value):
    value = normalize_name(value)

    if looks_like_bad_candidate(value):
        return

    key = value.casefold()

    if key not in {x.casefold() for x in candidates}:
        candidates.append(value)


# ---------------------------------------------------------------------
# CANDIDATE EXTRACTION
# ---------------------------------------------------------------------

def extract_candidates(data):
    """
    Person/movie-first extraction.

    Priority:
      1. structured entities.people / movies
      2. explicit person/movie patterns
      3. capitalized proper-name sequences from headline/title
      4. emphasis words

    Maximum of three Wikipedia searches per slide.
    """

    slide = data.get("slide", data)

    candidates = []

    # 1. Structured entities if Qwen has supplied them.
    entities = slide.get("entities") or data.get("entities") or {}

    people = entities.get("people", []) if isinstance(entities, dict) else []
    movies = entities.get("movies", []) if isinstance(entities, dict) else []

    if isinstance(people, str):
        people = [people]

    if isinstance(movies, str):
        movies = [movies]

    for value in people:
        if isinstance(value, dict):
            value = (
                value.get("name")
                or value.get("person")
                or value.get("title")
                or ""
            )
        add_candidate(candidates, value)

    for value in movies:
        if isinstance(value, dict):
            value = (
                value.get("name")
                or value.get("title")
                or value.get("movie")
                or ""
            )
        add_candidate(candidates, value)

    # 2. Headline/title sources.
    texts = []

    for key in ("headline", "title", "hero_text"):
        value = slide.get(key)
        if value:
            texts.append(clean(value))

    source = slide.get("source") or data.get("source") or {}
    if isinstance(source, dict):
        for key in ("title", "headline"):
            value = source.get(key)
            if value:
                texts.append(clean(value))

    # Some versions of the converter put the source title here.
    for key in ("source_title", "original_title"):
        value = slide.get(key)
        if value:
            texts.append(clean(value))

    # 3. Strong role patterns.
    role_pattern = re.compile(
        r"\b(?:starring|starrer|stars|starred by|"
        r"actor|actress|director|producer|"
        r"with|featuring|feat\.?)\s+"
        r"([A-Z][A-Za-z.'’\-]+"
        r"(?:\s+[A-Z][A-Za-z.'’\-]+){1,3})"
    )

    for text in texts:
        for match in role_pattern.finditer(text):
            add_candidate(candidates, match.group(1))

    # 4. Capitalized proper-name sequences.
    # Avoid common headline words.
    stop = {
        "The", "This", "That", "With", "After", "Before", "Latest",
        "Exclusive", "Confirmed", "Big", "New", "Film", "Movie",
        "Series", "Actor", "Actress", "Director", "Producer",
        "Release", "Date", "January", "February", "March", "April",
        "May", "June", "July", "August", "September", "October",
        "November", "December", "Monday", "Tuesday", "Wednesday",
        "Thursday", "Friday", "Saturday", "Sunday",
        "India", "Indian",
    }

    proper_pattern = re.compile(
        r"\b[A-Z][A-Za-z.'’\-]+"
        r"(?:\s+[A-Z][A-Za-z.'’\-]+){1,3}\b"
    )

    for text in texts:
        for match in proper_pattern.finditer(text):
            candidate = match.group(0)
            first = candidate.split()[0]

            if first in stop:
                continue

            add_candidate(candidates, candidate)

    # 5. Quoted titles.
    for text in texts:
        for match in re.findall(r"[“\"]([^”\"]{2,60})[”\"]", text):
            add_candidate(candidates, match)

    # 6. Emphasis words only as a last resort, and only if they form
    # a plausible multi-word proper name.
    emphasis = slide.get("emphasis_words") or slide.get("emphasis") or []

    if isinstance(emphasis, str):
        emphasis = [emphasis]

    for value in emphasis:
        if isinstance(value, dict):
            value = value.get("text") or value.get("word") or ""
        if " " in str(value):
            add_candidate(candidates, value)

    # Remove obvious sentence-like candidates.
    candidates = [
        x for x in candidates
        if not looks_like_bad_candidate(x)
    ]

    return candidates[:MAX_CANDIDATES_PER_SLIDE]


# ---------------------------------------------------------------------
# WIKIPEDIA SEARCH
# ---------------------------------------------------------------------

def search_wikipedia(query):
    params = {
        "action": "query",
        "format": "json",
        "list": "search",
        "srsearch": query,
        "srnamespace": 0,
        "srlimit": 8,
        "utf8": 1,
    }

    log(f'Searching Wikipedia: "{query}"')

    try:
        response = session.get(
            WIKIPEDIA_API,
            params=params,
            headers=WIKI_HEADERS,
            timeout=30,
        )

        log(f"HTTP {response.status_code} for query={query!r}", "DEBUG")
        response.raise_for_status()

        return response.json().get("query", {}).get("search", [])

    except Exception as exc:
        log(f'Wikipedia search failed for "{query}": {exc}', "ERROR")
        return []


def choose_article(query, results):
    if not results:
        return None

    normalized = clean(query).casefold()

    # Exact match.
    for result in results:
        title = clean(result.get("title"))
        if title.casefold() == normalized:
            return result

    # Score title overlap.
    query_words = {
        w.casefold()
        for w in re.findall(r"[A-Za-z0-9]+", query)
        if len(w) > 2
    }

    scored = []

    for result in results:
        title = clean(result.get("title"))
        title_words = {
            w.casefold()
            for w in re.findall(r"[A-Za-z0-9]+", title)
            if len(w) > 2
        }

        overlap = len(query_words & title_words)
        exact_prefix = title.casefold().startswith(normalized)

        score = overlap * 10 + (5 if exact_prefix else 0)

        scored.append((score, result))

    scored.sort(key=lambda x: x[0], reverse=True)

    return scored[0][1]


# ---------------------------------------------------------------------
# ARTICLE IMAGE
# ---------------------------------------------------------------------

def get_article_image(pageid):
    params = {
        "action": "query",
        "format": "json",
        "pageids": pageid,
        "prop": "pageimages",
        "piprop": "original",
        "pilicense": "any",
    }

    try:
        response = session.get(
            WIKIPEDIA_API,
            params=params,
            headers=WIKI_HEADERS,
            timeout=30,
        )

        log(
            f"Image metadata HTTP {response.status_code} "
            f"for pageid={pageid}",
            "DEBUG",
        )

        response.raise_for_status()

        pages = response.json().get("query", {}).get("pages", {})
        page = pages.get(str(pageid))

        if not page:
            return None

        original = page.get("original")

        if not original:
            return None

        return {
            "pageid": page.get("pageid"),
            "title": page.get("title"),
            "image_url": original.get("source"),
            "width": original.get("width"),
            "height": original.get("height"),
        }

    except Exception as exc:
        log(
            f"Failed to retrieve image metadata for pageid={pageid}: {exc}",
            "ERROR",
        )
        return None


# ---------------------------------------------------------------------
# IMAGE DOWNLOAD
# ---------------------------------------------------------------------

def clean_image_url(url):
    """
    Remove Wikipedia API tracking parameters.

    Example:
      ...png?utm_source=en.wikipedia.org...
    becomes:
      ...png
    """

    parsed = urlparse(url)

    return (
        f"{parsed.scheme}://"
        f"{parsed.netloc}"
        f"{parsed.path}"
    )


def image_extension(url, content_type=""):
    path = urlparse(url).path.lower()

    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        if path.endswith(ext):
            return ext

    content_type = content_type.lower()

    if "jpeg" in content_type:
        return ".jpg"
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"

    return ".jpg"


def download_image(url, output_path):
    clean_url = clean_image_url(url)

    log(f"Original image URL: {url}", "DEBUG")
    log(f"Clean image URL: {clean_url}", "DEBUG")

    try:
        response = session.get(
            clean_url,
            headers=IMAGE_HEADERS,
            timeout=60,
            stream=True,
            allow_redirects=True,
        )

        log(
            f"Image HTTP {response.status_code}; "
            f"Content-Type={response.headers.get('Content-Type')}",
            "DEBUG",
        )

        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "")

        if not content_type.lower().startswith("image/"):
            raise ValueError(
                f"Not an image response: {content_type}"
            )

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        total = 0

        with open(output_path, "wb") as f:
            for chunk in response.iter_content(
                chunk_size=64 * 1024
            ):
                if chunk:
                    f.write(chunk)
                    total += len(chunk)

        if total == 0:
            raise ValueError("Downloaded image is empty")

        log(
            f"Downloaded {total:,} bytes -> {output_path}",
            "DEBUG",
        )

        return True

    except Exception as exc:
        log(
            f"Image download failed: {exc}",
            "ERROR",
        )

        try:
            if output_path.exists():
                output_path.unlink()
        except Exception:
            pass

        return False


# ---------------------------------------------------------------------
# CACHE
# ---------------------------------------------------------------------

def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_json(path):
    try:
        return json.loads(
            path.read_text(encoding="utf-8")
        )
    except Exception:
        return {}


# ---------------------------------------------------------------------
# PROCESS ONE SLIDE
# ---------------------------------------------------------------------

def process_slide(slide_path, image_dir, allowed=None, force=False):
    """
    Compatibility-preserving entry point.

    Existing run.sh/pipeline can continue calling:

        process_slide(path, image_dir, allowed, force)

    'allowed' is retained for compatibility, but Wikipedia article
    images are not treated as license-verified by this stage.
    """

    slide_path = Path(slide_path)
    image_dir = Path(image_dir)

    data = json.loads(
        slide_path.read_text(encoding="utf-8")
    )

    slide = data.get("slide", data)

    number = slide.get("number", "?")
    headline = slide.get("headline", "")

    # Persistent cache configured in config.yaml.
    # Fall back to the existing project-wide location.
    global CONFIG

    images_cfg = (
        CONFIG.get("images", {})
        if isinstance(CONFIG, dict)
        else {}
    )

    cache_root = Path(
        images_cfg.get(
            "cache_folder",
            DEFAULT_CACHE,
        )
    )

    log(f"===== Slide {number} =====")
    log(f"File: {slide_path}", "DEBUG")
    log(f"Headline: {headline}")
    log(f"Persistent cache root: {cache_root}", "DEBUG")
    log(f"Force refresh: {force}", "DEBUG")

    candidates = extract_candidates(data)

    log(
        f"Candidates ({len(candidates)}): {candidates}",
        "DEBUG",
    )

    open_image = data.setdefault(
        "open_image",
        {},
    )

    open_image.update({
        "provider": "Wikipedia / Wikimedia",
        "search_queries": candidates,
    })

    # --------------------------------------------------------------
    # CACHE-FIRST
    # --------------------------------------------------------------

    for query in candidates:

        entity_dir = cache_root / slugify(query)
        metadata_path = entity_dir / "metadata.json"

        if not force and metadata_path.exists():

            metadata = load_json(metadata_path)

            if (
                metadata.get("status") == "ok"
                and metadata.get("local_file")
                and Path(
                    metadata["local_file"]
                ).exists()
            ):

                log(
                    f"CACHE HIT: {query} -> "
                    f"{metadata['local_file']}"
                )

                open_image.update(metadata)
                open_image["status"] = "cached"

                slide["image_url"] = ""

                save_json(
                    slide_path,
                    data,
                )

                return True

    # --------------------------------------------------------------
    # WIKIPEDIA SEARCH
    # --------------------------------------------------------------

    for query in candidates:

        entity_dir = cache_root / slugify(query)
        metadata_path = entity_dir / "metadata.json"

        # Don't reuse negative cache unless --force is supplied.
        if not force and metadata_path.exists():

            metadata = load_json(metadata_path)

            if metadata.get("status") == "not_found":
                log(
                    f"NEGATIVE CACHE HIT: {query}",
                    "DEBUG",
                )
                continue

        results = search_wikipedia(query)

        if not results:
            continue

        article = choose_article(
            query,
            results,
        )

        if not article:
            continue

        log(
            f'Wikipedia article selected: '
            f'{article.get("title")!r}'
        )

        pageid = article.get("pageid")

        if not pageid:
            continue

        image_info = get_article_image(pageid)

        if not image_info:
            log(
                f'No article image for "{query}"',
                "WARN",
            )
            continue

        width = int(image_info.get("width") or 0)
        height = int(image_info.get("height") or 0)

        if width < MIN_WIDTH or height < MIN_HEIGHT:
            log(
                f'Image too small for "{query}": '
                f"{width}x{height}",
                "WARN",
            )
            continue

        image_url = image_info.get("image_url")

        if not image_url:
            continue

        # ----------------------------------------------------------
        # DOWNLOAD
        # ----------------------------------------------------------

        clean_url = clean_image_url(image_url)

        # First determine extension from URL.
        ext = image_extension(clean_url)

        image_path = entity_dir / f"image{ext}"

        if not download_image(
            image_url,
            image_path,
        ):
            continue

        wikipedia_title = article.get(
            "title",
            query,
        )

        wikipedia_url = (
            "https://en.wikipedia.org/wiki/"
            + quote(
                wikipedia_title.replace(" ", "_"),
                safe="_()",
            )
        )

        metadata = {
            "status": "ok",

            "provider": "Wikipedia / Wikimedia",

            "entity": query,

            "wikipedia": {
                "title": wikipedia_title,
                "pageid": pageid,
                "url": wikipedia_url,
            },

            "image": {
                "source_url": clean_url,
                "local_file": str(
                    image_path.resolve()
                ),
                "width": width,
                "height": height,
            },

            "source_domain": "upload.wikimedia.org",

            "license_verification": {
                "status": "NOT_VERIFIED",
                "note": (
                    "Image was obtained from the main image of "
                    "a Wikipedia article. The image may originate "
                    "from Wikimedia Commons, but this pipeline "
                    "does not currently verify the Commons file "
                    "license because commons.wikimedia.org is "
                    "not reachable from the current network."
                ),
            },

            "search_query": query,
        }

        save_json(
            metadata_path,
            metadata,
        )

        open_image.update(metadata)

        # Painter should use local_file, not the remote BH/Wikipedia URL.
        slide["image_url"] = ""

        save_json(
            slide_path,
            data,
        )

        log(
            f"SUCCESS: {image_path}"
        )

        return True

    # --------------------------------------------------------------
    # NOTHING FOUND
    # --------------------------------------------------------------

    log(
        "All Wikipedia candidates failed",
        "WARN",
    )

    # Cache negative result for each attempted entity.
    for query in candidates:

        entity_dir = cache_root / slugify(query)
        metadata_path = entity_dir / "metadata.json"

        if force or not metadata_path.exists():

            save_json(
                metadata_path,
                {
                    "status": "not_found",
                    "provider": "Wikipedia / Wikimedia",
                    "entity": query,
                    "search_query": query,
                    "reason": (
                        "No usable Wikipedia article image "
                        "was found."
                    ),
                },
            )

    open_image.update({
        "status": "not_found",
        "provider": "Wikipedia / Wikimedia",
        "search_queries": candidates,
        "fallback": "cinematic_background",
        "reason": (
            "No usable Wikipedia article image was found."
        ),
    })

    slide["image_url"] = ""

    save_json(
        slide_path,
        data,
    )

    return False


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--category",
        required=True,
    )

    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore existing positive/negative cache entries.",
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable detailed diagnostics.",
    )

    args = parser.parse_args()

    global DEBUG
    DEBUG = args.debug

    global CONFIG
    CONFIG = load_config(args.config)

    output_root = Path(
        CONFIG["output"]["folder"]
    )

    qwen_dir = (
        output_root
        / "qwen_input"
        / args.category
    )

    image_dir = (
        output_root
        / "open_images"
        / args.category
    )

    if not qwen_dir.exists():
        raise FileNotFoundError(
            f"Category input folder not found: {qwen_dir}"
        )

    slides = sorted(
        qwen_dir.glob("slide_*.json")
    )

    print("==========================================")
    print(" Wikipedia Image Fetcher")
    print("==========================================")
    print(f"Category      : {args.category}")
    print(f"Slides        : {len(slides)}")
    print(f"Output images : {image_dir}")
    print(
        "Provider      : Wikipedia -> upload.wikimedia.org"
    )
    print(
        "License       : NOT VERIFIED at image-fetch stage"
    )

    found = 0

    for path in slides:

        try:

            ok = process_slide(
                path,
                image_dir,
                None,
                args.force,
            )

            if ok:
                found += 1

        except Exception as exc:

            print(
                f"    [ERROR] {path.name}: {exc}"
            )

        # Keep requests serialized.
        time.sleep(0.5)

    image_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    print()
    print(
        f"Completed: {found}/{len(slides)} slides "
        f"have Wikipedia images."
    )

    if found:
        print(
            "Images are stored in the persistent cache "
            "and referenced through open_image.local_file."
        )


if __name__ == "__main__":
    main()
