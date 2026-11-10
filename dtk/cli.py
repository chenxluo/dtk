"""Command-line interface for the Derived Store."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from .evaluation import evaluate_file
from .search import get_tag, search
from .store import build_database, database_info, open_db


def _default_db() -> Path:
    return Path(os.environ.get("DTK_DB", "data/dtk.db"))


def _default_source() -> str | None:
    return os.environ.get("DTK_SOURCE_DB")


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _print_search(result: dict[str, Any]) -> None:
    print(f"query: {result['query']}")
    print(f"matched_by: {result['matched_by']}")
    if not result["results"]:
        print("no matching tag")
        return

    for item in result["results"]:
        alias = f" via {item['matched_alias']}" if item.get("matched_alias") else ""
        print(
            f"{item['rank']}. {item['tag']} [{item['type']}] "
            f"posts={item['post_count']} match={item['matched_by']}{alias}"
        )
        if item.get("wiki_excerpt"):
            print(f"   {item['wiki_excerpt']}")


def _print_tag(record: dict[str, Any]) -> None:
    print(f"tag: {record['tag']}")
    print(f"tag_id: {record['tag_id']}")
    print(f"type: {record['type']}")
    print(f"post_count: {record['post_count']}")
    print("aliases:")
    for alias in record["aliases"]:
        print(f"  - {alias}")
    print("wiki:")
    print(record["wiki"] or "(none)")


def _parse_ks(raw: str) -> tuple[int, ...]:
    try:
        ks = tuple(sorted({int(value) for value in raw.split(",") if value.strip()}))
    except ValueError as error:
        raise ValueError("--k must be comma-separated positive integers") from error
    if not ks or any(k <= 0 for k in ks):
        raise ValueError("--k must contain positive integers")
    return ks


def _add_db_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        default=str(_default_db()),
        help="Derived Store path (default: %(default)s; env: DTK_DB)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dtk", description="Danbooru Tag Knowledge CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser(
        "import", help="build a Derived Store from a read-only Source Snapshot"
    )
    import_parser.add_argument(
        "--source",
        default=_default_source(),
        required=_default_source() is None,
        help="source SQLite path (env: DTK_SOURCE_DB)",
    )
    import_parser.add_argument("--db", default=str(_default_db()))
    import_parser.add_argument("--json", action="store_true", help="print import manifest as JSON")

    search_parser = subparsers.add_parser("search", help="search Canonical Tags")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.add_argument("--json", action="store_true")
    _add_db_argument(search_parser)

    get_parser = subparsers.add_parser("get", help="show one Canonical Tag record")
    get_parser.add_argument("tag")
    get_parser.add_argument("--json", action="store_true")
    _add_db_argument(get_parser)

    info_parser = subparsers.add_parser("info", help="show snapshot and import manifest")
    info_parser.add_argument("--json", action="store_true")
    _add_db_argument(info_parser)

    eval_parser = subparsers.add_parser("eval", help="evaluate a JSONL benchmark")
    eval_parser.add_argument("benchmark")
    eval_parser.add_argument("--k", default="5,10")
    eval_parser.add_argument("--json", action="store_true")
    _add_db_argument(eval_parser)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "import":
            manifest = build_database(args.source, args.db)
            if args.json:
                _print_json(manifest)
            else:
                print(f"imported {manifest['derived_tag_rows']} tags to {manifest['destination']}")
                print(
                    f"wiki={manifest['derived_wiki_rows']} "
                    f"aliases={manifest['derived_alias_rows']}"
                )
            return 0

        if args.command == "info":
            conn = open_db(args.db)
            try:
                info = database_info(conn)
            finally:
                conn.close()
            if args.json:
                _print_json(info)
            else:
                for key in sorted(info):
                    print(f"{key}: {info[key]}")
            return 0

        if args.command == "search":
            conn = open_db(args.db)
            try:
                result = search(conn, args.query, limit=args.limit)
            finally:
                conn.close()
            if args.json:
                _print_json(result)
            else:
                _print_search(result)
            return 0

        if args.command == "get":
            conn = open_db(args.db)
            try:
                record = get_tag(conn, args.tag)
            finally:
                conn.close()
            if record is None:
                print(f"tag not found: {args.tag}", file=sys.stderr)
                return 1
            if args.json:
                _print_json(record)
            else:
                _print_tag(record)
            return 0

        if args.command == "eval":
            ks = _parse_ks(args.k)
            conn = open_db(args.db)
            try:
                metrics, failures = evaluate_file(conn, args.benchmark, ks=ks)
            finally:
                conn.close()
            payload = {"metrics": metrics, "failures": failures}
            if args.json:
                _print_json(payload)
            else:
                for key, value in metrics.items():
                    if key == "matched_by":
                        print("matched_by:")
                        for name, count in sorted(value.items()):
                            print(f"  {name}: {count}")
                    else:
                        print(f"{key}: {value}")
                if failures:
                    print("failures:")
                    for failure in failures:
                        print(
                            f"  {failure['id']} | {failure['query']} | "
                            f"expected={failure['expected']} returned={failure['returned']}"
                        )
            return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
