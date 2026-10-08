# Persistence policy

This policy covers the FDP Ecosystem Template metadata in this repository: the Turtle files under `fdp/`, and the website at <https://staticfdp.github.io/fdp-ecosystem-template/>.

## What is kept

- **Every version of every metadata record is kept.** The metadata lives in git, and every change is a commit in the public history of [StaticFDP/fdp-ecosystem-template](https://github.com/StaticFDP/fdp-ecosystem-template). Any earlier state can be retrieved by commit.
- **Records are not deleted silently.** When a data source disappears or a record is withdrawn, its catalog stays in the history. The removal is a reviewed pull request that says why.
- **Identifiers are not reused.** A catalog folder name (`fdp/<id>-fdp/`) and the local names of datasets and distributions are never given to a different resource.

## Where it is served

- **Canonical IRIs:** `https://raw.githubusercontent.com/StaticFDP/fdp-ecosystem-template/main/fdp/…`
- **Website copy:** <https://staticfdp.github.io/fdp-ecosystem-template/fdp/…>, served as `text/turtle`

If the repository moves, every IRI is rewritten with `scripts/set-base.sh` and the move is announced in the README.

## What is not promised

This FDP describes resources run by others. Their availability and persistence are governed by their own policies. A distribution that stops working is marked as such in its description, or removed through a reviewed pull request; the history keeps the earlier description.

## Contact

Open an issue at <https://github.com/StaticFDP/fdp-ecosystem-template/issues>. Maintainer: Andra Waagmeester ([ORCID 0000-0001-9773-4008](https://orcid.org/0000-0001-9773-4008)).
