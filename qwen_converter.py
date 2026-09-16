#!/usr/bin/env python3

import json
import re
import argparse
from pathlib import Path
from html import unescape

import yaml
import requests


# ============================================================
# Configuration
# ============================================================

DEFAULT_CONFIG = "config.yaml"

QWEN_SUBFOLDER = "qwen_input"

HEADLINE_MAX_CHARS = 110
STORY_MAX_CHARS = 450


# ============================================================
# Text utilities
# ============================================================

def clean_text(text):
    """Remove HTML and normalize whitespace."""

    if not text:
        return ""

    text = str(text)

    # Decode HTML entities
    text = unescape(text)

    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", text)

    # Remove common RSS noise
    text = re.sub(
        r"Also Read\s*:.*$",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL
    )

    # Remove Instagram embed noise
    text = re.sub(
        r"Instagram.*?$",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL
    )

    # Normalize whitespace
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def shorten_text(text, max_chars):
    """Shorten text without cutting a word."""

    text = clean_text(text)

    if len(text) <= max_chars:
        return text

    shortened = text[:max_chars]

    if " " in shortened:
        shortened = shortened.rsplit(" ", 1)[0]

    return shortened.rstrip(".,;:-") + "..."


def clean_headline(title):
    """Create a shorter Instagram-friendly headline."""

    title = clean_text(title)

    if len(title) <= HEADLINE_MAX_CHARS:
        return title

    separators = [
        " | ",
        " — ",
        " – ",
        " - ",
        "; "
    ]

    for separator in separators:

        parts = title.split(separator)

        if len(parts) > 1:

            candidate = parts[0].strip()

            if 35 <= len(candidate) <= HEADLINE_MAX_CHARS:
                return candidate

    return shorten_text(
        title,
        HEADLINE_MAX_CHARS
    )


def slugify(text, max_length=70):
    """Create filesystem-safe folder/file names."""

    text = clean_text(text).lower()

    text = re.sub(
        r"[^a-z0-9]+",
        "_",
        text
    )

    text = text.strip("_")

    return text[:max_length]


# ============================================================
# Config loader
# ============================================================

def load_config(config_file):
    """Load config.yaml."""

    config_path = Path(config_file)

    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}"
        )

    with open(
        config_path,
        "r",
        encoding="utf-8"
    ) as f:

        config = yaml.safe_load(f)

    if not config:
        raise ValueError(
            "config.yaml is empty."
        )

    return config


# ============================================================
# Source JSON loader
# ============================================================

def get_source_json_path(config):
    """
    Build source JSON path from:

    output.folder
    output.filename
    """

    output_config = config.get("output", {})

    output_folder = output_config.get("folder")
    output_filename = output_config.get("filename")

    if not output_folder:
        raise ValueError(
            "Missing output.folder in config.yaml"
        )

    if not output_filename:
        raise ValueError(
            "Missing output.filename in config.yaml"
        )

    return Path(output_folder) / output_filename


def load_source_json(json_path):

    if not json_path.exists():

        raise FileNotFoundError(
            f"Source JSON not found:\n{json_path}"
        )

    print()
    print(f"Reading source JSON:")
    print(f"  {json_path}")

    with open(
        json_path,
        "r",
        encoding="utf-8"
    ) as f:

        return json.load(f)



# ============================================================
# Ollama / Qwen
# ============================================================

def get_qwen_config(config):
    qwen_config = config.get("qwen", {})

    return {
        "url": qwen_config.get(
            "ollama_url",
            "http://localhost:11434"
        ),
        "model": qwen_config.get(
            "model",
            "qwen3:8b"
        ),
        "temperature": qwen_config.get(
            "temperature",
            0.2
        ),
        "timeout": qwen_config.get(
            "timeout",
            120
        )
    }


def check_ollama(qwen_config):
    url = (
        qwen_config["url"].rstrip("/")
        + "/api/tags"
    )

    print("\nChecking Ollama...")

    try:
        response = requests.get(
            url,
            timeout=10
        )
        response.raise_for_status()

        models = response.json().get(
            "models",
            []
        )

        model_names = [
            model.get("name")
            for model in models
        ]

        requested_model = qwen_config["model"]

        if requested_model not in model_names:
            print(
                f"WARNING: Model '{requested_model}' "
                "was not found."
            )
            print("Available models:")

            for name in model_names:
                print(f"  - {name}")

            return False

        print("Ollama OK")
        print(f"Model: {requested_model}")

        return True

    except Exception as e:
        print(f"WARNING: Cannot connect to Ollama: {e}")
        return False


def qwen_summarize(title, description, qwen_config):
    """
    Use local Ollama/Qwen to generate an Instagram headline
    and a meaningful 2-3 sentence summary.
    """

    title = clean_text(title)
    description = clean_text(description)

    prompt = f"""
You are an experienced Bollywood entertainment news editor.

Create Instagram-ready content from the source article below.

SOURCE TITLE:
{title}

SOURCE DESCRIPTION:
{description}

Return ONLY valid JSON in exactly this format:

{{
  "headline": "short Instagram headline",
  "summary": "meaningful 2-3 sentence summary"
}}

Rules:
1. Preserve the facts in the source.
2. Do not invent information.
3. Do not speculate.
4. Identify the main news or event.
5. Mention important people, films, dates or numbers when present.
6. Remove unnecessary repetition.
7. Remove promotional language.
8. Use simple, natural English.
9. The headline should be concise and attention-grabbing.
10. The summary should explain the actual story rather than mechanically shortening it.
11. Headline maximum approximately 110 characters.
12. Summary maximum approximately 450 characters.
13. Do not use emojis.
14. Do not add hashtags.
15. Return ONLY JSON. Do not include explanations.
"""

    endpoint = (
        qwen_config["url"].rstrip("/")
        + "/api/generate"
    )

    payload = {
        "model": qwen_config["model"],
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": qwen_config["temperature"]
        }
    }

    response = requests.post(
        endpoint,
        json=payload,
        timeout=qwen_config["timeout"]
    )
    response.raise_for_status()

    result = response.json()

    raw_response = result.get(
        "response",
        ""
    )

    if not raw_response:
        raise ValueError(
            "Ollama returned an empty response."
        )

    # Qwen3 may return internal reasoning in <think>...</think>.
    # Remove it before parsing the JSON answer.
    raw_response = re.sub(
        r"<think>.*?</think>",
        "",
        raw_response,
        flags=re.IGNORECASE | re.DOTALL
    ).strip()

    # Remove accidental Markdown JSON fences.
    raw_response = re.sub(
        r"^```(?:json)?\s*",
        "",
        raw_response,
        flags=re.IGNORECASE
    )

    raw_response = re.sub(
        r"\s*```$",
        "",
        raw_response
    ).strip()

    try:
        parsed = json.loads(raw_response)

    except json.JSONDecodeError as e:
        raise ValueError(
            "Qwen returned invalid JSON after removing "
            f"<think> content: {raw_response[:1000]}"
        ) from e

    headline = clean_text(
        parsed.get("headline", "")
    )

    summary = clean_text(
        parsed.get("summary", "")
    )

    if not headline:
        headline = clean_headline(title)

    if not summary:
        summary = shorten_text(
            description,
            STORY_MAX_CHARS
        )

    return {
        "headline": shorten_text(
            headline,
            HEADLINE_MAX_CHARS
        ),
        "summary": shorten_text(
            summary,
            STORY_MAX_CHARS
        )
    }


# ============================================================
# Story conversion
# ============================================================

def convert_story(
    story,
    category_name,
    slide_number,
    total_slides,
    qwen_config,
    ollama_available
):
    """Convert one story into Qwen JSON."""

    title = story.get(
        "title",
        ""
    )

    description = story.get(
        "description",
        ""
    )

    category_label = (
        story.get("category")
        or category_name.replace(
            "_",
            " "
        ).title()
    )

    heading = story.get(
        "heading",
        ""
    )

    result = {

        "slide": {

            "number": slide_number,

            "total_slides": total_slides,

            "category": category_name,

            "category_label": category_label,

            "heading": clean_text(
                heading
            ),

            "headline": clean_headline(
                title
            ),

            "story": shorten_text(
                description,
                STORY_MAX_CHARS
            ),

            "published_at": story.get(
                "published_at"
            ),

            "published_date": story.get(
                "published_date"
            ),

            "published_time": story.get(
                "published_time"
            ),

            "timezone": story.get(
                "timezone"
            ),

            "image_url": story.get(
                "image"
            ),

            "source_url": story.get(
                "url"
            )
        },

        "qwen": {

            "task":
                "instagram_carousel_slide",

            "format": {

                "aspect_ratio": "9:16",

                "width": 1080,

                "height": 1350
            },

            "language": "English",

            "visual_style":
                "Bold Bollywood entertainment news, "
                "dark cinematic background, "
                "high contrast, "
                "modern Instagram design.",

            "use_source_image": True,

            "rules": [

                "Use the supplied source image as the primary visual when available.",

                "Do not invent facts.",

                "Do not change the meaning of the story.",

                "Keep the headline prominent and readable.",

                "Keep body text concise.",

                "Do not overcrowd the slide.",

                "Maintain strong visual hierarchy.",

                "Keep all important text within safe margins.",

                "Do not add unrelated people or imagery.",

                "Create a polished 9:16 Instagram slide."
            ]
        }
    }

    return result


# ============================================================
# Category processing
# ============================================================

def process_category(
    category_name,
    category_data,
    qwen_root,
    qwen_config,
    ollama_available
):
    """
    Process ONE category.

    IMPORTANT:
    slide numbering starts from 1 for every category.
    """

    stories = category_data.get(
        "stories",
        []
    )

    if not stories:

        print(
            f"\n[SKIP] {category_name}: "
            f"no stories"
        )

        return 0

    category_folder = (
        qwen_root
        / slugify(category_name)
    )

    category_folder.mkdir(
        parents=True,
        exist_ok=True
    )

    total_slides = len(stories)

    combined_slides = []

    print()
    print(
        "=" * 60
    )

    print(
        f"CATEGORY: {category_name}"
    )

    print(
        f"Stories : {total_slides}"
    )

    print(
        "=" * 60
    )

    # --------------------------------------------------------
    # RESET NUMBERING HERE
    # --------------------------------------------------------

    for slide_number, story in enumerate(
        stories,
        start=1
    ):

        slide = convert_story(
            story=story,
            category_name=category_name,
            slide_number=slide_number,
            total_slides=total_slides,
            qwen_config=qwen_config,
            ollama_available=ollama_available
        )

        combined_slides.append(
            slide
        )

        filename = (
            f"slide_{slide_number:03d}.json"
        )

        output_file = (
            category_folder
            / filename
        )

        with open(
            output_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                slide,
                f,
                ensure_ascii=False,
                indent=2
            )

        print(
            f"  Created: {filename}"
        )

    # --------------------------------------------------------
    # Category-level JSON
    # --------------------------------------------------------

    category_json = {

        "metadata": {

            "category":
                category_name,

            "total_slides":
                total_slides,

            "format":
                "9:16",

            "width":
                1080,

            "height":
                1350
        },

        "slides":
            combined_slides
    }

    combined_file = (
        category_folder
        / f"{slugify(category_name)}.json"
    )

    with open(
        combined_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            category_json,
            f,
            ensure_ascii=False,
            indent=2
        )

    print(
        f"  Created: {combined_file.name}"
    )

    return total_slides


# ============================================================
# Main conversion
# ============================================================

def convert(config_file):

    # --------------------------------------------------------
    # Load YAML
    # --------------------------------------------------------

    config = load_config(
        config_file
    )

    # --------------------------------------------------------
    # Get source JSON path
    # --------------------------------------------------------

    source_json = get_source_json_path(
        config
    )

    # --------------------------------------------------------
    # Read source JSON
    # --------------------------------------------------------

    data = load_source_json(
        source_json
    )

    # --------------------------------------------------------
    # Qwen configuration
    # --------------------------------------------------------

    qwen_config = get_qwen_config(config)

    print()
    print("Qwen configuration:")
    print(f"  Ollama : {qwen_config['url']}")
    print(f"  Model  : {qwen_config['model']}")

    ollama_available = check_ollama(
        qwen_config
    )

    # --------------------------------------------------------
    # Get categories
    # --------------------------------------------------------

    categories = data.get(
        "categories",
        {}
    )

    if not isinstance(
        categories,
        dict
    ):

        raise ValueError(
            "The source JSON does not contain "
            "a valid 'categories' object."
        )

    # --------------------------------------------------------
    # Output:
    #
    # OP_JSON/
    #     qwen_input/
    #         news/
    #         features/
    #         ...
    # --------------------------------------------------------

    output_folder = Path(
        config["output"]["folder"]
    )

    qwen_root = (
        output_folder
        / QWEN_SUBFOLDER
    )

    qwen_root.mkdir(
        parents=True,
        exist_ok=True
    )

    print()
    print(
        f"Qwen output folder:"
    )

    print(
        f"  {qwen_root}"
    )

    # --------------------------------------------------------
    # Process categories
    # --------------------------------------------------------

    summary = {}

    for category_name, category_data in categories.items():

        count = process_category(
            category_name=category_name,
            category_data=category_data,
            qwen_root=qwen_root,
            qwen_config=qwen_config,
            ollama_available=ollama_available
        )

        summary[
            category_name
        ] = count

    # --------------------------------------------------------
    # Manifest
    # --------------------------------------------------------

    manifest = {

        "source_file":
            source_json.name,

        "source_path":
            str(source_json),

        "output_path":
            str(qwen_root),

        "qwen": {
            "enabled": ollama_available,
            "model": qwen_config["model"],
            "ollama_url": qwen_config["url"]
        },

        "categories":
            summary,

        "total_slides":
            sum(summary.values())
    }

    manifest_file = (
        qwen_root
        / "manifest.json"
    )

    with open(
        manifest_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            manifest,
            f,
            ensure_ascii=False,
            indent=2
        )

    # --------------------------------------------------------
    # Final summary
    # --------------------------------------------------------

    print()
    print(
        "=" * 60
    )

    print(
        "CONVERSION COMPLETE"
    )

    print(
        "=" * 60
    )

    for category, count in summary.items():

        print(
            f"{category:25} : {count} slides"
        )

    print(
        "-" * 60
    )

    print(
        f"{'TOTAL':25} : "
        f"{sum(summary.values())} slides"
    )

    print()
    print(
        f"Manifest:"
    )

    print(
        f"  {manifest_file}"
    )


# ============================================================
# CLI
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "BollywoodKoko RSS JSON → "
            "category-wise Qwen JSON converter"
        )
    )

    parser.add_argument(
        "-c",
        "--config",
        default=DEFAULT_CONFIG,
        help=(
            "Path to config.yaml "
            "(default: config.yaml)"
        )
    )

    args = parser.parse_args()

    convert(
        args.config
    )


if __name__ == "__main__":
    main()