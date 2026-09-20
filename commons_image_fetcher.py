#!/usr/bin/env python3
"""
BollywoodKoko - Wikimedia Commons licensed image fetcher

Runs after qwen_slide_designer.py and before paint_slides.py.
For each slide it searches Wikimedia Commons for a relevant image, verifies
an explicitly reusable license, downloads a local copy, and stores attribution
metadata in the slide JSON.

It deliberately does NOT fall back to the original Bollywood Hungama image.
"""

import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests
import yaml


DEBUG = True

def log(message, level="INFO"):
    """Consistent diagnostic logging. DEBUG messages appear only with --debug."""
    if level == "DEBUG" and not DEBUG:
        return
    print(f"[IMAGE][{level}] {message}", flush=True)

def log_candidates(candidates):
    log("Extracted search candidates:")
    if not candidates:
        log("  (none)", "WARN")
        return
    for i, candidate in enumerate(candidates, 1):
        log(f"  {i}. {candidate}")


DEFAULT_CONFIG = "config.yaml"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "BollywoodKoko/1.0 (licensed image fetcher; contact project owner)"

DEFAULT_ALLOWED = {
    "public domain",
    "cc0",
    "cc by 4.0",
    "cc by 3.0",
    "cc by 2.0",
}


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def clean(value):
    value = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", value).strip()


def strip_wiki_markup(value):
    value = clean(value)
    value = re.sub(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", r"\1", value)
    value = re.sub(r"\[https?://[^\s]+\s+([^\]]+)\]", r"\1", value)
    return value


def extract_candidates(slide):
    """
    Person-first candidate extraction.

    Priority:
      1. structured entities.people
      2. explicit person-name patterns in story/description
      3. hyphenated/collaborative name patterns
      4. movie/show title
      5. emphasis/headline fallback

    Returns unique canonical-ish search strings while preserving order.
    """
    candidates = []
    seen = set()

    def add(value, reason=""):
        if not value:
            return
        value = re.sub(r"\s+", " ", str(value)).strip(" ,.;:!?\"'")
        if not value:
            return
        key = value.casefold()
        if key in seen:
            return
        # Avoid obvious generic phrases.
        generic = {
            "bollywood news", "latest bollywood news", "news",
            "bollywood", "movie", "film", "actor", "actress",
            "director", "producer", "celebrity"
        }
        if key in generic:
            return
        seen.add(key)
        candidates.append(value)
        if reason:
            log(f"candidate += {value!r} ({reason})", "DEBUG")

    slide_obj = slide.get("slide", slide)
    source = slide.get("source", {}) or {}
    design = slide.get("design", {}) or {}
    entities = slide.get("entities", {}) or {}

    headline = slide_obj.get("headline", "") or ""
    story = slide_obj.get("story", "") or ""
    description = source.get("description", "") or ""
    source_title = source.get("title", "") or ""

    # 1. Structured entities from a future/current Qwen output.
    people = entities.get("people", []) if isinstance(entities, dict) else []
    if isinstance(people, list):
        for person in people:
            if isinstance(person, dict):
                add(person.get("name"), "entities.people")
            else:
                add(person, "entities.people")

    # 2. Strong person-name patterns.
    text = " ".join([headline, story, description, source_title])

    # Common editorial constructions:
    # "X and Y welcome..."
    # "X-Y starrer..."
    # "X directed by Y"
    # "directed by Y"
    # "actor X", "actress X", "director X"
    # "starring X"
    role_patterns = [
        r"\b(?:actor|actress|director|filmmaker|producer|singer|rapper|star|superstar)\s+"
        r"([A-Z][A-Za-z.'’-]+(?:\s+[A-Z][A-Za-z.'’-]+){1,3})",
        r"\bdirected by\s+([A-Z][A-Za-z.'’-]+(?:\s+[A-Z][A-Za-z.'’-]+){1,3})",
        r"\bstarring\s+([A-Z][A-Za-z.'’-]+(?:\s+[A-Z][A-Za-z.'’-]+){1,3})",
    ]

    for pattern in role_patterns:
        for match in re.finditer(pattern, text):
            add(match.group(1), "role pattern")

    # Explicit hyphenated cast pattern:
    # "Akshay Kumar-Saif Ali Khan starrer"
    for match in re.finditer(
        r"\b([A-Z][A-Za-z.'’-]+\s+[A-Z][A-Za-z.'’-]+)"
        r"\s*-\s*"
        r"([A-Z][A-Za-z.'’-]+\s+[A-Z][A-Za-z.'’-]+)\b",
        text,
    ):
        add(match.group(1), "hyphenated cast")
        add(match.group(2), "hyphenated cast")

    # "X and Y" where both sides look like two-part proper names.
    for match in re.finditer(
        r"\b([A-Z][A-Za-z.'’-]+\s+[A-Z][A-Za-z.'’-]+)"
        r"\s+and\s+"
        r"([A-Z][A-Za-z.'’-]+\s+[A-Z][A-Za-z.'’-]+)\b",
        text,
    ):
        add(match.group(1), "paired names")
        add(match.group(2), "paired names")

    # Social/Instagram attribution can expose a person.
    for match in re.finditer(
        r"@([a-zA-Z][a-zA-Z0-9_.]{2,})", description
    ):
        # Don't blindly turn handles into search candidates; only use
        # handles that map cleanly to a likely full name.
        handle = match.group(1).replace(".", " ").replace("_", " ")
        words = [w for w in handle.split() if w]
        if len(words) >= 2:
            add(" ".join(w.capitalize() for w in words), "social handle")

    # 3. Structured movie/show entities, if present.
    movies = entities.get("movies", []) if isinstance(entities, dict) else []
    if isinstance(movies, list):
        for movie in movies:
            if isinstance(movie, dict):
                add(movie.get("title"), "entities.movies")
            else:
                add(movie, "entities.movies")

    # 4. Conservative title extraction.
    # Search for quoted titles and common "starrer" construction.
    for match in re.finditer(r"[“\"]([^“\"']{2,80})[”\"]", text):
        value = match.group(1).strip()
        if 1 <= len(value.split()) <= 10:
            add(value, "quoted title")

    for match in re.finditer(
        r"\b(?:starrer|film|movie|series|show)\s+([A-Z][A-Za-z0-9:'’&.\-]+(?:\s+[A-Z][A-Za-z0-9:'’&.\-]+){0,6})",
        text,
        flags=re.IGNORECASE,
    ):
        add(match.group(1), "title construction")

    # 5. Emphasis words / headline fallback.
    for word in design.get("emphasis_words", []) or []:
        if isinstance(word, str) and len(word.split()) >= 2:
            add(word, "design emphasis")

    # Keep headline as the final broad fallback, never first.
    if not candidates and headline:
        add(headline, "headline fallback")

    log_candidates(candidates)
    return candidates

def html_value(ext, key):
    item = ext.get(key) or {}
    if isinstance(item, dict):
        return clean(item.get("value", ""))
    return clean(item)


def normalize_license(value):
    value = clean(value).lower()
    value = value.replace("creative commons attribution", "cc by")
    value = value.replace("creative commons attribution 4.0", "cc by 4.0")
    value = value.replace("creative commons attribution 3.0", "cc by 3.0")
    value = value.replace("creative commons attribution 2.0", "cc by 2.0")
    return value


def allowed_license(short_name, long_name, allowed):
    combined = f"{short_name} {long_name}".lower()
    if "noncommercial" in combined or "nc" in combined or "no derivatives" in combined or "nd" in combined:
        return False
    norm = normalize_license(short_name)
    return norm in allowed or any(a in norm for a in allowed)


def search_commons(query, allowed):
    """Search Wikimedia Commons and return candidates with diagnostics."""
    log(f'Searching Wikimedia Commons: "{query}"')
    log(f"Allowed licenses: {sorted(allowed)}", "DEBUG")

    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": 6,
        "gsrlimit": 10,
        "prop": "imageinfo|info",
        "iiprop": "url|mime|size|extmetadata",
    }

    try:
        response = requests.get(
            COMMONS_API,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        log(f"HTTP {response.status_code} for query={query!r}", "DEBUG")
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        log(f"Wikimedia request failed: {exc}", "ERROR")
        return []

    pages = list((payload.get("query", {}) or {}).get("pages", {}).values())
    log(f'Results returned: {len(pages)} for "{query}"')

    if not pages:
        log(f'NO RESULTS for "{query}"', "WARN")
        return []

    results = []

    for idx, page in enumerate(pages, 1):
        title = page.get("title", "")
        imageinfo = page.get("imageinfo", [{}])
        info = imageinfo[0] if imageinfo else {}
        ext = info.get("extmetadata", {}) or {}

        def meta(name):
            value = ext.get(name, {})
            return value.get("value", "") if isinstance(value, dict) else str(value)

        license_short = meta("LicenseShortName")
        usage_terms = meta("UsageTerms")
        license_url = meta("LicenseUrl")
        mime = info.get("mime", "")
        width = info.get("width")
        height = info.get("height")
        image_url = info.get("url", "")

        log(
            f'Candidate {idx}: title={title!r}, license={license_short!r}, '
            f'usage={usage_terms!r}, mime={mime!r}, size={width}x{height}',
            "DEBUG",
        )

        # Normalize license text because Commons may expose variations.
        license_text = " ".join(
            x for x in [license_short, usage_terms] if x
        ).strip()

        allowed_match = any(
            allowed_name.casefold() in license_text.casefold()
            for allowed_name in allowed
        )

        if not allowed_match:
            log(
                f'REJECT license: {title!r} -> '
                f'LicenseShortName={license_short!r}, UsageTerms={usage_terms!r}',
                "DEBUG",
            )
            continue

        if not image_url or not mime.startswith("image/"):
            log(f'REJECT non-image/missing URL: {title!r}', "DEBUG")
            continue

        results.append({
            "title": title,
            "pageid": page.get("pageid"),
            "url": image_url,
            "mime": mime,
            "width": width,
            "height": height,
            "license": license_short,
            "usage_terms": usage_terms,
            "license_url": license_url,
            "descriptionurl": info.get("descriptionurl", ""),
        })

    log(f'Accepted candidates after license/image filtering: {len(results)}')
    return results

def download_image(url, path):
    r = requests.get(url, timeout=45, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    content_type = r.headers.get("Content-Type", "")
    if not content_type.startswith("image/"):
        raise ValueError(f"Not an image response: {content_type}")
    path.write_bytes(r.content)


def process_slide(slide, config, force=False):
    """Person-first, cache-first Wikimedia image resolution."""
    images_cfg = config.get("images", {}) or {}
    cache_root = Path(images_cfg.get("cache_folder", "./images"))
    allowed = set(images_cfg.get("allowed_licenses", DEFAULT_ALLOWED))

    slide_obj = slide.get("slide", slide)
    number = slide_obj.get("number", "?")
    headline = slide_obj.get("headline", "")

    log(f"===== Slide {number} =====")
    log(f"Headline: {headline}")
    log(f"Cache root: {cache_root}")
    log(f"Force refresh: {force}")

    candidates = extract_candidates(slide)

    open_image = slide.setdefault("open_image", {})
    open_image.update({
        "provider": "Wikimedia Commons",
        "search_queries": candidates,
    })

    if not candidates:
        log("No search candidates extracted", "WARN")
        open_image.update({
            "status": "not_found",
            "fallback": "cinematic_background",
            "reason": "No person/movie search candidate could be extracted.",
        })
        return slide

    # Import existing helpers from the current module scope.
    for query in candidates:
        slug = slugify(query)
        cache_dir = cache_root / slug
        cache_dir.mkdir(parents=True, exist_ok=True)

        image_path = cache_dir / "image.jpg"
        metadata_path = cache_dir / "metadata.json"

        log(f'Candidate "{query}"')
        log(f"Cache directory: {cache_dir}", "DEBUG")

        if not force and image_path.exists():
            log(f"CACHE HIT: {image_path}")
            metadata = {}
            if metadata_path.exists():
                try:
                    metadata = json.loads(metadata_path.read_text())
                except Exception as exc:
                    log(f"Could not read cache metadata: {exc}", "WARN")

            open_image.update({
                "status": "cached",
                "provider": "Wikimedia Commons",
                "query": query,
                "local_file": str(image_path),
                "file_page": metadata.get("file_page", ""),
                "license": metadata.get("license", ""),
                "attribution": metadata.get("attribution", ""),
            })
            log(f"Using cached image for {query!r}")
            return slide

        if image_path.exists() and force:
            log(f"CACHE BYPASS (--force): {image_path}", "DEBUG")
        else:
            log("CACHE MISS", "DEBUG")

        results = search_commons(query, allowed)

        if not results:
            log(f'No usable Wikimedia candidates for "{query}"', "WARN")
            continue

        for result_idx, result in enumerate(results, 1):
            log(
                f'Trying accepted candidate {result_idx}/{len(results)}: '
                f'{result["title"]!r}'
            )

            try:
                downloaded = download_image(result["url"], image_path)
            except Exception as exc:
                log(
                    f'Download failed for {result["title"]!r}: {exc}',
                    "ERROR",
                )
                continue

            if not downloaded or not image_path.exists():
                log(
                    f'Download did not produce expected file: {image_path}',
                    "WARN",
                )
                continue

            file_title = result["title"].replace("File:", "", 1)
            file_page = (
                images_cfg.get("commons_file_page", "https://commons.wikimedia.org/wiki/")
                + quote(file_title.replace(" ", "_"))
            )

            metadata = {
                "query": query,
                "title": result["title"],
                "file_page": file_page,
                "source_url": result.get("descriptionurl", ""),
                "image_url": result["url"],
                "license": result.get("license", ""),
                "usage_terms": result.get("usage_terms", ""),
                "license_url": result.get("license_url", ""),
                "attribution": f'{file_title} — {result.get("license", "Wikimedia Commons")}',
            }

            metadata_path.write_text(
                json.dumps(metadata, indent=2, ensure_ascii=False)
            )

            open_image.update({
                "status": "downloaded",
                "provider": "Wikimedia Commons",
                "query": query,
                "local_file": str(image_path),
                "file_page": file_page,
                "license": result.get("license", ""),
                "attribution": metadata["attribution"],
            })

            log(f"SUCCESS: {image_path}")
            log(f'License: {result.get("license", "")}')
            log(f"Commons page: {file_page}")
            return slide

    log("All person-first candidates failed", "WARN")
    open_image.update({
        "status": "not_found",
        "provider": "Wikimedia Commons",
        "search_queries": candidates,
        "fallback": "cinematic_background",
        "reason": "No sufficiently relevant image with an allowed license was found.",
    })
    return slide

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="Enable detailed image-stage diagnostics")
    parser.add_argument("--category", required=True)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    global DEBUG
    DEBUG = True
    log("Debug logging: ON (default)")

    config = load_config(args.config)
    output_root = Path(config["output"]["folder"])
    qwen_dir = output_root / "qwen_input" / args.category
    image_dir = output_root / "open_images" / args.category
    if not qwen_dir.exists():
        raise FileNotFoundError(f"Category input folder not found: {qwen_dir}")

    configured = config.get("images", {}).get("allowed_licenses") or []
    allowed = {normalize_license(x) for x in configured} or DEFAULT_ALLOWED

    slides = sorted(qwen_dir.glob("slide_*.json"))
    print("==========================================")
    print(" Wikimedia Commons Image Fetcher")
    print("==========================================")
    print(f"Category      : {args.category}")
    print(f"Slides        : {len(slides)}")
    print(f"Output images : {image_dir}")
    print(f"Allowed       : {', '.join(sorted(allowed))}")

    credits = []
    found = 0
    for path in slides:
        try:
            ok = process_slide(path, image_dir, allowed, args.force)
            if ok:
                found += 1
            data = json.loads(path.read_text(encoding="utf-8"))
            img = data.get("open_image", {})
            if img.get("status") == "ok":
                credits.append(
                    f"Slide {data['slide'].get('number')}: {img.get('file_name')} | "
                    f"{img.get('attribution')} | {img.get('file_page_url')}"
                )
        except Exception as exc:
            print(f"    [ERROR] {path.name}: {exc}")
        time.sleep(0.2)

    image_dir.mkdir(parents=True, exist_ok=True)
    (image_dir / "image_credits.txt").write_text(
        "\n".join(credits) + ("\n" if credits else ""),
        encoding="utf-8",
    )
    print(f"\nCompleted: {found}/{len(slides)} slides have verified Commons images.")


if __name__ == "__main__":
    main()
