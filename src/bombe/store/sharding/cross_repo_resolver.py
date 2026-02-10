"""Cross-repo import resolver for the Bombe shard catalog.

Resolves external dependencies against the shard catalog's exported symbol
cache to create cross-repo edges, and provides the post-indexing sync
function that refreshes exported symbols and discovers inter-repo links.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from bombe.models import CrossRepoEdge, GlobalSymbolURI
from bombe.store.database import Database
from bombe.store.sharding.catalog import ShardCatalog

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def compute_repo_id(repo_root: Path) -> str:
    """Compute deterministic repo_id from canonical path.

    Uses sha256[:16] of the POSIX path string, matching
    ``_repo_id_from_path`` in *models.py*.
    """
    canonical = repo_root.expanduser().resolve().as_posix()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Cross-repo resolution
# ---------------------------------------------------------------------------


def resolve_cross_repo_imports(
    catalog: ShardCatalog,
    repo_id: str,
    shard_db: Database,
) -> list[CrossRepoEdge]:
    """Resolve unresolved external_deps against the exported symbol cache.

    For each external dependency recorded in *shard_db*:

    1. Look up the file's language from the ``files`` table.
    2. Query ``catalog.resolve_external_import(module_name, language)`` for
       candidate matches in other shards.
    3. Skip any match whose ``repo_id`` is the same as *repo_id* (self-edges
       are not cross-repo).
    4. Build a :class:`CrossRepoEdge` for every remaining match.
    5. Deduplicate edges by ``(source_uri.uri, target_uri.uri, relationship)``.

    Returns the deduplicated list of :class:`CrossRepoEdge`.
    """
    edges: list[CrossRepoEdge] = []
    seen: set[tuple[str, str, str]] = set()

    # Fetch all external deps from the shard database.
    try:
        ext_deps = shard_db.query(
            "SELECT file_path, import_statement, module_name, line_number "
            "FROM external_deps;"
        )
    except Exception:
        logger.exception("Failed to query external_deps from shard database")
        return edges

    for dep in ext_deps:
        file_path: str = dep["file_path"]
        module_name: str = dep["module_name"]

        # Determine the language of the source file.
        try:
            lang_rows = shard_db.query(
                "SELECT language FROM files WHERE path = ?;",
                (file_path,),
            )
        except Exception:
            logger.warning(
                "Failed to query language for file %s; skipping dep %s",
                file_path,
                module_name,
            )
            continue

        if not lang_rows:
            logger.debug(
                "No files entry for path %s; skipping dep %s",
                file_path,
                module_name,
            )
            continue

        language: str = lang_rows[0]["language"]

        # Ask the catalog for matching exported symbols.
        try:
            matches = catalog.resolve_external_import(module_name, language)
        except Exception:
            logger.warning(
                "Catalog lookup failed for module_name=%s language=%s",
                module_name,
                language,
            )
            continue

        for match in matches:
            # Skip self-references (same repo).
            match_repo_id: str = match["repo_id"]
            if match_repo_id == repo_id:
                continue

            source_uri = GlobalSymbolURI(
                repo_id=repo_id,
                qualified_name=module_name,
                file_path=file_path,
            )
            target_uri = GlobalSymbolURI(
                repo_id=match_repo_id,
                qualified_name=match["qualified_name"],
                file_path=match["file_path"],
            )

            dedup_key = (source_uri.uri, target_uri.uri, "IMPORTS")
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            edges.append(
                CrossRepoEdge(
                    source_uri=source_uri,
                    target_uri=target_uri,
                    relationship="IMPORTS",
                    confidence=0.8,
                    provenance="import_resolution",
                )
            )

    logger.info(
        "Resolved %d cross-repo edges for repo_id=%s from %d external deps",
        len(edges),
        repo_id,
        len(ext_deps),
    )
    return edges


# ---------------------------------------------------------------------------
# Post-indexing sync
# ---------------------------------------------------------------------------


def post_index_cross_repo_sync(
    repo_root: Path,
    db: Database,
    catalog: ShardCatalog,
) -> dict[str, Any]:
    """Post-indexing step: sync exported symbols and resolve cross-repo imports.

    Workflow:

    1. Compute ``repo_id`` from *repo_root*.
    2. Persist ``repo_id`` in the shard's ``repo_meta`` table.
    3. Refresh the catalog's exported-symbol cache for this shard.
    4. Gather local symbol/edge counts and update catalog shard stats.
    5. Remove stale cross-repo edges for this repo in the catalog.
    6. Resolve cross-repo imports and upsert new edges.

    Returns a summary dict suitable for telemetry or logging.
    """
    repo_id = compute_repo_id(repo_root)

    # -- 2. Store repo_id in shard meta --------------------------------
    try:
        db.set_repo_meta("repo_id", repo_id)
    except Exception:
        logger.exception("Failed to set repo_id in repo_meta for %s", repo_id)

    # -- 3. Refresh exported symbols -----------------------------------
    exported_count = 0
    try:
        exported_count = catalog.refresh_exported_symbols(repo_id, db)
    except Exception:
        logger.exception(
            "Failed to refresh exported symbols for repo_id=%s", repo_id
        )

    # -- 4. Gather local counts and update shard stats -----------------
    symbol_count = 0
    edge_count = 0

    try:
        sym_rows = db.query("SELECT COUNT(*) AS cnt FROM symbols;")
        symbol_count = int(sym_rows[0]["cnt"]) if sym_rows else 0
    except Exception:
        logger.exception("Failed to count symbols for repo_id=%s", repo_id)

    try:
        edge_rows = db.query("SELECT COUNT(*) AS cnt FROM edges;")
        edge_count = int(edge_rows[0]["cnt"]) if edge_rows else 0
    except Exception:
        logger.exception("Failed to count edges for repo_id=%s", repo_id)

    try:
        catalog.update_shard_stats(repo_id, symbol_count, edge_count)
    except Exception:
        logger.exception(
            "Failed to update shard stats for repo_id=%s", repo_id
        )

    # -- 5. Delete old cross-repo edges --------------------------------
    try:
        catalog.delete_cross_repo_edges_for_repo(repo_id)
    except Exception:
        logger.exception(
            "Failed to delete old cross-repo edges for repo_id=%s", repo_id
        )

    # -- 6. Resolve cross-repo imports ---------------------------------
    edges: list[CrossRepoEdge] = []
    try:
        edges = resolve_cross_repo_imports(catalog, repo_id, db)
    except Exception:
        logger.exception(
            "Failed to resolve cross-repo imports for repo_id=%s", repo_id
        )

    # -- 7. Upsert new cross-repo edges --------------------------------
    try:
        catalog.upsert_cross_repo_edges(edges)
    except Exception:
        logger.exception(
            "Failed to upsert %d cross-repo edges for repo_id=%s",
            len(edges),
            repo_id,
        )

    return {
        "repo_id": repo_id,
        "exported_symbols": exported_count,
        "cross_repo_edges_discovered": len(edges),
        "symbol_count": symbol_count,
        "edge_count": edge_count,
    }
