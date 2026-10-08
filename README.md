# FDP Ecosystem Template

Start your own static [FAIR Data Point](https://specs.fairdatapoint.org/) ecosystem: Turtle metadata in a GitHub repository, served by GitHub Pages, with a browsable website, self-service contributions and automatic FAIR assessment. No server needed.

Each catalog describes one resource and **every way to obtain it**: portal, APIs, SPARQL endpoints, bulk dumps, cloud buckets, copies in aggregators. Each way is typed with an access method and comes with a "How to get it" recipe.

**Start here:** <https://fdp.semscape.org/ecosystems/> (the landing page, with all ecosystems and a one-click start).
**Demo:** <https://fdp.semscape.org/fdp-ecosystem-template/> (this repository, with one example source).
**In use:** [Biodiversity FDP](https://koetai.github.io/biodiversity-fdp/) ([source](https://github.com/Koetai/biodiversity-fdp)).

## Start your own

1. Click **Use this template → Create a new repository**. Keep it **public**: GitHub Actions and Pages are then free.
2. In the new repository, go to **Settings → Pages** and set **Source** to **GitHub Actions**.
3. Go to **Actions → Deploy viewer to GitHub Pages → Run workflow**. The first deploy initialises your copy:
   - points `fdp.config.json` at your repository and site
   - rebases every IRI
   - writes your `README.md` and `PERSISTENCE.md`

   Your site appears at `https://<owner>.github.io/<repository>/`.
4. Make it yours:
   - **`fdp.config.json`:** name, headline (`*word*` is emphasised), description, keywords, maintainer, colours, fonts
   - **`assets/logo.svg`:** your logo, also used as the favicon. It is drawn in the theme colour wherever it uses `currentColor`.
5. Replace the example: remove `fdp/wikidata-fdp/` and its entry in `fdp/index/catalog.ttl`. Then add sources through **Issues → New issue → Add a data source**, or follow [CONTRIBUTING.md](CONTRIBUTING.md).

Optional:
- **Let the bot open pull requests:** *Settings → Actions → General → Allow GitHub Actions to create and approve pull requests*. This is used by the issue form and the engine updates.
- **Persistent identifiers:** register a [w3id](https://w3id.org), then run `scripts/set-base.sh https://w3id.org/<your-id>/`.

## What you get

| | |
|---|---|
| **Website** | Index → catalog → dataset → "ways to get it", search, access-method filters, contributors, FAIR results; light and dark mode; static HTML and schema.org JSON-LD for search engines |
| **Live FDPs** | An index entry can point at someone else's FAIR Data Point. The site crawls it in the browser. |
| **Self-service** | An issue form becomes a validated pull request (`scripts/add_source.py`) |
| **Credit** | Contributors from commits, pull requests and form submissions, with names, Wikidata items and ORCID iDs from Wikidata (P2037). Published in the site and as RDF (`dcterms:contributor`). |
| **FAIR assessment** | Walks every level of the FDP and runs the [OSTrails FAIR Champion](https://tests.ostrails.eu/) tests: weekly, after deploys and on demand |
| **Validation** | Every push checks that the Turtle parses, the DCAT shape, the vocabulary terms and the index consistency |

## Engine and content

| Yours (content) | The engine (from this template) |
|---|---|
| `fdp.config.json`, `fdp/`, `assets/`, `README.md`, `PERSISTENCE.md` | `scripts/`, `site/`, `.github/`, `fair/tests.txt`, `CONTRIBUTING.md`, `CLAUDE.md`. The full list is in [`.fdp-engine`](.fdp-engine). |

Engine files read everything instance-specific from `fdp.config.json` (via `scripts/fdpconfig.py`), so the engine can be updated without touching your content:

- **Automatically:** *Actions → Update engine* (also runs monthly) opens a pull request with the template's latest engine.

  GitHub's built-in token cannot change workflow files. Add a repository secret `ENGINE_UPDATE_TOKEN` (a fine-grained token with Contents, Pull requests and Workflows write access) to include them; otherwise the pull request lists the workflow changes for you.
- **Locally:** `scripts/update-engine.sh`, then review with `git diff` and commit.

## Local preview

```bash
pip install rdflib
python3 scripts/validate.py
scripts/build-site.sh && python3 -m http.server -d _site 8000
```

## License

The code is released under the [MIT](LICENSE) license. The FDP metadata is released under CC0 (see the index catalog).
