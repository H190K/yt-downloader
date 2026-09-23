"""argv helpers shared by unit tests."""
from __future__ import annotations


def values_of(args: list[str], flag: str) -> list[str]:
    """All values that follow ``flag`` in an argv list (flags may repeat, e.g. ``-P``)."""
    return [args[i + 1] for i, a in enumerate(args[:-1]) if a == flag]


def value_of(args: list[str], flag: str) -> str | None:
    vals = values_of(args, flag)
    return vals[-1] if vals else None


def output_templates(args: list[str]) -> list[str]:
    """Values of every ``-o`` / ``--output`` option (with or without a ``TYPE:`` prefix)."""
    return values_of(args, "-o") + values_of(args, "--output")


def main_output_template(args: list[str]) -> str:
    """The default (non ``TYPE:``-prefixed) output template."""
    for t in output_templates(args):
        if t.split(":", 1)[0] not in ("temp", "thumbnail", "subtitle", "infojson", "pl_thumbnail",
                                      "description", "annotation", "chapter", "pl_video",
                                      "pl_infojson", "pl_description", "link"):
            return t
    raise AssertionError(f"no default -o template in {args}")
