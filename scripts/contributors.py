#!/usr/bin/env python3
"""Collect who contributed to this FDP and write contributors.json for the viewer.

Sources, all from the repository itself:

* commits (git history), mapped to GitHub accounts through the commits API;
* merged pull requests;
* Wikidata: accounts linked through P2037 (GitHub username) give the person's
  preferred name, Wikidata item and ORCID;
* "Add a data source" issues: the person who filled in the form is credited for the
  catalog, even though the github-actions bot makes the commit (the commit message
  says "Generated from issue #N").

Per catalog folder it records who added it (first commit touching fdp/<folder>/)
and everyone who changed it since. Bots and AI co-author trailers are left out.

It also writes the same information as Turtle (fdp/<indexFolder>/contributors.ttl),
so FDP clients see who contributed: dcterms:creator / dcterms:contributor on the index
and on each catalog, with foaf:Person nodes linked to GitHub, Wikidata and ORCID.
The Turtle holds no timestamps, so it only changes when the contributors change.

Run in the Pages workflow (needs full history: actions/checkout with fetch-depth: 0):
    python3 scripts/contributors.py _site/contributors.json [--turtle fdp/<indexFolder>/contributors.ttl]
Locally it uses GH_TOKEN / GITHUB_TOKEN, then `gh auth token`, then anonymous access.
"""
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fdpconfig import BASE_IRI, INDEX_FOLDER, REPOSITORY, ROOT  # noqa: E402

USER_AGENT = f"fdp-ecosystem/1.0 (https://github.com/{REPOSITORY})"
API = "https://api.github.com"


def sh(*args):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout


def repo_slug():
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    remote = sh("git", "remote", "get-url", "origin").strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", remote)
    if not m:
        raise SystemExit(f"Cannot work out the GitHub repository from {remote!r}")
    return m.group(1)


def token():
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(var):
            return os.environ[var]
    try:
        return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001 - anonymous access is fine for a public repo
        return None


TOKEN = token()


def api(path):
    """GET a GitHub API path, following pagination. Returns a list or dict."""
    url, out = f"{API}{path}", None
    while url:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT})
        if TOKEN:
            req.add_header("Authorization", f"Bearer {TOKEN}")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
                nxt = re.search(r'<([^>]+)>;\s*rel="next"', r.headers.get("Link", ""))
        except urllib.error.URLError:
            # Some local Python installs lack CA certificates; curl uses the system store.
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                cmd = ["curl", "-sSfL", "-H", "Accept: application/vnd.github+json", "-D", f"{tmp}/h", "-o", f"{tmp}/b", url]
                if TOKEN:
                    cmd[2:2] = ["-H", f"Authorization: Bearer {TOKEN}"]
                subprocess.run(cmd, check=True)
                head, data = Path(f"{tmp}/h").read_text(), json.loads(Path(f"{tmp}/b").read_text())
            nxt = re.search(r'<([^>]+)>;\s*rel="next"', head)
        if isinstance(data, list):
            out = (out or []) + data
            url = nxt.group(1) if nxt else None
        else:
            return data
    return out or []


WIKIDATA_ENDPOINTS = ["https://query.wikidata.org/sparql", "https://qlever.dev/api/wikidata"]


def wikidata_people(logins):
    """Map lower-cased GitHub login → {wikidata, name, orcid} via Wikidata P2037 (GitHub username)."""
    if not logins:
        return {}
    values = ", ".join(json.dumps(l.lower()) for l in logins)
    query = f"""PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?item ?gh ?label ?orcid WHERE {{
  ?item wdt:P2037 ?gh .
  FILTER(LCASE(STR(?gh)) IN ({values}))
  OPTIONAL {{ ?item rdfs:label ?label FILTER(LANG(?label) = "en") }}
  OPTIONAL {{ ?item wdt:P496 ?orcid }}
}}"""
    for endpoint in WIKIDATA_ENDPOINTS:
        try:
            raw = subprocess.run(["curl", "-sSf", "-m", "60", "-A", USER_AGENT,
                                  "-H", "Accept: application/sparql-results+json", "--data-urlencode", f"query={query}", endpoint],
                                 capture_output=True, text=True, check=True).stdout
            found = {}
            for b in json.loads(raw)["results"]["bindings"]:
                found.setdefault(b["gh"]["value"].lower(), {
                    "wikidata": b["item"]["value"],
                    "name": b.get("label", {}).get("value"),
                    "orcid": b.get("orcid", {}).get("value")})
            return found
        except Exception as e:  # noqa: BLE001 - try the next endpoint, then go without
            print(f"Wikidata lookup via {endpoint} failed: {e}", file=sys.stderr)
    return {}


def is_bot(login, kind=None):
    return not login or kind == "Bot" or login.endswith("[bot]")


def lit(text):
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def turtle(data):
    """contributors.ttl: who created and contributed to the index and each catalog."""
    base = BASE_IRI
    pid = lambda login: ":contributor-" + re.sub(r"[^A-Za-z0-9_-]", "-", login)
    out = [f"""@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix foaf:    <http://xmlns.com/foaf/0.1/> .
@prefix owl:     <http://www.w3.org/2002/07/owl#> .
@prefix prov:    <http://www.w3.org/ns/prov#> .
@prefix rdfs:    <http://www.w3.org/2000/01/rdf-schema#> .
@prefix :        <{base}{INDEX_FOLDER}/> .

# Generated by scripts/contributors.py from the GitHub repository and Wikidata (P2037).
# Do not edit by hand; it is rewritten on every deploy.
"""]
    people = data["people"]
    if people:
        out.append(f"<{base}{INDEX_FOLDER}/>\n    dcterms:contributor {' , '.join(pid(p['login']) for p in people)} .\n")
    for folder, info in sorted(data["catalogs"].items()):
        if folder == INDEX_FOLDER:
            continue
        others = [l for l in info["contributors"] if l != info["addedBy"]]
        lines = [f"<{base}{folder}/catalog>", f"    dcterms:creator {pid(info['addedBy'])}"]
        if others:
            lines[-1] += " ;"
            lines.append(f"    dcterms:contributor {' , '.join(pid(l) for l in others)}")
        out.append("\n".join(lines) + " .\n")
    for p in people:
        b = [pid(p["login"]), "    a foaf:Person, prov:Agent ;"]
        if p["name"]:
            b.append(f"    foaf:name {lit(p['name'])} ;")
        b.append(f"    foaf:nick {lit(p['login'])} ;")
        b.append(f"    foaf:account <https://github.com/{p['login']}> ;")
        if p["wikidata"]:
            b.append(f"    owl:sameAs <{p['wikidata']}> ;")
        if p["orcid"]:
            b.append(f"    owl:sameAs <https://orcid.org/{p['orcid']}> ;")
        b.append(f"    foaf:depiction <{p['avatar']}> .")
        out.append("\n".join(b) + "\n")
        out.append(f"<https://github.com/{p['login']}> a foaf:OnlineAccount ; foaf:accountServiceHomepage <https://github.com> ; foaf:accountName {lit(p['login'])} .\n")
    return "\n".join(out)


def main():
    args = sys.argv[1:]
    ttl_path = None
    if "--turtle" in args:
        i = args.index("--turtle")
        ttl_path = Path(args[i + 1])
        del args[i:i + 2]
    out_path = Path(args[0]) if args else ROOT / "site" / "contributors.json"
    repo = repo_slug()
    people = {}

    def person(login, avatar=None, url=None):
        p = people.setdefault(login, {"login": login, "name": None, "wikidata": None, "orcid": None, "avatar": avatar, "url": url or f"https://github.com/{login}",
                                      "commits": 0, "pullRequests": [], "requests": [], "catalogs": [], "since": None})
        p["avatar"] = p["avatar"] or avatar
        return p

    def seen(p, date):
        if date and (not p["since"] or date < p["since"]):
            p["since"] = date

    # Data-source issues: who asked for which source.
    issue_author = {}
    for i in api(f"/repos/{repo}/issues?state=all&labels=data-source&per_page=100"):
        login = (i.get("user") or {}).get("login")
        if is_bot(login, (i.get("user") or {}).get("type")):
            continue
        issue_author[i["number"]] = login
        p = person(login, i["user"].get("avatar_url"), i["user"].get("html_url"))
        p["requests"].append({"number": i["number"], "title": i["title"], "url": i["html_url"]})
        seen(p, i["created_at"][:10])

    # Commits → GitHub logins (the API knows which account an email belongs to).
    sha_login, git_name = {}, {}
    for c in api(f"/repos/{repo}/commits?per_page=100"):
        a = c.get("author") or {}
        if a.get("login"):
            sha_login[c["sha"]] = (a["login"], a.get("type"), a.get("avatar_url"), a.get("html_url"))
            name = ((c.get("commit") or {}).get("author") or {}).get("name")
            if name and name != a["login"] and not git_name.get(a["login"]):
                git_name[a["login"]] = name

    # Walk local history (oldest first) for files touched per commit.
    log = sh("git", "log", "--reverse", "--no-merges", "--format=%x1e%H%x1f%aI%x1f%B%x1f", "--name-only", "--", "fdp")
    catalogs = {}
    for entry in filter(None, log.split("\x1e")):
        sha, date, message, files = (entry.split("\x1f") + ["", "", "", ""])[:4]
        login, kind, avatar, url = sha_login.get(sha, (None, None, None, None))
        m = re.search(r"Generated from issue #(\d+)", message)
        if m and int(m.group(1)) in issue_author:   # bot commit made for someone's form
            login, kind, avatar, url = issue_author[int(m.group(1))], "User", None, None
        if is_bot(login, kind):
            continue
        p = person(login, avatar, url)
        seen(p, date[:10])
        for f in files.split():
            mm = re.match(r"fdp/([^/]+)/", f)
            if not mm or mm.group(1) == "vocab":
                continue
            folder = mm.group(1)
            c = catalogs.setdefault(folder, {"addedBy": login, "added": date[:10], "contributors": []})
            if login not in c["contributors"]:
                c["contributors"].append(login)
            if folder not in p["catalogs"]:
                p["catalogs"].append(folder)

    # Commit counts over the whole repository (site, scripts and data alike).
    for c in api(f"/repos/{repo}/contributors?per_page=100"):
        if not is_bot(c.get("login"), c.get("type")):
            person(c["login"], c.get("avatar_url"), c.get("html_url"))["commits"] = c["contributions"]

    # Merged pull requests (not the bot's own data-source PRs; their requester is credited above).
    for pr in api(f"/repos/{repo}/pulls?state=closed&per_page=100"):
        login = (pr.get("user") or {}).get("login")
        if not pr.get("merged_at") or is_bot(login, (pr.get("user") or {}).get("type")):
            continue
        p = person(login, pr["user"].get("avatar_url"), pr["user"].get("html_url"))
        p["pullRequests"].append({"number": pr["number"], "title": pr["title"], "url": pr["html_url"], "merged": pr["merged_at"][:10]})
        seen(p, pr["created_at"][:10])

    wikidata = wikidata_people(list(people))
    for p in people.values():
        wd = wikidata.get(p["login"].lower(), {})
        p["wikidata"], p["orcid"] = wd.get("wikidata"), wd.get("orcid")
        if wd.get("name"):
            p["name"] = wd["name"]
            continue
        try:
            p["name"] = api(f"/users/{p['login']}").get("name")
        except Exception:  # noqa: BLE001 - the login is enough
            pass
        # No display name on the profile: use the name from the git commits, unless that is just the login.
        if not p["name"] and git_name.get(p["login"]) and git_name[p["login"]] != p["login"]:
            p["name"] = git_name[p["login"]]
        p["avatar"] = p["avatar"] or f"https://github.com/{p['login']}.png"

    ranked = sorted(people.values(), key=lambda p: (-(p["commits"] + 3 * len(p["pullRequests"]) + 3 * len(p["requests"])), p["since"] or ""))
    data = {"generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "repository": repo, "people": ranked, "catalogs": catalogs}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    if ttl_path:
        ttl_path.write_text(turtle(data))
    print(f"{len(ranked)} contributors, {len(catalogs)} catalogs → {out_path}")


if __name__ == "__main__":
    main()
