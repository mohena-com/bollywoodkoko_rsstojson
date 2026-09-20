#!/usr/bin/env python3
"""
Wikipedia image fetcher for BollywoodKoko.

Purpose:
- Extract conservative, headline-first entity candidates.
- Search Wikipedia for article images.
- Download images into the persistent image cache.
- Handle Wikipedia HTTP 429 with Retry-After/exponential backoff.
- Preserve compatibility with:
      process_slide(slide_path, image_dir, allowed=None, force=False)
- Do NOT claim image licenses are verified.
"""

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests


WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
DEFAULT_CACHE_ROOT = (
    "/Volumes/Extreme SSD/webmaster-ai/POJO_PROJECT/data/images"
)
TIMEOUT = 20

MAX_RETRIES = 4
RETRY_DELAYS = [3, 6, 12, 24]

session = requests.Session()
session.headers.update({
    "User-Agent": (
        "BollywoodKoko/2.0 "
        "(Wikipedia image fetcher; contact via project owner)"
    ),
    "Accept": "application/json",
})


def log(message, level="INFO"):
    print(f"[WIKI][{level}] {message}", flush=True)


def clean(value):
    if value is None:
        return ""

    value = str(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def slugify(text):
    text = str(text).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = text.strip("_")
    return text or "unknown"


def wikipedia_get(params):
    """
    Rate-limit-aware Wikipedia API request.
    """

    for attempt in range(MAX_RETRIES):
        try:
            response = session.get(
                WIKIPEDIA_API,
                params=params,
                timeout=TIMEOUT,
            )

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")

                if retry_after:
                    try:
                        delay = float(retry_after)
                    except ValueError:
                        delay = RETRY_DELAYS[
                            min(attempt, len(RETRY_DELAYS) - 1)
                        ]
                else:
                    delay = RETRY_DELAYS[
                        min(attempt, len(RETRY_DELAYS) - 1)
                    ]

                delay += random.uniform(0.5, 1.5)

                log(
                    f"HTTP 429. Waiting {delay:.1f}s before retry...",
                    "WARN",
                )
                time.sleep(delay)
                continue

            response.raise_for_status()
            return response

        except requests.RequestException as exc:
            if attempt >= MAX_RETRIES - 1:
                log(
                    f"Request failed after {MAX_RETRIES} attempts: {exc}",
                    "ERROR",
                )
                return None

            delay = RETRY_DELAYS[
                min(attempt, len(RETRY_DELAYS) - 1)
            ]

            log(
                f"Request failed: {exc}. Retrying in {delay}s...",
                "WARN",
            )
            time.sleep(delay)

    return None


def extract_candidates(data):
    """
    Extract high-quality Wikipedia search candidates.

    Priority:
      1. Qwen emphasis words
      2. Headline
      3. Hero text
      4. Supporting text

    The function intentionally avoids arbitrary sentence fragments,
    dates and generic phrases.
    """

    slide = data.get("slide", data)
    design = data.get("design", {}) or {}

    candidates = []

    def add(value, reason=""):
        if not value:
            return

        value = clean(value)
        if not value:
            return

        reject = {
            "baby girl",
            "social media post",
            "foot image",
            "announcement",
            "date",
            "bollywood news",
            "latest news",
            "news",
            "industry",
            "live",
            "title track",
        }

        if value.casefold() in reject:
            return

        # Reject obvious dates.
        if re.fullmatch(
            r"(January|February|March|April|May|June|July|August|"
            r"September|October|November|December)\s+\d{1,2}"
            r"(,\s*\d{4})?",
            value,
            re.I,
        ):
            return

        if re.fullmatch(
            r"\d{1,2}(st|nd|rd|th)?\s+\w+\s+\d{4}",
            value,
            re.I,
        ):
            return

        # Wikipedia search candidates should be entity-like.
        if len(value.split()) > 6:
            return

        if value.endswith((".", ",", ":", ";")):
            return

        generic = {
            "the",
            "will release",
            "will release on",
            "has been postponed",
            "social media",
            "avoid clash",
            "clash",
            "release",
            "released",
            "pushed",
            "incoming",
        }

        if value.casefold() in generic:
            return

        if value not in candidates:
            candidates.append(value)
            if reason:
                log(f"candidate += {value!r} ({reason})", "DEBUG")

    # ---------------------------------------------------------
    # 1. Explicit Qwen emphasis words
    # ---------------------------------------------------------

    emphasis = design.get("emphasis_words", [])

    if isinstance(emphasis, str):
        emphasis = [emphasis]

    for item in emphasis:
        add(item, "Qwen emphasis")

    # ---------------------------------------------------------
    # 2. Headline / hero text
    # ---------------------------------------------------------

    headline = (
        slide.get("headline")
        or data.get("headline")
        or design.get("hero_text")
        or ""
    )

    headline = clean(headline)

    # Capitalized multi-word entities.
    proper_name_pattern = re.compile(
        r"\b"
        r"(?:[A-Z][A-Za-zÀ-ÖØ-öø-ÿ0-9'-]*"
        r"(?:\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ0-9'-]*){1,5})"
        r"\b"
    )

    for match in proper_name_pattern.findall(headline):
        add(match, "headline proper-name")

    # Specific useful patterns.
    patterns = [
        # X's Ranger / X's January
        r"\b([A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+){0,3})"
        r"['’]s\s+([A-Z][A-Za-z0-9'-]+)",

        # Golmaal 5 / Housefull 5 etc.
        r"\b([A-Z][A-Za-z]+(?:\s+\d+))\b",

        # Karnataka High Court / Bombay High Court etc.
        r"\b([A-Z][A-Za-z]+\s+"
        r"(?:High|Supreme|District)\s+Court)\b",

        # Capitalized movie/person names.
        r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){1,4})\b",
    ]

    for pattern in patterns:
        for match in re.findall(pattern, headline):
            if isinstance(match, tuple):
                for item in match:
                    add(item, "headline pattern")
            else:
                add(match, "headline pattern")

    # ---------------------------------------------------------
    # 3. Clean possessives and duplicates
    # ---------------------------------------------------------

    cleaned_candidates = []

    for candidate in candidates:
        candidate = re.sub(r"['’]s$", "", candidate).strip()

        if candidate and candidate not in cleaned_candidates:
            cleaned_candidates.append(candidate)

    candidates = cleaned_candidates

    # ---------------------------------------------------------
    # 4. Supporting text only if headline produced too little
    # ---------------------------------------------------------

    if len(candidates) < 2:
        supporting = (
            design.get("supporting_text")
            or slide.get("summary")
            or data.get("summary")
            or ""
        )

        for match in proper_name_pattern.findall(clean(supporting)):
            add(match, "supporting text")

    # ---------------------------------------------------------
    # 5. Final quality filter
    # ---------------------------------------------------------

    blacklist = {
        "the",
        "this",
        "that",
        "will",
        "has",
        "have",
        "with",
        "from",
        "into",
        "avoid",
        "clash",
        "release",
        "released",
        "postponed",
        "pushed",
        "incoming",
        "live",
        "title track",
        "january",
        "september",
    }

    final = []

    for candidate in candidates:
        candidate = clean(candidate)

        if not candidate:
            continue

        if candidate.casefold() in blacklist:
            continue

        if len(candidate.split()) > 5:
            continue

        if re.search(r"\.\s", candidate):
            continue

        if candidate not in final:
            final.append(candidate)

    # Limit API searches.
    final = final[:4]

    log("Extracted search candidates:")

    for index, candidate in enumerate(final, 1):
        log(f"  {index}. {candidate}")

    return final


def search_wikipedia(query):
    params = {
        "action": "query",
        "format": "json",
        "list": "search",
        "srsearch": query,
        "srlimit": 5,
        "srnamespace": 0,
    }

    response = wikipedia_get(params)

    if response is None:
        return []

    try:
        payload = response.json()
    except ValueError:
        return []

    return payload.get("query", {}).get("search", [])


def choose_article(query, results):
    if not results:
        return None

    q = clean(query).casefold()

    # Exact title first.
    for item in results:
        title = clean(item.get("title", ""))
        if title.casefold() == q:
            return title

    # Otherwise first search result.
    return clean(results[0].get("title", ""))


def get_article_image(title):
    params = {
        "action": "query",
        "format": "json",
        "titles": title,
        "prop": "pageimages",
        "piprop": "original",
    }

    response = wikipedia_get(params)

    if response is None:
        return None

    try:
        payload = response.json()
    except ValueError:
        return None

    pages = (
        payload.get("query", {})
        .get("pages", {})
    )

    for page in pages.values():
        original = page.get("original")

        if not original:
            continue

        return {
            "title": page.get("title", title),
            "source_url": original.get("source", ""),
            "width": original.get("width"),
            "height": original.get("height"),
        }

    return None


def find_wikipedia_image(query):
    log(f'Candidate "{query}"')

    results = search_wikipedia(query)

    if not results:
        log(f'No Wikipedia results for "{query}"', "DEBUG")
        return None

    title = choose_article(query, results)

    if not title:
        return None

    log(f"Selected article: {title}")

    image = get_article_image(title)

    if not image:
        log(f"No article image for {title!r}", "DEBUG")
        return None

    return {
        "provider": "Wikipedia / Wikimedia",
        "status": "ok",
        "entity": title,
        "image": image,
        "license_verification": {
            "status": "NOT_VERIFIED",
            "reason": (
                "Image was retrieved through Wikipedia. "
                "The pipeline does not independently verify "
                "the underlying Wikimedia Commons license."
            ),
        },
    }


def get_cache_root(data=None, image_dir=None):
    if image_dir:
        return Path(image_dir)

    config = data.get("config", {}) if isinstance(data, dict) else {}

    images_config = config.get("images", {}) or {}

    configured = images_config.get("cache_folder")

    if configured:
        return Path(configured)

    return Path(DEFAULT_CACHE_ROOT)


def download_image(url, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        response = session.get(
            url,
            timeout=TIMEOUT,
            stream=True,
        )

        response.raise_for_status()

        content_type = response.headers.get(
            "Content-Type",
            "",
        ).lower()

        if not (
            content_type.startswith("image/")
            or urlparse(url).path.lower().endswith(
                (".jpg", ".jpeg", ".png", ".webp")
            )
        ):
            log(
                f"Unexpected content type: {content_type}",
                "WARN",
            )
            return False

        with destination.open("wb") as handle:
            for chunk in response.iter_content(1024 * 128):
                if chunk:
                    handle.write(chunk)

        return destination.exists() and destination.stat().st_size > 0

    except requests.RequestException as exc:
        log(f"Image download failed: {exc}", "ERROR")
        return False


def process_slide(slide_path, image_dir, allowed=None, force=False):
    """
    Compatibility-preserving entry point used by run.sh/pipeline.
    """

    slide_path = Path(slide_path)
    image_root = Path(image_dir) if image_dir else Path(DEFAULT_CACHE_ROOT)

    try:
        with slide_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        log(f"{slide_path}: unable to read JSON: {exc}", "ERROR")
        return False

    candidates = extract_candidates(data)

    if not candidates:
        log(f"{slide_path.name}: no usable Wikipedia candidates", "WARN")
        return False

    open_image = data.get("open_image", {}) or {}

    # Reuse existing local image unless force is requested.
    existing = (
        open_image.get("image", {}) or {}
    ).get("local_file")

    if existing and not force:
        existing_path = Path(existing)

        if existing_path.exists():
            log(f"Using cached image: {existing_path}")
            return True

    result = None

    # Search candidates sequentially and STOP after first success.
    for index, query in enumerate(candidates):

        result = find_wikipedia_image(query)

        if result:
            break

        # Avoid hammering Wikipedia with back-to-back searches.
        if index < len(candidates) - 1:
            time.sleep(1.5)

    if not result:
        data["open_image"] = {
            "provider": "Wikipedia / Wikimedia",
            "status": "not_found",
            "license_verification": {
                "status": "NOT_VERIFIED"
            },
        }

        with slide_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)

        log(f"{slide_path.name}: no Wikipedia image found", "WARN")
        return False

    entity = result["entity"]
    slug = slugify(entity)

    entity_dir = image_root / slug
    entity_dir.mkdir(parents=True, exist_ok=True)

    source_url = result["image"]["source_url"]

    suffix = Path(
        urlparse(source_url).path
    ).suffix.lower()

    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".jpg"

    local_file = entity_dir / f"image{suffix}"

    if force or not local_file.exists():
        log(f"Downloading image: {source_url}")

        if not download_image(source_url, local_file):
            return False
    else:
        log(f"Using cached image: {local_file}")

    result["image"]["local_file"] = str(local_file)

    # Keep image_url empty so downstream code doesn't accidentally
    # download/use the original remote source.
    data["image_url"] = ""

    data["open_image"] = result

    # Persist metadata.
    metadata = {
        "provider": result["provider"],
        "entity": result["entity"],
        "source_url": source_url,
        "local_file": str(local_file),
        "license_verification": result["license_verification"],
    }

    with (entity_dir / "metadata.json").open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            metadata,
            handle,
            ensure_ascii=False,
            indent=2,
        )

    with slide_path.open("w", encoding="utf-8") as handle:
        json.dump(
            data,
            handle,
            ensure_ascii=False,
            indent=2,
        )

    log(
        f"[SUCCESS] {slide_path.name}: "
        f"{entity} -> {local_file}"
    )

    return True


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--category",
        required=True,
    )

    parser.add_argument(
        "--input-dir",
        default=None,
    )

    parser.add_argument(
        "--image-dir",
        default=DEFAULT_CACHE_ROOT,
    )

    parser.add_argument(
        "--force",
        action="store_true",
    )

    parser.add_argument(
        "--debug",
        action="store_true",
    )

    args = parser.parse_args()

    if args.input_dir:
        input_dir = Path(args.input_dir)
    else:
        input_dir = (
            Path(
                "/Volumes/Extreme SSD/webmaster-ai/POJO_PROJECT/bollywood"
            )
            / "OP_JSON"
            / "qwen_input"
            / args.category
        )

    if not input_dir.exists():
        log(f"Input directory does not exist: {input_dir}", "ERROR")
        sys.exit(1)

    slide_files = sorted(input_dir.glob("slide_*.json"))

    if not slide_files:
        log(f"No slide JSON files found in {input_dir}", "WARN")
        return

    log(f"Category : {args.category}")
    log(f"Input    : {input_dir}")
    log(f"Cache    : {args.image_dir}")
    log(f"Slides   : {len(slide_files)}")

    success = 0

    for slide_path in slide_files:
        log(f"Processing {slide_path.name}")

        if process_slide(
            slide_path,
            args.image_dir,
            allowed=None,
            force=args.force,
        ):
            success += 1

    log(
        f"Completed: {success}/{len(slide_files)} "
        f"slides have Wikipedia images."
    )


if __name__ == "__main__":
    main()
