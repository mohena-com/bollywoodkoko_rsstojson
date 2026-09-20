#!/usr/bin/env python3

"""
BollywoodKoko - Image Resolver API client

This module contains NO Wikipedia/Wikimedia image-fetching logic.

It:
    1. Reads Qwen-generated slide JSON files for a category.
    2. Gets image_subjects from each slide.
    3. Normalizes/validates the subjects.
    4. Calls the standalone local Image Resolver API.
    5. Writes the successful API result into slide.open_image.

Expected API:
    POST http://127.0.0.1:8010/image
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests


# ============================================================
# CONFIGURATION
# ============================================================

DEFAULT_API_URL = "http://127.0.0.1:8010"

IMAGE_RESOLVER_API = os.getenv(
    "IMAGE_RESOLVER_API",
    DEFAULT_API_URL,
).rstrip("/")

API_ENDPOINT = f"{IMAGE_RESOLVER_API}/image"

API_TIMEOUT = int(
    os.getenv("IMAGE_RESOLVER_TIMEOUT", "60")
)

DEFAULT_OP_JSON = Path(
    "/Volumes/Extreme SSD/"
    "webmaster-ai/POJO_PROJECT/bollywood/OP_JSON"
)

OP_JSON = Path(
    os.getenv(
        "BOLLYWOODKOKO_OP_JSON",
        str(DEFAULT_OP_JSON),
    )
)


# ============================================================
# ENTITY TYPES
# ============================================================

TYPE_ALIASES = {
    "person": "person",
    "actor": "person",
    "actress": "person",
    "celebrity": "person",
    "singer": "person",
    "director": "person",
    "producer": "person",
    "filmmaker": "person",

    "movie": "movie",
    "film": "movie",

    "tv": "tv_show",
    "tv_show": "tv_show",
    "tv show": "tv_show",
    "series": "tv_show",
    "show": "tv_show",

    "place": "place",
    "venue": "place",
    "location": "place",

    "organization": "organization",
    "organisation": "organization",
    "company": "organization",

    "event": "event",

    "other": "other",
}


# These are concepts, not image entities.
GENERIC_SUBJECTS = {
    "actor",
    "actors",
    "actress",
    "actresses",
    "celebrity",
    "celebrities",
    "star",
    "stars",
    "bollywood",
    "hollywood",
    "film",
    "films",
    "movie",
    "movies",
    "cinema",
    "entertainment",
    "box office",
    "blockbuster",
    "film industry",
    "movie industry",
    "music",
    "song",
    "songs",
    "theatre",
    "theatres",
    "news",
    "latest news",
    "industry",
}


# ============================================================
# HELPERS
# ============================================================

def log(message, level="INFO"):
    print(f"[IMAGE][{level}] {message}")


def normalize_text(value):
    if value is None:
        return ""

    value = str(value)
    value = re.sub(r"\s+", " ", value).strip()

    if len(value) >= 2 and value[0] == value[-1]:
        if value[0] in ('"', "'"):
            value = value[1:-1].strip()

    return value


def normalize_type(value):
    value = normalize_text(value).casefold()
    return TYPE_ALIASES.get(value, "other")


def normalize_subject(name, subject_type=None):
    name = normalize_text(name)

    if not name:
        return None

    name = name.strip(" \t\r\n,.;:|")

    if not name:
        return None

    if name.casefold() in GENERIC_SUBJECTS:
        return None

    return {
        "name": name,
        "type": normalize_type(subject_type),
    }


def deduplicate_subjects(subjects):
    result = []
    seen = set()

    for subject in subjects:
        key = (
            subject["name"].casefold(),
            subject["type"],
        )

        if key in seen:
            continue

        seen.add(key)
        result.append(subject)

    # Preserve Qwen's priority order.
    return result[:4]


# ============================================================
# IMAGE SUBJECT EXTRACTION
# ============================================================

def extract_image_subjects(data):
    """
    Preferred Qwen format:

        "image_subjects": [
            {
                "name": "Sayani Gupta",
                "type": "person"
            },
            {
                "name": "Haiwaan",
                "type": "movie"
            }
        ]

    Backward-compatible format:

        "image_subjects": [
            "Sayani Gupta",
            "Haiwaan"
        ]

    IMPORTANT:
    Do NOT extract names from headline/summary/article text.
    Qwen image_subjects is the source of truth.
    """

    slide = data.get("slide", {})

    raw = slide.get("image_subjects")

    if raw is None:
        raw = data.get("image_subjects")

    if raw is None:
        log(
            "image_subjects is missing from slide JSON.",
            "WARN",
        )
        return []

    if not isinstance(raw, list):
        raw = [raw]

    subjects = []

    for item in raw:

        # Preferred structured format.
        if isinstance(item, dict):

            name = (
                item.get("name")
                or item.get("entity")
                or item.get("subject")
                or item.get("title")
            )

            subject_type = (
                item.get("type")
                or item.get("entity_type")
                or item.get("category")
                or "other"
            )

            subject = normalize_subject(
                name,
                subject_type,
            )

            if subject:
                subjects.append(subject)

            continue

        # Backward compatibility.
        if isinstance(item, str):

            subject = normalize_subject(
                item,
                "other",
            )

            if subject:
                subjects.append(subject)

    subjects = deduplicate_subjects(subjects)

    if subjects:
        log(
            "Image subjects: "
            + ", ".join(
                f'{item["name"]} [{item["type"]}]'
                for item in subjects
            )
        )
    else:
        log(
            "No valid image subjects found.",
            "WARN",
        )

    return subjects


# ============================================================
# IMAGE RESOLVER API
# ============================================================

def call_image_api(subject):

    payload = {
        "name": subject["name"],
        "type": subject["type"],
    }

    log(
        f'Resolving "{subject["name"]}" '
        f'[{subject["type"]}]'
    )

    try:

        response = requests.post(
            API_ENDPOINT,
            json=payload,
            timeout=API_TIMEOUT,
        )

        log(
            f"API response: HTTP {response.status_code}",
            "DEBUG",
        )

        response.raise_for_status()

        result = response.json()

        if not isinstance(result, dict):
            raise ValueError(
                "Image Resolver API returned invalid JSON"
            )

        return result

    except requests.exceptions.ConnectionError as exc:

        return {
            "status": "api_error",
            "error": (
                f"Cannot connect to Image Resolver API "
                f"at {API_ENDPOINT}: {exc}"
            ),
        }

    except requests.exceptions.Timeout:

        return {
            "status": "api_error",
            "error": (
                f"Image Resolver API timed out "
                f"after {API_TIMEOUT} seconds"
            ),
        }

    except requests.exceptions.HTTPError as exc:

        return {
            "status": "api_error",
            "error": str(exc),
        }

    except Exception as exc:

        return {
            "status": "api_error",
            "error": str(exc),
        }


# ============================================================
# SLIDE UPDATE
# ============================================================

def write_slide(data, slide_path):

    slide_path.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def set_image_result(data, subject, result):

    slide = data.setdefault(
        "slide",
        {}
    )

    # Paint_slides.py uses local_file.
    slide["image_url"] = ""

    license_info = (
        result.get("license") or {}
    )

    data["open_image"] = {

        "provider": result.get(
            "provider",
            "Image Resolver API",
        ),

        "status": "ok",

        "entity": result.get(
            "name",
            subject["name"],
        ),

        "entity_type": result.get(
            "type",
            subject["type"],
        ),

        "retrieval_method": result.get(
            "retrieval_method"
        ),

        "cached": bool(
            result.get("cached", False)
        ),

        "image": {

            "source_url": result.get(
                "image_url",
                "",
            ),

            "local_file": result.get(
                "local_file",
                "",
            ),
        },

        "license_verification": {

            "status": license_info.get(
                "status",
                "NOT_VERIFIED",
            ),

            "license": license_info.get(
                "name"
            ),

            "source_url": license_info.get(
                "source_url"
            ),
        },

        "image_api": {

            "endpoint": API_ENDPOINT,

            "requested_name": subject["name"],

            "requested_type": subject["type"],
        },
    }


def set_image_failure(
    data,
    subjects,
    errors,
):

    slide = data.setdefault(
        "slide",
        {}
    )

    slide["image_url"] = ""

    data["open_image"] = {

        "provider": "Image Resolver API",

        "status": "not_found",

        "search_subjects": subjects,

        "license_verification": {
            "status": "NOT_VERIFIED",
        },

        "fallback": "cinematic_background",

        "errors": errors,

        "image_api": {
            "endpoint": API_ENDPOINT,
        },
    }


# ============================================================
# PROCESS ONE SLIDE
# ============================================================

def process_slide(
    slide_path,
    force=False,
):

    slide_path = Path(slide_path)

    try:

        data = json.loads(
            slide_path.read_text(
                encoding="utf-8"
            )
        )

    except Exception as exc:

        log(
            f"Cannot read {slide_path}: {exc}",
            "ERROR",
        )

        return False

    slide = data.get(
        "slide",
        {}
    )

    slide_number = slide.get(
        "number",
        slide_path.stem,
    )

    log("-" * 70)
    log(f"Slide: {slide_number}")
    log(f"File : {slide_path}")

    subjects = extract_image_subjects(
        data
    )

    if not subjects:

        set_image_failure(
            data,
            [],
            [
                "No valid image_subjects "
                "were supplied by Qwen."
            ],
        )

        write_slide(
            data,
            slide_path,
        )

        return False

    errors = []

    # Qwen ordering determines priority.
    # First successful subject becomes hero image.
    for subject in subjects:

        result = call_image_api(
            subject
        )

        if result.get("status") != "ok":

            error = result.get(
                "error",
                "Image not found",
            )

            errors.append(
                f'{subject["name"]} '
                f'[{subject["type"]}]: '
                f'{error}'
            )

            log(
                f'Failed: "{subject["name"]}"',
                "WARN",
            )

            continue

        local_file = result.get(
            "local_file"
        )

        if not local_file:

            errors.append(
                f'{subject["name"]}: '
                "API returned success without "
                "local_file"
            )

            continue

        local_path = Path(
            local_file
        )

        if not local_path.exists():

            errors.append(
                f'{subject["name"]}: '
                f'local file does not exist: '
                f'{local_file}'
            )

            log(
                f"Returned file does not exist: "
                f"{local_file}",
                "WARN",
            )

            continue

        set_image_result(
            data,
            subject,
            result,
        )

        write_slide(
            data,
            slide_path,
        )

        if result.get("cached"):

            cache_status = "CACHE HIT"

        else:

            cache_status = "DOWNLOADED/CACHED"

        log(
            f'SUCCESS: "{subject["name"]}" '
            f'[{subject["type"]}] '
            f'-> {local_file} '
            f'({cache_status})'
        )

        return True

    set_image_failure(
        data,
        subjects,
        errors,
    )

    write_slide(
        data,
        slide_path,
    )

    log(
        "No image resolved for this slide.",
        "WARN",
    )

    return False


# ============================================================
# CATEGORY
# ============================================================

def get_category_dir(category):

    return (
        OP_JSON
        / "qwen_input"
        / category
    )


def find_slides(category):

    category_dir = get_category_dir(
        category
    )

    if not category_dir.exists():

        log(
            f"Category directory not found: "
            f"{category_dir}",
            "ERROR",
        )

        return []

    return sorted(
        category_dir.glob(
            "slide_*.json"
        )
    )


def process_category(
    category,
    force=False,
):

    category_dir = get_category_dir(
        category
    )

    log("=" * 70)
    log(
        "Image Resolver API"
    )
    log(
        f"Category : {category}"
    )
    log(
        f"Input    : {category_dir}"
    )
    log(
        f"API      : {API_ENDPOINT}"
    )
    log(
        "Provider : delegated to local Image Resolver API"
    )
    log("=" * 70)

    slides = find_slides(
        category
    )

    if not slides:

        log(
            "No slide JSON files found.",
            "WARN",
        )

        return 0, 0

    log(
        f"Found {len(slides)} slide(s)."
    )

    success_count = 0
    failure_count = 0

    for slide_path in slides:

        if process_slide(
            slide_path,
            force=force,
        ):

            success_count += 1

        else:

            failure_count += 1

    print()

    log("=" * 70)

    log(
        f"Image resolution completed: "
        f"{success_count} success, "
        f"{failure_count} fallback/failure"
    )

    log("=" * 70)

    return (
        success_count,
        failure_count,
    )


# ============================================================
# CLI
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Resolve BollywoodKoko slide images "
            "through the local Image Resolver API."
        )
    )

    parser.add_argument(
        "--category",
        "-category",
        required=True,
        help=(
            "Category under "
            "OP_JSON/qwen_input/"
        ),
    )

    parser.add_argument(
        "--api",
        default=None,
        help=(
            "Image Resolver API base URL. "
            "Default: http://127.0.0.1:8010"
        ),
    )

    parser.add_argument(
        "--op-json",
        default=None,
        help=(
            "Override OP_JSON root directory."
        ),
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Compatibility option. "
            "Cache policy is controlled by "
            "the Image Resolver API."
        ),
    )

    args = parser.parse_args()

    global IMAGE_RESOLVER_API
    global API_ENDPOINT
    global OP_JSON

    if args.api:

        IMAGE_RESOLVER_API = (
            args.api.rstrip("/")
        )

        API_ENDPOINT = (
            f"{IMAGE_RESOLVER_API}/image"
        )

    if args.op_json:

        OP_JSON = Path(
            args.op_json
        ).expanduser()

    success_count, failure_count = (
        process_category(
            args.category,
            force=args.force,
        )
    )

    # Do not fail the whole pipeline just because
    # one slide has no image. paint_slides.py has
    # a cinematic fallback.
    if success_count == 0 and failure_count > 0:

        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()