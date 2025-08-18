#!/usr/bin/env bash
# build.sh

# Install dependencies
pip install -r requirements.txt

# Force pyppeteer to download Chromium during build
python -m pyppeteer install
