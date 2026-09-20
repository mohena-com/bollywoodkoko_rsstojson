#!/usr/bin/env python3
"""
Create a reel by combining generated slide PNGs with a randomly selected
background music file from a music folder.

Usage:
    python create_reel.py
    python create_reel.py --slides-dir ".../painted_slides" --music-dir "/Volumes/.../music" --output ".../reel.mp4"
"""

import argparse
import random
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


DEFAULT_MUSIC_DIR = Path("/Volumes/Extreme SSD/webmaster-ai/POJO_PROJECT/data/music")
DEFAULT_OUTPUT = "reel.mp4"
DEFAULT_DURATION_PER_SLIDE = 5.0


def load_config(config_path: Path):
    if yaml is None or not config_path.exists():
        return {}

    with config_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_slides_dir(config):
    output_cfg = config.get("output", {}) if isinstance(config, dict) else {}
    output_folder = output_cfg.get("folder")
    if output_folder:
        return Path(output_folder) / "painted_slides"
    return Path("painted_slides")


def get_music_dir(config):
    # Music location is controlled by config.yaml.
    reel_cfg = config.get("reel", {}) if isinstance(config, dict) else {}
    music_folder = reel_cfg.get("music_folder")
    if not music_folder:
        raise ValueError("config.yaml: reel.music_folder is required.")
    return Path(music_folder)


def get_category_slides_dir(config, category: str):
    base = get_slides_dir(config)
    return base / category


def get_category_reel_output(config, category: str):
    output_cfg = config.get("output", {}) if isinstance(config, dict) else {}
    output_folder = output_cfg.get("folder")
    if not output_folder:
        raise ValueError("config.yaml: output.folder is required.")
    return Path(output_folder) / "reels" / category / f"{category}_reel.mp4"


def get_categories(slides_root: Path):
    """Return category directories containing slide_*.png files."""
    if not slides_root.exists():
        return []

    categories = []
    for p in sorted(slides_root.iterdir()):
        if not p.is_dir():
            continue
        if any(
            f.is_file() and f.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            and f.name.startswith("slide_")
            for f in p.iterdir()
        ):
            categories.append(p)
    return categories


def get_audio_files(music_dir: Path):
    valid_exts = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
    files = [
        p for p in music_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in valid_exts
    ]
    return sorted(files)


def get_slide_files(slides_dir: Path):
    """Return slides only from the requested category directory."""
    valid_exts = {".png", ".jpg", ".jpeg", ".webp"}
    files = [
        p for p in slides_dir.glob("slide_*")
        if p.is_file() and p.suffix.lower() in valid_exts
    ]
    return sorted(files)


def choose_random_music(music_dir: Path):
    files = get_audio_files(music_dir)
    if not files:
        raise FileNotFoundError(
            f"No music files found in: {music_dir}. Add .mp3/.wav/.m4a/.aac/.flac/.ogg files there."
        )
    return random.choice(files)


def create_concat_list(slide_files, duration_per_slide: float, list_path: Path):
    with list_path.open("w", encoding="utf-8") as f:
        for slide in slide_files:
            f.write(f"file '{slide.as_posix()}'\n")
            f.write(f"duration {duration_per_slide}\n")

        # Add the last file once more without duration to keep the final frame visible.
        if slide_files:
            f.write(f"file '{slide_files[-1].as_posix()}'\n")


def build_reel(slides_dir: Path, music_dir: Path, output_path: Path, duration_per_slide: float = 5.0):
    if not slides_dir.exists():
        raise FileNotFoundError(f"Slides directory does not exist: {slides_dir}")

    slide_files = get_slide_files(slides_dir)
    if not slide_files:
        raise FileNotFoundError(f"No slide images found in: {slides_dir}")

    music_file = choose_random_music(music_dir)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found in PATH. Please install ffmpeg first.")

    list_path = output_path.with_suffix(".txt")
    create_concat_list(slide_files, duration_per_slide, list_path)

    temp_output = output_path.with_suffix(".tmp.mp4")

    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-i",
        str(music_file),
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(temp_output),
    ]

    print(f"Music folder : {music_dir}")
    print(f"Selected music: {music_file}")
    print(f"Slides       : {len(slide_files)}")
    print(f"Duration/slide: {duration_per_slide}s")
    print(f"Creating reel: {output_path}")

    subprocess.run(cmd, check=True)

    # Move temp output to final. If ffmpeg wrote the final file directly, this keeps the name simple.
    if temp_output.exists():
        temp_output.replace(output_path)

    list_path.unlink(missing_ok=True)

    print(f"Reel created successfully: {output_path}")


def parse_args():
    ap = argparse.ArgumentParser(description="Create a reel from generated slide images and random background music.")
    ap.add_argument("--config", type=Path, default=Path("config.yaml"), help="Path to config.yaml")
    ap.add_argument("--slides-dir", type=Path, default=None, help="Directory containing generated slide images")
    ap.add_argument("--music-dir", type=Path, default=None, help="Directory containing music files")
    ap.add_argument("--output", type=Path, default=None, help="Output MP4 path")
    ap.add_argument("--duration-per-slide", type=float, default=None, help="Seconds to display each slide")
    ap.add_argument("--category", type=str, default=None,
                    help="Create only this category, e.g. news. If omitted, create reels for all categories.")
    return ap.parse_args()


def main():
    args = parse_args()
    config = load_config(args.config)

    slides_root = get_slides_dir(config)
    music_dir = args.music_dir or get_music_dir(config)

    duration = (
        args.duration_per_slide
        if args.duration_per_slide is not None
        else float(
            (config.get("reel", {}) if isinstance(config, dict) else {})
            .get("duration_per_slide", DEFAULT_DURATION_PER_SLIDE)
        )
    )

    try:
        # Explicit category: create only that category's reel.
        if args.category:
            category = args.category.strip()
            slides_dir = args.slides_dir or get_category_slides_dir(
                config, category
            )
            output_path = args.output or get_category_reel_output(
                config, category
            )

            build_reel(
                slides_dir,
                music_dir,
                Path(output_path),
                duration_per_slide=duration,
            )
            return

        # No category: automatically create a reel for every category
        # under painted_slides/.
        if args.slides_dir:
            # If the user explicitly supplies --slides-dir without --category,
            # preserve the old single-reel behavior.
            output_path = args.output or (
                Path(str(config.get("output", {}).get("folder", ".")))
                / DEFAULT_OUTPUT
            )
            build_reel(
                args.slides_dir,
                music_dir,
                Path(output_path),
                duration_per_slide=duration,
            )
            return

        categories = get_categories(slides_root)

        if not categories:
            raise FileNotFoundError(
                f"No category slide folders found in: {slides_root}"
            )

        print(f"Found {len(categories)} category folder(s) in: {slides_root}")

        success = 0
        failures = 0

        for category_dir in categories:
            category = category_dir.name
            output_path = get_category_reel_output(config, category)

            print(f"\n=== Category: {category} ===")

            try:
                build_reel(
                    category_dir,
                    music_dir,
                    output_path,
                    duration_per_slide=duration,
                )
                success += 1
            except Exception as exc:
                failures += 1
                print(
                    f"[ERROR] Category '{category}' failed: {exc}",
                    file=sys.stderr,
                )

        print(
            f"\nCompleted: {success} reel(s) created, "
            f"{failures} failed."
        )

        if failures:
            raise SystemExit(1)

    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
