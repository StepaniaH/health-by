## Running tests

Install the Python development dependencies (`pip install -e '.[dev]'`) and Node.js before running
`python -m pytest`. The browser module tests execute Node.js directly and are not skipped when it is
missing. Tests use a synthetic source registered only by the test fixtures; product startup is tested
separately with the default Google Health source and an empty database.

## Maintainer releases

Releases are tag-only: pushing a `v*` tag creates the GitHub release from `CHANGELOG.md`
and publishes the image.

Before tagging:

1. Move the notes out of `## Unreleased` into a dated `## [X.Y.Z]` section and set the same
   version in `pyproject.toml` and `health_by/__init__.py`.
2. Run `pytest`, `scripts/check_i18n.py` and `scripts/check_web_refs.py` on a clean checkout.
3. Confirm the repository has valid `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` secrets.
4. Create and push an annotated tag:

   ```sh
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin vX.Y.Z
   ```

The tag publishes `stepaniah/health-by:vX.Y.Z` and moves `latest`, and the same image to
`ghcr.io/stepaniah/health-by`. Do not move or reuse a published tag; roll back by deploying an
earlier version tag.

## Local development data

The product has no demo mode and ships no sample data. `devtools/make_dev_data.py` builds a local
database with the same shape as a real one, using the synthetic generator that stays outside the
packaged `health_by` module.

```sh
.venv/bin/python -m devtools.make_dev_data            # writes ./dev-data, last 30 days
HEALTH_BY_DATA=./dev-data .venv/bin/python -m health_by.cli serve
```

`--days N` and `--out PATH` change the window and the destination. `dev-data/` is ignored by git.
The stored provider stays at its product default, so the database opens normally; syncing it again
requires real credentials, but browsing, analysis and the web interface work as they do with real data.

The generated database reports more raw coverage days than normalized days — 13 more for any window.
That is how the generator fetches (each day returns a 14-day payload, so the earliest request reaches
13 days before the first synced day), not missing data.
