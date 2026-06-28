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


def method_name(operation_id: str, http_method: str, path: str) -> str:
    """Derive a Stainless method name from the operationId.

    Follows the existing config convention: snake_case the operationId and
    rename a leading `get` to `retrieve` (Stainless idiom for single-object GET).
    Falls back to <verb>_<last-path-segment> when there is no operationId.
    """
    if operation_id:
        name = snake(operation_id)
    else:
        segment = snake(re.sub(r"[{}]", "", path.rstrip("/").split("/")[-1]))
        name = f"{http_method}_{segment}"
    if name == "get" or name.startswith("get_"):
        name = "retrieve" + name[3:]
    return name


def common_prefix_segments(paths):
    """Longest common leading path-segment list shared by all paths."""
    split = [[s for s in p.strip("/").split("/") if s] for p in paths]
    if not split:
        return []
    prefix = []
    for seg_group in zip(*split):
        first = seg_group[0]
        if first.startswith("{") or any(s != first for s in seg_group):
            break
        prefix.append(first)
    return prefix


def build_resources(openapi: dict) -> dict:
    """Return the resources tree as plain dicts (insertion-ordered)."""
    paths = openapi.get("paths", {})
    prefix = common_prefix_segments(paths.keys())

    # tag -> {method_name: "verb /path"}, preserving first-seen order
    by_tag = {}
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for verb, op in item.items():
            if verb.lower() not in HTTP_METHODS or not isinstance(op, dict):
                continue
            tags = op.get("tags") or ["default"]
            tag = tags[0]
            mname = method_name(op.get("operationId", ""), verb.lower(), path)
            by_tag.setdefault(tag, {})[mname] = f"{verb.lower()} {path}"

    # leaf: resource per tag
    leaf = {}
    for tag, methods in by_tag.items():
        leaf[snake(tag)] = {"methods": methods}

    # nest leaf under the common prefix segments
    inner = leaf
    for seg in reversed(prefix):
        inner = {seg: {"subresources": inner}}
    return inner


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
    # descend through the prefix to the leaf resource map for an accurate count
    node = tree
    while node and "subresources" in next(iter(node.values())):
        node = next(iter(node.values()))["subresources"]
    print(f"Updated {config_path.name}: wrote {len(node)} resources from {openapi_path.name}.")


if __name__ == "__main__":
    main()
