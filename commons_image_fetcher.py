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

DEFAULT_CONFIG = "config.yaml"
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
    Build image-search candidates with this priority:
      1. People explicitly identified in slide/source data.
      2. Person names extracted from the article description.
      3. Movie/show names.
      4. Headline as a last resort.

    Person-first searching is important because Wikimedia Commons commonly
    indexes people better than entertainment-news headlines.
    """
    design = slide.get("design", {}) or {}
    source = slide.get("source", {}) or {}

    people = []
    movies = []

    # Prefer structured entities if qwen_converter/qwen_slide_designer has
    # already supplied them.
    entities = slide.get("entities", {}) or {}
    for value in entities.get("people", []) or []:
        value = clean(value)
        if value:
            people.append(value)

    for value in entities.get("movies", []) or []:
        value = clean(value)
        if value:
            movies.append(value)

    description = clean(source.get("description", ""))
    headline = clean(slide.get("headline", ""))
    source_title = clean(source.get("title", ""))

    # Common Indian entertainment-news construction:
    # "The Akshay Kumar-Saif Ali Khan starrer Haiwaan..."
    # Extract capitalized multi-word names and names connected by hyphens.
    name_patterns = [
        r"\b[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3}\b",
    ]

    text_to_scan = f"{description} {headline} {source_title}"
    for pattern in name_patterns:
        for match in re.findall(pattern, text_to_scan):
            candidate = clean(match)

            # Remove obvious non-person phrases.
            blocked = {
                "The Film", "The Movie", "The Weekend", "Day One",
                "Day Two", "Day Three", "Day Four", "Day Five",
                "Day Six", "Day Seven", "Day Eight", "Day Nine",
                "Day Ten", "Second Friday", "First Week",
                "Box Office", "Bollywood Hungama", "Special Analysis",
                "Wikimedia Commons",
            }

            if candidate in blocked:
                continue

            # Don't treat obvious numeric/statistical phrases as names.
            if re.search(r"\b(?:Day|Rs|Crore|Lakhs|Friday|Saturday|Sunday|Monday|Tuesday|Wednesday|Thursday)\b", candidate, re.I):
                continue

            if candidate not in people and len(candidate.split()) >= 2:
                people.append(candidate)

    # Handle hyphenated person names such as "Akshay Kumar-Saif Ali Khan".
    for match in re.findall(
        r"\b([A-Z][A-Za-z.'-]+\s+[A-Z][A-Za-z.'-]+)-([A-Z][A-Za-z.'-]+\s+[A-Z][A-Za-z.'-]+)\b",
        description,
    ):
        for candidate in match:
            candidate = clean(candidate)
            if candidate and candidate not in people:
                people.insert(0, candidate)

    # Keep the strongest likely person candidates first.
    people = list(dict.fromkeys(people))

    # Try explicit/obvious movie title from the source title/headline.
    for text_value in (source_title, headline):
        match = re.search(
            r"\b(?:starrer|film|movie|film's)\s+([A-Z][A-Za-z0-9'*-]+(?:\s+[A-Z][A-Za-z0-9'*-]+){0,3})",
            text_value,
            re.I,
        )
        if match:
            movies.append(clean(match.group(1)))

    movies = list(dict.fromkeys(movies))

    # Last-resort queries from emphasis words / headline.
    fallback = []
    emphasis = design.get("emphasis_words") or []
    if isinstance(emphasis, list):
        q = " ".join(clean(x) for x in emphasis if clean(x))
        if q:
            fallback.append(q)

    if headline:
        fallback.append(headline)

    # Person-first, then movie/show, then generic fallback.
    result = []
    for q in people + movies + fallback:
        q = clean(q)
        if q and q.lower() not in {x.lower() for x in result}:
            result.append(q)

    return result[:8]

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


def search_commons(query, allowed, commons_api):
    params = {
        "action": "query",
        "generator": "search",
        "gsrnamespace": 6,
        "gsrsearch": query,
        "gsrlimit": 12,
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": 1800,
        "format": "json",
        "formatversion": 2,
    }
    r = requests.get(
        commons_api,
        params=params,
        timeout=30,
        headers={"User-Agent": USER_AGENT},
    )
    r.raise_for_status()
    pages = (r.json().get("query") or {}).get("pages") or []

    candidates = []
    qtokens = {x.lower() for x in re.findall(r"[A-Za-z0-9]+", query) if len(x) > 2}
    for page in pages:
        info = (page.get("imageinfo") or [{}])[0]
        ext = info.get("extmetadata") or {}
        short = html_value(ext, "LicenseShortName")
        long_name = html_value(ext, "License")
        if not allowed_license(short, long_name, allowed):
            continue
        mime = info.get("mime", "")
        if not mime.startswith("image/") or mime == "image/svg+xml":
            continue
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        if width < 500 or height < 500:
            continue

        title = clean(page.get("title", ""))
        description = " ".join([
            html_value(ext, "ImageDescription"),
            html_value(ext, "ObjectName"),
            title,
        ])
        dtokens = {x.lower() for x in re.findall(r"[A-Za-z0-9]+", description) if len(x) > 2}
        overlap = len(qtokens & dtokens)
        # Search already ranks results, but require at least one meaningful token
        # overlap unless the query is a single strong proper-name token.
        if qtokens and overlap == 0 and len(qtokens) > 1:
            continue

        thumb = info.get("thumburl") or info.get("url")
        author = html_value(ext, "Artist") or html_value(ext, "Credit") or "Unknown author"
        license_url = html_value(ext, "LicenseUrl")
        file_page = commons_file_page.rstrip("/") + "/" + quote(
            title.replace(" ", "_"),
            safe="/:()_-",
        )

        candidates.append({
            "pageid": page.get("pageid"),
            "title": title,
            "image_url": thumb,
            "original_url": info.get("url", ""),
            "width": width,
            "height": height,
            "author": strip_wiki_markup(author),
            "license": short,
            "license_url": license_url,
            "file_page_url": file_page,
            "score": overlap * 10 + min(width * height / 1_000_000, 10),
        })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


def download_image(url, path):
    r = requests.get(url, timeout=45, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    content_type = r.headers.get("Content-Type", "")
    if not content_type.startswith("image/"):
        raise ValueError(f"Not an image response: {content_type}")
    path.write_bytes(r.content)


def slugify(value):
    value = clean(value).lower()
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    return value[:80] or "unknown"


def load_cache(index_path):
    if not index_path.exists():
        return {}
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(index_path, cache):
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def cached_entry(cache, query, cache_root):
    key = clean(query).lower()
    entry = cache.get(key)
    if not entry:
        return None
    if entry.get("status") == "ok":
        local = Path(entry.get("local_file", ""))
        if local.exists():
            return entry
        # Stale cache entry: allow a fresh lookup.
        return None
    if entry.get("status") == "not_found":
        return entry
    return None


def download_and_cache(selected, query, cache_root, cache):
    entity_dir = cache_root / slugify(query)
    entity_dir.mkdir(parents=True, exist_ok=True)

    ext = ".jpg" if "jpeg" in selected["image_url"].lower() or "jpg" in selected["image_url"].lower() else ".png"
    local_path = entity_dir / f"image{ext}"
    download_image(selected["image_url"], local_path)

    attribution = f"{selected['author']} / {selected['license']}"
    entry = {
        "status": "ok",
        "provider": "Wikimedia Commons",
        "entity": query,
        "file_name": selected["title"].removeprefix("File:"),
        "file_page_url": selected["file_page_url"],
        "image_url": selected["original_url"],
        "local_file": str(local_path),
        "author": selected["author"],
        "license": selected["license"],
        "license_url": selected["license_url"],
        "attribution": attribution,
        "search_query": query,
    }
    cache[clean(query).lower()] = entry
    return entry


def process_slide(path, cache_root, cache, index_path, allowed, commons_api, force=False):
    data = json.loads(path.read_text(encoding="utf-8"))
    slide = data.get("slide", {})
    queries = extract_candidates(slide)

    # Normal runs always prefer the persistent local cache. --force explicitly
    # refreshes the Wikimedia lookup; without --force there is no second request.
    if not force:
        for query in queries:
            entry = cached_entry(cache, query, cache_root)
            if entry and entry.get("status") == "ok":
                data["open_image"] = entry.copy()
                data["open_image"]["status"] = "cached"
                data["slide"]["image_url"] = ""
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"    [CACHE] {path.name}: {query} -> {entry['local_file']}")
                return True
            if entry and entry.get("status") == "not_found":
                # Keep looking in case another query for this slide has an image.
                continue

    selected = None
    selected_query = None
    search_queries = queries

    if not force:
        uncached_queries = []
        for query in queries:
            entry = cache.get(clean(query).lower())
            if entry and entry.get("status") in {"ok", "not_found"}:
                continue
            uncached_queries.append(query)
        # If every query was previously checked and no image was found, do not
        # hit Wikimedia again. The negative cache is intentional.
        search_queries = uncached_queries

    for query in search_queries:
        try:
            results = search_commons(query, allowed, commons_api)
        except Exception as exc:
            print(f"    [WARN] Commons search failed for '{query}': {exc}")
            continue
        if results:
            selected = results[0]
            selected_query = query
            break

    if not selected:
        for query in queries:
            cache[clean(query).lower()] = {
                "status": "not_found",
                "provider": "Wikimedia Commons",
                "entity": query,
                "search_query": query,
                "reason": "No sufficiently relevant image with an allowed license was found.",
            }
        data["open_image"] = {
            "status": "not_found",
            "provider": "Wikimedia Commons",
            "search_queries": queries,
            "fallback": "cinematic_background",
            "reason": "No sufficiently relevant image with an allowed license was found.",
        }
        data["slide"]["image_url"] = ""
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        save_cache(index_path, cache)
        print(f"    [NONE] {path.name}: no verified reusable image; painter will use cinematic background")
        return False

    entry = download_and_cache(selected, selected_query, cache_root, cache)
    data["open_image"] = entry.copy()
    data["slide"]["image_url"] = ""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    save_cache(index_path, cache)
    print(f"    [OK] {path.name}: downloaded {selected['title']} | {selected['license']}")
    return True

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", required=True)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)

    images_config = config.get("images", {}) or {}
    commons_api = images_config.get("commons_api")
    commons_file_page = images_config.get("commons_file_page")

    if not commons_api:
        raise ValueError("Missing images.commons_api in config.yaml")
    if not commons_file_page:
        raise ValueError("Missing images.commons_file_page in config.yaml")
    output_root = Path(config["output"]["folder"])
    qwen_dir = output_root / "qwen_input" / args.category
    image_root = Path(config.get("images", {}).get("cache_folder") or (output_root / "open_images"))
    index_path = image_root / "index.json"
    cache = load_cache(index_path)
    if not qwen_dir.exists():
        raise FileNotFoundError(f"Category input folder not found: {qwen_dir}")

    images_config = config.get("images", {}) or {}
    commons_api = images_config.get("commons_api")
    if not commons_api:
        raise ValueError("Missing images.commons_api in config.yaml")

    configured = images_config.get("allowed_licenses") or []
    allowed = {normalize_license(x) for x in configured} or DEFAULT_ALLOWED

    slides = sorted(qwen_dir.glob("slide_*.json"))
    print("==========================================")
    print(" Wikimedia Commons Image Fetcher")
    print("==========================================")
    print(f"Category      : {args.category}")
    print(f"Slides        : {len(slides)}")
    print(f"Image cache   : {image_root}")
    print(f"Allowed       : {', '.join(sorted(allowed))}")

    credits = []
    found = 0
    for path in slides:
        try:
            ok = process_slide(path, image_root, cache, index_path, allowed, commons_api, args.force)
            if ok:
                found += 1
            data = json.loads(path.read_text(encoding="utf-8"))
            img = data.get("open_image", {})
            if img.get("status") in {"ok", "cached"}:
                credits.append(
                    f"Slide {data['slide'].get('number')}: {img.get('file_name')} | "
                    f"{img.get('attribution')} | {img.get('file_page_url')}"
                )
        except Exception as exc:
            print(f"    [ERROR] {path.name}: {exc}")
        time.sleep(0.2)

    category_credit_dir = image_root / "_credits" / args.category
    category_credit_dir.mkdir(parents=True, exist_ok=True)
    (category_credit_dir / "image_credits.txt").write_text(
        "\n".join(credits) + ("\n" if credits else ""),
        encoding="utf-8",
    )
    print(f"\nCompleted: {found}/{len(slides)} slides have verified Commons images.")


if __name__ == "__main__":
    main()
