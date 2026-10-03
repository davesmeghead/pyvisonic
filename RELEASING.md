# Releasing pyvisonic

Releases are built and published by the public GitHub Actions workflow
`.github/workflows/publish.yml`. The `pypi` environment requires approval by
davesmeghead before uploading. No PyPI API token is needed.

## One-time PyPI setup

As the owner of the existing `pyvisonic` project, open:
https://pypi.org/manage/project/pyvisonic/settings/publishing/

Add a GitHub Trusted Publisher with:

- Owner: `davesmeghead`
- Repository: `pyvisonic`
- Workflow filename: `publish.yml`
- Environment: `pypi`

Use the existing project's Publishing page, not a pending publisher for a new project.

## Publish a release

1. Update `__version__` in `src/pyvisonic/__init__.py` to an unused PyPI version.
2. Commit and push to `main`. Wait for the test/build workflow to pass.
3. Tag that commit with the matching version and push the tag. For the prepared
   next release:

   ```sh
   git switch main
   git pull --ff-only
   git tag -a v4.0.3 -m "Release 4.0.3"
   git push origin v4.0.3
   ```

4. Open the run in GitHub Actions. After tests and packaging pass, review the
   commit and approve the deployment to the `pypi` environment.
5. Check that PyPI lists the new version with Trusted Publishing and provenance.
6. Update consuming applications to use the new version after testing it.

A push to `main`, a pull request, or a manual workflow run only tests and builds;
only a pushed version tag can publish. The workflow rejects tags that do not
match the installed package version. The publishing job downloads the artifacts
from the build job and generates PyPI attestations through the official action.

## Imported release

The `v4.0.2` tag records the source imported from the existing PyPI source archive.
That version was originally built and published locally, not through this CI.
The source archive SHA-256 is:

`0c8c3f6411c5cb8cb34a135d4cf4f9b03e86c57394f5cc08519238c1f2aaa756`

Generated `PKG-INFO` and `*.egg-info` files are omitted from version control.
Do not push the old tag again to trigger publishing: 4.0.2 is already on PyPI.

## Local checks

```sh
python3.14 -m venv .venv
. .venv/bin/activate
python -m pip install '.[test,examples]'
python -m pytest -q
python -m build
python -m twine check --strict dist/*
```
