#!/bin/bash
# Point every IRI in fdp/ at a new base and record it in fdp.config.json.
#
#   scripts/set-base.sh <org> <repo> [branch]        # raw.githubusercontent.com of another repository
#   scripts/set-base.sh https://w3id.org/my-fdp/     # any base, e.g. a w3id after registering it
set -euo pipefail
cd "$(dirname "$0")/.."

if [ $# -eq 1 ] && [[ "$1" =~ ^https?:// ]]; then
  NEW="${1%/}/"
elif [ $# -ge 2 ]; then
  NEW="https://raw.githubusercontent.com/$1/$2/${3:-main}/fdp/"
else
  echo "usage: $0 <org> <repo> [branch]   or   $0 <base IRI>" >&2
  exit 1
fi
OLD=$(python3 scripts/fdpconfig.py get baseIri)
[ "$OLD" != "$NEW" ] || { echo "Already using $NEW"; exit 0; }

find fdp -name '*.ttl' -print0 | xargs -0 perl -pi -e "s#\Q$OLD\E#$NEW#g"
python3 - "$NEW" "$@" <<'PY'
import json, sys
from pathlib import Path
p = Path("fdp.config.json"); c = json.loads(p.read_text())
c["baseIri"] = sys.argv[1]
if len(sys.argv) >= 4:                       # org repo [branch]: the repository moved
    c["repository"] = f"{sys.argv[2]}/{sys.argv[3]}"
    c["branch"] = sys.argv[4] if len(sys.argv) > 4 else c.get("branch", "main")
p.write_text(json.dumps(c, indent=2, ensure_ascii=False) + "\n")
PY

echo "Rebased $OLD -> $NEW"
python3 scripts/validate.py | tail -2
