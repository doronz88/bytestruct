# Contributing to bytestruct

## Where things are

- `bytestruct/__init__.py` holds the whole library. A comment after the imports lists its sections in
  order, from the `ByteReader` through the codecs and field formats to the `BinaryStruct` record compiler.
- `bytestruct/exceptions.py` holds the error tree (`BinaryDataError` and its subclasses).
- `tests/test_bytestruct.py` tests the library. `tests/test_docs_examples.py` runs the examples from the
  documentation, so a docs change that breaks an example fails CI.
- `docs/` is the [documentation site](https://doronz88.github.io/bytestruct/), built with MkDocs Material.
  The API reference (`docs/api.md`) is generated from the docstrings.

Start with [How it works](https://doronz88.github.io/bytestruct/how-it-works/) (`docs/how-it-works.md`). It
walks through what `BinaryStruct.__init_subclass__` compiles when a record class is defined, and how parse
and build use the result. The private names it mentions (`_layout`, `_runs`, `_specialize`, ...) match the
source.

## Setting up

Python 3.9 is the oldest supported version, and [uv](https://docs.astral.sh/uv/) is the easiest way to get
it:

```shell
git clone https://github.com/doronz88/bytestruct
cd bytestruct
uv venv
uv pip install -e ".[test]"
uvx pre-commit install
```

## Checks

CI runs these on Python 3.9 to 3.15 on Linux, macOS and Windows:

```shell
uv run --extra test pytest            # the tests
uvx pre-commit run --all-files        # ruff check, ruff format and pyright (uses .venv)
uv run --python 3.9 --extra test pytest  # the oldest supported Python
```

- pyright (pinned in `.pre-commit-config.yaml`) must report 0 errors. Suppressions must be rule-specific
  (`# pyright: ignore[ruleName]`).
- Annotations are evaluated at runtime (`typing.get_type_hints`), so keep them valid on Python 3.9: use
  `Optional`/`Union`, not `X | Y`.

## Documentation

```shell
uv pip install -e ".[docs]"   # Python 3.10+
mkdocs serve                  # live preview at http://127.0.0.1:8000
mkdocs build --strict         # what CI runs; warnings fail it
```

Public names need a docstring, since it is what the API reference shows. A new guide page goes in the `nav`
(and the `llmstxt` sections) of `mkdocs.yml`.

## Changes

- Keep a behavior change and its tests in the same commit, and update the docs in the same pull request.
- Commit subjects are scoped, for example `bytestruct: Raise BinaryBuildError for ...` or `docs: ...`.
- The hot paths (the closures `_specialize` installs, `BinaryStruct._unpack` and `_build_fixed`) are
  benchmarked in [How it works](https://doronz88.github.io/bytestruct/how-it-works/#benchmark). If you
  change them, rerun the benchmark and include the numbers in the pull request.

## Releasing

The version comes from the git tag (`setuptools_scm`); no file holds it. Creating a GitHub release builds
the package and publishes it to PyPI with trusted publishing:

```shell
gh release create vX.Y.Z --target master --title vX.Y.Z --generate-notes
```
