#!/bin/bash

set -e

# ============================================================
# CONFIGURATION
# ============================================================

# Default category.
# Can be overridden from the command line:
#   ./run.sh news
CATEGORY="${1:-news}"

# Fixed OP_JSON location used for archiving
OP_JSON="/Volumes/Extreme SSD/webmaster-ai/POJO_PROJECT/bollywood/OP_JSON"

# Project directory containing this script
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Archive location
BASE_DIR="/Volumes/Extreme SSD/webmaster-ai/POJO_PROJECT/bollywood"
ARCHIVE_DIR="$BASE_DIR/archive/$(date +%m-%d-%Y)"

echo "=========================================="
echo " BollywoodKoko Pipeline"
echo "=========================================="
echo "Category       : $CATEGORY"
echo "Project folder : $PROJECT_DIR"
echo "OP_JSON        : $OP_JSON"
echo "Archive folder : $ARCHIVE_DIR"
echo ""

# --------------------------------------------------
# 1. Archive previous OP_JSON
# --------------------------------------------------

if [ -d "$OP_JSON" ]; then

    echo "[1/5] Archiving existing OP_JSON..."

    mkdir -p "$ARCHIVE_DIR"

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
python "$PROJECT_DIR/bh_rss_extractor.py"

# --------------------------------------------------
# 3. Convert articles using Qwen
#    Process ONLY selected category
# --------------------------------------------------

echo ""
echo "[3/5] Running Qwen converter for category: $CATEGORY..."
python "$PROJECT_DIR/qwen_converter.py" --category "$CATEGORY"

# --------------------------------------------------
# 4. Generate design and paint slides
#    Process ONLY selected category
# --------------------------------------------------

echo ""
echo "[4/5] Generating slide designs..."
python "$PROJECT_DIR/qwen_slide_designer.py" --category "$CATEGORY"

echo ""
echo "      Painting slides..."
python "$PROJECT_DIR/paint_slides.py" --category "$CATEGORY"

# --------------------------------------------------
# 5. Create reels
#    Process ONLY selected category
# --------------------------------------------------

echo ""
echo "[5/5] Creating reels for category: $CATEGORY..."
python "$PROJECT_DIR/create_reel_with_list_categories.py" --category "$CATEGORY"

echo ""
echo "=========================================="
echo " Pipeline completed successfully"
echo " Category: $CATEGORY"
echo "=========================================="
