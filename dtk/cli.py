"""Command-line interface for the Derived Store."""
from __future__ import annotations

import argparse
import json
import os
import sys
import subprocess
import zipfile
import time
from pathlib import Path
from typing import Any, Sequence

from .evaluation import evaluate_file, load_benchmark
from .config import load_config
from .release import build_release
from .intent import IntentClassifier
from .mcp import StdioMCPServer, run_stdio
from .relations import cooccurring_tags
from .store import build_database, database_info, open_db, rebuild_search_index
from .research.audit import audit_research
from .enrich import enrich_tags
from .research.engine import run_research
from .research.fetch import Fetcher
from .research.llm import ResearchLLMClient
from .research.queue import (
    RESEARCH_MIN_POST_COUNT,
    build_research_queue,
    explicit_research_units,
)
from .research.searx import SearXNGClient
from .research.publish import load_research_records, publish_research
from .research.tag_task import TagResearchTask
from .research.parallel import ParallelMCPSearchClient, ParallelSearchClient, load_parallel_key
from .translations import FFDKJ_URL, fetch_ffdkj_sqlite, import_translations
from .upstream import DEFAULT_BASE_URL as UPSTREAM_BASE_URL, import_implications, import_wikis, upstream_status
from .vectors import (
    EmbeddingClient,
    VectorIndex,
    build_vector_index,
    build_research_vector_index,
    default_embedding_model,
    default_lm_base_url,
)
from .web import create_server


def _config() -> dict:
    return load_config()


def _default_db() -> Path:
    return Path(_config()["db_path"])


def _default_source() -> str | None:
    return _config()["source_db_path"]


def _default_vectors() -> Path:
    return Path(_config()["vectors_path"])

def _default_research_model() -> str:
    return _config()["research_model"]

def _default_research_vectors() -> Path:
    return Path(_config()["research_vectors_path"])


def _default_searx_url() -> str:
    return _config()["searx_url"]


def _key_file_config(path: str) -> dict[str, str]:
    """Parse the label/value files under key/ into base_url/model/api_key."""
    values: dict[str, str] = {}
    labels = {"base url", "模型 id", "model", "key", "api key"}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.lower() in labels:
            continue
        if "://" in line and "base_url" not in values:
            values["base_url"] = line
        elif "model" not in values:
            values["model"] = line
        elif "api_key" not in values:
            values["api_key"] = line
    if not values:
        raise ValueError(f"no base URL/model/key found in {path}")
    return values


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
    if record.get("implications"):
        print("implications:")
        for tag in record["implications"]["implies"]:
            print(f"  -> {tag}")
        for tag in record["implications"]["implied_by"]:
            print(f"  <- {tag}")
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
        "--research-vectors",
        default=str(_default_research_vectors()),
        help="published research vector index (default: %(default)s; env: DTK_RESEARCH_VECTORS)",
    )
    parser.add_argument(
        "--no-vectors",
        dest="vectors",
        action="store_false",
        help="disable semantic retrieval and use FTS only",
    )
    parser.add_argument("--embedding-model", default=default_embedding_model())
    parser.add_argument("--endpoint", default=default_lm_base_url())
    parser.add_argument("--intent", action="store_true", help="use local LLM to reject non-visual queries")
    parser.add_argument("--intent-model", default=_default_research_model())
    parser.add_argument("--intent-key-file", help="LLM key file (base URL/model/key) for the intent gate")
    parser.add_argument(
        "--insecure-tls",
        action="store_true",
        help="skip TLS verification for LAN endpoints with self-signed certificates",
    )


def _vector_state(
    args: argparse.Namespace, queries: Sequence[str]
) -> tuple[VectorIndex | None, VectorIndex | None, dict[str, Any] | None]:
    if not args.vectors:
        return None, None, None

    official_index: VectorIndex | None = None
    vector_path = Path(args.vectors)
    if vector_path.is_file():
        official_index = VectorIndex.load(vector_path)
        if official_index.metadata.get("model") != args.embedding_model:
            raise ValueError(
                f"vector index model {official_index.metadata.get('model')!r} does not match "
                f"--embedding-model {args.embedding_model!r}"
            )
    elif os.environ.get("DTK_VECTORS") or vector_path != _default_vectors():
        raise FileNotFoundError(f"vector index not found: {vector_path}")

    research_index: VectorIndex | None = None
    research_path = Path(args.research_vectors)
    if research_path.is_file():
        research_index = VectorIndex.load(research_path)
        if research_index.metadata.get("model") != args.embedding_model:
            raise ValueError(
                f"research vector model {research_index.metadata.get('model')!r} does not match "
                f"--embedding-model {args.embedding_model!r}"
            )
    elif os.environ.get("DTK_RESEARCH_VECTORS"):
        raise FileNotFoundError(f"research vector index not found: {research_path}")

    if official_index is None and research_index is None:
        return None, None, None
    client = EmbeddingClient(args.endpoint, args.embedding_model)
    unique_queries = list(dict.fromkeys(queries))
    vectors = client.embed(unique_queries)
    return official_index, research_index, dict(zip(unique_queries, vectors))


def _intent_state(args: argparse.Namespace) -> IntentClassifier | None:
    if not args.intent:
        return None
    endpoint, model, api_key = args.endpoint, args.intent_model, None
    if args.intent_key_file:
        config = _key_file_config(args.intent_key_file)
        endpoint = config.get("base_url", endpoint)
        model = config.get("model", model)
        api_key = config.get("api_key")
    return IntentClassifier(
        ResearchLLMClient(
            endpoint,
            model,
            api_key=api_key,
            cache_dir="cache/intent",
            verify_tls=not args.insecure_tls,
        )
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

    research_embed_parser = subparsers.add_parser(
        "embed-research", help="build vectors for published research documents"
    )
    research_embed_parser.add_argument("--db", default=str(_default_db()))
    research_embed_parser.add_argument("--out", default=str(_default_research_vectors()))
    research_embed_parser.add_argument("--batch-size", type=int, default=32)
    research_embed_parser.add_argument("--model", default=default_embedding_model())
    research_embed_parser.add_argument("--endpoint", default=default_lm_base_url())
    research_embed_parser.add_argument("--json", action="store_true")

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

    related_parser = subparsers.add_parser(
        "related", help="show co-occurring General/Meta Tags ranked by Lift"
    )
    related_parser.add_argument("tag")
    related_parser.add_argument("--limit", type=int, default=20)
    related_parser.add_argument("--min-count", type=int, default=20)
    related_parser.add_argument("--cache", default="cache/cooccurrence")
    related_parser.add_argument("--json", action="store_true")
    _add_db_argument(related_parser)

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

    mcp_parser = subparsers.add_parser(
        "mcp", help="serve read-only Tag tools over stdio MCP"
    )
    _add_db_argument(mcp_parser)
    _add_vector_arguments(mcp_parser)

    research_parser = subparsers.add_parser(
        "research", help="research missing-Wiki Tags into candidate JSONL"
    )
    research_parser.add_argument("--db", default=str(_default_db()))
    research_parser.add_argument("--out", default="out/research/missing_wiki.jsonl")
    research_parser.add_argument("--cache", default="cache/research")
    research_parser.add_argument("--limit", type=int, default=10)
    research_parser.add_argument("--min-post-count", type=int, default=RESEARCH_MIN_POST_COUNT)
    research_parser.add_argument("--max-acts", type=int, default=6)
    research_parser.add_argument("--top-n", type=int, default=5)
    research_parser.add_argument("--types", default="general,meta")
    research_parser.add_argument(
        "--tag", action="append", default=[], help="explicit canonical Tag; bypasses usage cutoff"
    )
    research_parser.add_argument("--model", default=_default_research_model())
    research_parser.add_argument("--endpoint", default=default_lm_base_url())
    research_parser.add_argument(
        "--key-file",
        help="LLM key file with base URL/model/key (overrides --endpoint/--model)",
    )
    research_parser.add_argument(
        "--insecure-tls",
        action="store_true",
        help="skip TLS verification for LAN endpoints with self-signed certificates",
    )
    research_parser.add_argument(
        "--search-provider",
        choices=("searx", "parallel", "parallel-mcp"),
        default="searx",
    )
    research_parser.add_argument(
        "--parallel-mode", choices=("turbo", "fast", "basic", "advanced"), default="fast"
    )
    research_parser.add_argument(
        "--parallel-key",
        help="Parallel API key (env PARALLEL_API_KEY or key/parallel.txt)",
    )
    research_parser.add_argument(
        "--parallel-domains",
        help="comma-separated include_domains handed to the Parallel source policy",
    )
    research_parser.add_argument("--searx", default=_default_searx_url())
    research_parser.add_argument("--dry-run", action="store_true")
    research_parser.add_argument("--json", action="store_true")

    enrich_parser = subparsers.add_parser(
        "enrich", help="generate retrieval terms and Chinese definitions (LLM)"
    )
    enrich_parser.add_argument("--db", default=str(_default_db()))
    enrich_parser.add_argument("--cache", default="cache/research")
    enrich_parser.add_argument("--min-count", type=int, default=10000)
    enrich_parser.add_argument("--limit", type=int, default=None)
    enrich_parser.add_argument("--workers", type=int, default=2)
    enrich_parser.add_argument("--model", default=_default_research_model())
    enrich_parser.add_argument("--endpoint", default=default_lm_base_url())
    enrich_parser.add_argument("--key-file")
    enrich_parser.add_argument("--insecure-tls", action="store_true")
    enrich_parser.add_argument("--json", action="store_true")

    publish_parser = subparsers.add_parser(
        "publish-research", help="publish explicitly accepted research candidates"
    )
    publish_parser.add_argument("records")
    publish_parser.add_argument("--db", default=str(_default_db()))
    publish_parser.add_argument("--tags", required=True)
    publish_parser.add_argument("--json", action="store_true")

    audit_parser = subparsers.add_parser(
        "audit-research", help="mechanically audit research JSONL"
    )
    audit_parser.add_argument("records")
    audit_parser.add_argument("--json", action="store_true")

    release_build_parser = subparsers.add_parser(
        "release-build", help="build data release artifacts (db zip + optional vectors zip)"
    )
    _add_db_argument(release_build_parser)
    release_build_parser.add_argument("--vectors", default=str(_default_vectors()))
    release_build_parser.add_argument("--research-vectors", default=str(_default_research_vectors()))
    release_build_parser.add_argument("--out", default=_config()["release_out"])

    release_publish_parser = subparsers.add_parser(
        "release-publish", help="upload built artifacts to a GitHub Release via gh"
    )
    release_publish_parser.add_argument("--dist", default=_config()["release_out"])
    release_publish_parser.add_argument("--repo", default=_config()["release_repo"])
    release_publish_parser.add_argument("--notes", default="DTK data release")

    implications_parser = subparsers.add_parser(
        "import-implications", help="import official Danbooru Tag Implications"
    )
    _add_db_argument(implications_parser)
    implications_parser.add_argument("--base-url", default=UPSTREAM_BASE_URL)
    implications_parser.add_argument("--status", default="active")
    implications_parser.add_argument("--delay", type=float, default=0.7)
    implications_parser.add_argument("--json", action="store_true")

    wikis_parser = subparsers.add_parser(
        "import-wikis", help="fill snapshot Wiki gaps from the official Wiki API"
    )
    _add_db_argument(wikis_parser)
    wikis_parser.add_argument("--base-url", default=UPSTREAM_BASE_URL)
    wikis_parser.add_argument("--min-count", type=int, default=100)
    wikis_parser.add_argument("--artist-min-count", type=int, default=1000)
    wikis_parser.add_argument("--limit", type=int, default=None)
    wikis_parser.add_argument("--workers", type=int, default=6)
    wikis_parser.add_argument("--delay", type=float, default=1.0)
    wikis_parser.add_argument("--json", action="store_true")

    translations_parser = subparsers.add_parser(
        "import-translations", help="import community Chinese tag-name translations"
    )
    _add_db_argument(translations_parser)
    translations_parser.add_argument(
        "--file", default=str(Path("cache/upstream/ffdkj_tag.sqlite"))
    )
    translations_parser.add_argument("--json", action="store_true")

    index_parser = subparsers.add_parser(
        "rebuild-index", help="rebuild the FTS index from snapshot + additive data"
    )
    _add_db_argument(index_parser)
    index_parser.add_argument("--json", action="store_true")

    upstream_parser = subparsers.add_parser(
        "check-upstream", help="compare live Danbooru with the local Source Snapshot"
    )
    _add_db_argument(upstream_parser)
    upstream_parser.add_argument("--base-url", default=UPSTREAM_BASE_URL)
    upstream_parser.add_argument("--json", action="store_true")

    return parser


def _embed_progress(done: int, total: int) -> None:
    if done == total or done % 1000 == 0:
        print(f"embedded {done}/{total}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    # background pipes default to ascii on some hosts; Chinese progress lines must survive
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
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

        if args.command == "embed-research":
            manifest = build_research_vector_index(
                args.db,
                args.out,
                EmbeddingClient(args.endpoint, args.model),
                batch_size=args.batch_size,
            )
            if args.json:
                _print_json(manifest)
            else:
                print(
                    f"embedded {manifest['document_count']} research documents "
                    f"({manifest['tag_count']} tags) to {args.out}"
                )
            return 0

        if args.command == "research":
            selected_types = tuple(
                value.strip() for value in args.types.split(",") if value.strip()
            )
            if not selected_types:
                raise ValueError("--types must contain general and/or meta")
            connection = open_db(args.db)
            try:
                if args.tag:
                    units = explicit_research_units(connection, args.tag)
                else:
                    units = build_research_queue(
                        connection,
                        limit=args.limit,
                        min_post_count=args.min_post_count,
                        types=selected_types,
                    )
            finally:
                connection.close()
            if args.dry_run:
                if args.json:
                    _print_json(units)
                else:
                    for unit in units:
                        print(
                            f"{unit['post_count']:>8} {unit['tag']} [{unit['type']}]"
                        )
                return 0

            endpoint, model, api_key = args.endpoint, args.model, None
            if args.key_file:
                config = _key_file_config(args.key_file)
                endpoint = config.get("base_url", endpoint)
                model = config.get("model", model)
                api_key = config.get("api_key")
            if args.search_provider == "parallel-mcp":
                search_client = ParallelMCPSearchClient(cache_dir=args.cache)
            elif args.search_provider == "parallel":
                search_client = ParallelSearchClient(
                    args.parallel_key or load_parallel_key(),
                    mode=args.parallel_mode,
                    include_domains=[
                        domain.strip()
                        for domain in (args.parallel_domains or "").split(",")
                        if domain.strip()
                    ]
                    or None,
                    cache_dir=args.cache,
                )
                if not search_client.api_key:
                    raise ValueError(
                        "Parallel API key missing: use --parallel-key, "
                        "PARALLEL_API_KEY, or key/parallel.txt"
                    )
            else:
                search_client = SearXNGClient(args.searx, cache_dir=args.cache)
            stats = run_research(
                TagResearchTask(),
                units,
                args.out,
                llm=ResearchLLMClient(
                    endpoint,
                    model,
                    api_key=api_key,
                    cache_dir=args.cache,
                    verify_tls=not args.insecure_tls,
                ),
                search=search_client,
                fetcher=Fetcher(cache_dir=args.cache),
                max_acts=args.max_acts,
                top_n=args.top_n,
            )
            stats["output"] = args.out
            if args.json:
                _print_json(stats)
            else:
                for key, value in stats.items():
                    print(f"{key}: {value}")
            return 0

        if args.command == "enrich":
            endpoint, model, api_key = args.endpoint, args.model, None
            if args.key_file:
                config = _key_file_config(args.key_file)
                endpoint = config.get("base_url", endpoint)
                model = config.get("model", model)
                api_key = config.get("api_key")
            started = time.monotonic()

            def _enrich_progress(done: int, total: int, note: str) -> None:
                elapsed = time.monotonic() - started
                rate = done / elapsed if elapsed else 0.0
                remaining = (total - done) / rate if rate else 0.0
                suffix = f" ({note})" if note else ""
                print(
                    f"[enrich] {done}/{total} ({rate:.1f}/s, ~{remaining/60:.0f}m left){suffix}",
                    flush=True,
                )

            result = enrich_tags(
                args.db,
                ResearchLLMClient(
                    endpoint,
                    model,
                    api_key=api_key,
                    cache_dir=args.cache,
                    verify_tls=not args.insecure_tls,
                ),
                min_post_count=args.min_count,
                limit=args.limit,
                workers=args.workers,
                progress=_enrich_progress,
            )
            if args.json:
                _print_json(result)
            else:
                for key, value in result.items():
                    print(f"{key}: {value}")
            return 0

        if args.command == "publish-research":
            accepted_tags = {
                value.strip() for value in args.tags.split(",") if value.strip()
            }
            if not accepted_tags:
                raise ValueError("--tags must name at least one accepted Tag")
            records = [
                record
                for record in load_research_records(args.records)
                if record.get("tag") in accepted_tags
            ]

            found_tags = {record.get("tag") for record in records}
            missing_tags = accepted_tags - found_tags
            if missing_tags:
                raise ValueError(f"records missing accepted tags: {sorted(missing_tags)}")
            manifest = publish_research(
                args.db,
                records,
                accepted_tags=accepted_tags,
            )
            if args.json:
                _print_json(manifest)
            else:
                print(
                    f"published {manifest['published_tags']} tags; "
                    f"documents={manifest['documents']} evidence={manifest['evidence_rows']}"
                )
            return 0

        if args.command == "audit-research":
            result = audit_research(args.records)
            if args.json:
                _print_json(result)
            else:
                for key in (
                    "records",
                    "completed",
                    "unknown",
                    "other",
                    "invalid",
                    "tokens_in",
                    "tokens_out",
                ):
                    print(f"{key}: {result[key]}")
                for issue in result["issues"]:
                    print(f"issue: {issue['tag']}: {'; '.join(issue['errors'])}")
            return 1 if result["invalid"] else 0

        if args.command == "release-build":
            manifest = build_release(
                args.out,
                db_path=args.db,
                vectors_path=args.vectors,
                research_vectors_path=args.research_vectors,
            )
            _print_json(manifest)
            print(f"artifacts written to {args.out}")
            return 0

        if args.command == "release-publish":
            if not args.repo:
                raise ValueError("--repo is required (or set release_repo / DTK_RELEASE_REPO)")
            dist = Path(args.dist)
            files = sorted(
                p for p in dist.iterdir()
                if p.is_file() and (p.suffix == ".zip" or p.name == "SHA256SUMS.txt")
            )
            if not files:
                raise FileNotFoundError(f"no release artifacts in {dist}; run release-build first")
            versions = {json.loads(zipfile.ZipFile(p).read("manifest.json"))["version"] for p in files if p.suffix == ".zip"}
            if len(versions) != 1:
                raise ValueError(f"dist contains mixed versions: {sorted(versions)}")
            tag = versions.pop()
            completed = subprocess.run(
                [
                    "gh", "release", "create", tag,
                    *(str(p) for p in files),
                    "--repo", args.repo,
                    "--title", tag,
                    "--notes", args.notes,
                ],
                capture_output=True, text=True,
            )
            if completed.returncode != 0:
                print(completed.stdout)
                print(completed.stderr, file=sys.stderr)
                raise RuntimeError(f"gh release create failed (exit {completed.returncode})")
            print(completed.stdout.strip())
            print(f"published {tag} to {args.repo} ({len(files)} files)")
            return 0

        if args.command == "import-implications":
            result = import_implications(
                args.db,
                base_url=args.base_url,
                status=args.status,
                delay=args.delay,
            )
            if args.json:
                _print_json(result)
            else:
                print(
                    f"imported {result['imported']} implications "
                    f"({result['pages']} pages, status={result['status']})"
                )
            return 0

        if args.command == "import-wikis":
            started = time.monotonic()

            def _wiki_progress(done: int, total: int) -> None:
                elapsed = time.monotonic() - started
                rate = done / elapsed if elapsed else 0.0
                remaining = (total - done) / rate if rate else 0.0
                print(
                    f"[wikis] {done}/{total} ({rate:.1f}/s, ~{remaining/60:.0f}m left)",
                    flush=True,
                )

            result = import_wikis(
                args.db,
                base_url=args.base_url,
                min_count=args.min_count,
                artist_min_count=args.artist_min_count,
                limit=args.limit,
                workers=args.workers,
                request_delay=args.delay,
                progress=_wiki_progress,
            )
            if args.json:
                _print_json(result)
            else:
                print(
                    f"checked {result['candidates']} tags: "
                    f"{result['hits']} wikis, {result['misses']} misses"
                )
            return 0

        if args.command == "check-upstream":
            conn = open_db(args.db)
            try:
                result = upstream_status(conn, base_url=args.base_url)
            finally:
                conn.close()
            if args.json:
                _print_json(result)
            else:
                for key, value in result.items():
                    print(f"{key}: {value}")
            return 0

        if args.command == "import-translations":
            source_file = Path(args.file)
            if not source_file.is_file():
                print(f"downloading {FFDKJ_URL} -> {source_file}", flush=True)
                fetch_ffdkj_sqlite(source_file)
            result = import_translations(args.db, source_file)
            if args.json:
                _print_json(result)
            else:
                print(
                    f"matched {result['matched']}/{result['source_rows']} rows "
                    f"from {result['label']}"
                )
            return 0

        if args.command == "rebuild-index":
            result = rebuild_search_index(args.db)
            if args.json:
                _print_json(result)
            else:
                print(f"rebuilt FTS index: {result['fts_rows']} rows")
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
                research_vectors_path=args.research_vectors,
                embedding_model=args.embedding_model,
                intent=args.intent,
                intent_model=args.intent_model,
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

        if args.command == "mcp":
            vector_index, research_index, _ = _vector_state(args, [])
            server = StdioMCPServer(
                args.db,
                vector_index=vector_index,
                research_index=research_index,
                embedding_client=EmbeddingClient(args.endpoint, args.embedding_model),
                intent_parser=_intent_state(args),
            )
            run_stdio(server)
            return 0

        if args.command == "search":
            concepts = split_concepts(args.query)
            vector_index, research_index, vector_map = _vector_state(args, concepts)
            intent_parser = _intent_state(args)
            conn = open_db(args.db)
            try:
                if len(concepts) == 1:
                    result = search(
                        conn,
                        args.query,
                        limit=args.limit,
                        vector_index=vector_index,
                        research_index=research_index,
                        intent_parser=intent_parser,
                        query_vector=vector_map[concepts[0]] if vector_map else None,
                    )
                else:
                    result = search_grouped(
                        conn,
                        args.query,
                        limit=args.limit,
                        vector_index=vector_index,
                        research_index=research_index,
                        intent_parser=intent_parser,
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

        if args.command == "related":
            conn = open_db(args.db)
            try:
                result = cooccurring_tags(
                    conn,
                    args.tag,
                    limit=args.limit,
                    min_count=args.min_count,
                    cache_dir=args.cache,
                )
            finally:
                conn.close()
            if args.json:
                _print_json(result)
            else:
                print(f"tag: {result['tag']}")
                print(f"matched_by: {result['matched_by']}")
                for item in result["results"]:
                    print(
                        f"{item['tag']} posts={item['post_count']} "
                        f"cooccurrence={item['cooccurrence_count']} lift={item['lift']:.3f}"
                    )
            return 0

        if args.command == "eval":
            ks = _parse_ks(args.k)
            entries = load_benchmark(args.benchmark)
            vector_index, research_index, vector_map = _vector_state(
                args, [entry["query"] for entry in entries]
            )
            intent_parser = _intent_state(args)
            conn = open_db(args.db)
            try:
                metrics, failures = evaluate_file(
                    conn,
                    args.benchmark,
                    ks=ks,
                    vector_index=vector_index,
                    research_index=research_index,
                    intent_parser=intent_parser,
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
