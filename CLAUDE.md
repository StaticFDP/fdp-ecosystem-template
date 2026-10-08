# FDP ecosystem — notes for Claude

A static FAIR Data Point in Turtle (`fdp/`) with a client-side viewer (`site/`), deployed to GitHub Pages and built from the [FDP ecosystem template](https://github.com/StaticFDP/fdp-ecosystem-template).

- **Instance vs engine.** Everything instance-specific (name, IRIs, theme, logo, maintainer) is in `fdp.config.json`, `fdp/` and `assets/`. Engine files are listed in `.fdp-engine`. Never hardcode instance values in engine files; read them via `scripts/fdpconfig.py`.
- **Adding a data source.** Follow [CONTRIBUTING.md](CONTRIBUTING.md): three DCAT levels, and on every distribution an access-method `dcterms:type` plus a "How to get it:" description. Prefer `scripts/add_source.py` with a JSON spec (`scripts/example-source.json`). For a source with its own FDP, add a live index entry.
- **Verify before writing.** Check that URLs resolve and endpoints answer a real query; state counts and dates "on YYYY-MM-DD".
- **After any change**, validate and preview:

  ```bash
  python3 scripts/validate.py
  scripts/build-site.sh && python3 -m http.server -d _site 8000
  ```

- **Moving IRIs.** Use `scripts/set-base.sh` (to another repository or a w3id); it also updates the config.
