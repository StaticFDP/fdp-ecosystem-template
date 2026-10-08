#!/usr/bin/env python3
"""Read fdp.config.json, the single place where an FDP ecosystem is described.

    from fdpconfig import CONFIG, ROOT, INDEX_FILE      # in Python scripts
    python3 scripts/fdpconfig.py get siteUrl              # in shell / workflows
    python3 scripts/fdpconfig.py get theme.light.primary
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "fdp.config.json").read_text())

INDEX_FOLDER = CONFIG["indexFolder"]
INDEX_FILE = ROOT / "fdp" / INDEX_FOLDER / "catalog.ttl"
BASE_IRI = CONFIG["baseIri"]
SITE_URL = CONFIG["siteUrl"]
NAME = CONFIG["name"]
REPOSITORY = CONFIG["repository"]


def get(path, default=None):
    node = CONFIG
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "get":
        value = get(sys.argv[2])
        if value is None:
            sys.exit(f"fdp.config.json has no {sys.argv[2]}")
        print(json.dumps(value) if isinstance(value, (dict, list)) else value)
    else:
        sys.exit(__doc__)
