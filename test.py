import json
import re
from pathlib import Path

import requests


# ------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------

QUERY = "Deepika Padukone"

OUTPUT_DIR = Path("./wikimedia_test")
IMAGE_FILE = OUTPUT_DIR / "deepika_padukone.jpg"
METADATA_FILE = OUTPUT_DIR / "deepika_padukone_metadata.json"

COMMONS_API = "https://commons.wikimedia.org/w/api.php"

USER_AGENT = (
    "BollywoodKoko/1.0 "
    "(Wikimedia Commons image test; contact: your-email@example.com)"
)

ALLOWED_LICENSES = {
    "public domain",
    "cc0",
    "cc by 4.0",
    "cc by 3.0",
    "cc by 2.0",
}


# ------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------

def clean(value):
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_license(value):
    value = clean(value).lower()

    value = value.replace("creative commons attribution 4.0", "cc by 4.0")
    value = value.replace("creative commons attribution 3.0", "cc by 3.0")
    value = value.replace("creative commons attribution 2.0", "cc by 2.0")
    value = value.replace("creative commons zero", "cc0")

    return value


def get_license(extmetadata):
    license_short = clean(
        extmetadata.get("LicenseShortName", {}).get("value", "")
    )

    usage_terms = clean(
        extmetadata.get("UsageTerms", {}).get("value", "")
    )

    license_url = clean(
        extmetadata.get("LicenseUrl", {}).get("value", "")
    )

    return license_short, usage_terms, license_url


def license_allowed(license_short, usage_terms):
    values = {
        normalize_license(license_short),
        normalize_license(usage_terms),
    }

    for value in values:
        if value in ALLOWED_LICENSES:
            return True

    return False


# ------------------------------------------------------------
# SEARCH WIKIMEDIA
# ------------------------------------------------------------

def search_wikimedia(query):

    print("=" * 70)
    print(f"Searching Wikimedia Commons for: {query}")
    print("=" * 70)

    params = {
        "action": "query",
        "format": "json",

        # Search files
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": "6",
        "gsrlimit": "10",

        # Get image information + metadata
        "prop": "imageinfo|info",
        "iiprop": "url|mime|size|extmetadata",
        "iiurlwidth": "1600",
    }

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Connection": "close",
    }
    print(f"COMMONS_API : {COMMONS_API}")
    print(f"params : {params}")
    print(f"headers : {headers}")

    try:
        response = requests.get(
            COMMONS_API,
            params=params,
            headers=headers,
            timeout=30,
        )

        print()
        print("HTTP status:", response.status_code)
        print("URL:", response.url)
        print()

        response.raise_for_status()

        data = response.json()

    except Exception as e:
        print("[ERROR] Wikimedia request failed:")
        print(repr(e))
        return []

    pages = data.get("query", {}).get("pages", {})

    results = []

    for page in pages.values():

        title = clean(page.get("title"))

        imageinfo = page.get("imageinfo", [])

        if not imageinfo:
            continue

        info = imageinfo[0]

        extmetadata = info.get("extmetadata", {})

        license_short, usage_terms, license_url = get_license(
            extmetadata
        )

        result = {
            "pageid": page.get("pageid"),
            "title": title,
            "file_page": (
                "https://commons.wikimedia.org/wiki/"
                + title.replace(" ", "_")
            ),
            "image_url": info.get("thumburl") or info.get("url"),
            "original_url": info.get("url"),
            "mime": info.get("mime"),
            "width": info.get("width"),
            "height": info.get("height"),
            "license": license_short,
            "usage_terms": usage_terms,
            "license_url": license_url,
        }

        results.append(result)

    return results


# ------------------------------------------------------------
# DISPLAY RESULTS
# ------------------------------------------------------------

def show_results(results):

    print("=" * 70)
    print(f"RESULTS FOUND: {len(results)}")
    print("=" * 70)

    for i, item in enumerate(results, start=1):

        allowed = license_allowed(
            item["license"],
            item["usage_terms"],
        )

        print()
        print(f"[{i}] {item['title']}")
        print("-" * 70)

        print("License     :", item["license"])
        print("Usage terms :", item["usage_terms"])
        print("Dimensions  :", f"{item['width']} x {item['height']}")
        print("MIME        :", item["mime"])
        print("Allowed     :", allowed)

        print("Image URL   :", item["image_url"])
        print("File page   :", item["file_page"])

        if item["license_url"]:
            print("License URL :", item["license_url"])


# ------------------------------------------------------------
# DOWNLOAD
# ------------------------------------------------------------

def download_image(item):

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    url = item["image_url"]

    print()
    print("=" * 70)
    print("DOWNLOADING IMAGE")
    print("=" * 70)
    print(url)

    headers = {
        "User-Agent": USER_AGENT,
        "Connection": "close",
    }

    try:

        response = requests.get(
            url,
            headers=headers,
            timeout=60,
        )

        print("HTTP status:", response.status_code)

        response.raise_for_status()

        IMAGE_FILE.write_bytes(response.content)

        METADATA_FILE.write_text(
            json.dumps(item, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print()
        print("SUCCESS")
        print("Image   :", IMAGE_FILE.resolve())
        print("Metadata:", METADATA_FILE.resolve())
        print("Size    :", len(response.content), "bytes")

        return True

    except Exception as e:

        print()
        print("[ERROR] Image download failed:")
        print(repr(e))

        return False


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------

def main():

    results = search_wikimedia(QUERY)

    if not results:
        print()
        print("No Wikimedia results returned.")
        return

    show_results(results)

    print()
    print("=" * 70)
    print("LOOKING FOR ACCEPTABLE LICENSE")
    print("=" * 70)

    for item in results:

        if license_allowed(
            item["license"],
            item["usage_terms"],
        ):

            print()
            print("Found acceptable image:")
            print(item["title"])
            print("License:", item["license"])

            if download_image(item):
                return

    print()
    print("No acceptable licensed image found.")


if __name__ == "__main__":
    main()