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
    return spec


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
