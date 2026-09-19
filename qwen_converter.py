#!/usr/bin/env python3
"""
qwen_converter.py

Convert the Bollywood Hungama RSS JSON into category-wise Qwen input JSON
for Instagram carousel generation.

Key features:
- Calls local Ollama/Qwen for EVERY story.
- Generates a meaningful editorial summary instead of truncating the source.
- Generates English + Hindi story summaries in one Qwen call.
- Removes Qwen <think>...</think> output before JSON parsing.
- Uses Ollama JSON output mode.
- Keeps the original source title/description in the output.
- Falls back safely to source text if Qwen is unavailable.
- Resets slide numbering for every category/carousel.
- Targets Instagram 9:16, 1080x1920.
"""

import argparse
import html
import json
import re
import sys
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup


DEFAULT_CONFIG = "config.yaml"
QWEN_SUBFOLDER = "qwen_input"

HEADLINE_MAX_CHARS = 110

# This is ONLY a safety limit. Normal Qwen summaries are NOT truncated.
SUMMARY_SAFETY_MAX_CHARS = 1200

# Image discovery is only used when the RSS/source JSON does not already
# provide an image URL. This keeps the existing image path untouched.
IMAGE_DISCOVERY_TIMEOUT = 20


DEFAULT_QWEN_CONFIG = {
    "ollama_url": "http://localhost:11434",
    "model": "qwen3:8b",
    "temperature": 0.2,
    "timeout": 120,
}


def clean_text(value):
    """Clean HTML/entities/whitespace while preserving the actual content."""
    if value is None:
        return ""

    text = html.unescape(str(value))

    # Remove HTML tags.
    text = re.sub(r"<[^>]+>", " ", text)

    # Normalize whitespace.
    text = re.sub(r"\s+", " ", text).strip()

    return text


def clean_headline(value):
    """Clean a headline without unnecessarily shortening it."""
    text = clean_text(value)

    if len(text) <= HEADLINE_MAX_CHARS:
        return text

    # Prefer cutting at a word boundary.
    shortened = text[:HEADLINE_MAX_CHARS].rsplit(" ", 1)[0].strip()

    return shortened if shortened else text[:HEADLINE_MAX_CHARS].strip()


def safety_limit_summary(text):
    """
    Prevent runaway model output.

    Important:
    We deliberately DO NOT use the old 450-character truncation here.
    A normal Qwen summary should pass through untouched.
    """
    text = clean_text(text)

    if len(text) <= SUMMARY_SAFETY_MAX_CHARS:
        return text

    # Only truncate if Qwen clearly ignored the requested summary length.
    shortened = text[:SUMMARY_SAFETY_MAX_CHARS].rsplit(" ", 1)[0].strip()
    return shortened + "..."


def enforce_english_story(text):
    """
    The English story must contain English text only.

    Qwen can occasionally ignore the language instruction. Since the source
    article is English, detect obvious Devanagari and reject that output so
    the caller can fall back to a source-based English summary.
    """
    text = clean_text(text)

    if not text:
        return ""

    # Any Devanagari character means the English summary was not produced
    # entirely in English.
    if re.search(r"[\u0900-\u097F]", text):
        return ""

    return text


def preserve_source_names_in_hindi(text, source_text):
    """
    Restore important English names/titles from the source if Qwen
    transliterated them into Hindi.

    This is intentionally conservative: only well-defined quoted titles
    and common entertainment entities are considered.
    """
    text = clean_text(text)
    source_text = clean_text(source_text)

    if not text or not source_text:
        return text

    protected = set()

    # Quoted titles/episode names in the source.
    for match in re.findall(r'[“"]([^”"]+)[”"]', source_text):
        candidate = clean_text(match)
        if candidate and re.search(r"[A-Za-z]", candidate):
            protected.add(candidate)

    # Explicitly preserve multi-word entertainment names appearing in source.
    # This also covers the common "Matka King"-type case.
    patterns = [
        r"\bMatka King\b",
        r"\bPrime Video\b",
        r"\bBrij Bhatti\b",
        r"\bEk Aur Baazi\b",
    ]

    for pattern in patterns:
        for match in re.findall(pattern, source_text, flags=re.IGNORECASE):
            protected.add(match)

    # Replace Hindi transliterations when the exact English source term
    # exists. Keep this conservative to avoid mangling unrelated Hindi.
    replacements = {
        "मातक राज": "Matka King",
        "मटका किंग": "Matka King",
        "मटका राजा": "Matka King",
        "प्राइम वीडियो": "Prime Video",
        "बृज भट्टी": "Brij Bhatti",
        "एक और बाज़ी": "Ek Aur Baazi",
        "एक और बाजी": "Ek Aur Baazi",
    }

    for hindi, english in replacements.items():
        if english in protected or re.search(
            re.escape(english), source_text, flags=re.IGNORECASE
        ):
            text = text.replace(hindi, english)

    return text


def slugify(value):
    value = clean_text(value).lower()
    value = re.sub(r"[^a-z0-9_-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "category"


def load_config(config_path):
    path = Path(config_path)

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    return config


def get_source_json_path(config):
    output_cfg = config.get("output", {})

    folder = output_cfg.get("folder")
    filename = output_cfg.get("filename")

    if not folder or not filename:
        raise ValueError(
            "config.yaml must contain output.folder and output.filename"
        )

    return Path(folder) / filename


def load_source_json(path):
    if not path.exists():
        raise FileNotFoundError(f"Source JSON not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_qwen_config(config):
    qwen_cfg = config.get("qwen", {}) or {}

    result = DEFAULT_QWEN_CONFIG.copy()
    result.update(qwen_cfg)

    return result


def load_prompt_template():
    """
    Load the Qwen prompt from qwen_converter.properties.

    The properties file is expected to sit next to qwen_converter.py.
    It contains the prompt template with {title} and {description}
    placeholders.
    """
    prompt_path = Path(__file__).resolve().with_name("qwen_converter.properties")

    if not prompt_path.exists():
        raise FileNotFoundError(
            f"Prompt properties file not found: {prompt_path}"
        )

    prompt = prompt_path.read_text(encoding="utf-8").strip()

    if not prompt:
        raise ValueError(f"Prompt properties file is empty: {prompt_path}")

    return prompt

PROMPT_TEMPLATE = load_prompt_template()

def check_ollama(ollama_url, timeout=10):
    """Check whether Ollama is reachable."""
    url = ollama_url.rstrip("/") + "/api/tags"

    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"[WARN] Ollama is not reachable: {exc}")
        return False


def strip_thinking(text):
    """
    Qwen3 may return:
        <think>...</think>
        {"headline": "...", ...}

    Remove the thinking section before JSON parsing.
    """
    if not text:
        return ""

    text = re.sub(
        r"<think>.*?</think>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    return text.strip()


def strip_markdown_json_fence(text):
    """Remove ```json ... ``` if the model adds Markdown fences."""
    text = text.strip()

    match = re.match(
        r"^```(?:json)?\s*(.*?)\s*```$",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    if match:
        return match.group(1).strip()

    return text


def parse_qwen_json(raw_text):
    """
    Parse Qwen JSON robustly.

    Handles:
    - <think>...</think>
    - Markdown JSON fences
    - occasional text before/after the JSON object
    """
    text = strip_thinking(raw_text)
    text = strip_markdown_json_fence(text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting the first JSON object.
    start = text.find("{")
    end = text.rfind("}")

    if start >= 0 and end > start:
        candidate = text[start : end + 1]

        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    raise ValueError(
        "Could not parse valid JSON from Qwen response.\n"
        f"Raw response:\n{text}"
    )


def qwen_summarize(
    title,
    description,
    ollama_url,
    model,
    temperature=0.2,
    timeout=120,
):
    """
    Ask Qwen to produce:
    - a cleaned Instagram headline
    - an English 60-90 word summary
    - a Hindi 60-90 word summary
    """

    title = clean_text(title)
    description = clean_text(description)

    prompt_template = PROMPT_TEMPLATE

    prompt = prompt_template.format(
        title=title,
        description=description,
    )

    

    endpoint = ollama_url.rstrip("/") + "/api/generate"

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": temperature,
        },
    }

    response = requests.post(
        endpoint,
        json=payload,
        timeout=timeout,
    )

    response.raise_for_status()

    result = response.json()

    raw_response = result.get("response", "")

    if not raw_response:
        raise ValueError("Ollama returned an empty response.")

    data = parse_qwen_json(raw_response)

    headline = clean_headline(data.get("headline", ""))
    summary = enforce_english_story(data.get("summary", ""))
    summary = safety_limit_summary(summary)
    summary_hindi = safety_limit_summary(
        preserve_source_names_in_hindi(
            data.get("summary_hindi", ""),
            description,
        )
    )

    # If Qwen accidentally returns a Hindi headline, do not use it.
    if headline and re.search(r"[\u0900-\u097F]", headline):
        headline = ""

    if not headline:
        headline = clean_headline(title)

    if not summary:
        raise ValueError(
            "Qwen returned a non-English/empty English summary."
        )

    if not summary_hindi:
        raise ValueError("Qwen returned an empty Hindi summary.")

    return {
        "headline": headline,
        "summary": summary,
        "summary_hindi": summary_hindi,
    }


def _first_non_empty(*values):
    """Return the first non-empty value as a string."""
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value:
            return str(value).strip()
    return ""


def extract_image_url_from_story(story):
    """
    Extract an image URL from common RSS/source-JSON structures.

    Supported forms include:
      image_url, image, thumbnail
      media_content[].url
      media_thumbnail[].url
      enclosures[].href/url
    """
    direct = _first_non_empty(
        story.get("image_url"),
        story.get("image"),
        story.get("thumbnail"),
    )
    if direct:
        return direct

    for key in ("media_content", "media_thumbnail", "enclosures"):
        items = story.get(key) or []
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            continue

        for item in items:
            if not isinstance(item, dict):
                continue
            url = _first_non_empty(
                item.get("url"),
                item.get("href"),
                item.get("src"),
            )
            if url:
                return url

    return ""


def extract_image_from_article_url(source_url):
    """
    Fallback image discovery from the article page when RSS contains no
    image URL. Prefer Open Graph/Twitter metadata, then JSON-LD image.
    """
    if not source_url:
        return ""

    try:
        response = requests.get(
            source_url,
            timeout=IMAGE_DISCOVERY_TIMEOUT,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/153.0 Safari/537.36 BollywoodKoko/1.0"
                )
            },
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # 1. Open Graph
        for selector in [
            ("meta", {"property": "og:image"}),
            ("meta", {"name": "og:image"}),
            ("meta", {"property": "og:image:url"}),
            ("meta", {"name": "twitter:image"}),
            ("meta", {"property": "twitter:image"}),
        ]:
            tag = soup.find(*selector)
            if tag and tag.get("content"):
                return tag["content"].strip()

        # 2. link rel=image_src
        tag = soup.find("link", rel=lambda value: value and "image_src" in value)
        if tag and tag.get("href"):
            return tag["href"].strip()

        # 3. JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            raw = script.string or script.get_text(strip=True)
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except Exception:
                continue

            candidates = payload if isinstance(payload, list) else [payload]
            for obj in candidates:
                if not isinstance(obj, dict):
                    continue
                image = obj.get("image")
                if isinstance(image, str) and image.strip():
                    return image.strip()
                if isinstance(image, dict):
                    url = image.get("url") or image.get("contentUrl")
                    if url:
                        return str(url).strip()
                if isinstance(image, list):
                    for item in image:
                        if isinstance(item, str) and item.strip():
                            return item.strip()
                        if isinstance(item, dict):
                            url = item.get("url") or item.get("contentUrl")
                            if url:
                                return str(url).strip()

    except Exception as exc:
        print(f"    [WARN] Article image discovery failed: {exc}")

    return ""

def extract_story_fields(story):
    """
    Handle the expected Bollywood Hungama story structure while allowing
    small variations in field names. If RSS does not contain an image,
    fall back to the article URL and discover og:image/twitter:image/JSON-LD.
    """
    title = (
        story.get("title")
        or story.get("headline")
        or story.get("name")
        or ""
    )

    description = (
        story.get("description")
        or story.get("summary")
        or story.get("content")
        or ""
    )

    source_url = (
        story.get("source_url")
        or story.get("link")
        or story.get("url")
        or ""
    )

    image_url = extract_image_url_from_story(story)

    if not image_url and source_url:
        print("    → No RSS image; discovering image from article page")
        image_url = extract_image_from_article_url(source_url)
        if image_url:
            print(f"    ✓ Image found: {image_url}")
        else:
            print("    ⚠ No article image found")

    return {
        "title": clean_text(title),
        "description": clean_text(description),
        "published_at": story.get("published_at")
        or story.get("pubDate")
        or story.get("published")
        or "",
        "image_url": image_url,
        "source_url": source_url,
    }


def build_slide(
    story,
    category_key,
    category_cfg,
    slide_number,
    total_slides,
    qwen_result,
    timezone,
):
    fields = extract_story_fields(story)

    category_label = (
        category_cfg.get("label")
        or category_cfg.get("heading")
        or category_key.replace("_", " ").title()
    )

    heading = (
        category_cfg.get("heading")
        or category_label
    )

    published_at = fields["published_at"]

    published_date = ""
    published_time = ""

    if published_at:
        published_text = str(published_at)

        # Keep this intentionally simple; source formatting remains intact.
        if "T" in published_text:
            published_date, published_time = published_text.split("T", 1)
            published_time = published_time.replace("Z", "")
        elif " " in published_text:
            parts = published_text.split(" ", 1)
            published_date = parts[0]
            published_time = parts[1]

    return {
        "slide": {
            "number": slide_number,
            "total_slides": total_slides,
            "category": category_key,
            "category_label": category_label,
            "heading": heading,
            "headline": qwen_result["headline"],
            "story": qwen_result["summary"],
            "story_hindi": qwen_result["summary_hindi"],
            "published_at": published_at,
            "published_date": published_date,
            "published_time": published_time,
            "timezone": timezone,
            "image_url": fields["image_url"],
            "source_url": fields["source_url"],
            "content_source": "qwen",
        },
        "source": {
            "title": fields["title"],
            "description": fields["description"],
        },
        "qwen": {
            "task": "instagram_carousel_slide",
            "model": None,
            "format": {
                "aspect_ratio": "9:16",
                "width": 1080,
                "height": 1920,
            },
            "language": "English + Hindi",
            "visual_style": (
                "Bold Bollywood entertainment news, dark cinematic background, "
                "high contrast, modern Instagram design."
            ),
            "use_source_image": True,
            "rules": [
                "Use the Qwen headline and summaries as supplied.",
                "English story is a meaningful editorial summary, not source truncation.",
                "Hindi story conveys the same facts in natural Devanagari.",
                "Do not invent facts.",
                "Instagram canvas: 1080x1920, 9:16 portrait.",
            ],
        },
    }


def get_stories_for_category(category_data):
    """
    IMPORTANT:
    Use categories -> stories[].

    Do not use all_stories[] because that can duplicate stories.
    """
    stories = category_data.get("stories", [])

    if isinstance(stories, list):
        return stories

    return []


def convert_category(
    category_key,
    category_data,
    output_root,
    qwen_cfg,
    timezone,
    category_cfg,
):
    stories = get_stories_for_category(category_data)

    category_dir = output_root / slugify(category_key)
    category_dir.mkdir(parents=True, exist_ok=True)

    # Remove old slide JSON files so stale slides do not remain.
    for old_file in category_dir.glob("slide_*.json"):
        old_file.unlink()

    total_slides = len(stories)

    category_manifest = {
        "category": category_key,
        "category_label": (
            category_cfg.get("label")
            or category_cfg.get("heading")
            or category_key.replace("_", " ").title()
        ),
        "total_slides": total_slides,
        "format": {
            "aspect_ratio": "9:16",
            "width": 1080,
            "height": 1920,
        },
        "slides": [],
    }

    print()
    print("=" * 70)
    print(f"Category: {category_key}")
    print(f"Stories : {total_slides}")
    print("=" * 70)

    for index, story in enumerate(stories, start=1):
        fields = extract_story_fields(story)

        print(
            f"[{index:02d}/{total_slides:02d}] "
            f"Qwen processing: {fields['title'][:90]}"
        )

        try:
            qwen_result = qwen_summarize(
                title=fields["title"],
                description=fields["description"],
                ollama_url=qwen_cfg["ollama_url"],
                model=qwen_cfg["model"],
                temperature=float(qwen_cfg["temperature"]),
                timeout=int(qwen_cfg["timeout"]),
            )

            print("    ✓ Qwen summary generated")

        except Exception as exc:
            print(f"    ⚠ Qwen failed: {exc}")
            print("    → Using source fallback")

            fallback_summary = safety_limit_summary(fields["description"])

            qwen_result = {
                "headline": clean_headline(fields["title"]),
                "summary": fallback_summary,
                "summary_hindi": "",
            }

        slide = build_slide(
            story=story,
            category_key=category_key,
            category_cfg=category_cfg,
            slide_number=index,
            total_slides=total_slides,
            qwen_result=qwen_result,
            timezone=timezone,
        )

        slide["qwen"]["model"] = qwen_cfg["model"]

        slide_path = category_dir / f"slide_{index:03d}.json"

        with slide_path.open("w", encoding="utf-8") as f:
            json.dump(
                slide,
                f,
                ensure_ascii=False,
                indent=2,
            )

        category_manifest["slides"].append(
            {
                "number": index,
                "file": slide_path.name,
                "headline": slide["slide"]["headline"],
                "source_url": slide["slide"]["source_url"],
            }
        )

    manifest_path = category_dir / f"{slugify(category_key)}.json"

    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(
            category_manifest,
            f,
            ensure_ascii=False,
            indent=2,
        )

    return category_manifest


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Convert Bollywood Hungama RSS JSON into category-wise "
            "Qwen/Ollama Instagram carousel JSON."
        )
    )

    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help="Path to config.yaml",
    )

    parser.add_argument(
        "--no-ollama-check",
        action="store_true",
        help="Skip the initial Ollama connectivity check.",
    )

    args = parser.parse_args()

    try:
        config = load_config(args.config)

        source_json_path = get_source_json_path(config)
        source_data = load_source_json(source_json_path)

        qwen_cfg = get_qwen_config(config)

        timezone = (
            config.get("source", {}).get("timezone")
            or "Asia/Kolkata"
        )

        output_root = source_json_path.parent / QWEN_SUBFOLDER
        output_root.mkdir(parents=True, exist_ok=True)

        print(f"Source JSON : {source_json_path}")
        print(f"Output root : {output_root}")
        print(f"Ollama URL  : {qwen_cfg['ollama_url']}")
        print(f"Qwen model  : {qwen_cfg['model']}")
        print("Format      : 1080x1920 (9:16)")

        if not args.no_ollama_check:
            if not check_ollama(qwen_cfg["ollama_url"]):
                print()
                print(
                    "[ERROR] Ollama is not reachable. "
                    "Start Ollama and run the converter again."
                )
                sys.exit(1)

        categories = source_data.get("categories", {})

        if not isinstance(categories, dict):
            raise ValueError(
                "Expected source JSON structure: categories -> object"
            )

        manifests = {}

        for category_key, category_data in categories.items():
            if not isinstance(category_data, dict):
                continue

            category_cfg = (
                config.get("feeds", {}).get(category_key, {})
                or {}
            )

            manifest = convert_category(
                category_key=category_key,
                category_data=category_data,
                output_root=output_root,
                qwen_cfg=qwen_cfg,
                timezone=timezone,
                category_cfg=category_cfg,
            )

            manifests[category_key] = manifest

        master_manifest = {
            "source": {
                "file": str(source_json_path),
                "name": config.get("source", {}).get(
                    "name",
                    "Bollywood Hungama",
                ),
            },
            "qwen": {
                "ollama_url": qwen_cfg["ollama_url"],
                "model": qwen_cfg["model"],
            },
            "format": {
                "aspect_ratio": "9:16",
                "width": 1080,
                "height": 1920,
            },
            "categories": manifests,
        }

        master_manifest_path = output_root / "manifest.json"

        with master_manifest_path.open("w", encoding="utf-8") as f:
            json.dump(
                master_manifest,
                f,
                ensure_ascii=False,
                indent=2,
            )

        print()
        print("=" * 70)
        print("CONVERSION COMPLETE")
        print("=" * 70)
        print(f"Output: {output_root}")
        print(f"Manifest: {master_manifest_path}")

    except Exception as exc:
        print()
        print(f"[ERROR] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
