#!/bin/bash
# Assemble the GitHub Pages site: the static viewer plus the fdp/ Turtle files,
# so the viewer can load them same-origin (and the FDP is also served at
# https://<org>.github.io/<repo>/fdp/... with a text/turtle content type).
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf _site
mkdir -p _site
cp -R site/. _site/
cp -R fdp _site/fdp
[ -d assets ] && cp -R assets _site/assets
touch _site/.nojekyll
# Static HTML, schema.org JSON-LD, signposting and a sitemap for crawlers and FAIR harvesters.
python3 scripts/build_static.py _site ${PUBLIC_URL:+"$PUBLIC_URL"}
echo "Built _site/ ($(find _site -type f | wc -l | tr -d ' ') files)"
