#!/usr/bin/env python3
"""
Regenerate the `resources:` block of stainless.yml from openapi.yml.

Grouping strategy: by OpenAPI tag (flat). Each operation's first tag becomes a
resource nested under the common path prefix (e.g. public.api.v1). Every other
part of stainless.yml (comments, targets, settings, client_settings, readme,
diagnostics, ...) is left exactly as-is.

Usage:
    python update_resources.py [--openapi openapi.yml] [--config stainless.yml] [--dry-run]
"""

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

HTTP_METHODS = ("get", "put", "post", "delete", "patch", "options", "head", "trace")


def snake(name: str) -> str:
    """camelCase / PascalCase / 'Spaced Words' -> snake_case."""
    name = name.replace(" ", "_").replace("-", "_")
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    name = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", name)
    name = re.sub(r"_+", "_", name)
    return name.lower().strip("_")


GENERIC_VERBS = ("get", "list", "create", "update", "delete")


def noun_stopwords(resource_name: str) -> set:
    """Singular + plural variants of every word in the resource name.

    These are stripped from method names so `listContacts` -> `list`,
    `getHeatmapCompetitors` -> `competitors`, etc.
    """
    stop = set()
    for word in resource_name.split("_"):
        if not word:
            continue
        stop.add(word)
        stop.add(word[:-1] if word.endswith("s") else word + "s")
    return stop


def method_name(operation_id: str, http_method: str, path: str, stop: set) -> str:
    """Derive a short Stainless method name from the operationId.

    Strips the resource noun (e.g. `contact`/`contacts`) and shortens GET verbs:
    a bare `get` becomes `retrieve`; a `get` followed by a real suffix is dropped
    entirely (`getHeatmapCompetitors` -> `competitors`). Other verbs are kept
    (`listLocations` -> `list_locations`).
    """
    if operation_id:
        tokens = snake(operation_id).split("_")
    else:
        segment = snake(re.sub(r"[{}]", "", path.rstrip("/").split("/")[-1]))
        tokens = [http_method, segment]

    tokens = [t for t in tokens if t not in stop] or [http_method]

    if tokens[0] == "get":
        tokens = ["retrieve"] if len(tokens) == 1 else tokens[1:]

    return "_".join(tokens)


def build_resources(openapi: dict) -> dict:
    """Return a flat {resource: {methods: {...}}} tree (insertion-ordered)."""
    paths = openapi.get("paths", {})

    # tag -> list of (verb, path, operationId), preserving first-seen order
    by_tag = {}
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for verb, op in item.items():
            if verb.lower() not in HTTP_METHODS or not isinstance(op, dict):
                continue
            tag = (op.get("tags") or ["default"])[0]
            by_tag.setdefault(tag, []).append((verb.lower(), path, op.get("operationId", "")))

    resources = {}
    for tag, ops in by_tag.items():
        rname = snake(tag)
        stop = noun_stopwords(rname)
        methods = {}
        for verb, path, op_id in ops:
            name = method_name(op_id, verb, path, stop)
            if name in methods:  # collision: fall back to the fuller name
                fallback = method_name(op_id, verb, path, set())
                name = fallback if fallback not in methods else f"{name}_{verb}"
            methods[name] = f"{verb} {path}"
        resources[rname] = {"methods": methods}
    return resources


# ---- YAML emission (2-space indent, block style, matching stainless.yml) ----

def emit(node, indent=0):
    pad = "  " * indent
    lines = []
    for key, val in node.items():
        if isinstance(val, dict):
            lines.append(f"{pad}{key}:")
            lines.extend(emit(val, indent + 1))
        else:
            lines.append(f"{pad}{key}: {val}")
    return lines


def render_resources_block(tree: dict) -> str:
    lines = ["resources:"]
    lines.extend(emit(tree, indent=1))
    return "\n".join(lines) + "\n"


def replace_resources_block(config_text: str, new_block: str) -> str:
    """Replace the top-level `resources:` block, keeping everything else."""
    lines = config_text.splitlines(keepends=True)
    start = end = None
    for i, line in enumerate(lines):
        if re.match(r"^resources:\s*$", line):
            start = i
            break
    if start is None:
        raise SystemExit("Could not find a top-level `resources:` block in the config.")
    # block ends at the next top-level key (column-0, non-comment, non-blank)
    for j in range(start + 1, len(lines)):
        if re.match(r"^[A-Za-z0-9_]", lines[j]):
            end = j
            break
    if end is None:
        end = len(lines)
    return "".join(lines[:start]) + new_block + "".join(lines[end:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--openapi", default="openapi.yml")
    ap.add_argument("--config", default="stainless.yml")
    ap.add_argument("--dry-run", action="store_true", help="print result, do not write")
    args = ap.parse_args()

    base = Path(__file__).resolve().parent
    openapi_path = (base / args.openapi) if not Path(args.openapi).is_absolute() else Path(args.openapi)
    config_path = (base / args.config) if not Path(args.config).is_absolute() else Path(args.config)

    with open(openapi_path, "r", encoding="utf-8") as f:
        openapi = yaml.safe_load(f)

    tree = build_resources(openapi)
    new_block = render_resources_block(tree)

    config_text = config_path.read_text(encoding="utf-8")
    updated = replace_resources_block(config_text, new_block)

    if args.dry_run:
        sys.stdout.write(new_block)
        return

    config_path.write_text(updated, encoding="utf-8")
    print(f"Updated {config_path.name}: wrote {len(tree)} resources from {openapi_path.name}.")


if __name__ == "__main__":
    main()
