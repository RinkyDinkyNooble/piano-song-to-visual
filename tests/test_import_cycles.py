"""No module in `psv` imports itself back through another.

Counted the way CodeQL counts them: an import inside a function or behind
``TYPE_CHECKING`` is still an edge. Both of the cycles this replaced were
written that way on purpose to stay out of the interpreter's way, and both
still meant two modules that could not be read, changed or loaded one without
the other. One of them made validating a config that named an effect import
the whole renderer, numpy included.
"""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "src"


def _module_name(path: Path) -> str:
    parts = path.relative_to(SOURCE).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def import_graph() -> dict[str, set[str]]:
    """Every `psv` module and the `psv` modules it imports, anywhere in it."""
    files = {_module_name(path): path for path in SOURCE.rglob("*.py")}
    graph: dict[str, set[str]] = {name: set() for name in files}
    for name, path in files.items():
        is_package = path.name == "__init__.py"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    package = name if is_package else name.rpartition(".")[0]
                    for _ in range(node.level - 1):
                        package = package.rpartition(".")[0]
                    base = f"{package}.{base}" if base else package
                # `from psv.render import frame` names a module, not a symbol.
                targets = [base] + [f"{base}.{alias.name}" for alias in node.names]
            else:
                continue
            graph[name].update(
                target for target in targets if target in files and target != name
            )
    return graph


def cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Each strongly connected group of more than one module."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    found: list[list[str]] = []

    def visit(node: str) -> None:
        index[node] = low[node] = len(index)
        stack.append(node)
        for nxt in graph[node]:
            if nxt not in index:
                visit(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in stack:
                low[node] = min(low[node], index[nxt])
        if low[node] == index[node]:
            group = []
            while True:
                member = stack.pop()
                group.append(member)
                if member == node:
                    break
            if len(group) > 1:
                found.append(sorted(group))

    for node in sorted(graph):
        if node not in index:
            visit(node)
    return found


def _without_package_inits(graph: dict[str, set[str]]) -> dict[str, set[str]]:
    """Drop the edge from a package to the modules it re-exports.

    `psv.render` re-exporting `psv.render.frame` is how a package presents its
    contents, and every submodule importing a sibling passes through the
    package on the way. Counting that as a cycle would flag every package that
    has an ``__init__`` that says what is in it.
    """
    return {
        name: {
            target
            for target in targets
            if not (target.startswith(name + ".") and name in graph)
        }
        for name, targets in graph.items()
    }


def test_no_module_imports_itself_back() -> None:
    found = cycles(_without_package_inits(import_graph()))
    assert not found, f"import cycles: {found}"


def test_config_does_not_reach_into_the_renderer() -> None:
    """Validating a config must not need numpy, which the renderer does."""
    assert not {
        target
        for target in import_graph()["psv.config"]
        if target.startswith("psv.render")
    }
