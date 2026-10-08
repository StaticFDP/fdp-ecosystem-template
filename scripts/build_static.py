#!/usr/bin/env python3
"""Make the built site visible to search engines and FAIR harvesters.

The viewer renders everything with JavaScript, so a crawler that does not run it
sees an empty page. This step, run after scripts/build-site.sh, writes into _site/:

* static HTML inside <main id="view"> listing every catalog, dataset and way to get
  the data (the viewer replaces it as soon as it loads, so visitors see no change);
* schema.org JSON-LD (DataCatalog → Dataset → DataDownload) in <head>;
* FAIR signposting links (describedby, cite-as, license) in <head>;
* sitemap.xml (submit it in Google Search Console; a robots.txt under a project
  path like /<repo>/ would be ignored by crawlers, so none is written);
* the instance's title, description, fonts, colours and logo from fdp.config.json.

    python3 scripts/build_static.py _site [public URL; default: siteUrl from fdp.config.json]
"""
import html
import json
import re
import sys
from pathlib import Path

from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import DCAT, DCTERMS, FOAF, RDF, RDFS

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fdpconfig import CONFIG, INDEX_FOLDER, ROOT, SITE_URL  # noqa: E402
FDP = Namespace("https://w3id.org/fdp/fdp-o#")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
IANA = "https://www.iana.org/assignments/media-types/"
DCAT_VERSION = URIRef("http://www.w3.org/ns/dcat#version")   # DCAT 3; rdflib's DCAT namespace is DCAT 2


def text(g, s, p):
    vals = list(g.objects(s, p))
    en = [v for v in vals if getattr(v, "language", None) in ("en", None)]
    return str((en or vals or [""])[0])


THEME_VARS = {"bg": "--bg", "surface": "--surface", "tint": "--tint", "tint2": "--tint-2", "primary": "--primary",
              "primaryHover": "--primary-hover", "onPrimary": "--on-primary", "text": "--text", "sub": "--sub",
              "faint": "--faint", "border": "--border", "codeBg": "--code-bg"}


def css_vars(palette):
    return "; ".join(f"{THEME_VARS[k]}: {v}" for k, v in (palette or {}).items() if k in THEME_VARS and re.match(r"^#[0-9a-fA-F]{3,8}$", v))


def instance_head(e, public):
    """Title, description, favicon, fonts, theme, logo and runtime config of this instance."""
    theme = CONFIG.get("theme", {})
    heading, body = theme.get("headingFont", "Source Serif 4"), theme.get("bodyFont", "Source Sans 3")
    fam = lambda f: re.sub(r"[^A-Za-z0-9 ]", "", f).replace(" ", "+")
    fonts = theme.get("fontsUrl") or (f"https://fonts.googleapis.com/css2?family={fam(heading)}:ital,wght@0,400;0,700;1,400"
                                      f"&family={fam(body)}:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap")
    light, dark = css_vars(theme.get("light")), css_vars(theme.get("dark"))
    dark_block = f"{dark}; color-scheme: dark" if dark else ""
    style = (f":root {{ --serif: '{heading}', Georgia, serif; --sans: '{body}', system-ui, sans-serif; {light} }}"
             + (f" @media (prefers-color-scheme: dark) {{ :root:not([data-theme=\"light\"]) {{ {dark_block} }} }}"
                f" :root[data-theme=\"dark\"] {{ {dark_block} }}" if dark else ""))
    logo_path = ROOT / theme.get("logo", "assets/logo.svg")
    logo_svg = ""
    if logo_path.exists():
        logo_svg = re.sub(r"<!--.*?-->|<\?xml[^>]*\?>", "", logo_path.read_text(), flags=re.S).strip()
        logo_svg = re.sub(r'(<svg\b[^>]*?)\s+color="[^"]*"', r"\1", logo_svg, count=1)   # inline copy follows the theme colour
    runtime = {k: CONFIG.get(k) for k in ("name", "headline", "indexFolder", "repository", "branch", "siteUrl")}
    runtime_json = json.dumps(runtime, ensure_ascii=False).replace("</", "<\\/")
    return (f"<title>{e(CONFIG['name'])}</title>\n"
            f'<meta name="description" content="{e(CONFIG.get("description", ""))}">\n'
            + (f'<link rel="icon" type="image/svg+xml" href="{e(theme.get("logo", "assets/logo.svg"))}">\n' if logo_svg else "")
            + f'<link rel="alternate" type="text/turtle" href="fdp/{e(INDEX_FOLDER)}/catalog.ttl">\n'
            f'<link rel="stylesheet" href="{e(fonts)}">\n'
            f'<style id="fdp-theme">{style}</style>\n'
            f"<script>window.FDP_CONFIG = {runtime_json};</script>\n"
            + (f'<template id="fdp-logo">{logo_svg}</template>\n' if logo_svg else ""))


def main():
    site = Path(sys.argv[1])
    public = (sys.argv[2] if len(sys.argv) > 2 else SITE_URL).rstrip("/") + "/"
    g = Graph()
    for f in sorted((ROOT / "fdp").glob("**/*.ttl")):
        g.parse(f, format="turtle")

    # The index: the metadata service whose catalogs point (rdfs:seeAlso) at other documents.
    root = max(set(g.subjects(FDP.hasCatalog, None)),
               key=lambda r: sum(1 for c in g.objects(r, FDP.hasCatalog) if (c, RDFS.seeAlso, None) in g))
    index_file = public + f"fdp/{INDEX_FOLDER}/catalog.ttl"
    label = lambda t: text(g, t, SKOS.prefLabel) or str(t).rsplit("#", 1)[-1]

    # Curated catalogs in index order: the dcat:Catalog that lists datasets in each linked file.
    entries = []
    for ref in sorted(g.objects(root, FDP.hasCatalog), key=str):
        f = next(iter(g.objects(ref, RDFS.seeAlso)), None)
        if not f or not str(f).endswith("/catalog.ttl"):
            continue  # live FDPs are crawled by the viewer, not described here
        cat = URIRef(str(f).rsplit("/", 1)[0] + "/catalog")
        if (cat, RDF.type, DCAT.Catalog) in g:
            entries.append((str(f).split("/fdp/", 1)[1].split("/")[0], cat))
    order = [m for m in re.findall(r":catalog-([\w-]+)", (ROOT / "fdp" / INDEX_FOLDER / "catalog.ttl").read_text())]
    entries.sort(key=lambda e: next((i for i, o in enumerate(order) if e[0].startswith(o)), 99))

    # ── static HTML ──
    e = html.escape
    parts = [f'<div class="wrap static-snapshot"><section><h1>{e(text(g, root, DCTERMS.title))}</h1>',
             f'<p>{e(text(g, root, DCTERMS.description))}</p>',
             f'<p>Machine-readable metadata: <a href="{e(index_file)}">FAIR Data Point index (Turtle)</a>.</p></section>']
    ld_datasets = []
    for key, cat in entries:
        parts.append(f'<section><h2><a href="#/{e(key)}">{e(text(g, cat, DCTERMS.title))}</a></h2><p>{e(text(g, cat, DCTERMS.description))}</p><ul>')
        for ds in sorted(g.objects(cat, DCAT.dataset), key=str):
            parts.append(f'<li><strong>{e(text(g, ds, DCTERMS.title))}</strong> — {e(text(g, ds, DCTERMS.description))}<ul>')
            downloads = []
            for d in sorted(g.objects(ds, DCAT.distribution), key=str):
                url = str(next(iter(g.objects(d, DCAT.accessURL)), ""))
                method = next(iter(g.objects(d, DCTERMS.type)), None)
                parts.append(f'<li>{e(label(method)) + ": " if method else ""}<a href="{e(url)}">{e(text(g, d, DCTERMS.title))}</a></li>')
                mt = str(next(iter(g.objects(d, DCAT.mediaType)), ""))
                downloads.append({"@type": "DataDownload", "name": text(g, d, DCTERMS.title),
                                  "contentUrl": str(next(iter(g.objects(d, DCAT.downloadURL)), "")) or url,
                                  **({"encodingFormat": mt.replace(IANA, "")} if mt else {})})
            parts.append("</ul></li>")
            lic = next(iter(g.objects(ds, DCTERMS.license)), None) or next(iter(g.objects(cat, DCTERMS.license)), None)
            ld_datasets.append({
                "@type": "Dataset", "@id": str(ds), "identifier": str(ds), "name": text(g, ds, DCTERMS.title),
                "description": text(g, ds, DCTERMS.description) or text(g, cat, DCTERMS.title),
                "url": f"{public}#/{key}/{str(ds).rsplit('/', 1)[-1]}",
                **({"version": text(g, ds, DCAT_VERSION)} if text(g, ds, DCAT_VERSION) else {}),
                **({"license": str(lic)} if lic else {}),
                "isPartOf": {"@type": "DataCatalog", "name": text(g, cat, DCTERMS.title), "url": f"{public}#/{key}"},
                "distribution": downloads})
        parts.append("</ul></section>")
    parts.append("</div>")

    ld = {"@context": "https://schema.org/", "@type": "DataCatalog", "@id": str(root), "identifier": sorted({str(root), *map(str, g.objects(root, DCTERMS.identifier)), public}),
          "name": text(g, root, DCTERMS.title), "description": text(g, root, DCTERMS.description),
          "url": public, "license": str(next(iter(g.objects(root, DCTERMS.license)), "")),
          "keywords": CONFIG.get("keywords", ["FAIR Data Point", "DCAT"]),
          "creator": [{"@type": "Person", "name": text(g, p, FOAF.name),
                       "sameAs": [str(x) for x in g.objects(p, URIRef("http://www.w3.org/2002/07/owl#sameAs"))]}
                      for p in g.objects(root, DCTERMS.contributor)],
          "dataset": ld_datasets}

    ld_json = json.dumps(ld, ensure_ascii=False).replace("</", "<\\/")   # never close the script element early
    head = instance_head(e, public) + (f'<script type="application/ld+json">{ld_json}</script>\n'
            f'<link rel="describedby" type="text/turtle" href="{e(index_file)}">\n'
            f'<link rel="cite-as" href="{e(str(root))}">\n'
            f'<link rel="license" href="{e(ld["license"])}">\n')
    page = (site / "index.html").read_text()
    page = re.sub(r"<title>.*?</title>\s*<!-- fdp:head[^>]*-->", lambda m: head, page, count=1, flags=re.S)
    page = re.sub(r'(<main id="view">).*?(</main>)', lambda m: m.group(1) + "".join(parts) + m.group(2), page, count=1, flags=re.S)
    (site / "index.html").write_text(page)

    ttl = sorted(p.relative_to(site).as_posix() for p in (site / "fdp").glob("**/*.ttl"))
    (site / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                                      + "".join(f"  <url><loc>{e(public + u)}</loc></url>\n" for u in [""] + ttl) + "</urlset>\n")
    print(f"static snapshot: {len(entries)} catalogs, {len(ld_datasets)} datasets; sitemap with {len(ttl) + 1} URLs")


if __name__ == "__main__":
    main()
