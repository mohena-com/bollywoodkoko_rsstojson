#!/usr/bin/env python3
"""
BollywoodKoko - Qwen Slide Designer

Purpose
-------
Use Qwen3:8b through Ollama as the CREATIVE DIRECTOR for every Instagram
carousel slide.

Pipeline
--------
slide JSON produced by qwen_converter.py
        |
        v
qwen_slide_designer.py
        |
        | Qwen3:8b
        v
same slide JSON + design specification
        |
        v
paint_slides.py
        |
        v
PNG 1080x1920

IMPORTANT
---------
Qwen is responsible for deciding HOW the slide should look:
- visual concept
- layout
- image treatment and placement
- headline/story hierarchy
- hero text/stat
- Hindi/English treatment
- emphasis
- typography
- spacing
- decorative elements

The renderer remains responsible for drawing the pixels.  This separation
keeps the pipeline deterministic while making the visual design AI-driven.
"""

import argparse
import json
import re
from pathlib import Path

import requests
import yaml


SUPPORTED_LAYOUTS = [
    "hero_stat",
    "hero_title",
    "split_story",
    "portrait_focus",
    "announcement",
    "release_date",
    "award",
    "streaming_record",
    "box_office",
    "quote_focus",
    "comparison",
    "standard_news",
]


SYSTEM_PROMPT = r"""
You are the Creative Director for BollywoodKoko, an automated Instagram
Bollywood/news publishing system.

Your job is to DESIGN ONE INSTAGRAM NEWS SLIDE from the supplied editorial
JSON.

You are NOT writing a generic article and you are NOT producing a generic
template. You must make a deliberate visual decision based on the actual
story.

Canvas:
- 1080 x 1920 pixels
- aspect ratio 9:16
- Instagram portrait

The final slide is rendered by a deterministic Pillow renderer. Therefore
your response must be a precise MACHINE-READABLE DESIGN SPECIFICATION.

EDITORIAL CONTENT RULES
-----------------------
1. Use only facts contained in the supplied slide/source data.
2. Do not invent facts, quotes, numbers, dates, people, awards, revenue,
   view counts, rankings, or visual claims.
3. English headline must remain English.
4. English story must remain English.
5. Hindi story must be natural Hindi in Devanagari.
6. NEVER translate, transliterate, or convert proper names into Devanagari.
   Keep movie, series, show, song, character, platform, company and person
   names in their original English spelling.
   Examples:
     Matka King
     Prime Video
     Brij Bhatti
     Ek Aur Baazi
     Vijay Varma
7. The news date MUST appear on every slide.
8. The slide number MUST appear on every slide.
9. Bilingual slides are required: English editorial content followed by the
   Hindi version, unless the input explicitly has no Hindi content.
10. Do not duplicate the same sentence unnecessarily.
11. Do not create clickbait that changes the factual meaning.

VISUAL DESIGN RULES
-------------------
Choose the layout that best matches the story.

Available layouts:
- hero_stat       : a major number/record/viewership/percentage dominates
- hero_title      : headline is the primary visual
- split_story     : image and text have clearly separated visual zones
- portrait_focus  : person/character is the visual focus
- announcement    : casting, sequel, confirmation, announcement
- release_date    : release date is the key information
- award           : award/recognition is the key information
- streaming_record: streaming views/rankings/platform performance
- box_office     : collection/opening/box-office statistic
- quote_focus     : a supplied quote or statement is central
- comparison      : two or more explicitly supplied entities are compared
- standard_news   : normal news story without a dominant special feature

Do NOT choose hero_stat, box_office, comparison, award, etc. unless the source
actually contains the relevant information.

VISUAL HIERARCHY
----------------
The slide should normally have:
1. category badge
2. date
3. slide counter
4. hero visual / image
5. primary headline or hero fact
6. English story
7. Hindi story
8. source/brand footer

Do not force every item into the same location. Move them based on the story.

IMAGE
-----
The input provides image_url. Treat it as the main source image.
Specify:
- position
- crop
- scale
- opacity
- blur
- darkening
- whether the image is background/full bleed/portrait panel

Do not ask for an image that is not supplied.

TEXT
----
Specify relative positions using percentages of the 1080x1920 canvas.
Coordinates must be integers:
- x: 0..1080
- y: 0..1920
- width: 1..1080
- height: 1..1920
- A full-bleed image may legitimately be 1080x1920.

Font sizes are in pixels.

Use readable typography. Do not create text overflow.
Hindi needs enough line height and a Devanagari-capable font.

EMPHASIS
---------
Use emphasis_words only for words actually present in the supplied content.
Do not invent emphasis text.

DESIGN VARIETY
--------------
Avoid making every slide look identical.
Different stories should naturally produce different layouts, hierarchy,
image treatment and text placement.

However, readability and brand consistency must remain strong:
- dark cinematic Bollywood aesthetic
- high contrast
- modern Instagram editorial design
- restrained decorative elements
- no clutter
- no tiny body text

OUTPUT
------
Return ONLY valid JSON matching the requested schema.
Do not use Markdown.
Do not include <think>.
"""


def strip_thinking(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE)
    text = text.replace("```", "")
    return text.strip()


def extract_balanced_json(text: str) -> str:
    """
    Extract the first balanced JSON object from Qwen output.

    Qwen occasionally emits a syntactically broken preamble or additional
    text around the JSON. A simple first-{ / last-} slice is not sufficient
    when the response contains braces inside strings.
    """
    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON object found in Qwen response.")

    depth = 0
    in_string = False
    escaped = False

    for i in range(start, len(text)):
        ch = text[i]

        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]

    raise ValueError("Qwen response contains an incomplete JSON object.")


def parse_json(text: str):
    cleaned = strip_thinking(text)

    # First try the complete response.
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Then extract a balanced JSON object.
    candidate = extract_balanced_json(cleaned)

    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Qwen returned malformed JSON: {e}. "
            f"Response excerpt: {candidate[max(0, e.pos-120):e.pos+120]}"
        ) from e


def ollama_generate(base_url, model, prompt, temperature, timeout):
    url = base_url.rstrip("/") + "/api/generate"

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        # Qwen3 can spend substantial time generating a hidden reasoning
        # trace. Slide design is a constrained JSON task, so disable thinking
        # when supported by the installed Ollama version.
        "think": False,
        "options": {
            "temperature": temperature,
            "num_predict": 800,
        },
    }

    response = requests.post(url, json=payload, timeout=timeout)
    response.raise_for_status()

    result = response.json()
    return result.get("response", "")


def validate_design(design):
    """
    Validate and normalize Qwen's design specification.

    Qwen is the creative director, so its layout decisions are preserved.
    The renderer, however, needs predictable element objects. If Qwen omits
    an optional renderer field, we supply a safe default instead of rejecting
    the whole slide.
    """
    required = [
        "layout",
        "story_type",
        "visual_priority",
        "hero_text",
        "hero_label",
        "secondary_text",
        "secondary_label",
        "supporting_text",
        "emphasis_words",
        "image_treatment",
        "image_position",
        "text_position",
        "headline_style",
        "story_style",
        "show_story",
        "show_date",
        "show_source_brand",
        "canvas",
        "elements",
        "design_notes",
    ]

    for key in required:
        if key not in design:
            raise ValueError(f"Missing design field: {key}")

    if design["layout"] not in SUPPORTED_LAYOUTS:
        raise ValueError(
            f"Unsupported layout '{design['layout']}'. "
            f"Expected one of: {', '.join(SUPPORTED_LAYOUTS)}"
        )

    if not isinstance(design["emphasis_words"], list):
        design["emphasis_words"] = []

    if not isinstance(design["elements"], list):
        design["elements"] = []

    canvas = design["canvas"]
    canvas["width"] = 1080
    canvas["height"] = 1920
    canvas["aspect_ratio"] = "9:16"

    normalized = []

    # Safe defaults. These are fallbacks only; Qwen's supplied coordinates
    # and visual choices are retained whenever they are valid.
    default_positions = {
        "badge": (65, 55, 220, 65),
        "date": (300, 55, 260, 65),
        "counter": (900, 65, 115, 50),
        "image": (0, 0, 1080, 800),
        "headline": (65, 850, 950, 240),
        "hero_stat": (65, 850, 950, 220),
        "label": (65, 1080, 950, 70),
        "story_en": (65, 1160, 950, 260),
        "story_hi": (65, 1430, 950, 350),
        "footer": (65, 1830, 950, 55),
        "accent": (65, 800, 950, 8),
    }

    for idx, raw in enumerate(design["elements"], 1):
        # Accept a string shorthand such as "image" or "headline".
        if isinstance(raw, str):
            element = {"type": raw}
        elif isinstance(raw, dict):
            element = dict(raw)
        else:
            continue

        element_type = str(element.get("type", "")).strip().lower()
        if not element_type:
            # Do not let a malformed Qwen element kill the entire slide.
            continue

        element["type"] = element_type

        x, y, w, h = default_positions.get(
            element_type,
            (65, 850, 950, 120)
        )

        def safe_int(value, fallback):
            try:
                return int(round(float(value)))
            except (TypeError, ValueError):
                return fallback

        element["x"] = safe_int(element.get("x"), x)
        element["y"] = safe_int(element.get("y"), y)
        element["width"] = safe_int(element.get("width"), w)
        element["height"] = safe_int(element.get("height"), h)

        element["x"] = max(0, min(1080, element["x"]))
        element["y"] = max(0, min(1920, element["y"]))
        element["width"] = max(1, min(1080 - element["x"], element["width"]))
        element["height"] = max(1, min(1920 - element["y"], element["height"]))

        element.setdefault("text", "")
        element.setdefault("font_size", 32)
        element.setdefault("font_weight", "regular")
        element.setdefault("language", "none")
        element.setdefault("align", "left")
        element.setdefault("opacity", 255)
        element.setdefault("style", "")

        normalized.append(element)

    design["elements"] = normalized[:8]

    return True


def build_prompt(slide):
    # Give Qwen the editorial material and explicitly prevent it from
    # changing the source content.
    compact = {
        "slide": slide.get("slide", {}),
        "source": slide.get("source", {}),
    }

    schema = {
        "layout": "one of the supported layout names",
        "story_type": "short factual classification",
        "visual_priority": "what should attract attention first",
        "hero_text": "short text or empty string",
        "hero_label": "short label or empty string",
        "secondary_text": "short text or empty string",
        "secondary_label": "short label or empty string",
        "supporting_text": "short supporting text or empty string",
        "emphasis_words": ["only words already present in supplied content"],
        "image_treatment": "e.g. full_bleed_darkened, portrait_cutout, panel",
        "image_position": "e.g. top, center, left, right, background",
        "text_position": "e.g. lower, upper, left, right, split",
        "headline_style": {
            "size": "large/medium/small",
            "weight": "bold/regular",
            "alignment": "left/center/right",
        },
        "story_style": {
            "size": "medium/small",
            "alignment": "left/center/right",
            "english_then_hindi": True,
        },
        "show_story": True,
        "show_date": True,
        "show_source_brand": True,
        "canvas": {
            "width": 1080,
            "height": 1920,
            "aspect_ratio": "9:16",
        },
        "elements": [
            {
                "type": "badge|date|counter|image|headline|hero_stat|label|story_en|story_hi|footer|accent",
                "x": 0,
                "y": 0,
                "width": 1080,
                "height": 1920,
                "text": "only when applicable",
                "font_size": 32,
                "font_weight": "regular|bold",
                "language": "none|english|hindi",
                "align": "left|center|right",
                "opacity": 255,
                "style": "very short rendering instruction",
            }
        ],
        "design_notes": "concise instructions for the renderer",
    }

    return f"""
{SYSTEM_PROMPT}

DESIGN JSON SCHEMA:
{json.dumps(schema, ensure_ascii=False, indent=2)}

SUPPLIED EDITORIAL SLIDE:
{json.dumps(compact, ensure_ascii=False, indent=2)}

Now create the visual design specification for THIS slide.
Remember:
- 1080x1920
- 9:16
- date on every slide
- slide number on every slide
- English + Hindi
- preserve English proper names inside Hindi
- use only supplied facts
- make the design story-specific
- return JSON only
- Keep the response compact: maximum 8 visual elements.
- Every element MUST be a JSON object with a non-empty 'type'.
- Never return an element as null, a number, an array, or an empty object.
- Do not explain your reasoning.
- design_notes must be one short sentence.
- Do not put comments or trailing commas in JSON.
- Do not output any text before or after the JSON object.
"""


def design_one(slide, cfg):
    qwen_cfg = cfg.get("qwen", {})

    ollama_url = qwen_cfg.get("ollama_url", "http://localhost:11434")
    model = qwen_cfg.get("model", "qwen3:8b")
    temperature = float(qwen_cfg.get("temperature", 0.2))
    timeout = int(qwen_cfg.get("timeout", 180))

    prompt = build_prompt(slide)

    raw = ollama_generate(
        ollama_url,
        model,
        prompt,
        temperature,
        timeout,
    )

    try:
        design = parse_json(raw)
    except ValueError as first_error:
        print("    WARN: Qwen returned malformed JSON; retrying compact JSON-only request...")

        repair_prompt = f"""
Return ONLY one valid JSON object. No markdown, no explanation, no thinking.

The previous response for this slide could not be parsed:
{str(first_error)}

Recreate the same slide design using the schema and editorial content below.
Keep it compact: maximum 7 elements. Every element should have integer x, y, width, height values within
1080x1920. If unsure, use the safe default element coordinates from the schema.

SCHEMA:
{json.dumps({
    "layout": "hero_stat|hero_title|split_story|portrait_focus|announcement|release_date|award|streaming_record|box_office|quote_focus|comparison|standard_news",
    "story_type": "string",
    "visual_priority": "string",
    "hero_text": "string",
    "hero_label": "string",
    "secondary_text": "string",
    "secondary_label": "string",
    "supporting_text": "string",
    "emphasis_words": [],
    "image_treatment": "string",
    "image_position": "string",
    "text_position": "string",
    "headline_style": {"size": "large|medium|small", "weight": "bold|regular", "alignment": "left|center|right"},
    "story_style": {"size": "medium|small", "alignment": "left|center|right", "english_then_hindi": True},
    "show_story": True,
    "show_date": True,
    "show_source_brand": True,
    "canvas": {"width": 1080, "height": 1920, "aspect_ratio": "9:16"},
    "elements": [],
    "design_notes": "one short sentence"
}, ensure_ascii=False, indent=2)}

EDITORIAL:
{json.dumps({
    "slide": slide.get("slide", {}),
    "source": slide.get("source", {})
}, ensure_ascii=False, indent=2)}
"""

        raw_retry = ollama_generate(
            ollama_url,
            model,
            repair_prompt,
            0.0,
            timeout,
        )
        design = parse_json(raw_retry)

    validate_design(design)

    design["generated_by"] = "qwen"
    design["model"] = model
    design["version"] = "2.0"

    return design


def process_file(jf, cfg, force=False):
    with jf.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if data.get("design") and not force:
        print(f"  SKIP {jf.name} (design already exists; use --force)")
        return False

    print(f"  DESIGN {jf.name} -> Qwen")

    design = design_one(data, cfg)
    data["design"] = design

    # Preserve UTF-8, Hindi and English names exactly.
    with jf.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(
        f"    layout={design['layout']} | "
        f"priority={design['visual_priority']}"
    )

    return True


def main():
    ap = argparse.ArgumentParser(
        description="Use Qwen3:8b to design BollywoodKoko carousel slides."
    )
    ap.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml",
    )
    ap.add_argument(
        "--category",
        default=None,
        help="Process only one qwen_input category, e.g. news",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Regenerate design even when design already exists",
    )
    args = ap.parse_args()

    config_path = Path(args.config)

    if not config_path.exists():
        raise SystemExit(f"Config not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    output_cfg = cfg.get("output", {})
    output_folder = output_cfg.get("folder")
    if not output_folder:
        raise SystemExit("config.yaml: output.folder is required")

    input_root = Path(output_folder) / "qwen_input"

    if not input_root.exists():
        raise SystemExit(f"qwen_input directory not found: {input_root}")

    if args.category:
        categories = [input_root / args.category]
    else:
        categories = sorted(
            p for p in input_root.iterdir()
            if p.is_dir()
        )

    print("BollywoodKoko Qwen Slide Designer")
    print("----------------------------------")
    print(f"Config : {config_path}")
    print(f"Input  : {input_root}")

    qwen_cfg = cfg.get("qwen", {})
    print(f"Ollama : {qwen_cfg.get('ollama_url', 'http://localhost:11434')}")
    print(f"Model  : {qwen_cfg.get('model', 'qwen3:8b')}")
    print("Canvas : 1080x1920 (9:16)")

    total = 0

    for category_dir in categories:
        if not category_dir.exists():
            print(f"\n[WARN] Category not found: {category_dir}")
            continue

        files = sorted(category_dir.glob("slide_*.json"))

        if not files:
            continue

        print(f"\n[{category_dir.name}] {len(files)} slide(s)")

        for jf in files:
            try:
                if process_file(jf, cfg, args.force):
                    total += 1
            except Exception as e:
                print(f"    ERROR: {e}")

    print(f"\nCompleted. Qwen designed {total} slide(s).")


if __name__ == "__main__":
    main()
