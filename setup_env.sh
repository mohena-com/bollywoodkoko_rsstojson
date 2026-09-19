#!/bin/bash

set -e

echo "Installing native image dependencies..."

brew install libraqm freetype fribidi harfbuzz

echo "Configuring pkg-config..."

export PKG_CONFIG_PATH="$(brew --prefix libraqm)/lib/pkgconfig:$(brew --prefix harfbuzz)/lib/pkgconfig:$(brew --prefix fribidi)/lib/pkgconfig:$(brew --prefix freetype)/lib/pkgconfig"

echo "Removing existing Pillow..."

python -m pip uninstall -y Pillow || true

echo "Installing project requirements..."

python -m pip install --no-cache-dir --no-binary=Pillow -r requirements.txt

echo ""
echo "Checking Pillow..."

python -c "from PIL import features; import PIL; print('Pillow:', PIL.__version__); print('RAQM:', features.check('raqm')); print('FreeType:', features.check('freetype2'))"