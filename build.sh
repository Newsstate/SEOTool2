#!/usr/bin/env bash
set -euo pipefail

# Install Python deps
pip install -r requirements.txt

# Install Playwright & Chromium browser (no --with-deps on Render Python runtime)
pip install playwright
python -m playwright install chromium

# (Optional) Show versions to the build log
python -c "import sys; import playwright; print('Python', sys.version); print('Playwright', playwright.__version__)"

git add build.sh
git update-index --chmod=+x build.sh
git commit -m "Use build.sh for Playwright + Chromium install"
