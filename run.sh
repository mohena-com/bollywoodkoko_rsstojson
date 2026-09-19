#!/bin/bash

set -e

# Project root
BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Current output and archive directories
OP_JSON="$BASE_DIR/OP_JSON"
ARCHIVE_DIR="$BASE_DIR/archive/$(date +%m-%d-%Y)"

echo "=========================================="
echo " BollywoodKoko Pipeline"
echo "=========================================="
echo "Base directory : $BASE_DIR"
echo "Output folder  : $OP_JSON"
echo "Archive folder : $ARCHIVE_DIR"
echo ""

# --------------------------------------------------
# 1. Archive previous OP_JSON
# --------------------------------------------------

if [ -d "$OP_JSON" ]; then

    echo "[1/5] Archiving existing OP_JSON..."

    mkdir -p "$ARCHIVE_DIR"

    # If today's archive already contains OP_JSON,
    # remove it so the archive represents the latest run.
    if [ -d "$ARCHIVE_DIR/OP_JSON" ]; then
        echo "      Removing existing today's archive..."
        rm -rf "$ARCHIVE_DIR/OP_JSON"
    fi

    mv "$OP_JSON" "$ARCHIVE_DIR/OP_JSON"

    echo "      Archived to:"
    echo "      $ARCHIVE_DIR/OP_JSON"

else

    echo "[1/5] No existing OP_JSON folder to archive."

fi

# --------------------------------------------------
# 2. Run RSS extractor
# --------------------------------------------------

echo ""
echo "[2/5] Running RSS extractor..."
python bh_rss_extractor.py

# --------------------------------------------------
# 3. Convert articles using Qwen
# --------------------------------------------------

echo ""
echo "[3/5] Running Qwen converter..."
python qwen_converter.py

# --------------------------------------------------
# 4. Generate slide designs and paint slides
# --------------------------------------------------

echo ""
echo "[4/5] Generating slide designs..."
python qwen_slide_designer.py

echo ""
echo "      Painting slides..."
python paint_slides.py

# --------------------------------------------------
# 5. Create reels
# --------------------------------------------------

echo ""
echo "[5/5] Creating reels..."
# python create_reel.py

echo "[a/g] Generating News Reels..."
python create_reel_with_list_categories.py --category news
# --------------------------------------------------
# 5.a News Reels Created
# --------------------------------------------------

echo "[b/g] Generating Features Reels..."
python create_reel_with_list_categories.py --category features
# --------------------------------------------------
# 5.b Features
# --------------------------------------------------

echo "[c/g] Generating Movie Release Dates Reels..."
python create_reel_with_list_categories.py --category movie_release_dates
# --------------------------------------------------
# 5.c Movie Release Dates Reels Created
# --------------------------------------------------

echo "[d/g] Generating Movie Reviews Reels..."
python create_reel_with_list_categories.py --category movie_reviews
# --------------------------------------------------
# 5.d Movie Reviews Reels Created
# --------------------------------------------------

echo "[e/g] Generating Previews Reels..."
python create_reel_with_list_categories.py --category movie_previews
# --------------------------------------------------
# 5.e Movie Previews Reels Created
# --------------------------------------------------

echo "[f/g] Generating Music Reviews Reels..."
python create_reel_with_list_categories.py --category music_reviews
# --------------------------------------------------
# 5.f Music Reviews Reels Created
# --------------------------------------------------

echo "[f/g] Generating Special Analysis Reels..."
python create_reel_with_list_categories.py --category special_analysis
# --------------------------------------------------
# 5.g Special Analysis Reels Created
# --------------------------------------------------
echo ""
echo "=========================================="
echo " Pipeline completed successfully"
echo "=========================================="

 news
  
  
  
  
  
  