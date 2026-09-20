#!/usr/bin/env python3

"""
BollywoodKoko Image Resolver Client

IMPORTANT:
This module no longer fetches images from Wikipedia or Wikimedia Commons.

It calls the standalone local Image Resolver API:

    POST http://127.0.0.1:8010/image

The API owns:
    - Wikipedia / Wikimedia lookup
    - image download
    - caching
    - provider-specific logic

This module owns only:
    1. Extracting the correct image subjects from slide JSON.
    2. Calling the local API for those subjects.
    3. Writing the API result back into slide JSON.

Compatible with the existing pipeline entry point:

    process_slide(slide_path, image_dir, allowed, force=False)
"""

import argparse
import json
import os
import re
from pathlib import Path
from urllib.parse import quote

import requests


# ============================================================
# CONFIG
# ============================================================

IMAGE_API_URL = os.getenv(
    "IMAGE_RESOLVER_API",
    "http://127.0.0.1:8010",
).rstrip("/")

IMAGE_API_ENDPOINT = f"{IMAGE_API_URL}/image"

API_TIMEOUT = int(os.getenv("IMAGE_RESOLVER_TIMEOUT", "60"))

GENERIC_SUBJECTS = {
    "box office",
    "blockbuster",
    "hollywood",
    "bollywood",
    "movie business",
    "entertainment",
    "cinema",
    "film industry",
    "film",
    "movie",
    "actor",
    "actress",
    "celebrity",
    "star",
    "stars",
    "industry",
    "music",
    "song",
    "news",
    "latest news",
    "theatre",
    "theatres",
}

TYPE_ALIASES = {
    "actor": "person",
    "actress": "person",
    "celebrity": "person",
    "singer": "person",
    "director": "person",
    "producer": "person",
    "filmmaker": "person",
    "person": "person",
    "movie": "movie",
    "film": "movie",
    "series": "tv_show",
    "show": "tv_show",
    "tv show": "tv_show",
    "tv_show": "tv_show",
    "place": "place",
    "venue": "place",
    "organization": "organization",
    "company": "organization",
    "event": "event",
    "other": "other",
}


# ============================================================
# LOGGING / HELPERS
# ============================================================

def log(message, level="INFO"):
    print(f"[IMAGE][{level}] {message}")


def clean(value):
    if value is None:
        return ""

    value = str(value)
    value = re.sub(r"\s+", " ", value).strip()

    # Remove accidental surrounding quotes.
    if len(value) >= 2 and value[0] == value[-1]:
        if value[0] in {'"', "'"}:
            value = value[1:-1].strip()

    return value


def normalize_type(value):
    value = clean(value).casefold()

    if value in TYPE_ALIASES:
        return TYPE_ALIASES[value]

    return "other"


def normalize_subject(name, subject_type=None):
    name = clean(name)

    if not name:
        return None

    # Never send generic concepts to the image resolver.
    if name.casefold() in GENERIC_SUBJECTS:
        return None

    # Remove common accidental punctuation around names.
    name = name.strip(" \t\r\n,.;:|")

    if not name:
        return None

    return {
        "name": name,
        "type": normalize_type(subject_type or "other"),
    }


def dedupe_subjects(subjects):
    result = []
    seen = set()

    for subject in subjects:
        if not subject:
            continue

        key = (
            subject["name"].casefold(),
            subject["type"],
        )

        if key in seen:
            continue

        seen.add(key)
        result.append(subject)

    return result


# ============================================================
# IMAGE SUBJECT EXTRACTION
# ============================================================

def extract_candidates(data):
    """
    Extract image subjects ONLY from the structured Qwen
    image_subjects field.

    Preferred format:

        "image_subjects": [
            {"name": "Sayani Gupta", "type": "person"},
            {"name": "Haiwaan", "type": "movie"}
        ]

    Backward compatibility is retained for the old format:

        "image_subjects": [
            "Sayani Gupta",
            "Haiwaan"
        ]

    Deliberately do NOT scrape names from headline/story text.
    That was the source of previous bad candidates such as:

        "will release on"
        "with Rohit Shetty. Interestingly"
        "to seva initiatives..."
        "8th January 2027"

    Qwen is now responsible for selecting source-supported visual
    subjects; this module only normalizes them.
    """

    slide = data.get("slide", data)

    raw = slide.get("image_subjects")

    if raw is None:
        raw = data.get("image_subjects")

    if not raw:
        log(
            "image_subjects missing; no image candidate will be inferred "
            "from headline/story text.",
            "WARN",
        )
        return []

    if not isinstance(raw, list):
        raw = [raw]

    subjects = []

    for item in raw:
        # New structured format.
        if isinstance(item, dict):
            name = (
                item.get("name")
                or item.get("entity")
                or item.get("title")
                or item.get("subject")
            )

            subject_type = (
                item.get("type")
                or item.get("entity_type")
                or item.get("category")
                or "other"
            )
            print(f"name : {name} / {subject_type}")
            subject = normalize_subject(name, subject_type)

            if subject:
                subjects.append(subject)

            continue

        # Old string format.
        if isinstance(item, str):
            subject = normalize_subject(item, "other")

            if subject:
                subjects.append(subject)

    subjects = dedupe_subjects(subjects)[:4]

    log(
        "Resolved image subjects: "
        + (
            ", ".join(
                f'{x["name"]} [{x["type"]}]'
                for x in subjects
            )
            if subjects
            else "NONE"
        )
    )

    return subjects


# ============================================================
# LOCAL IMAGE API
# ============================================================

def call_image_api(subject):
    """
    Call the standalone Image Resolver API.

    No Wikipedia/Wikimedia HTTP request is made here.
    """

    payload = {
        "name": subject["name"],
        "type": subject["type"],
    }

    log(
        f'Calling Image Resolver API: '
        f'"{subject["name"]}" [{subject["type"]}]'
    )
    log(
        f"POST {IMAGE_API_ENDPOINT}",
        "DEBUG",
    )

    try:
        response = requests.post(
            IMAGE_API_ENDPOINT,
            json=payload,
            timeout=API_TIMEOUT,
        )

        log(
            f"API HTTP status: {response.status_code}",
            "DEBUG",
        )

        response.raise_for_status()

        result = response.json()

        if not isinstance(result, dict):
            raise ValueError("Image API returned non-object JSON")

        return result

    except requests.exceptions.ConnectionError as exc:
        log(
            f"Image Resolver API is not reachable: {exc}",
            "ERROR",
        )
        return {
            "status": "api_error",
            "error": (
                f"Image Resolver API unavailable at "
                f"{IMAGE_API_ENDPOINT}"
            ),
        }

    except requests.exceptions.Timeout:
        log(
            f"Image Resolver API timeout after {API_TIMEOUT}s",
            "ERROR",
        )
        return {
            "status": "api_error",
            "error": "Image Resolver API request timed out",
        }

    except Exception as exc:
        log(
            f"Image Resolver API failed: {exc}",
            "ERROR",
        )
        return {
            "status": "api_error",
            "error": str(exc),
        }


# ============================================================
# SLIDE RESULT
# ============================================================

def apply_success(data, subject, result):
    """
    Convert the reusable Image Resolver response into the
    open_image structure expected by the existing painter.
    """

    local_file = result.get("local_file")
    image_url = result.get("image_url")

    license_info = result.get("license") or {}

    data["slide"]["image_url"] = ""

    data["open_image"] = {
        "provider": result.get("provider", "Image Resolver API"),
        "status": "ok",
        "entity": result.get("name", subject["name"]),
        "entity_type": result.get("type", subject["type"]),
        "retrieval_method": result.get("retrieval_method"),
        "cached": result.get("cached", False),
        "image": {
            "source_url": image_url or "",
            "local_file": local_file or "",
        },
        "license_verification": {
            "status": license_info.get(
                "status",
                "NOT_VERIFIED",
            ),
            "license": license_info.get("name"),
            "source_url": license_info.get("source_url"),
        },
        "image_api": {
            "endpoint": IMAGE_API_ENDPOINT,
            "requested_name": subject["name"],
            "requested_type": subject["type"],
        },
    }

    return data


def apply_not_found(data, subjects, errors):
    data["slide"]["image_url"] = ""

    data["open_image"] = {
        "provider": "Image Resolver API",
        "status": "not_found",
        "search_subjects": subjects,
        "license_verification": {
            "status": "NOT_VERIFIED",
        },
        "fallback": "cinematic_background",
        "errors": errors,
    }

    return data


# ============================================================
# PIPELINE ENTRY POINT
# ============================================================

def process_slide(slide_path, image_dir=None, allowed=None, force=False):
    """
    Compatibility-preserving entry point used by run.sh.

    Parameters retained so existing run.sh does not need to change:

        process_slide(path, image_dir, allowed, force)

    image_dir and allowed are intentionally unused because image
    storage and provider logic now belong to the Image Resolver API.
    """

    slide_path = Path(slide_path)

    data = json.loads(
        slide_path.read_text(encoding="utf-8")
    )

    slide = data.setdefault("slide", {})

    number = slide.get("number", "?")
    headline = slide.get("headline", "")

    log("=" * 70)
    log(f"Slide {number}")
    log(f"File: {slide_path}")
    log(f"Headline: {headline}")
    log(f"Image API: {IMAGE_API_ENDPOINT}")
    log("Provider-specific image fetching: DISABLED")
    log("Image fetching is delegated to Image Resolver API.")

    subjects = extract_candidates(data)

    if not subjects:
        log(
            "No valid image_subjects found.",
            "WARN",
        )

        apply_not_found(
            data,
            [],
            [
                "No valid image_subjects were supplied by Qwen."
            ],
        )

        slide_path.write_text(
            json.dumps(
                data,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        return False

    # Record exactly what was requested.
    data["open_image"] = {
        "provider": "Image Resolver API",
        "status": "searching",
        "search_subjects": subjects,
        "image_api": {
            "endpoint": IMAGE_API_ENDPOINT,
        },
    }

    errors = []

    # Important:
    # Preserve Qwen ordering. The first successful subject wins.
    # This means the most visually relevant subject selected by Qwen
    # remains the hero image.
    for subject in subjects:

        result = call_image_api(subject)

        if result.get("status") != "ok":
            error = result.get(
                "error",
                f'No image for "{subject["name"]}"',
            )

            errors.append(
                f'{subject["name"]} [{subject["type"]}]: {error}'
            )

            log(
                f'No usable image for "{subject["name"]}"',
                "WARN",
            )

            continue

        local_file = result.get("local_file")

        if not local_file:
            errors.append(
                f'{subject["name"]}: API returned success '
                f'without local_file'
            )

            log(
                f'API returned no local_file for "{subject["name"]}"',
                "WARN",
            )

            continue

        local_path = Path(local_file)

        if not local_path.exists():
            errors.append(
                f'{subject["name"]}: API returned non-existent '
                f'local file: {local_file}'
            )

            log(
                f"API returned missing local file: {local_file}",
                "WARN",
            )

            continue

        data = apply_success(
            data,
            subject,
            result,
        )

        log(
            f'SUCCESS: {subject["name"]} -> {local_file}'
        )

        if result.get("cached"):
            log(
                "Image was served from Image Resolver cache.",
                "DEBUG",
            )

        slide_path.write_text(
            json.dumps(
                data,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        return True

    # No subject produced an image.
    log(
        "No image could be resolved for any Qwen image_subject.",
        "WARN",
    )

    apply_not_found(
        data,
        subjects,
        errors,
    )

    slide_path.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return False


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Resolve slide image_subjects through the local "
            "Image Resolver API."
        )
    )

    parser.add_argument(
        "slide",
        help="Slide JSON file.",
    )

    parser.add_argument(
        "--api",
        default=None,
        help="Image Resolver API base URL.",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Compatibility option. The local API owns cache policy; "
            "this flag is currently not required."
        ),
    )

    args = parser.parse_args()

    global IMAGE_API_URL, IMAGE_API_ENDPOINT

    if args.api:
        IMAGE_API_URL = args.api.rstrip("/")
        IMAGE_API_ENDPOINT = f"{IMAGE_API_URL}/image"

    ok = process_slide(
        args.slide,
        None,
        None,
        args.force,
    )

    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
