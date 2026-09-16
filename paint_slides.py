#!/usr/bin/env python3
import argparse
import io
import json
import re
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter


# Instagram portrait default: 1080x1920 (9:16).
# Use --ratio 9:4 if you literally want 9:4.
DEFAULT_INPUT = "../OP_JSON/qwen_input"
DEFAULT_OUTPUT = "../OP_JSON/painted_slides"
DEFAULT_WIDTH = 1080
DEFAULT_RATIO = "9:16"


def font_path(hindi=False, bold=False):
    candidates = []
    if hindi:
        candidates += [
            "/System/Library/Fonts/Supplemental/NotoSansDevanagari-Regular.ttf",
            "/System/Library/Fonts/Supplemental/NotoSansDevanagari-Bold.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansDevanagari-Regular.ttf",
        ]
    if bold:
        candidates += [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Helvetica Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
    candidates += [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    raise FileNotFoundError("No suitable font found.")


def make_font(size, hindi=False, bold=False):
    return ImageFont.truetype(font_path(hindi, bold), size)


def wrap(draw, text, fnt, max_width):
    words = re.sub(r"\s+", " ", str(text or "")).strip().split()
    lines, current = [], ""
    for word in words:
        test = word if not current else current + " " + word
        if draw.textbbox((0, 0), test, font=fnt)[2] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def draw_wrapped(draw, text, x, y, fnt, max_width, fill, spacing=12):
    lines = wrap(draw, text, fnt, max_width)
    box = draw.textbbox((0, 0), "Ag", font=fnt)
    line_h = box[3] - box[1] + spacing
    for line in lines:
        draw.text((x, y), line, font=fnt, fill=fill)
        y += line_h
    return y


def download(url):
    if not url:
        return None
    try:
        r = requests.get(
            url,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 BollywoodKoko/1.0"},
        )
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception as e:
        print(f"  [WARN] Image download failed: {e}")
        return None


def cover(image, width, height):
    if image is None:
        return Image.new("RGB", (width, height), (18, 18, 22))

    sw, sh = image.size
    target = width / height
    source = sw / sh

    if source > target:
        nw = int(sh * target)
        left = (sw - nw) // 2
        image = image.crop((left, 0, left + nw, sh))
    else:
        nh = int(sw / target)
        top = (sh - nh) // 2
        image = image.crop((0, top, sw, top + nh))

    return image.resize((width, height), Image.Resampling.LANCZOS)


def ratio_dimensions(ratio, width):
    a, b = ratio.split(":")
    return width, round(width * float(b) / float(a))


def render(data, output, width, height, language):
    s = data["slide"]

    headline = s.get("headline", "")
    story = s.get("story_hindi", "") if language == "hindi" else s.get("story", "")
    category = s.get("category_label") or s.get("category") or "News"
    date = s.get("published_date", "")
    number = f'{s.get("number", "")}/{s.get("total_slides", "")}'
    image = download(s.get("image_url", ""))

    canvas = cover(image, width, height).filter(ImageFilter.GaussianBlur(4)).convert("RGBA")

    # Dark overlay + strong bottom gradient.
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 125))
    canvas = Image.alpha_composite(canvas, overlay)

    grad = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(int(height * .25), height):
        alpha = int(210 * (y - height * .25) / (height * .75))
        gd.line((0, y, width, y), fill=(0, 0, 0, alpha))
    canvas = Image.alpha_composite(canvas, grad)

    draw = ImageDraw.Draw(canvas)
    scale = width / 1080
    margin = int(65 * scale)
    maxw = width - 2 * margin

    cat_font = make_font(int(28 * scale), bold=True)
    head_font = make_font(int(58 * scale), hindi=(language == "hindi"), bold=True)
    story_font = make_font(int(34 * scale), hindi=(language == "hindi"))
    meta_font = make_font(int(24 * scale))

    # Category badge.
    badge = category.upper()
    bb = draw.textbbox((0, 0), badge, font=cat_font)
    bw, bh = bb[2] - bb[0] + 38 * scale, bb[3] - bb[1] + 20 * scale
    draw.rounded_rectangle(
        (margin, 55 * scale, margin + bw, 55 * scale + bh),
        radius=int(16 * scale),
        fill=(0, 0, 0, 185),
    )
    draw.text((margin + 19 * scale, 65 * scale), badge, font=cat_font, fill="white")

    # Slide number.
    nb = draw.textbbox((0, 0), number, font=meta_font)
    draw.text((width - margin - (nb[2] - nb[0]), 70 * scale), number, font=meta_font, fill="white")

    # Headline.
    y = int(height * .47)
    draw.rounded_rectangle(
        (margin - 15 * scale, y - 25 * scale, width - margin + 15 * scale, y + 285 * scale),
        radius=int(24 * scale),
        fill=(0, 0, 0, 120),
    )
    y = draw_wrapped(draw, headline, margin, y, head_font, maxw, "white", int(14 * scale))

    # Story.
    y += int(42 * scale)
    y = draw_wrapped(draw, story, margin, y, story_font, maxw, (245, 245, 245, 255), int(13 * scale))

    # Footer.
    footer = "Latest Bollywood News"
    if date:
        footer += f"  •  {date}"
    draw.text((margin, height - 65 * scale), footer, font=meta_font, fill=(230, 230, 230, 230))

    brand = "BOLLYWOODKOKO"
    bw = draw.textbbox((0, 0), brand, font=meta_font)[2]
    draw.text((width - margin - bw, height - 65 * scale), brand, font=meta_font, fill="white")

    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output, "PNG", optimize=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--ratio", default=DEFAULT_RATIO)
    ap.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    ap.add_argument("--height", type=int, default=None)
    ap.add_argument("--language", choices=["english", "hindi"], default="english")
    ap.add_argument("--category", default=None)
    args = ap.parse_args()

    inp = Path(args.input)
    out = Path(args.output)

    if args.height:
        width, height = args.width, args.height
    else:
        width, height = ratio_dimensions(args.ratio, args.width)

    categories = [inp / args.category] if args.category else sorted(p for p in inp.iterdir() if p.is_dir())

    print(f"Input : {inp}")
    print(f"Output: {out}")
    print(f"Canvas: {width}x{height} ({args.ratio})")
    print(f"Lang  : {args.language}")

    total = 0
    for cat in categories:
        files = sorted(cat.glob("slide_*.json"))
        if not files:
            continue

        cat_out = out / cat.name
        print(f"\n{cat.name}: {len(files)} slides")

        for i, jf in enumerate(files, 1):
            try:
                with jf.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                num = data["slide"].get("number", i)
                target = cat_out / f"slide_{int(num):03d}.png"
                print(f"  [{i}/{len(files)}] {jf.name} -> {target.name}")
                render(data, target, width, height, args.language)
                total += 1
            except Exception as e:
                print(f"  [ERROR] {jf}: {e}")

    print(f"\nDone. Rendered {total} slides into {out}")


if __name__ == "__main__":
    main()
