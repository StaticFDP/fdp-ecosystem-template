# Persistence policy

This policy covers the {{name}} metadata in this repository: the Turtle files under `fdp/`, and the website at <{{siteUrl}}>.

## What is kept

- **Every version of every metadata record is kept.** The metadata lives in git, and every change is a commit in the public history of [{{repository}}](https://github.com/{{repository}}). Any earlier state can be retrieved by commit.
- **Records are not deleted silently.** When a data source disappears or a record is withdrawn, its catalog stays in the history. The removal is a reviewed pull request that says why.
- **Identifiers are not reused.** A catalog folder name (`fdp/<id>-fdp/`) and the local names of datasets and distributions are never given to a different resource.

## Where it is served

- **Canonical IRIs:** `{{baseIri}}…`
- **Website copy:** <{{siteUrl}}fdp/…>, served as `text/turtle`

If the repository moves, every IRI is rewritten with `scripts/set-base.sh` and the move is announced in the README.

## What is not promised

This FDP describes resources run by others. Their availability and persistence are governed by their own policies. A distribution that stops working is marked as such in its description, or removed through a reviewed pull request; the history keeps the earlier description.

## Contact

Open an issue at <https://github.com/{{repository}}/issues>.{{maintainerLine}}
