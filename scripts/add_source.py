#!/usr/bin/env python3
"""Add a data source to this FDP ecosystem (see fdp.config.json).

Two kinds of source:

* curated — writes fdp/<slug>-fdp/catalog.ttl (catalog → datasets → distributions,
  each distribution typed with the access-method vocabulary and a "How to get it"
  text) and registers it in the index;
* live    — the source runs its own FAIR Data Point; only an index entry pointing at
  it is added, and the viewer crawls it in the browser.

Input is either a JSON spec (see CONTRIBUTING.md and scripts/example-source.json)
or the body of a GitHub "Add a data source" issue form:

    python3 scripts/add_source.py scripts/example-source.json
    python3 scripts/add_source.py --issue-body issue.md --summary pr-body.md

Everything that comes from the spec is untrusted: slugs, URLs and media types are
checked against strict patterns and literals are escaped before they reach Turtle.
"""
import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fdpconfig import BASE_IRI, INDEX_FILE as INDEX, INDEX_FOLDER, NAME, ROOT  # noqa: E402

VOCAB = ROOT / "fdp" / "vocab" / "access-methods.ttl"
IANA = "https://www.iana.org/assignments/media-types/"

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
URL = re.compile(r"^https?://[^\s<>\"{}|\\^`]+$")
MEDIA_TYPE = re.compile(r"^[a-z]+/[a-z0-9.+-]+$")
NO_RESPONSE = {"", "_no response_", "none", "n/a", "-"}


class SpecError(ValueError):
    pass


# ── helpers ─────────────────────────────────────────────────────────────────

def lit(text, lang="en"):
    """A single-line Turtle literal; quotes and backslashes escaped."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"@{lang}' if lang else f'"{text}"'


def url(value, field, required=True):
    value = (value or "").strip()
    if not value:
        if required:
            raise SpecError(f"{field} is required")
        return None
    if not URL.match(value):
        raise SpecError(f"{field} is not a valid http(s) URL: {value!r}")
    return value


def access_methods():
    return re.findall(r"^am:([a-z-]+)\s+a skos:Concept\s*;", VOCAB.read_text(), re.M)


def base_iri():
    return BASE_IRI


def local_id(text, used):
    """A short, unique local name such as dist-sparql or dist-sparql-2."""
    base = re.sub(r"[^a-z0-9]+", "-", (text or "item").lower()).strip("-")
    if len(base) > 40:
        base = base[:40].rsplit("-", 1)[0]
    base = base or "item"
    name, i = base, 2
    while name in used:
        name, i = f"{base}-{i}", i + 1
    used.add(name)
    return name


# ── spec ────────────────────────────────────────────────────────────────────

def validate(spec):
    s = dict(spec)
    s["slug"] = (s.get("slug") or "").strip().lower()
    if not SLUG.match(s["slug"]):
        raise SpecError("slug must be 2-41 characters: lowercase letters, digits and hyphens (e.g. world-flora-online)")
    s["mode"] = (s.get("mode") or "curated").strip().lower()
    if s["mode"] not in ("curated", "live"):
        raise SpecError("mode must be 'curated' or 'live'")
    if not (s.get("title") or "").strip():
        raise SpecError("title is required")
    s["license"] = url(s.get("license"), "license", required=False)
    s["landingPage"] = url(s.get("landingPage"), "landingPage", required=False)
    pub = s.get("publisher") or {}
    s["publisher"] = {"name": (pub.get("name") or "").strip(), "homepage": url(pub.get("homepage"), "publisher homepage", required=False)}

    if s["mode"] == "live":
        s["fdpUrl"] = url(s.get("fdpUrl"), "fdpUrl")
        return s

    if not s["publisher"]["name"]:
        raise SpecError("publisher name is required")
    methods = access_methods()
    datasets = s.get("datasets") or []
    if not datasets:
        raise SpecError("at least one dataset with one distribution is required")
    for ds in datasets:
        if not (ds.get("title") or "").strip():
            raise SpecError("every dataset needs a title")
        ds["license"] = url(ds.get("license"), "dataset license", required=False)
        ds["landingPage"] = url(ds.get("landingPage"), "dataset landingPage", required=False)
        if ds.get("issued") and not re.match(r"^\d{4}-\d{2}-\d{2}$", ds["issued"]):
            raise SpecError(f"issued must be YYYY-MM-DD, got {ds['issued']!r}")
        if not ds.get("distributions"):
            raise SpecError(f"dataset {ds['title']!r} has no distributions")
        for d in ds["distributions"]:
            if d.get("method") not in methods:
                raise SpecError(f"access method {d.get('method')!r} is not one of: {', '.join(methods)}")
            if not (d.get("title") or "").strip():
                raise SpecError("every distribution needs a title")
            d["url"] = url(d.get("url"), f"URL of {d['title']!r}")
            d["download"] = url(d.get("download"), f"download URL of {d['title']!r}", required=False)
            d["endpoint"] = url(d.get("endpoint"), f"endpoint of {d['title']!r}", required=False)
            d["docs"] = url(d.get("docs"), f"docs URL of {d['title']!r}", required=False)
            mt = (d.get("mediaType") or "").strip()
            if mt and not MEDIA_TYPE.match(mt):
                raise SpecError(f"mediaType {mt!r} is not a media type like text/csv")
            d["mediaType"] = mt or None
    return s


# Media types implied by an access method when the spec gives none.
DEFAULT_MEDIA = {
    "web-portal": "text/html", "sparql-endpoint": "application/sparql-results+json",
    "rest-api": "application/json", "darwin-core-archive": "application/zip", "rdf-dump": "text/turtle",
}


def catalog_ttl(s, base, today):
    folder = f"{s['slug']}-fdp"
    out = [f"""@prefix dcat:    <http://www.w3.org/ns/dcat#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix foaf:    <http://xmlns.com/foaf/0.1/> .
@prefix rdfs:    <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:     <http://www.w3.org/2001/XMLSchema#> .
@prefix fdp:     <https://w3id.org/fdp/fdp-o#> .
@prefix r3d:     <http://www.re3data.org/schema/3-0#> .
@prefix am:      <{base}vocab/access-methods#> .
@prefix :        <{base}{folder}/> .

<{base}{folder}/>
    a fdp:MetadataService, r3d:Repository ;
    dcterms:title {lit(s['title'] + ' FAIR Data Point — ' + NAME)} ;
    dcterms:publisher :publisher-source ;
    dcterms:modified "{today}"^^xsd:date ;
    fdp:hasCatalog :catalog .
"""]
    used = set()
    ds_ids = [local_id(ds.get("version") and f"{s['slug']}-{ds['version']}" or ds["title"], used) for ds in s["datasets"]]
    cat = [":catalog", "    a dcat:Catalog ;", f"    dcterms:title {lit(s['title'])} ;"]
    if s.get("description"):
        cat.append(f"    dcterms:description {lit(s['description'])} ;")
    cat.append("    dcterms:publisher :publisher-source ;")
    if s["license"]:
        cat.append(f"    dcterms:license <{s['license']}> ;")
    if s["landingPage"]:
        cat.append(f"    dcat:landingPage <{s['landingPage']}> ;")
    cat.append(f"    dcat:dataset {' , '.join(':' + i for i in ds_ids)} .")
    out.append("\n".join(cat) + "\n")

    for ds, ds_id in zip(s["datasets"], ds_ids):
        dist_ids = [local_id("dist-" + d["title"], used) for d in ds["distributions"]]
        block = [f":{ds_id}", "    a dcat:Dataset ;", f"    dcterms:title {lit(ds['title'])} ;"]
        if ds.get("description"):
            block.append(f"    dcterms:description {lit(ds['description'])} ;")
        block.append(f"    dcat:version {lit(ds.get('version') or 'live (described ' + today + ')', None)} ;")
        if ds.get("issued"):
            block.append(f'    dcterms:issued "{ds["issued"]}"^^xsd:date ;')
        block.append("    dcterms:publisher :publisher-source ;")
        lic = ds.get("license") or s["license"]
        if lic:
            block.append(f"    dcterms:license <{lic}> ;")
        if ds.get("landingPage"):
            block.append(f"    dcat:landingPage <{ds['landingPage']}> ;")
        block.append(f"    dcat:distribution {' , '.join(':' + i for i in dist_ids)} .")
        out.append("\n".join(block) + "\n")

        for d, d_id in zip(ds["distributions"], dist_ids):
            how = (d.get("howto") or "").strip()
            how = how if how.lower().startswith("how to get it") else f"How to get it: {how or 'open the URL.'}"
            mt = d["mediaType"] or DEFAULT_MEDIA.get(d["method"])
            b = [f":{d_id}", "    a dcat:Distribution ;", f"    dcterms:title {lit(d['title'])} ;",
                 f"    dcterms:description {lit(how)} ;", f"    dcterms:type am:{d['method']} ;"]
            if mt:
                b.append(f"    dcat:mediaType <{IANA}{mt}> ;")
            if d["download"]:
                b.append(f"    dcat:downloadURL <{d['download']}> ;")
            if d["endpoint"]:
                b.append(f"    dcat:accessService :svc-{d_id} ;")
            b.append(f"    dcat:accessURL <{d['url']}> .")
            out.append("\n".join(b) + "\n")
            if d["endpoint"]:
                svc = [f":svc-{d_id}", "    a dcat:DataService ;", f"    dcterms:title {lit(d['title'])} ;",
                       f"    dcat:endpointURL <{d['endpoint']}> ;"]
                if d["docs"]:
                    svc.append(f"    dcat:endpointDescription <{d['docs']}> ;")
                if d["method"] == "sparql-endpoint":
                    svc.append("    dcterms:conformsTo <https://www.w3.org/TR/sparql11-protocol/> ;")
                svc.append("    dcterms:publisher :publisher-source .")
                out.append("\n".join(svc) + "\n")

    pub = s["publisher"]
    out.append(f":publisher-source a foaf:Organization ; foaf:name {lit(pub['name'])}"
               + (f" ; foaf:homepage <{pub['homepage']}>" if pub["homepage"] else "") + " .")
    curator = re.search(r"^:publisher-curator .*$", INDEX.read_text(), re.M)
    if curator:
        out.append(curator.group(0))
    return "\n".join(out) + "\n"


def register_in_index(s, base, today):
    text = INDEX.read_text()
    entry = f"catalog-{s['slug']}"
    if re.search(rf"^:{re.escape(entry)}\b", text, re.M):
        raise SpecError(f"the index already has an entry :{entry}")
    target = s["fdpUrl"] if s["mode"] == "live" else f"{base}{s['slug']}-fdp/catalog.ttl"
    # Append to the fdp:hasCatalog list (its last item ends with " .").
    m = re.search(r"(fdp:hasCatalog\b[^.]*?)(\s*\.)", text, re.S)
    if not m:
        raise SystemExit("Could not find fdp:hasCatalog in the index")
    text = text[:m.end(1)] + f" ,\n        :{entry}" + text[m.end(1):]
    # Keep ldp:contains (the catalog documents) in step with fdp:hasCatalog.
    m = re.search(r"(ldp:contains\b[^;]*?)(\s*;)", text, re.S)
    if m:
        text = text[:m.end(1)] + f" ,\n        <{target}>" + text[m.end(1):]
    block = [f":{entry}", "    a dcat:Catalog ;", f"    dcterms:title {lit(s['title'])} ;"]
    if s.get("license"):
        block.append(f"    dcterms:license <{s['license']}> ;")
    block.append(f"    rdfs:seeAlso <{target}> .")
    block = "\n".join(block) + "\n\n"
    m = re.search(r"^:publisher-curator\b", text, re.M)
    text = text[:m.start()] + block + text[m.start():] if m else text.rstrip("\n") + "\n\n" + block
    text = re.sub(r'(dcterms:modified ")\d{4}-\d{2}-\d{2}("\^\^xsd:date)', rf"\g<1>{today}\g<2>", text, count=1)
    INDEX.write_text(text)


def check_live_fdp(fdp_url):
    """Fetch the FDP as Turtle; report whether it lists catalogs and allows cross-site reads."""
    notes = []
    try:
        head = subprocess.run(["curl", "-sIL", "-m", "30", "-H", "Origin: https://example.org", "-H", "Accept: text/turtle", fdp_url],
                              capture_output=True, text=True, timeout=60).stdout.lower()
        body = subprocess.run(["curl", "-sL", "-m", "30", "-H", "Accept: text/turtle", fdp_url],
                              capture_output=True, text=True, timeout=60).stdout
    except Exception as e:  # noqa: BLE001 - report, don't crash the workflow
        return [f"Could not fetch {fdp_url}: {e}"]
    if "metadatacatalog" not in body.lower() and "dcat:catalog" not in body.lower() and "ns/dcat#catalog" not in body.lower():
        notes.append("⚠️ The URL did not return FDP metadata listing catalogs (fdp:metadataCatalog / dcat:catalog) as Turtle.")
    if "access-control-allow-origin" not in head:
        notes.append("⚠️ The server sends no Access-Control-Allow-Origin header, so browsers cannot read it and the viewer will show it as unreachable.")
    return notes or ["✅ Returns FDP metadata as Turtle and allows cross-site reads."]


# ── issue form ──────────────────────────────────────────────────────────────

LABELS = {
    "kind of source": "mode", "short id": "slug", "name": "title", "description": "description",
    "publisher": "publisher_name", "publisher website": "publisher_url", "licence": "license",
    "website": "landingPage", "fair data point url": "fdpUrl", "dataset title": "dataset_title",
    "dataset version": "dataset_version", "dataset description": "dataset_description",
    "ways to get the data": "distributions",
}


def spec_from_issue(body):
    """Parse a GitHub issue-form body (### Label / value sections) into a spec."""
    fields = {}
    for m in re.finditer(r"^###\s+(.+?)\s*$\n(.*?)(?=^###\s|\Z)", body, re.S | re.M):
        label = m.group(1).strip().lower()
        key = LABELS.get(label)
        if key:
            value = m.group(2).strip()
            fields[key] = "" if value.lower() in NO_RESPONSE else value
    mode = "live" if "live" in fields.get("mode", "").lower() else "curated"
    spec = {
        "slug": fields.get("slug"), "mode": mode, "title": fields.get("title"),
        "description": fields.get("description"), "license": fields.get("license"),
        "landingPage": fields.get("landingPage"), "fdpUrl": fields.get("fdpUrl"),
        "publisher": {"name": fields.get("publisher_name"), "homepage": fields.get("publisher_url")},
    }
    if mode == "curated":
        spec["datasets"] = parse_distribution_lines(
            fields.get("distributions", ""), fields.get("dataset_title") or fields.get("title"),
            fields.get("dataset_version"), fields.get("dataset_description"))
    return spec


def parse_distribution_lines(text, title, version, description):
    """Lines 'method | title | url | how to get it'; '# Dataset title | version | description' starts another dataset."""
    text = re.sub(r"^```\w*\s*$", "", text, flags=re.M)
    datasets = [{"title": title, "version": version, "description": description, "distributions": []}]
    for raw in text.splitlines():
        line = raw.strip().lstrip("-*").strip()
        if not line:
            continue
        if line.startswith("#"):
            parts = [p.strip() for p in line.lstrip("#").split("|")]
            datasets.append({"title": parts[0], "version": parts[1] if len(parts) > 1 else None,
                             "description": parts[2] if len(parts) > 2 else None, "distributions": []})
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            raise SpecError(f"cannot read line {raw!r}: use 'method | title | URL | how to get it'")
        d = {"method": parts[0].lower(), "title": parts[1], "url": parts[2],
             "howto": " | ".join(parts[3:]) if len(parts) > 3 else ""}
        if d["method"] in ("sparql-endpoint", "rest-api"):
            d["endpoint"] = d["url"]
        if d["method"] in ("bulk-download", "darwin-core-archive", "rdf-dump"):
            d["download"] = d["url"]
        datasets[-1]["distributions"].append(d)
    return [ds for ds in datasets if ds["distributions"]]


# ── main ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", nargs="?", help="JSON spec file")
    ap.add_argument("--issue-body", help="markdown body of an 'Add a data source' issue")
    ap.add_argument("--summary", help="write a pull-request description (markdown) here")
    ap.add_argument("--title-file", help="write a one-line PR title here")
    args = ap.parse_args()
    if bool(args.spec) == bool(args.issue_body):
        ap.error("give either a JSON spec file or --issue-body")

    try:
        raw = spec_from_issue(Path(args.issue_body).read_text()) if args.issue_body else json.loads(Path(args.spec).read_text())
        s = validate(raw)
        today = datetime.date.today().isoformat()
        base = base_iri()
        lines = [f"Adds **{s['title']}** (`{s['slug']}`) to the {NAME} as a **{s['mode']}** source.", ""]
        if s["mode"] == "curated":
            folder = ROOT / "fdp" / f"{s['slug']}-fdp"
            if folder.exists():
                raise SpecError(f"fdp/{folder.name}/ already exists; edit that catalog instead")
            folder.mkdir(parents=True)
            (folder / "catalog.ttl").write_text(catalog_ttl(s, base, today))
            n = sum(len(ds["distributions"]) for ds in s["datasets"])
            lines += [f"- New file `fdp/{folder.name}/catalog.ttl`: {len(s['datasets'])} dataset(s), {n} distribution(s)"]
            for ds in s["datasets"]:
                lines += [f"  - **{ds['title']}**" + (f" ({ds['version']})" if ds.get("version") else "")]
                lines += [f"    - `{d['method']}` [{d['title']}]({d['url']})" for d in ds["distributions"]]
        else:
            lines += [f"- Links the live FAIR Data Point <{s['fdpUrl']}>; the viewer crawls it in the browser."]
            lines += ["- Check: " + n for n in check_live_fdp(s["fdpUrl"])]
        register_in_index(s, base, today)
        lines += [f"- Registered in `fdp/{INDEX_FOLDER}/catalog.ttl`", "",
                  "Review the Turtle and the preview after merging; GitHub Pages redeploys automatically."]
        if args.summary:
            Path(args.summary).write_text("\n".join(lines) + "\n")
        if args.title_file:
            one_line = re.sub(r"[\r\n]+", " ", s["title"])[:80]
            Path(args.title_file).write_text(f"Add data source: {one_line}\n")
        print("\n".join(lines))
    except (SpecError, json.JSONDecodeError) as e:
        print(f"Cannot add this data source: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
