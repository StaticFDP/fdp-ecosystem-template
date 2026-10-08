#!/usr/bin/env python3
"""Turn a fresh copy of the FDP ecosystem template into your own FDP.

After "Use this template" on GitHub, the deploy workflow runs this once (with
--if-needed); you can also run it yourself:

    python3 scripts/init.py                                   # repository from GITHUB_REPOSITORY / git remote
    python3 scripts/init.py --repository my-org/plant-fdp --name "Plant FDP"

It points fdp.config.json at the new repository (repository, siteUrl, baseIri),
rebases every IRI in fdp/, drops the template's contributor credits and writes a
README.md and PERSISTENCE.md for the new FDP. Nothing happens when the config
already names this repository.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "fdp.config.json"
TEMPLATES = ROOT / "scripts" / "templates"
TEMPLATE_NAME = "FDP Ecosystem Template"


def current_repository():
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    try:
        remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except subprocess.CalledProcessError:
        return None
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", remote)
    return m.group(1) if m else None


def site_url(repository):
    owner, name = repository.split("/")
    if name.lower() == f"{owner.lower()}.github.io":
        return f"https://{owner.lower()}.github.io/"
    return f"https://{owner.lower()}.github.io/{name}/"


def humanise(name):
    words = re.split(r"[-_ ]+", name)
    return " ".join(w.upper() if w.lower() in ("fdp", "fair", "api", "rdf") else w.capitalize() for w in words if w)


def render(template, cfg):
    m = cfg.get("maintainer") or {}
    who = m.get("name") or (m.get("github") and f"@{m['github']}")
    line = (f" Maintainer: {who}" + (f" ([ORCID {m['orcid']}](https://orcid.org/{m['orcid']}))" if m.get("orcid") else "") + ".") if who else ""
    values = {**cfg, "template": cfg.get("template", {}).get("repository", "StaticFDP/fdp-ecosystem-template"), "maintainerLine": line}
    return re.sub(r"\{\{(\w+)\}\}", lambda x: str(values.get(x.group(1), "")), template)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repository", help="owner/name of this repository")
    ap.add_argument("--branch", default=os.environ.get("GITHUB_REF_NAME") or "main")
    ap.add_argument("--name", help="display name of the new FDP (default: from the repository name)")
    ap.add_argument("--if-needed", action="store_true", help="do nothing when the config already names this repository")
    args = ap.parse_args()

    cfg = json.loads(CONFIG_FILE.read_text())
    repository = args.repository or current_repository()
    if not repository or "/" not in repository:
        sys.exit("Cannot tell which repository this is; pass --repository owner/name")
    if cfg.get("repository") == repository:
        print(f"fdp.config.json already describes {repository}; nothing to initialise.")
        return
    if args.if_needed and cfg.get("repository") != cfg.get("template", {}).get("repository"):
        # A renamed or transferred instance, not a fresh template copy: leave it to scripts/set-base.sh.
        print(f"fdp.config.json names {cfg.get('repository')}, not the template; not touching it. Use scripts/set-base.sh to move.")
        return

    old = {k: cfg.get(k) for k in ("repository", "siteUrl", "baseIri")}
    owner, name = repository.split("/")
    cfg["repository"] = repository
    cfg["branch"] = args.branch
    cfg["siteUrl"] = site_url(repository)
    cfg["baseIri"] = f"https://raw.githubusercontent.com/{repository}/{args.branch}/fdp/"
    if args.name or cfg.get("name") in (None, "", TEMPLATE_NAME):
        cfg["name"] = args.name or humanise(name)
    # The template's maintainer is not the new FDP's: start from whoever created the copy.
    github = os.environ.get("GITHUB_ACTOR") or owner
    cfg["maintainer"] = {"name": "", "github": github, "orcid": ""}

    # Rebase IRIs and links in the metadata.
    swaps = [(old["baseIri"], cfg["baseIri"]), (old["siteUrl"], cfg["siteUrl"]),
             (f"github.com/{old['repository']}", f"github.com/{repository}")]
    changed = 0
    for f in (ROOT / "fdp").rglob("*.ttl"):
        text = f.read_text()
        new = text
        for a, b in swaps:
            if a:
                new = new.replace(a, b)
        if new != text:
            f.write_text(new)
            changed += 1
    # The template's own index title becomes the new FDP's.
    index = ROOT / "fdp" / cfg["indexFolder"] / "catalog.ttl"
    if index.exists():
        text = index.read_text().replace(f'"{TEMPLATE_NAME}', f'"{cfg["name"]}')
        index.write_text(text)
    # The curator of the new FDP is its maintainer, not the template's (the line appears in every catalog).
    curator = f':publisher-curator a foaf:Person ; foaf:name "{github}" ; foaf:homepage <https://github.com/{github}> .'
    for f in (ROOT / "fdp").rglob("*.ttl"):
        text = f.read_text()
        new = re.sub(r"^:publisher-curator .*$", curator, text, flags=re.M)
        if new != text:
            f.write_text(new)
    # Credits belong to the template's contributors; the first deploy regenerates them.
    contributors = ROOT / "fdp" / cfg["indexFolder"] / "contributors.ttl"
    if contributors.exists():
        contributors.unlink()

    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    for doc in ("README.md", "PERSISTENCE.md"):
        (ROOT / doc).write_text(render((TEMPLATES / doc).read_text(), cfg))
    print(f"Initialised {cfg['name']} for {repository}: {changed} catalog files rebased to {cfg['baseIri']}; site {cfg['siteUrl']}")


if __name__ == "__main__":
    main()
