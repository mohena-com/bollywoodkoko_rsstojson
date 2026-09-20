#!/bin/bash

set -e

# ============================================================
# CONFIGURATION
# ============================================================

CATEGORY="news"
TARGET_DATE="$(date +%Y-%m-%d)"

# Supported forms:
#   ./run.sh
#   ./run.sh news
#   ./run.sh features
#   ./run.sh features --date 2026-09-19
#   ./run.sh --category features --date 2026-09-19
#   ./run.sh -category features -date 2026-09-19

while [[ $# -gt 0 ]]; do
    case "$1" in
        --category|-category)
            if [[ -z "${2:-}" ]]; then
                echo "ERROR: --category requires a value."
                exit 1
            fi
            CATEGORY="$2"
            shift 2
            ;;
        --date|-date)
            if [[ -z "${2:-}" ]]; then
                echo "ERROR: --date requires a value."
                exit 1
            fi
            TARGET_DATE="$2"
            shift 2
            ;;
        news|features|movie_reviews|movie_previews|music_reviews|movie_release_dates|special_analysis)
            CATEGORY="$1"
            shift
            ;;
        -h|--help)
            echo "Usage:"
            echo "  ./run.sh"
            echo "  ./run.sh news"
            echo "  ./run.sh features --date 2026-09-19"
            echo "  ./run.sh --category features --date 2026-09-19"
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument: $1"
            echo "Use ./run.sh --help for usage."
            exit 1
            ;;
    esac
done

# Validate YYYY-MM-DD format.
if ! [[ "$TARGET_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    echo "ERROR: Date must be in YYYY-MM-DD format: $TARGET_DATE"
    exit 1
fi

OP_JSON="/Volumes/Extreme SSD/webmaster-ai/POJO_PROJECT/bollywood/OP_JSON"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="/Volumes/Extreme SSD/webmaster-ai/POJO_PROJECT/bollywood"
ARCHIVE_DIR="$BASE_DIR/archive/$(date +%m-%d-%Y)"

echo "=========================================="
echo " BollywoodKoko Pipeline"
echo "=========================================="
echo "Category       : $CATEGORY"
echo "Target date    : $TARGET_DATE"
echo "Project folder : $PROJECT_DIR"
echo "OP_JSON        : $OP_JSON"
echo "Archive folder : $ARCHIVE_DIR"
echo ""

# --------------------------------------------------
# 1. Archive previous OP_JSON
# --------------------------------------------------

if [ -d "$OP_JSON" ]; then
    echo "[1/7] Archiving existing OP_JSON..."

    mkdir -p "$ARCHIVE_DIR"

    if [ -d "$ARCHIVE_DIR/OP_JSON" ]; then
        echo "      Removing existing today's archive..."
        rm -rf "$ARCHIVE_DIR/OP_JSON"
    fi

    mv "$OP_JSON" "$ARCHIVE_DIR/OP_JSON"

    echo "      Archived to:"
    echo "      $ARCHIVE_DIR/OP_JSON"
else
    echo "[1/7] No existing OP_JSON folder to archive."
fi

# --------------------------------------------------
# 2. Run RSS extractor
# --------------------------------------------------

echo ""
echo "[2/7] Running RSS extractor for date: $TARGET_DATE..."
python "$PROJECT_DIR/bh_rss_extractor.py" --date "$TARGET_DATE"

# --------------------------------------------------
# 3. Convert articles using Qwen
# --------------------------------------------------

echo ""
echo "[3/7] Running Qwen converter for category: $CATEGORY..."
python "$PROJECT_DIR/qwen_converter.py" --category "$CATEGORY"

# --------------------------------------------------
# 4. Generate design and paint slides
# --------------------------------------------------

echo ""
echo "[4/7] Generating slide designs..."
python "$PROJECT_DIR/qwen_slide_designer.py" --category "$CATEGORY"

echo ""
echo "[5/7] Fetching/caching verified Wikimedia Commons images..."
python "$PROJECT_DIR/commons_image_fetcher.py" --category "$CATEGORY"   

echo "[6/7] Painting slides..."
python "$PROJECT_DIR/paint_slides.py" --category "$CATEGORY"

# --------------------------------------------------
# 5. Create reels
# --------------------------------------------------

echo ""
echo "[7/7] Creating reels for category: $CATEGORY..."
# python "$PROJECT_DIR/create_reel_with_list_categories.py" --category "$CATEGORY"
python "$PROJECT_DIR/create_reel_category_tts.py" --category "$CATEGORY"


echo ""
echo "=========================================="
echo " Pipeline completed successfully"
echo " Category: $CATEGORY"
echo " Date: $TARGET_DATE"
echo "=========================================="
