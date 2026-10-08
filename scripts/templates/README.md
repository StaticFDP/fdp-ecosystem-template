# {{name}}

A static [FAIR Data Point](https://specs.fairdatapoint.org/) published from this repository and browsable at **<{{siteUrl}}>**.

Each catalog describes one resource and every way to obtain it: portals, APIs, SPARQL endpoints, bulk files, cloud buckets and copies in aggregators. Each way is typed with an access method and comes with a "How to get it" recipe.

## Make it yours

- **`fdp.config.json`** holds the name, headline, description, colours, fonts, logo, maintainer and IRIs.
- **`fdp/`** holds the catalogs. The example catalog can be replaced; add sources with the [issue form](../../issues/new?template=add-data-source.yml) or see [CONTRIBUTING.md](CONTRIBUTING.md).
- **`assets/logo.svg`** is the logo, used in the site and as the favicon.

## How it works

- **Deploys:** GitHub Pages deploys on every push to `{{branch}}`. The FAIR assessment runs weekly and after deploys; the results are on the site under *FAIR tests*.
- **IRIs:** all IRIs start with `{{baseIri}}`. For persistent identifiers, register a w3id and run `scripts/set-base.sh https://w3id.org/<your-id>/`.
- **More ecosystems:** see the others, or start another one, at <{{templateLanding}}>.
- **Engine:** the engine (scripts, viewer and workflows, listed in [`.fdp-engine`](.fdp-engine)) comes from the [FDP ecosystem template](https://github.com/{{template}}). Get improvements with *Actions → Update engine*, or run `scripts/update-engine.sh`.
