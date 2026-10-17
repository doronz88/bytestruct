"""Run the Python examples of the documentation, so that they stay true.

Each page's ```python blocks run in order, in one namespace (later blocks build on earlier ones). In a
top-level ``try``/``except`` without an ``else``, the example demonstrates an error, so it fails when nothing is
raised. A block preceded by ``<!-- docs-test: skip -->`` is not run; a page whose examples import
construct is skipped when construct is not installed.
"""

import ast
import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGES = [*sorted((ROOT / "docs").glob("*.md")), ROOT / "README.md"]
BLOCK = re.compile(r"^(?P<marker><!-- docs-test: skip -->\n+)?```python\n(?P<code>.*?)^```$", re.MULTILINE | re.DOTALL)


def _examples(page: Path) -> list[tuple[int, str]]:
    text = page.read_text(encoding="utf-8")
    return [
        (text.count("\n", 0, match.start("code")) + 1, match.group("code"))
        for match in BLOCK.finditer(text)
        if not match.group("marker")
    ]


class _ExpectRaise(ast.NodeTransformer):
    """Give every top-level ``try``/``except`` without an ``else`` one that fails: the example promised an error.

    Functions are left alone: their ``try``/``except`` is part of the example's logic.
    """

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
        return node

    def visit_Try(self, node: ast.Try) -> ast.Try:
        self.generic_visit(node)
        if node.handlers and not node.orelse and not node.finalbody:
            node.orelse = ast.parse("raise AssertionError('the example expected an exception')").body
        return node


def test_every_page_has_examples() -> None:
    assert all(_examples(page) for page in PAGES if page.name not in {"api.md", "how-it-works.md"})


@pytest.mark.parametrize("page", PAGES, ids=[page.name for page in PAGES])
def test_examples_run(page: Path) -> None:
    examples = _examples(page)
    needs_construct = any(re.search(r"^\s*(import|from) construct\b", code, re.MULTILINE) for _, code in examples)
    if needs_construct and importlib.util.find_spec("construct") is None:
        pytest.skip("construct is not installed")
    # A real module, so that dataclasses and typing.get_type_hints can resolve the examples' classes.
    module = types.ModuleType(f"docs_example_{page.stem.replace('-', '_')}")
    sys.modules[module.__name__] = module
    try:
        for line, code in examples:
            tree = ast.fix_missing_locations(_ExpectRaise().visit(ast.parse(code)))
            ast.increment_lineno(tree, line - 1)
            exec(compile(tree, str(page), "exec"), module.__dict__)
    finally:
        del sys.modules[module.__name__]
