
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
