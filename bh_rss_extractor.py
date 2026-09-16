#!/usr/bin/env python3

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup

CONFIG_FILE = "config.yaml"


def load_config(config_file):
    with open(config_file, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def clean_html(text):
    if not text:
        return ""

    soup = BeautifulSoup(text, "html.parser")

    for tag in soup(["script", "style"]):
        tag.decompose()

    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def parse_pubdate(entry, timezone):
    parsed = entry.get("published_parsed")

    if not parsed:
        return None

    # feedparser's parsed date is UTC.
    dt = datetime(
        parsed.tm_year,
        parsed.tm_mon,
        parsed.tm_mday,
        parsed.tm_hour,
        parsed.tm_min,
        parsed.tm_sec,
        tzinfo=ZoneInfo("UTC"),
    )

    return dt.astimezone(timezone)


def extract_image(entry):
    for item in entry.get("media_content", []):
        if item.get("url"):
            return item["url"]

    for item in entry.get("media_thumbnail", []):
        if item.get("url"):
            return item["url"]

    for item in entry.get("enclosures", []):
        url = item.get("href") or item.get("url")
        if url:
            return url

    return None


def extract_story(entry, category, feed_config, timezone):
    published = parse_pubdate(entry, timezone)

    if not published:
        return None

    title = clean_html(entry.get("title", ""))
    description = clean_html(
        entry.get("description", "") or entry.get("summary", "")
    )
    url = entry.get("link", "")
    guid = entry.get("id") or entry.get("guid") or url or title

    return {
        "title": title,
        "category": feed_config["label"],
        "category_key": category,
        "heading": feed_config["heading"],
        "published_at": published.isoformat(),
        "published_date": published.strftime("%Y-%m-%d"),
        "published_time": published.strftime("%H:%M:%S"),
        "timezone": str(timezone),
        "description": description,
        "url": url,
        "image": extract_image(entry),
        "guid": guid,
        "source": "Bollywood Hungama",
    }


def fetch_feed(url):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0 Safari/537.36"
        )
    }

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return feedparser.parse(response.content)


def extract_stories(config, target_date):
    timezone = ZoneInfo(config["source"]["timezone"])
    feeds = config["feeds"]

    categories = {}
    all_stories = []
    feed_status = {}

    for category, feed_config in feeds.items():
        print(f"Fetching: {feed_config['label']}")

        try:
            feed = fetch_feed(feed_config["url"])

            feed_status[category] = {
                "status": "success",
                "entries_found": len(feed.entries),
            }

            stories = []

            for entry in feed.entries:
                story = extract_story(
                    entry, category, feed_config, timezone
                )

                if not story:
                    continue

                # Compare dates only after converting pubDate to IST.
                if story["published_date"] != target_date:
                    continue

                stories.append(story)
                all_stories.append(story)

            categories[category] = {
                "heading": feed_config["heading"],
                "label": feed_config["label"],
                "count": len(stories),
                "stories": stories,
            }

        except Exception as exc:
            print(f"ERROR: {feed_config['label']}: {exc}")

            feed_status[category] = {
                "status": "error",
                "error": str(exc),
            }

            categories[category] = {
                "heading": feed_config["heading"],
                "label": feed_config["label"],
                "count": 0,
                "stories": [],
            }

    # Deduplicate across feeds.
    unique = {}

    for story in all_stories:
        key = story["guid"] or story["url"] or story["title"]

        if key not in unique:
            unique[key] = story

    all_stories = list(unique.values())

    # Newest first.
    all_stories.sort(
        key=lambda x: x["published_at"],
        reverse=True
    )

    # Rebuild category counts after deduplication.
    for category in categories:
        category_stories = [
            story
            for story in all_stories
            if story["category_key"] == category
        ]

        category_stories.sort(
            key=lambda x: x["published_at"],
            reverse=True
        )

        categories[category]["stories"] = category_stories
        categories[category]["count"] = len(category_stories)

    return {
        "metadata": {
            "source": config["source"]["name"],
            "date": target_date,
            "timezone": config["source"]["timezone"],
            "generated_at": datetime.now(timezone).isoformat(),
            "total_stories": len(all_stories),
            "feed_count": len(feeds),
        },
        "headings": {
            "main": (
                f"{config['source']['name']} — News for {target_date}"
            ),
            "subheading": (
                "Stories published on the selected date "
                "in configured local timezone"
            ),
        },
        "categories": categories,
        "all_stories": all_stories,
        "feed_status": feed_status,
    }


def get_output_path(config):
    output_folder = Path(
        config["output"]["folder"]
    ).expanduser()

    output_folder.mkdir(
        parents=True,
        exist_ok=True
    )

    return output_folder / config["output"]["filename"]


def save_json(data, output_file):
    output_path = Path(output_file)
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    print()
    print("=" * 60)
    print(f"JSON saved: {output_path}")
    print(f"Stories: {data['metadata']['total_stories']}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Extract today's Bollywood Hungama RSS stories."
        )
    )

    parser.add_argument(
        "--config",
        default=CONFIG_FILE,
        help="YAML configuration file."
    )

    parser.add_argument(
        "--date",
        default=None,
        help="Date in YYYY-MM-DD format."
    )

    args = parser.parse_args()

    config = load_config(args.config)
    timezone = ZoneInfo(config["source"]["timezone"])

    target_date = (
        args.date
        if args.date
        else datetime.now(timezone).strftime("%Y-%m-%d")
    )

    print()
    print(f"Source   : {config['source']['name']}")
    print(f"Date     : {target_date}")
    print(f"Timezone : {config['source']['timezone']}")
    print()

    data = extract_stories(config, target_date)
    output_file = get_output_path(config)

    save_json(data, output_file)


if __name__ == "__main__":
    main()
