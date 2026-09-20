#!/usr/bin/env python3
"""
BollywoodKoko - Qwen Slide Designer v3

Qwen3:8b is used ONLY as the creative director.

Qwen decides:
    - story-specific layout
    - visual priority
    - hero text/label
    - image treatment and position
    - text hierarchy
    - emphasis words
    - English/Hindi presentation
    - date/brand visibility

Python/paint_slides.py decides:
    - exact pixel coordinates
    - wrapping
    - fonts
    - image cropping
    - drawing/rendering

This deliberately does NOT ask Qwen to generate pixel coordinates or
renderer "elements". That was causing unreliable JSON and unnecessary
generation time with qwen3:8b.
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
You are the Creative Director for BollywoodKoko.

Your task is to design ONE Instagram Bollywood/news carousel slide.

You are NOT the image renderer.
You are NOT asked to provide pixel coordinates.
You must make the visual/editorial design decision, and return a small
machine-readable design specification that a Python renderer will execute.

CANVAS
------
1080 x 1920 pixels
9:16 Instagram portrait.

EDITORIAL RULES
---------------
1. Use ONLY facts from the supplied slide/source.
2. Never invent facts, numbers, quotes, rankings, awards, dates, people,
   events or visual claims.
3. The supplied English headline and English story are authoritative.
4. The supplied Hindi story is authoritative for the Hindi version.
5. Hindi must remain natural Devanagari.
6. NEVER translate/transliterate proper names in Hindi.
   Keep movie, series, show, song, character, platform, company and person
   names in their original English spelling.
   Examples:
   Matka King
   Prime Video
   Brij Bhatti
   Ek Aur Baazi
   Vijay Varma
7. Every slide MUST show the news date.
8. Every slide MUST show the slide number.
9. Bilingual presentation is required when story_hindi is available:
   English first, Hindi second.
10. Do not duplicate information unnecessarily.

LAYOUT OPTIONS
--------------
Choose exactly ONE:

hero_stat
    A major number/statistic is the dominant visual.

hero_title
    The headline itself is the dominant visual.

split_story
    Image and editorial text occupy clearly separated zones.

portrait_focus
    A supplied person's portrait/image is the dominant visual.

announcement
    Announcement, sequel, confirmation, casting or major update.

release_date
    A release date is the key fact.

award
    Award, recognition or achievement is the key fact.

streaming_record
    Streaming views, rankings or platform performance is central.

box_office
    Box-office/collection/opening figures are central.

quote_focus
    A supplied quote/statement is central.

comparison
    Two or more entities are explicitly compared in the supplied content.

standard_news
    Normal news story with no special dominant visual feature.

DESIGN PRINCIPLES
-----------------
- Make different stories look meaningfully different.
- Do not use hero_stat unless the story contains a strong statistic.
- Do not use comparison unless an actual comparison exists.
- Do not use award unless an award/recognition exists.
- Do not use box_office unless box-office information exists.
- Do not use release_date unless release information exists.
- Prefer story-specific visual hierarchy over a fixed template.
- Keep the design bold, cinematic, modern and Bollywood-oriented.
- Maintain strong contrast and readability.
- Avoid clutter.
- Avoid tiny text.
- The image supplied by image_url is the primary visual asset.
- You may recommend background, panel, portrait, crop, darkening or blur
  treatment, but do not invent an image subject.

TEXT HIERARCHY
--------------
hero_text:
    A short, high-impact phrase taken from the supplied editorial content.
    Do NOT invent words that change factual meaning.

hero_label:
    Short factual label, or empty string.

secondary_text:
    Optional secondary fact taken from the supplied content.

secondary_label:
    Short factual label, or empty string.

supporting_text:
    Optional short supporting fact. Do not rewrite the whole article.

emphasis_words:
    Only words/phrases actually appearing in supplied content.

IMAGE
-----
image_treatment examples:
    full_bleed_darkened
    full_bleed_blur
    portrait_panel
    top_image
    bottom_image
    split_image
    clean_image
    image_with_gradient

image_position examples:
    background
    top
    bottom
    left
    right
    center

text_position examples:
    upper
    lower
    left
    right
    center
    split

TYPOGRAPHY
----------
headline_style:
    size: large | medium | small
    weight: bold | regular
    alignment: left | center | right

story_style:
    size: medium | small
    alignment: left | center | right
    english_then_hindi: true

OUTPUT
------
Return ONLY one valid JSON object.
No Markdown.
No explanation.
No comments.
No trailing commas.
No text before or after the JSON.

Keep the response compact.
"""


def strip_thinking(text):
    return re.sub(
        r"<think>.*?</think>",
        "",
        str(text or ""),
        flags=re.DOTALL | re.IGNORECASE,
    ).strip()


def extract_balanced_json(text):
    text = strip_thinking(text)
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

    raise ValueError("Incomplete JSON object from Qwen.")


def parse_json(text):
    cleaned = strip_thinking(text)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    candidate = extract_balanced_json(cleaned)

    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        context = candidate[max(0, e.pos - 180):e.pos + 180]
        raise ValueError(
            f"Malformed JSON at character {e.pos}: {context!r}"
        ) from e


def ollama_generate(base_url, model, prompt, temperature, timeout):
    url = base_url.rstrip("/") + "/api/generate"

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "think": False,
        "options": {
            "temperature": temperature,
            "num_predict": 650,
        },
    }

    response = requests.post(url, json=payload, timeout=timeout)
    response.raise_for_status()

    result = response.json()
    return result.get("response", "")


def validate_design(design):
    if not isinstance(design, dict):
        raise ValueError("Design must be a JSON object.")

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
        "design_notes",
    ]

    missing = [x for x in required if x not in design]
    if missing:
        raise ValueError(f"Missing fields: {', '.join(missing)}")

    if design["layout"] not in SUPPORTED_LAYOUTS:
        raise ValueError(
            f"Unsupported layout: {design['layout']}"
        )

    if not isinstance(design["emphasis_words"], list):
        design["emphasis_words"] = []

    if not isinstance(design["headline_style"], dict):
        design["headline_style"] = {
            "size": "large",
            "weight": "bold",
            "alignment": "left",
        }

    if not isinstance(design["story_style"], dict):
        design["story_style"] = {
            "size": "small",
            "alignment": "left",
            "english_then_hindi": True,
        }

    # Force non-negotiable production requirements.
    design["show_date"] = True
    design["show_story"] = True
    design["show_source_brand"] = True

    return design


def build_prompt(data):
    editorial = {
        "slide": data.get("slide", {}),
        "source": data.get("source", {}),
    }

    return f"""
{SYSTEM_PROMPT}

SUPPLIED EDITORIAL CONTENT
==========================
{json.dumps(editorial, ensure_ascii=False, indent=2)}

Return this exact schema shape and nothing else:

{{
  "layout": "one supported layout",
  "story_type": "short factual classification",
  "visual_priority": "what should attract attention first",
  "hero_text": "short phrase from supplied content or empty string",
  "hero_label": "short factual label or empty string",
  "secondary_text": "short supplied fact or empty string",
  "secondary_label": "short factual label or empty string",
  "supporting_text": "short supplied supporting fact or empty string",
  "emphasis_words": [],
  "image_treatment": "one treatment",
  "image_position": "one position",
  "text_position": "one position",
  "headline_style": {{
    "size": "large",
    "weight": "bold",
    "alignment": "left"
  }},
  "story_style": {{
    "size": "small",
    "alignment": "left",
    "english_then_hindi": true
  }},
  "show_story": true,
  "show_date": true,
  "show_source_brand": true,
  "design_notes": "one short sentence"
}}

IMPORTANT:
- Do not return an "elements" array.
- Do not return x/y/width/height coordinates.
- Do not redesign the editorial wording.
- Preserve English proper names inside Hindi.
- Return JSON only.
"""


def diagnostics(raw, stage):
    raw = str(raw or "")
    print(
        f"    [{stage}] response={len(raw)} chars, "
        f"{len(raw.split())} words"
    )


def design_one(data, cfg, debug=False):
    qwen_cfg = cfg.get("qwen", {})

    base_url = qwen_cfg.get(
        "ollama_url",
        "http://localhost:11434",
    )
    model = qwen_cfg.get("model", "qwen3:8b")
    temperature = float(qwen_cfg.get("temperature", 0.2))
    timeout = int(qwen_cfg.get("timeout", 180))

    prompt = build_prompt(data)

    raw = ollama_generate(
        base_url,
        model,
        prompt,
        temperature,
        timeout,
    )

    if debug:
        diagnostics(raw, "primary")

    try:
        design = parse_json(raw)
    except ValueError as first_error:
        print(
            "    WARN: invalid Qwen JSON; "
            "performing one repair-only retry..."
        )

        # IMPORTANT: retry repairs syntax/schema only. It must not make a new
        # creative decision.
        repair_prompt = f"""
You are repairing a JSON response.

Do NOT redesign the slide.
Do NOT change the chosen layout or creative decisions.

Return ONLY valid JSON.
No Markdown.
No explanation.
No comments.
No trailing commas.

The intended design was:

{raw}

Repair it into this exact compact schema:

{{
  "layout": "one supported layout",
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
  "headline_style": {{
    "size": "large|medium|small",
    "weight": "bold|regular",
    "alignment": "left|center|right"
  }},
  "story_style": {{
    "size": "medium|small",
    "alignment": "left|center|right",
    "english_then_hindi": true
  }},
  "show_story": true,
  "show_date": true,
  "show_source_brand": true,
  "design_notes": "one short sentence"
}}

Do not add elements or coordinates.
"""

        raw_retry = ollama_generate(
            base_url,
            model,
            repair_prompt,
            0.0,
            timeout,
        )

        if debug:
            diagnostics(raw_retry, "repair")

        try:
            design = parse_json(raw_retry)
        except ValueError as repair_error:
            raise ValueError(
                f"Primary Qwen JSON failed: {first_error}; "
                f"repair also failed: {repair_error}"
            ) from repair_error

    validate_design(design)
    return design


def process_file(jf, cfg, force=False, debug=False):
    with jf.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if data.get("design") and not force:
        print(
            f"  SKIP {jf.name} "
            "(design exists; use --force)"
        )
        return False

    print(f"  DESIGN {jf.name} -> Qwen")

    design = design_one(
        data,
        cfg,
        debug=debug,
    )

    data["design"] = {
        "generated_by": "qwen",
        "model": cfg.get("qwen", {}).get("model", "qwen3:8b"),
        "version": "3.0",
        **design,
    }

    with jf.open("w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(
        f"    layout={design['layout']} | "
        f"priority={design['visual_priority']}"
    )

    return True


def main():
    ap = argparse.ArgumentParser(
        description="BollywoodKoko Qwen Slide Designer v3"
    )

    ap.add_argument(
        "--config",
        default="config.yaml",
    )

    ap.add_argument(
        "--category",
        default=None,
    )

    ap.add_argument(
        "--force",
        action="store_true",
    )

    ap.add_argument(
        "--debug",
        action="store_true",
        help="Print Qwen response diagnostics.",
    )

    args = ap.parse_args()

    config_path = Path(args.config)

    if not config_path.exists():
        raise SystemExit(
            f"Config not found: {config_path}"
        )

    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    output_folder = cfg.get("output", {}).get("folder")

    if not output_folder:
        raise SystemExit(
            "config.yaml: output.folder is required"
        )

    input_root = Path(output_folder) / "qwen_input"

    if not input_root.exists():
        raise SystemExit(
            f"qwen_input not found: {input_root}"
        )

    if args.category:
        categories = [input_root / args.category]
    else:
        categories = sorted(
            p for p in input_root.iterdir()
            if p.is_dir()
        )

    qwen_cfg = cfg.get("qwen", {})

    print("BollywoodKoko Qwen Slide Designer v3")
    print("-------------------------------------")
    print(f"Config : {config_path}")
    print(f"Input  : {input_root}")
    print(
        f"Ollama : "
        f"{qwen_cfg.get('ollama_url', 'http://localhost:11434')}"
    )
    print(
        f"Model  : "
        f"{qwen_cfg.get('model', 'qwen3:8b')}"
    )
    print("Canvas : 1080x1920 (9:16)")
    print("Mode   : Qwen creative direction only")

    total = 0

    for category_dir in categories:
        if not category_dir.exists():
            print(
                f"\n[WARN] Category not found: "
                f"{category_dir}"
            )
            continue

        files = sorted(
            category_dir.glob("slide_*.json")
        )

        if not files:
            continue

        print(
            f"\n[{category_dir.name}] "
            f"{len(files)} slide(s)"
        )

        for jf in files:
            try:
                if process_file(
                    jf,
                    cfg,
                    force=args.force,
                    debug=args.debug,
                ):
                    total += 1
            except Exception as e:
                print(
                    f"    ERROR: {e}"
                )

    print(
        f"\nCompleted. "
        f"Qwen designed {total} slide(s)."
    )


if __name__ == "__main__":
    main()
