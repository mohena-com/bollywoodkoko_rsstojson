#!/usr/bin/env python3
"""
BollywoodKoko - Qwen Design Renderer v4

Consumes slide JSON produced by qwen_converter.py and qwen_slide_designer.py.

IMPORTANT:
- English + Hindi are ALWAYS rendered when story_hindi is available.
- There is NO --language switch.
- Hindi uses macOS Devanagari Sangam MN with Pillow RAQM shaping.
- Qwen's `design` object controls the visual direction/layout.
- Python controls exact coordinates, wrapping, sizing, image cropping and drawing.
- Reads input/output paths from config.yaml by default.
"""

import argparse
import io
import json
import re
from pathlib import Path

import requests
import yaml
from PIL import Image, ImageDraw, ImageFont, ImageFilter

try:
    from PIL import features
    RAQM_AVAILABLE = features.check("raqm")
except Exception:
    RAQM_AVAILABLE = False


DEFAULT_CONFIG = "config.yaml"
DEFAULT_WIDTH = 1080
DEFAULT_RATIO = "9:16"

MAC_HINDI_FONT = "/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc"
MAC_HINDI_FONT_BOLD = MAC_HINDI_FONT

# Prefer clean Noto Sans fonts for mobile readability when installed.
# Fall back to the existing macOS fonts so the renderer remains portable.
NOTO_HINDI_CANDIDATES = {
    False: [
        "/System/Library/Fonts/Supplemental/NotoSansDevanagari-Regular.ttf",
        "/Library/Fonts/NotoSansDevanagari-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansDevanagari-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    ],
    True: [
        "/System/Library/Fonts/Supplemental/NotoSansDevanagari-Bold.ttf",
        "/Library/Fonts/NotoSansDevanagari-Bold.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansDevanagari-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
    ],
}

ENGLISH_CANDIDATES = {
    False: [
        "/System/Library/Fonts/Supplemental/NotoSans-Regular.ttf",
        "/Library/Fonts/NotoSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica.ttf",
        "/System/Library/Fonts/SFNS.ttf",
    ],
    True: [
        "/System/Library/Fonts/Supplemental/NotoSans-Bold.ttf",
        "/Library/Fonts/NotoSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica Bold.ttf",
        "/System/Library/Fonts/SFNS.ttf",
    ],
}

SUPPORTED_LAYOUTS = {
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
}


def font_path(hindi=False, bold=False):
    if hindi:
        # Prefer Noto Sans Devanagari for clearer mobile rendering.
        for p in NOTO_HINDI_CANDIDATES[bool(bold)]:
            if Path(p).exists():
                return p

        # Existing macOS fallback.
        if Path(MAC_HINDI_FONT).exists():
            return MAC_HINDI_FONT

        raise FileNotFoundError(
            "No Devanagari font found. Expected Noto Sans Devanagari "
            f"or macOS font: {MAC_HINDI_FONT}"
        )

    candidates = ENGLISH_CANDIDATES[bool(bold)]
    for p in candidates:
        if Path(p).exists():
            return p

    raise FileNotFoundError("No suitable English font found.")


def make_font(size, hindi=False, bold=False):
    path = font_path(hindi=hindi, bold=bold)

    if hindi and RAQM_AVAILABLE:
        return ImageFont.truetype(
            path,
            size,
            layout_engine=ImageFont.Layout.RAQM,
        )

    return ImageFont.truetype(path, size)


def text_width(draw, text, font):
    bb = draw.textbbox((0, 0), str(text), font=font)
    return bb[2] - bb[0]


def line_height(draw, font, extra=0):
    # "Ag" is useful for Latin fonts; use representative Devanagari too.
    sample = "अग" if getattr(font, "_is_hindi", False) else "Ag"
    try:
        bb = draw.textbbox((0, 0), sample, font=font)
    except Exception:
        bb = draw.textbbox((0, 0), "Ag", font=font)
    return (bb[3] - bb[1]) + extra


def wrap_text(draw, text, font, max_width):
    """
    Wrap mixed English/Hindi text.

    Whitespace is used as the normal break point. Long unbroken tokens
    are hard-broken by character so they cannot overflow the slide.
    """
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return []

    words = text.split(" ")
    lines = []
    current = ""

    for word in words:
        candidate = word if not current else current + " " + word

        if text_width(draw, candidate, font) <= max_width:
            current = candidate
            continue

        if current:
            lines.append(current)
            current = ""

        if text_width(draw, word, font) <= max_width:
            current = word
            continue

        # Hard-wrap a very long token.
        chunk = ""
        for ch in word:
            candidate = chunk + ch
            if chunk and text_width(draw, candidate, font) > max_width:
                lines.append(chunk)
                chunk = ch
            else:
                chunk = candidate

        current = chunk

    if current:
        lines.append(current)

    return lines


def draw_wrapped(draw, text, x, y, font, max_width, fill, spacing=10,
                 max_lines=None):
    lines = wrap_text(draw, text, font, max_width)

    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        ellipsis = "..."
        while last and text_width(draw, last + ellipsis, font) > max_width:
            last = last[:-1].rstrip()
        lines[-1] = last + ellipsis

    # Font-specific bbox is safer than a fixed "Ag".
    try:
        bb = draw.textbbox((0, 0), "अग" if "अ" in str(text) else "Ag",
                           font=font)
    except Exception:
        bb = draw.textbbox((0, 0), "Ag", font=font)

    line_h = (bb[3] - bb[1]) + spacing

    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_h

    return y, len(lines), line_h


def wrapped_height(draw, text, font, max_width, spacing=10):
    lines = wrap_text(draw, text, font, max_width)
    if not lines:
        return 0
    try:
        bb = draw.textbbox((0, 0), "अग" if "अ" in str(text) else "Ag",
                           font=font)
    except Exception:
        bb = draw.textbbox((0, 0), "Ag", font=font)
    return len(lines) * ((bb[3] - bb[1]) + spacing)


def download(url):
    if not url:
        return None

    try:
        response = requests.get(
            url,
            timeout=30,
            headers={
                "User-Agent":
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X) "
                    "AppleWebKit/537.36 BollywoodKoko/1.0"
            },
        )
        response.raise_for_status()
        return Image.open(io.BytesIO(response.content)).convert("RGB")
    except Exception as exc:
        print(f"    [WARN] Image download failed: {exc}")
        return None


def load_slide_image(data):
    """
    Load the locally cached Wikipedia image.

    The Wikipedia fetcher deliberately records:
        license_verification.status = NOT_VERIFIED

    That status means the pipeline has not independently verified the
    underlying Wikimedia license; it must NOT prevent a successfully
    downloaded local image from being rendered.

    We intentionally do NOT fall back to the original remote image_url.
    """
    open_image = data.get("open_image", {}) or {}

    # Current Wikipedia fetcher structure:
    # open_image.image.local_file
    image = open_image.get("image", {}) or {}
    local_file = image.get("local_file")

    # Backward compatibility with the earlier structure:
    # open_image.local_file
    if not local_file:
        local_file = open_image.get("local_file")

    if local_file:
        path = Path(local_file)

        if path.exists() and path.is_file():
            try:
                print(
                    f"    [INFO] Using local Wikipedia image: {path}"
                )
                return Image.open(path).convert("RGB")
            except Exception as exc:
                print(
                    f"    [WARN] Local image load failed: {exc}"
                )

    return None


def cinematic_background(width, height, seed_text=""):
    """Create a deterministic, text-friendly cinematic fallback background."""
    import hashlib
    import random

    seed = int(hashlib.sha256(str(seed_text).encode("utf-8")).hexdigest()[:8], 16)
    rng = random.Random(seed)

    # Deep charcoal/navy base with a subtle warm/cool cinematic glow.
    base = Image.new("RGB", (width, height), (14, 16, 24))
    px = base.load()
    cx1 = int(width * (0.18 + rng.random() * 0.64))
    cy1 = int(height * (0.20 + rng.random() * 0.50))
    cx2 = int(width * (0.65 + rng.random() * 0.25))
    cy2 = int(height * (0.15 + rng.random() * 0.65))
    r1 = max(width, height) * 0.58
    r2 = max(width, height) * 0.46

    for y in range(height):
        for x in range(width):
            # Vertical darkening keeps lower text readable.
            vignette = 1.0 - 0.30 * ((y / max(height - 1, 1)) ** 1.7)
            d1 = ((x - cx1) ** 2 + (y - cy1) ** 2) ** 0.5 / r1
            d2 = ((x - cx2) ** 2 + (y - cy2) ** 2) ** 0.5 / r2
            g1 = max(0.0, 1.0 - d1) ** 2
            g2 = max(0.0, 1.0 - d2) ** 2
            r = int((18 + 38 * g1 + 28 * g2) * vignette)
            g = int((20 + 18 * g1 + 8 * g2) * vignette)
            b = int((31 + 48 * g1 + 20 * g2) * vignette)
            px[x, y] = (max(8, r), max(10, g), max(16, b))

    # Soft cinematic light streaks and grain, kept subtle behind typography.
    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    accent = (220, 120, 80, 42) if seed % 2 else (90, 150, 220, 42)
    for i in range(3):
        y = int(height * (0.20 + i * 0.25 + rng.uniform(-0.03, 0.03)))
        gd.line((0, y, width, y - int(height * 0.10)), fill=accent, width=max(2, int(width * 0.006)))
    glow = glow.filter(ImageFilter.GaussianBlur(max(10, int(width * 0.018))))
    base = Image.alpha_composite(base.convert("RGBA"), glow)

    # Fine grain adds texture without competing with the text.
    grain = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grain)
    step = max(3, int(width / 360))
    for y in range(0, height, step):
        for x in range(0, width, step):
            if rng.random() < 0.055:
                a = rng.randint(5, 16)
                gd.point((x, y), fill=(255, 255, 255, a))
    return Image.alpha_composite(base, grain).convert("RGB")


def cover(image, width, height, position="center"):
    if image is None:
        return Image.new("RGB", (width, height), (18, 18, 22))

    sw, sh = image.size
    target = width / height
    source = sw / sh

    if source > target:
        nw = int(sh * target)

        if position == "left":
            left = 0
        elif position == "right":
            left = sw - nw
        else:
            left = (sw - nw) // 2

        image = image.crop((left, 0, left + nw, sh))

    else:
        nh = int(sw / target)

        if position == "top":
            top = 0
        elif position == "bottom":
            top = sh - nh
        else:
            top = (sh - nh) // 2

        image = image.crop((0, top, sw, top + nh))

    return image.resize((width, height), Image.Resampling.LANCZOS)


def ratio_dimensions(ratio, width):
    a, b = ratio.split(":")
    return width, round(width * float(b) / float(a))


def format_date(date_text):
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", str(date_text or ""))
    if not m:
        return str(date_text or "")

    year, month, day = m.groups()
    months = [
        "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
        "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
    ]
    return f"{int(day):02d} {months[int(month) - 1]} {year}"


def rounded_panel(draw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(
        box,
        radius=radius,
        fill=fill,
        outline=outline,
        width=width,
    )


def draw_pill(draw, x, y, text, font, padding_x, padding_y, fill,
              text_fill="white"):
    bb = draw.textbbox((0, 0), text, font=font)
    tw = bb[2] - bb[0]
    th = bb[3] - bb[1]

    w = tw + padding_x * 2
    h = th + padding_y * 2

    rounded_panel(
        draw,
        (x, y, x + w, y + h),
        max(8, int(h * 0.35)),
        fill,
    )

    draw.text(
        (x + padding_x, y + padding_y - 2),
        text,
        font=font,
        fill=text_fill,
    )

    return w, h


def apply_image_treatment(canvas, treatment):
    treatment = str(treatment or "").lower()

    if "blur" in treatment:
        canvas = canvas.filter(ImageFilter.GaussianBlur(4))

    overlay_alpha = 115

    if "dark" in treatment:
        overlay_alpha = 135
    elif "light" in treatment:
        overlay_alpha = 70
    elif "gradient" in treatment:
        overlay_alpha = 80

    overlay = Image.new(
        "RGBA",
        canvas.size,
        (0, 0, 0, overlay_alpha),
    )
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay)

    # Strong lower gradient for text readability.
    width, height = canvas.size
    gradient = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(gradient)

    start = int(height * 0.28)
    for y in range(start, height):
        progress = (y - start) / max(1, height - start)
        alpha = int(205 * progress)
        gd.line((0, y, width, y), fill=(0, 0, 0, alpha))

    return Image.alpha_composite(canvas, gradient)


def add_top_bar(draw, s, width, scale, fonts):
    margin = int(58 * scale)
    top_y = int(48 * scale)

    category = str(
        s.get("category_label") or s.get("category") or "News"
    ).upper()

    draw_pill(
        draw,
        margin,
        top_y,
        category,
        fonts["cat"],
        int(18 * scale),
        int(9 * scale),
        fill=(0, 0, 0, 210),
    )

    number = f'{s.get("number", "")}/{s.get("total_slides", "")}'
    nb = draw.textbbox((0, 0), number, font=fonts["slide"])
    draw.text(
        (
            width - margin - (nb[2] - nb[0]),
            top_y + int(8 * scale),
        ),
        number,
        font=fonts["slide"],
        fill="white",
    )


def add_footer(draw, s, width, height, scale, fonts):
    margin = int(58 * scale)
    footer_y = height - int(68 * scale)

    footer = str(
        s.get("category_label")
        or s.get("category")
        or "Latest Bollywood News"
    )

    date = format_date(s.get("published_date", ""))
    if date:
        footer += f"  •  {date}"

    draw.text(
        (margin, footer_y),
        footer,
        font=fonts["footer"],
        fill=(235, 235, 235, 235),
    )

    brand = "BOLLYWOODKOKO"
    bw = text_width(draw, brand, fonts["footer"])
    draw.text(
        (width - margin - bw, footer_y),
        brand,
        font=fonts["footer"],
        fill="white",
    )


def get_design(data):
    design = data.get("design") or {}

    layout = str(design.get("layout") or "standard_news")
    if layout not in SUPPORTED_LAYOUTS:
        layout = "standard_news"

    return {
        "layout": layout,
        "visual_priority": str(
            design.get("visual_priority") or "image_with_gradient"
        ),
        "hero_text": str(design.get("hero_text") or ""),
        "hero_label": str(design.get("hero_label") or ""),
        "secondary_text": str(design.get("secondary_text") or ""),
        "secondary_label": str(design.get("secondary_label") or ""),
        "supporting_text": str(design.get("supporting_text") or ""),
        "emphasis_words": design.get("emphasis_words") or [],
        "image_treatment": str(
            design.get("image_treatment") or "dark cinematic"
        ),
        "image_position": str(
            design.get("image_position") or "center"
        ).lower(),
        "text_position": str(
            design.get("text_position") or "lower"
        ).lower(),
        "show_date": True,
        "show_story": True,
        "show_source_brand": True,
        "design_notes": str(design.get("design_notes") or ""),
    }


def draw_section_label(draw, text, x, y, font, scale):
    if not text:
        return y

    w = text_width(draw, text, font) + int(24 * scale)
    bb = draw.textbbox((0, 0), text, font=font)
    h = bb[3] - bb[1] + int(10 * scale)

    rounded_panel(
        draw,
        (x, y, x + w, y + h),
        int(10 * scale),
        (255, 255, 255, 35),
    )

    draw.text(
        (x + int(12 * scale), y + int(4 * scale)),
        text,
        font=font,
        fill="white",
    )

    return y + h


def render_standard(draw, s, design, fonts, width, height, scale):
    margin = int(58 * scale)
    maxw = width - 2 * margin

    headline = s.get("headline", "")
    story_en = s.get("story", "")
    story_hi = s.get("story_hindi", "")

    # The bilingual editorial block sits in the lower-middle area.
    card_top = int(height * 0.39)
    card_bottom = int(height * 0.91)

    x = margin
    y = card_top + int(28 * scale)

    y, _, _ = draw_wrapped(
        draw, headline, x, y,
        fonts["headline"], maxw, "white",
        int(15 * scale),
        max_lines=4,
    )

    y += int(22 * scale)

    y, _, _ = draw_wrapped(
        draw, story_en, x, y,
        fonts["story"], maxw, (245, 245, 245, 255),
        int(18 * scale),
        max_lines=6,
    )

    if story_hi:
        y += int(20 * scale)

        y = draw_section_label(
            draw, "हिंदी", x, y, fonts["hindi_label"], scale
        )
        y += int(10 * scale)

        draw_wrapped(
            draw, story_hi, x, y,
            fonts["hindi"], maxw, (245, 245, 245, 255),
            int(18 * scale),
            max_lines=7,
        )


def render_split(draw, s, design, fonts, width, height, scale):
    margin = int(58 * scale)
    gap = int(28 * scale)

    headline = s.get("headline", "")
    story_en = s.get("story", "")
    story_hi = s.get("story_hindi", "")

    # Large headline upper-left, editorial block lower half.
    y = int(height * 0.35)
    maxw = width - 2 * margin

    draw_wrapped(
        draw, headline,
        margin, y,
        fonts["headline_large"],
        maxw,
        "white",
        int(9 * scale),
        max_lines=3,
    )

    card_top = int(height * 0.55)
    card_bottom = int(height * 0.91)

    rounded_panel(
        draw,
        (margin, card_top, width - margin, card_bottom),
        int(24 * scale),
        (0, 0, 0, 205),
    )

    x = margin + int(24 * scale)
    inner_w = width - 2 * margin - int(48 * scale)
    y = card_top + int(22 * scale)

    y, _, _ = draw_wrapped(
        draw, story_en, x, y,
        fonts["story_small"], inner_w,
        (245, 245, 245, 255),
        int(18 * scale),
        max_lines=4,
    )

    if story_hi:
        y += int(14 * scale)
        y = draw_section_label(
            draw, "हिंदी", x, y, fonts["hindi_label"], scale
        )
        y += int(8 * scale)

        draw_wrapped(
            draw, story_hi, x, y,
            fonts["hindi_small"], inner_w,
            (245, 245, 245, 255),
            int(18 * scale),
            max_lines=5,
        )


def render_hero_title(draw, s, design, fonts, width, height, scale):
    margin = int(58 * scale)
    maxw = width - 2 * margin

    headline = s.get("headline", "")
    story_en = s.get("story", "")
    story_hi = s.get("story_hindi", "")

    # Hero headline.
    y = int(height * 0.31)

    if design["hero_label"]:
        y = draw_section_label(
            draw,
            design["hero_label"].upper(),
            margin,
            y,
            fonts["label"],
            scale,
        )
        y += int(12 * scale)

    y, _, _ = draw_wrapped(
        draw,
        headline,
        margin,
        y,
        fonts["hero_headline"],
        maxw,
        "white",
        int(10 * scale),
        max_lines=4,
    )

    # Compact bilingual editorial card.
    y += int(22 * scale)
    card_top = y
    card_bottom = int(height * 0.91)

    rounded_panel(
        draw,
        (
            margin - int(12 * scale),
            card_top,
            width - margin + int(12 * scale),
            card_bottom,
        ),
        int(24 * scale),
        (0, 0, 0, 185),
    )

    x = margin
    y = card_top + int(20 * scale)

    y, _, _ = draw_wrapped(
        draw, story_en, x, y,
        fonts["story_small"], maxw,
        (245, 245, 245, 255),
        int(18 * scale),
        max_lines=4,
    )

    if story_hi:
        y += int(18 * scale)
        y = draw_section_label(
            draw, "हिंदी", x, y,
            fonts["hindi_label"], scale
        )
        y += int(7 * scale)

        draw_wrapped(
            draw, story_hi, x, y,
            fonts["hindi_small"], maxw,
            (245, 245, 245, 255),
            int(18 * scale),
            max_lines=5,
        )


def render_announcement(draw, s, design, fonts, width, height, scale):
    margin = int(58 * scale)
    maxw = width - 2 * margin

    headline = s.get("headline", "")
    story_en = s.get("story", "")
    story_hi = s.get("story_hindi", "")

    y = int(height * 0.33)

    if design["hero_label"]:
        y = draw_section_label(
            draw,
            design["hero_label"].upper(),
            margin,
            y,
            fonts["label"],
            scale,
        )
        y += int(14 * scale)

    # Announcement layouts use a very large title.
    y, _, _ = draw_wrapped(
        draw,
        headline,
        margin,
        y,
        fonts["hero_headline"],
        maxw,
        "white",
        int(10 * scale),
        max_lines=4,
    )

    if design["hero_text"]:
        y += int(16 * scale)
        rounded_panel(
            draw,
            (
                margin,
                y,
                width - margin,
                y + int(70 * scale),
            ),
            int(18 * scale),
            (255, 255, 255, 30),
        )

        draw_wrapped(
            draw,
            design["hero_text"],
            margin + int(20 * scale),
            y + int(12 * scale),
            fonts["hero_stat"],
            maxw - int(40 * scale),
            "white",
            int(4 * scale),
            max_lines=2,
        )
        y += int(84 * scale)

    card_top = y + int(12 * scale)

    rounded_panel(
        draw,
        (
            margin,
            card_top,
            width - margin,
            int(height * 0.91),
        ),
        int(22 * scale),
        (0, 0, 0, 185),
    )

    x = margin + int(20 * scale)
    inner_w = maxw - int(40 * scale)
    y = card_top + int(18 * scale)

    y, _, _ = draw_wrapped(
        draw, story_en, x, y,
        fonts["story_small"], inner_w,
        (245, 245, 245, 255),
        int(18 * scale),
        max_lines=4,
    )

    if story_hi:
        y += int(12 * scale)
        y = draw_section_label(
            draw, "हिंदी", x, y,
            fonts["hindi_label"], scale
        )
        y += int(7 * scale)

        draw_wrapped(
            draw, story_hi, x, y,
            fonts["hindi_small"], inner_w,
            (245, 245, 245, 255),
            int(18 * scale),
            max_lines=5,
        )


def render_release_or_stat(draw, s, design, fonts, width, height, scale):
    margin = int(58 * scale)
    maxw = width - 2 * margin

    headline = s.get("headline", "")
    story_en = s.get("story", "")
    story_hi = s.get("story_hindi", "")

    # Hero fact/number supplied by Qwen.
    y = int(height * 0.32)

    if design["hero_label"]:
        y = draw_section_label(
            draw,
            design["hero_label"].upper(),
            margin,
            y,
            fonts["label"],
            scale,
        )
        y += int(10 * scale)

    hero = design["hero_text"] or headline

    y, _, _ = draw_wrapped(
        draw,
        hero,
        margin,
        y,
        fonts["hero_stat"],
        maxw,
        "white",
        int(10 * scale),
        max_lines=3,
    )

    if hero != headline:
        y += int(8 * scale)
        y, _, _ = draw_wrapped(
            draw,
            headline,
            margin,
            y,
            fonts["headline_medium"],
            maxw,
            (235, 235, 235, 255),
            int(7 * scale),
            max_lines=2,
        )

    y += int(18 * scale)

    rounded_panel(
        draw,
        (
            margin,
            y,
            width - margin,
            int(height * 0.91),
        ),
        int(22 * scale),
        (0, 0, 0, 190),
    )

    x = margin + int(20 * scale)
    inner_w = maxw - int(40 * scale)
    y += int(22 * scale)

    y, _, _ = draw_wrapped(
        draw, story_en, x, y,
        fonts["story_small"], inner_w,
        (245, 245, 245, 255),
        int(18 * scale),
        max_lines=4,
    )

    if story_hi:
        y += int(12 * scale)
        y = draw_section_label(
            draw, "हिंदी", x, y,
            fonts["hindi_label"], scale
        )
        y += int(7 * scale)

        draw_wrapped(
            draw, story_hi, x, y,
            fonts["hindi_small"], inner_w,
            (245, 245, 245, 255),
            int(18 * scale),
            max_lines=5,
        )


def render(data, output, width, height):
    if not RAQM_AVAILABLE:
        raise RuntimeError(
            "Pillow RAQM support is required for Hindi rendering. "
            "Run: python -c \"from PIL import features; "
            "print(features.check('raqm'))\""
        )

    s = data["slide"]
    design = get_design(data)

    headline = s.get("headline", "")
    story_hi = s.get("story_hindi", "")

    if not story_hi:
        print("    [WARN] story_hindi is empty; rendering English only.")

    scale = width / 1080.0
    margin = int(58 * scale)

    # Use a slightly smaller font for bilingual content to guarantee
    # comfortable fit on a 1080x1920 canvas.
    fonts = {
        "cat": make_font(int(27 * scale), bold=True),
        "slide": make_font(int(23 * scale)),
        "footer": make_font(int(21 * scale)),
        "headline": make_font(int(55 * scale), bold=True),
        "headline_medium": make_font(int(37 * scale), bold=True),
        "headline_large": make_font(int(54 * scale), bold=True),
        "hero_headline": make_font(int(57 * scale), bold=True),
        "hero_stat": make_font(int(68 * scale), bold=True),
        "story": make_font(int(41 * scale)),
        "story_small": make_font(int(41 * scale)),
        "hindi_label": make_font(int(22 * scale), hindi=True, bold=True),
        "hindi": make_font(int(40 * scale), hindi=True),
        "hindi_small": make_font(int(40 * scale), hindi=True),
        "label": make_font(int(21 * scale), bold=True),
    }

    image_position = design["image_position"]

    # Normalize common Qwen position phrases.
    if "left" in image_position:
        image_position = "left"
    elif "right" in image_position:
        image_position = "right"
    elif "top" in image_position:
        image_position = "top"
    elif "bottom" in image_position:
        image_position = "bottom"
    else:
        image_position = "center"

    # Wikipedia images are loaded ONLY from the local cache populated by
    # wikipedia_image_fetcher.py. We deliberately ignore image_url so the
    # pipeline never silently reuses the original publisher image.
    #
    # IMPORTANT:
    # license_verification.status == NOT_VERIFIED does not mean "do not
    # render". It means the pipeline has not independently verified the
    # underlying Wikimedia license.
    image = load_slide_image(data)

    if image is None and s.get("image_url"):
        print(
            "    [INFO] Remote source image ignored; "
            "no local Wikipedia image."
        )

    if image is not None:
        canvas = cover(
            image,
            width,
            height,
            position=image_position,
        ).convert("RGBA")

        canvas = apply_image_treatment(
            canvas,
            design["image_treatment"],
        )
    else:
        # No verified reusable image: use a polished deterministic cinematic
        # background instead of the original publisher image or a blank card.
        seed_text = "{}:{}:{}".format(
            s.get("category", ""),
            s.get("number", ""),
            headline,
        )
        canvas = cinematic_background(width, height, seed_text).convert("RGBA")
        print(
            "    [INFO] No local Wikipedia image; "
            "using cinematic fallback background."
        )

    draw = ImageDraw.Draw(canvas)

    # Top bar is deliberately independent of Qwen so date/slide identity
    # can never disappear.
    add_top_bar(draw, s, width, scale, fonts)

    layout = design["layout"]

    if layout in {"hero_title", "standard_news", "portrait_focus"}:
        if layout == "hero_title":
            render_hero_title(
                draw, s, design, fonts, width, height, scale
            )
        else:
            render_standard(
                draw, s, design, fonts, width, height, scale
            )

    elif layout in {"split_story", "comparison"}:
        render_split(
            draw, s, design, fonts, width, height, scale
        )

    elif layout in {
        "announcement",
        "quote_focus",
        "award",
    }:
        render_announcement(
            draw, s, design, fonts, width, height, scale
        )

    elif layout in {
        "hero_stat",
        "release_date",
        "streaming_record",
        "box_office",
    }:
        render_release_or_stat(
            draw, s, design, fonts, width, height, scale
        )

    else:
        render_standard(
            draw, s, design, fonts, width, height, scale
        )

    add_footer(
        draw,
        s,
        width,
        height,
        scale,
        fonts,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(
        output,
        "PNG",
        optimize=True,
    )


def load_config(config_path):
    with Path(config_path).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def main():
    ap = argparse.ArgumentParser(
        description="BollywoodKoko Qwen Design Renderer v4"
    )

    ap.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help="Path to config.yaml",
    )

    ap.add_argument(
        "--input",
        default=None,
        help="Override qwen_input directory",
    )

    ap.add_argument(
        "--output",
        default=None,
        help="Override painted_slides directory",
    )

    ap.add_argument(
        "--category",
        default=None,
        help="Render only one category",
    )

    ap.add_argument(
        "--ratio",
        default=DEFAULT_RATIO,
    )

    ap.add_argument(
        "--width",
        type=int,
        default=DEFAULT_WIDTH,
    )

    ap.add_argument(
        "--height",
        type=int,
        default=None,
    )

    args = ap.parse_args()

    cfg = load_config(args.config)

    output_folder = cfg.get("output", {}).get("folder")
    if not output_folder:
        raise SystemExit(
            "config.yaml: output.folder is required."
        )

    input_root = (
        Path(args.input)
        if args.input
        else Path(output_folder) / "qwen_input"
    )

    output_root = (
        Path(args.output)
        if args.output
        else Path(output_folder) / "painted_slides"
    )

    if args.height:
        width, height = args.width, args.height
    else:
        width, height = ratio_dimensions(
            args.ratio,
            args.width,
        )

    if not input_root.exists():
        raise SystemExit(
            f"Input directory not found: {input_root}"
        )

    categories = (
        [input_root / args.category]
        if args.category
        else sorted(
            p for p in input_root.iterdir()
            if p.is_dir()
        )
    )

    print("BollywoodKoko Qwen Design Renderer v4")
    print("--------------------------------------")
    print(f"Config : {args.config}")
    print(f"Input  : {input_root}")
    print(f"Output : {output_root}")
    print(f"Canvas : {width}x{height} ({args.ratio})")
    print(f"RAQM   : {RAQM_AVAILABLE}")
    print(f"Hindi  : {font_path(hindi=True)}")
    print(f"English: {font_path(hindi=False)}")
    print("Lang   : English + Hindi (always)")
    print("Design : Qwen creative direction")

    if not RAQM_AVAILABLE:
        raise SystemExit(
            "\nERROR: Pillow was installed without RAQM support.\n"
            "Expected: raqm=True"
        )

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

        cat_out = output_root / category_dir.name
        cat_out.mkdir(parents=True, exist_ok=True)

        print(
            f"\n[{category_dir.name}] "
            f"{len(files)} slide(s)"
        )

        for i, jf in enumerate(files, 1):
            try:
                with jf.open("r", encoding="utf-8") as f:
                    data = json.load(f)

                slide = data.get("slide", {})
                num = slide.get("number", i)

                target = cat_out / (
                    f"slide_{int(num):03d}.png"
                )

                design = get_design(data)

                print(
                    f"  [{i}/{len(files)}] "
                    f"{jf.name} -> {target.name} | "
                    f"layout={design['layout']} | "
                    f"priority={design['visual_priority']}"
                )

                render(
                    data,
                    target,
                    width,
                    height,
                )

                total += 1

            except Exception as exc:
                print(
                    f"    [ERROR] {jf.name}: "
                    f"{type(exc).__name__}: {exc}"
                )

    print(
        f"\nDone. Rendered {total} slide(s) into "
        f"{output_root}"
    )


if __name__ == "__main__":
    main()
