"""Command-line interface for the Derived Store."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from .evaluation import evaluate_file, load_benchmark
from .search import get_tag, search, search_grouped, split_concepts
from .store import build_database, database_info, open_db
from .vectors import (
    EmbeddingClient,
    VectorIndex,
    build_vector_index,
    default_embedding_model,
    default_lm_base_url,
)
from .web import create_server


def _default_db() -> Path:
    return Path(os.environ.get("DTK_DB", "data/dtk.db"))


def _default_source() -> str | None:
    return os.environ.get("DTK_SOURCE_DB")


def _default_vectors() -> Path:
    return Path(os.environ.get("DTK_VECTORS", "data/dtk_vectors.npz"))


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _print_result_items(items: list[dict[str, Any]], indent: str = "") -> None:
    if not items:
        print(f"{indent}no matching tag")
        return
    for item in items:
        alias = f" via {item['matched_alias']}" if item.get("matched_alias") else ""
        score = f" score={item['match_score']:.6g}" if "match_score" in item else ""
        print(
            f"{indent}{item['rank']}. {item['tag']} [{item['type']}] "
            f"posts={item['post_count']} match={item['matched_by']}{alias}{score}"
        )
        if item.get("wiki_excerpt"):
            print(f"{indent}   {item['wiki_excerpt']}")


def _print_search(result: dict[str, Any]) -> None:
    print(f"query: {result['query']}")
    print(f"matched_by: {result['matched_by']}")
    if "groups" in result:
        for group in result["groups"]:
            print(f"concept: {group['query']} ({group['matched_by']})")
            _print_result_items(group["results"], indent="  ")
        return
    _print_result_items(result["results"])


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


def _add_vector_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--vectors",
        default=str(_default_vectors()),
        help="vector index path (default: %(default)s; env: DTK_VECTORS)",
    )
    parser.add_argument(
        "--no-vectors",
        dest="vectors",
        action="store_false",
        help="disable semantic retrieval and use FTS only",
    )
    parser.add_argument("--embedding-model", default=default_embedding_model())
    parser.add_argument("--endpoint", default=default_lm_base_url())


def _vector_state(
    args: argparse.Namespace, queries: Sequence[str]
) -> tuple[VectorIndex | None, dict[str, Any] | None]:
    if not args.vectors:
        return None, None

    vector_path = Path(args.vectors)
    if not vector_path.is_file():
        if vector_path == _default_vectors() and not os.environ.get("DTK_VECTORS"):
            return None, None
        raise FileNotFoundError(f"vector index not found: {vector_path}")

    index = VectorIndex.load(vector_path)
    if index.metadata.get("model") != args.embedding_model:
        raise ValueError(
            f"vector index model {index.metadata.get('model')!r} does not match "
            f"--embedding-model {args.embedding_model!r}"
        )
    client = EmbeddingClient(args.endpoint, args.embedding_model)
    unique_queries = list(dict.fromkeys(queries))
    vectors = client.embed(unique_queries)
    return index, dict(zip(unique_queries, vectors))


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

    embed_parser = subparsers.add_parser(
        "embed", help="build the General/Meta semantic vector index"
    )
    embed_parser.add_argument("--db", default=str(_default_db()))
    embed_parser.add_argument("--out", default=str(_default_vectors()))
    embed_parser.add_argument("--min-post-count", type=int, default=100)
    embed_parser.add_argument("--batch-size", type=int, default=32)
    embed_parser.add_argument("--model", default=default_embedding_model())
    embed_parser.add_argument("--endpoint", default=default_lm_base_url())
    embed_parser.add_argument("--json", action="store_true")

    search_parser = subparsers.add_parser("search", help="search Canonical Tags")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.add_argument("--json", action="store_true")
    _add_db_argument(search_parser)
    _add_vector_arguments(search_parser)

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
    _add_vector_arguments(eval_parser)

    serve_parser = subparsers.add_parser("serve", help="serve the local human query UI")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    _add_db_argument(serve_parser)
    _add_vector_arguments(serve_parser)

    return parser


def _embed_progress(done: int, total: int) -> None:
    if done == total or done % 1000 == 0:
        print(f"embedded {done}/{total}", file=sys.stderr)


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

        if args.command == "embed":
            client = EmbeddingClient(args.endpoint, args.model)
            manifest = build_vector_index(
                args.db,
                args.out,
                client,
                min_post_count=args.min_post_count,
                batch_size=args.batch_size,
                progress=_embed_progress,
            )
            if args.json:
                _print_json(manifest)
            else:
                print(
                    f"embedded {manifest['tag_count']} tags "
                    f"({manifest['dimensions']}d) to {args.out}"
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

        if args.command == "serve":
            server = create_server(
                args.host,
                args.port,
                db_path=args.db,
                vectors_path=args.vectors or None,
                embedding_model=args.embedding_model,
                lm_base_url=args.endpoint,
            )
            print(
                f"DTK web UI: http://{args.host}:{server.server_port}/ "
                f"({'semantic' if server.vector_index else 'FTS only'})",
                flush=True,
            )
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                print("\nDTK web UI stopped")
            finally:
                server.server_close()
            return 0

        if args.command == "search":
            concepts = split_concepts(args.query)
            vector_index, vector_map = _vector_state(args, concepts)
            conn = open_db(args.db)
            try:
                if len(concepts) == 1:
                    result = search(
                        conn,
                        args.query,
                        limit=args.limit,
                        vector_index=vector_index,
                        query_vector=vector_map[concepts[0]] if vector_map else None,
                    )
                else:
                    result = search_grouped(
                        conn,
                        args.query,
                        limit=args.limit,
                        vector_index=vector_index,
                        query_vectors=vector_map,
                    )
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
            entries = load_benchmark(args.benchmark)
            vector_index, vector_map = _vector_state(
                args, [entry["query"] for entry in entries]
            )
            conn = open_db(args.db)
            try:
                metrics, failures = evaluate_file(
                    conn,
                    args.benchmark,
                    ks=ks,
                    vector_index=vector_index,
                    query_vectors=vector_map,
                )
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
