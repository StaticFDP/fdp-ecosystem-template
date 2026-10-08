#!/usr/bin/env python3
"""Record the address the site is actually served from (e.g. after a custom domain is set).

    python3 scripts/set-site-url.py https://fdp.example.org/my-fdp/

Updates siteUrl in fdp.config.json and every mention of the old address in fdp/,
README.md and PERSISTENCE.md. Prints "unchanged" when nothing needed doing. The Pages
workflow runs this after each deploy with the URL GitHub reports.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    if len(sys.argv) != 2 or not sys.argv[1].startswith("http"):
        sys.exit(__doc__)
    new = sys.argv[1].replace("http://", "https://", 1).rstrip("/") + "/"
    cfg_file = ROOT / "fdp.config.json"
    cfg = json.loads(cfg_file.read_text())
    old = cfg["siteUrl"]
    if old == new:
        print("unchanged")
        return
    cfg["siteUrl"] = new
    cfg_file.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    files = list((ROOT / "fdp").rglob("*.ttl")) + [ROOT / "README.md", ROOT / "PERSISTENCE.md"]
    for f in files:
        if f.exists() and old in (text := f.read_text()):
            f.write_text(text.replace(old, new))
    print(f"siteUrl {old} -> {new}")


if __name__ == "__main__":
    main()
