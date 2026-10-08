# Contributing to an FDP ecosystem

This repository is an FDP ecosystem built from the [FDP ecosystem template](https://github.com/StaticFDP/fdp-ecosystem-template). Everything instance-specific is in [`fdp.config.json`](fdp.config.json), [`fdp/`](fdp) and `assets/`; the engine (scripts, viewer, workflows) is listed in [`.fdp-engine`](.fdp-engine) and is updated from the template.

## Adding a data source

There are three ways to add a resource to this FDP. All of them end in a pull request: CI validates the Turtle, and after the merge GitHub Pages redeploys the site (`siteUrl` in [`fdp.config.json`](fdp.config.json)).

| Route | Use it when | You write |
|---|---|---|
| [1. Issue form](#1-the-issue-form-no-turtle-needed) | You want to describe a source without touching Turtle | A web form |
| [2. Link a live FAIR Data Point](#2-link-a-live-fair-data-point) | The source already runs its own FDP | One URL |
| [3. By hand or with the script](#3-by-hand-or-with-the-script) | You want full control, several datasets, or extra metadata | Turtle, or a JSON spec |

### 1. The issue form (no Turtle needed)

1. Open [**New issue → Add a data source**](../../issues/new?template=add-data-source.yml).
2. Choose **Curated**, fill in the name, publisher and licence, and list the ways to get the data, one per line:

   ```text
   access method | title | URL | how to get it
   ```

   Start a line with `# title | version | description` to begin another dataset.
3. Submit. The *Add data source from issue* workflow builds `fdp/<id>-fdp/catalog.ttl`, adds it to the index, validates it and opens a pull request. If the repository does not allow Actions to open pull requests, the workflow comments with a one-click link instead.
4. Wrong or missing values? Edit the issue. The workflow regenerates the branch. If it can't parse the form, it comments with the reason.
5. A maintainer reviews and merges.

### 2. Link a live FAIR Data Point

If the source publishes its own FDP (for example a germplasm bank running FAIR-in-a-box, or a Koetai node), don't copy its metadata: link it. The viewer crawls the FDP in the visitor's browser: FDP → catalogs → datasets and data services → distributions → access services. It is therefore always current.

Requirements for the remote FDP:

- `curl -H 'Accept: text/turtle' <url>` returns Turtle that lists `fdp:metadataCatalog` (or `dcat:catalog`).
- The server sends `Access-Control-Allow-Origin` (`*`, or this site's origin), so browsers may read it.

Use the issue form with **Live**, or add an entry to the index, `fdp/<indexFolder>/catalog.ttl` (`indexFolder` is set in `fdp.config.json`):

```turtle
:catalog-bgv-live
    a dcat:Catalog ;
    dcterms:title "BGV-UPM germplasm bank"@en ;
    rdfs:seeAlso <https://w3id.org/bgv-fdp> .
```

Also add `:catalog-bgv-live` to the `fdp:hasCatalog` list at the top of that file.

Remote FDPs rarely type their distributions with our access-method vocabulary. The viewer infers one from the media type, the endpoint URL and the presence of an access service, and labels it *inferred*. If you want "How to get it" notes and exact types, describe the source as a curated catalog (route 1 or 3) instead. You can also do both, as was done for FLAIR-GG.

### 3. By hand or with the script

#### With the script (JSON spec)

```bash
cp scripts/example-source.json my-source.json      # edit it
python3 scripts/add_source.py my-source.json        # writes fdp/<slug>-fdp/catalog.ttl and the index entry
python3 scripts/validate.py
```

The spec fields are:

- `slug`, `mode` (`curated` or `live`), `title`, `description`, `license`, `landingPage`
- `publisher {name, homepage}`
- `fdpUrl` (for live sources)
- `datasets[]`, each with `title`, `version`, `description`, `issued` (YYYY-MM-DD), `license`, `landingPage` and `distributions[]`
- each distribution has `method`, `title`, `url`, and optionally `download`, `endpoint`, `docs`, `mediaType` and `howto`

The script refuses invalid slugs, URLs or media types, unknown access methods, and existing folders.

#### By hand

1. Copy a small catalog, e.g. `fdp/geonames-fdp/catalog.ttl`, to `fdp/<slug>-fdp/catalog.ttl`. Change the `@prefix :` line and the root IRI to the new folder.
2. Add a `:catalog-<slug>` entry to the index, `fdp/<indexFolder>/catalog.ttl`, (title, licence, `rdfs:seeAlso` to the new file), and add it to `fdp:hasCatalog`.
3. Run `python3 scripts/validate.py`, then preview:

   ```bash
   scripts/build-site.sh && python3 -m http.server -d _site 8000
   ```

## Conventions

- **Three levels:** `dcat:Catalog` (one per infrastructure) → `dcat:Dataset` (one per release, snapshot or live state) → `dcat:Distribution` (one per way to get it).
- **Every distribution** has:
  - `dcterms:type` from [`fdp/vocab/access-methods.ttl`](fdp/vocab/access-methods.ttl): `web-portal`, `rest-api`, `sparql-endpoint`, `bulk-download`, `cloud-object-storage`, `darwin-core-archive`, `rdf-dump`, `source-repository`, `aggregator-mirror`, `oai-pmh`, `sql-query` or `change-feed`
  - a `dcterms:description` that starts with **"How to get it:"**: a concrete recipe (URL pattern, CLI command, required parameters, account or key needed)
  - `dcat:accessURL`, plus `dcat:downloadURL` for a single file
- **Endpoints** get a `dcat:DataService` (via `dcat:accessService`) with `dcat:endpointURL`, `dcat:endpointDescription` (OpenAPI or docs) and `dcterms:conformsTo` (e.g. the SPARQL 1.1 protocol).
- **Versions** use `dcat:version` (DCAT 3). There is no `dcterms:version`; `validate.py` rejects terms that DCMI Terms or FOAF don't define.
- **Media types** are IANA IRIs (`https://www.iana.org/assignments/media-types/…`). Formats come from the EU file-type authority.
- **Copies in aggregators** (GBIF, QLever, Koetai…) are distributions of the original dataset, typed `aggregator-mirror` or by their access method, with `prov:wasDerivedFrom` pointing at what they were derived from. Provenance is kept, not deduplicated.
- **Verify before you write.** Check that URLs resolve and that endpoints answer a real query. Record counts and dates as "on YYYY-MM-DD". If something is broken, say so in the description rather than leaving it out.
- All IRIs start with `baseIri` from `fdp.config.json`. To move them (another repository, or a w3id), run `scripts/set-base.sh <org> <repo> [branch]` or `scripts/set-base.sh <base IRI>`; it also updates the config.

## Credit

The site's Contributors section is rebuilt on every deploy by `scripts/contributors.py`. It draws on:

- the git history
- merged pull requests
- "Add a data source" issues: whoever fills in the form is credited for the catalog, even though the bot makes the commit

Each catalog page shows who added it and who improved it. The same information is part of the FDP metadata in `fdp/<indexFolder>/contributors.ttl`, which the index links with `rdfs:seeAlso`:

- `dcterms:creator` and `dcterms:contributor` on the index and on each catalog
- `foaf:Person` nodes with the person's GitHub account, and `owl:sameAs` links to their Wikidata item and ORCID

The deploy regenerates the file and commits it when it changes; don't edit it by hand.

Names come from Wikidata when your GitHub account is linked there. Add **[P2037 (GitHub username)](https://www.wikidata.org/wiki/Property:P2037)** to your Wikidata item, and the next deploy also shows your Wikidata item and ORCID iD. Without it, the name comes from your GitHub profile, or else from your git author name.

## FAIR assessment

The *FAIR assessment* workflow ([`.github/workflows/fair.yml`](.github/workflows/fair.yml)) walks the FDP level by level, following `ldp:contains`, `fdp:metadataCatalog`/`fdp:hasCatalog`, `dcat:catalog`, `dcat:dataset`, `dcat:service` and `dcat:distribution`. It runs the [OSTrails FAIR Champion](https://tests.ostrails.eu/) tests listed in [`fair/tests.txt`](fair/tests.txt) on every resource that resolves.

- **When it runs:** every Monday, after each deploy triggered by a push, or manually (*Actions → FAIR assessment → Run workflow*). You can choose another FDP to assess and a depth: `fdp`, `catalog`, `dataset` or `distribution`.
- **Where results appear:**
  - the run's summary page
  - a `fair-report` artifact
  - the website, at `#/fair`, with a badge on each catalog page
- **Resources that are not tested:** those whose IRIs don't resolve are listed but not tested. That is itself a FAIR finding (F1/A1).
- **Running it locally:**

  ```bash
  python3 scripts/fair_assess.py --depth catalog --out fair-report    # start and IRI mapping come from fdp.config.json
  ```

The test service is shared infrastructure, so keep deep runs occasional.

