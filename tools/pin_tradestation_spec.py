from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path


def extract_openapi_from_docusaurus_chunk(chunk_path: Path) -> dict[str, object]:
    chunk_text = chunk_path.read_text(encoding="utf-8")
    match = re.search(r"const s=JSON\.parse\('(?P<spec>.*?)'\);const", chunk_text, re.DOTALL)
    if match is None:
        raise ValueError("TradeStation OpenAPI JSON marker was not found")
    json_text = ast.literal_eval("'" + match.group("spec") + "'")
    spec = json.loads(json_text)
    if spec.get("openapi") != "3.0.3":
        raise ValueError("unexpected OpenAPI version in TradeStation spec")
    return filter_v3_only(spec)


def filter_v3_only(spec: dict[str, object]) -> dict[str, object]:
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("TradeStation spec is missing paths")
    v3_paths = {path: value for path, value in paths.items() if path.startswith("/v3/")}
    spec["paths"] = v3_paths

    components = spec.get("components")
    if isinstance(components, dict):
        schemas = components.get("schemas")
        if isinstance(schemas, dict):
            components["schemas"] = referenced_schemas(v3_paths, schemas)

    used_tags = path_tags(v3_paths)
    if isinstance(spec.get("tags"), list):
        spec["tags"] = [
            tag
            for tag in spec["tags"]
            if isinstance(tag, dict) and tag.get("name") in used_tags
        ]
    if isinstance(spec.get("x-tagGroups"), list):
        spec["x-tagGroups"] = [
            group
            for group in spec["x-tagGroups"]
            if isinstance(group, dict)
            and any(tag in used_tags for tag in group.get("tags", ()))
        ]

    remove_non_v3_stream_media_types(spec)
    return spec


def referenced_schemas(
    paths: dict[str, object],
    schemas: dict[str, object],
) -> dict[str, object]:
    referenced = schema_references(paths)
    resolved: set[str] = set()
    pending = list(referenced)
    while pending:
        schema_name = pending.pop()
        if schema_name in resolved:
            continue
        resolved.add(schema_name)
        schema = schemas.get(schema_name)
        if schema is None:
            continue
        pending.extend(schema_references(schema) - resolved)
    return {name: schemas[name] for name in sorted(resolved) if name in schemas}


def schema_references(value: object) -> set[str]:
    references: set[str] = set()
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            references.add(ref.rsplit("/", 1)[-1])
        for child in value.values():
            references.update(schema_references(child))
    elif isinstance(value, list):
        for child in value:
            references.update(schema_references(child))
    return references


def path_tags(paths: dict[str, object]) -> set[str]:
    tags: set[str] = set()
    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            if isinstance(operation, dict):
                tags.update(tag for tag in operation.get("tags", ()) if isinstance(tag, str))
    return tags


def remove_non_v3_stream_media_types(value: object) -> None:
    if isinstance(value, dict):
        content = value.get("content")
        if isinstance(content, dict):
            for media_type in tuple(content):
                if (
                    media_type.startswith("application/vnd.tradestation.streams.")
                    and media_type != "application/vnd.tradestation.streams.v3+json"
                ):
                    del content[media_type]
            if not content:
                del value["content"]
        for child in tuple(value.values()):
            remove_non_v3_stream_media_types(child)
    elif isinstance(value, list):
        for child in value:
            remove_non_v3_stream_media_types(child)


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "usage: pin_tradestation_spec.py <chunk-path> <output-json> <lock-file>",
            file=sys.stderr,
        )
        return 2

    chunk_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    lock_path = Path(sys.argv[3])
    spec = extract_openapi_from_docusaurus_chunk(chunk_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lock_path.write_text(
        json.dumps(
            {
                "api_version": "v3",
                "note": "Extracted from the official TradeStation Docusaurus specification page chunk.",
                "pinned_file": output_path.name,
                "retrieved_at": "2026-05-09",
                "source_url": "https://api.tradestation.com/docs/specification/",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(len([path for path in spec["paths"] if path.startswith("/v3/")]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
