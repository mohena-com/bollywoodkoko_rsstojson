#!/usr/bin/env python3

import argparse
import json
import random
import subprocess
import tempfile
from pathlib import Path

import requests
import yaml


DEFAULT_TTS_URL = "http://127.0.0.1:8090/tts"
DEFAULT_VOICE = "divya_page3"
DEFAULT_DURATION = 6.0


def load_config():
    config_path = Path(__file__).resolve().parent / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_paths(config):
    output_folder = Path(config["output"]["folder"])
    slides_root = output_folder / "painted_slides"
    reels_root = output_folder / "reels"
    music_folder = Path(config["reel"]["music_folder"])
    return output_folder, slides_root, reels_root, music_folder


def find_slide_json(output_folder, category, slide_number):
    candidates = [
        output_folder / "qwen_input" / category / f"slide_{slide_number:03d}.json",
        output_folder / "qwen_input" / category / f"slide_{slide_number}.json",
    ]

    for path in candidates:
        if path.exists():
            return path

    return None


def get_hindi_text(json_path):
    if not json_path:
        return ""

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        slide = data.get("slide", {})
        text = (
            slide.get("story_hindi")
            or slide.get("summary_hindi")
            or data.get("summary_hindi")
            or ""
        ).strip()

        # Read only the first line of the Hindi description for the Reel voiceover.
        return text.splitlines()[0].strip() if text.splitlines() else ""
    except Exception as exc:
        print(f"WARNING: Could not read {json_path}: {exc}")
        return ""


def generate_tts(text, voice, tts_url, output_wav):
    if not text:
        return None, 0.0

    response = requests.post(
        tts_url,
        json={"text": text, "voice": voice},
        timeout=300,
    )
    response.raise_for_status()

    result = response.json()

    audio_url = result.get("audio_url")
    duration = float(result.get("duration", 0.0))

    if not audio_url:
        raise RuntimeError(f"TTS response has no audio_url: {result}")

    if audio_url.startswith("http://") or audio_url.startswith("https://"):
        url = audio_url
    else:
        url = tts_url.rstrip("/").rsplit("/", 1)[0] + audio_url

    audio_response = requests.get(url, timeout=120)
    audio_response.raise_for_status()
    output_wav.write_bytes(audio_response.content)

    return output_wav, duration


def find_music(music_folder):
    extensions = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
    tracks = [
        p for p in music_folder.rglob("*")
        if p.is_file() and p.suffix.lower() in extensions
    ]

    if not tracks:
        raise FileNotFoundError(f"No music files found in {music_folder}")

    return tracks


def run_ffmpeg(image, music, voice, output, duration):
    cmd = [
        "ffmpeg",
        "-y",
        "-loop", "1",
        "-i", str(image),
        "-i", str(music),
    ]

    if voice:
        cmd += ["-i", str(voice)]

    cmd += [
        "-t", f"{duration:.2f}",
        "-map", "0:v:0",
        "-map", "1:a:0",
    ]

    if voice:
        cmd += ["-map", "2:a:0"]

    cmd += [
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        "-c:a", "aac",
        "-b:a", "192k",
        "-af", "afade=t=out:st={:.2f}:d=0.5".format(max(0.0, duration - 0.5)),
        "-shortest",
        "-movflags", "+faststart",
        str(output),
    ]

    # If TTS exists, mix voice + music instead of using only one audio stream.
    if voice:
        # Rebuild with an audio filter graph so voice is foreground and music is lower.
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(image),
            "-stream_loop", "-1", "-i", str(music),
            "-i", str(voice),
            "-t", f"{duration:.2f}",
            "-filter_complex",
            (
                # Music is the background signal and the Hindi voice is the
                # sidechain. When the voice is present, FFmpeg compresses
                # the music heavily. The voice itself is kept bold/full.
                "[1:a]volume=0.45[music];"
                "[2:a]volume=1.5,asplit=2[voice_sc][voice_mix];"
                "[music][voice_sc]sidechaincompress="
                "threshold=0.03:"
                "ratio=20:"
                "attack=20:"
                "release=700:"
                "makeup=1:"
                "mix=1[ducked_music];"
                "[ducked_music][voice_mix]amix=inputs=2:"
                "duration=longest:"
                "dropout_transition=0:"
                "normalize=0[aout]"
            ),
            "-map", "0:v:0",
            "-map", "[aout]",
            "-c:v", "libx264",
            "-tune", "stillimage",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            str(output),
        ]

    subprocess.run(cmd, check=True)


def process_category(category, config, tts_url, voice, requested_duration):
    output_folder, slides_root, reels_root, music_folder = get_paths(config)

    slides_dir = slides_root / category
    reels_dir = reels_root / category

    reels_dir.mkdir(parents=True, exist_ok=True)

    slides = sorted(slides_dir.glob("slide_*.png"))
    if not slides:
        raise FileNotFoundError(f"No slides found in {slides_dir}")

    music_tracks = find_music(music_folder)

    print("=" * 60)
    print(f"Category : {category}")
    print(f"Slides   : {len(slides)}")
    print(f"Reels    : {reels_dir}")
    print("=" * 60)

    for image in slides:
        slide_number = int(image.stem.split("_")[-1])
        output = reels_dir / f"slide_{slide_number:03d}_reel.mp4"

        json_path = find_slide_json(output_folder, category, slide_number)
        hindi_text = get_hindi_text(json_path)

        music = random.choice(music_tracks)

        with tempfile.TemporaryDirectory(prefix="bollywoodkoko_tts_") as tmp:
            voice_file = None
            voice_duration = 0.0

            if hindi_text:
                voice_file = Path(tmp) / f"slide_{slide_number:03d}.wav"
                print(f"[{slide_number:03d}] Generating Hindi voice...")
                voice_file, voice_duration = generate_tts(
                    hindi_text,
                    voice,
                    tts_url,
                    voice_file,
                )

            duration = max(
                requested_duration,
                voice_duration + 0.5 if voice_duration else requested_duration,
            )

            print(
                f"[{slide_number:03d}] "
                f"duration={duration:.2f}s "
                f"music={music.name}"
            )

            run_ffmpeg(
                image=image,
                music=music,
                voice=voice_file,
                output=output,
                duration=duration,
            )

        print(f"      -> {output}")


def main():
    parser = argparse.ArgumentParser(
        description="Create one Hindi-voiced Reel per painted slide."
    )
    parser.add_argument("--category", default="news")
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION)
    parser.add_argument("--tts-url", default=DEFAULT_TTS_URL)
    parser.add_argument("--voice", default=DEFAULT_VOICE)

    args = parser.parse_args()

    config = load_config()

    process_category(
        category=args.category,
        config=config,
        tts_url=args.tts_url,
        voice=args.voice,
        requested_duration=args.duration,
    )


if __name__ == "__main__":
    main()
