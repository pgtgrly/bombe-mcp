# Bombe Implementation Task Board

## Goal
Build an MVP `bombe` MCP server from `bombe-technical-spec.md` that can:
- index Java/Python/TypeScript/Go repositories,
- answer graph-based code navigation queries through 5 MCP tools,
- support incremental updates with git diff,
- hit baseline latency/indexing targets.

## Definition of Done (MVP)
- `bombe --repo /path/to/repo` starts MCP server over STDIO.
- Full index succeeds on all language fixtures.
- Incremental indexing updates only changed files.
- All 5 tools work end-to-end with stable JSON responses.
- `get_context` enforces token budget and uses graph expansion + ranking.
- Test suite passes (unit + integration).
- Benchmarks are recorded against at least one medium repo.

## Build Order
1. Foundation and schema.
2. Indexing passes 1-2.
3. Indexing passes 3-4.
4. Query layer and PageRank.
5. MCP tool wiring.
6. Incremental updates.
7. Benchmarking and hardening.

## Task Board

### Epic FND: Foundation
| ID | Priority | Depends On | Deliverable | Acceptance Criteria | Tests |
|---|---|---|---|---|---|
| FND-001 | P0 | - | `pyproject.toml`, package skeleton under `src/bombe` | `python -m bombe.server --help` works | smoke import test |
| FND-002 | P0 | FND-001 | Logging + config module | log level configurable; repo path validation | unit: config parsing |
| FND-003 | P0 | FND-001 | SQLite bootstrapping in `store/database.py` | schema tables + indexes created idempotently | unit: schema init idempotency |
| FND-004 | P1 | FND-002 | Shared domain models in `src/bombe/models.py` | typed models for files/symbols/edges/query responses | unit: model validation |

### Epic IDX1: Pass 1 (File Structure)
| ID | Priority | Depends On | Deliverable | Acceptance Criteria | Tests |
|---|---|---|---|---|---|
| IDX1-001 | P0 | FND-003 | Repo walker honoring `.gitignore` | ignored files absent from `files` table | unit: ignore matching |
| IDX1-002 | P0 | IDX1-001 | Language detector + file hashing | language + SHA-256 populated for supported files | unit: extension mapping/hash |
| IDX1-003 | P0 | IDX1-002 | File upsert pipeline | re-run index does not duplicate files | integration: repeated full index |

### Epic IDX2: Pass 2 (Symbol Extraction)
| ID | Priority | Depends On | Deliverable | Acceptance Criteria | Tests |
|---|---|---|---|---|---|
| IDX2-001 | P0 | FND-004 | Tree-sitter parser wrapper | parse returns syntax tree + source map | unit: parse fixtures |
| IDX2-002 | P0 | IDX2-001 | Python symbol extraction | functions/classes/methods emitted with line ranges | unit: python fixture |
| IDX2-003 | P0 | IDX2-001 | Java symbol extraction | class/interface/method extraction passes | unit: java fixture |
| IDX2-004 | P0 | IDX2-001 | TypeScript symbol extraction | function/class/interface extraction passes | unit: ts fixture |
| IDX2-005 | P0 | IDX2-001 | Go symbol extraction | func/method/struct/interface extraction passes | unit: go fixture |
| IDX2-006 | P1 | IDX2-002 | Parameter/signature normalization | parameter positions + signature string stored | unit: signature normalization |

### Epic IDX3: Pass 3 (Import Resolution)
| ID | Priority | Depends On | Deliverable | Acceptance Criteria | Tests |
|---|---|---|---|---|---|
| IDX3-001 | P0 | IDX2-002 | Python import resolver | local imports map to repository files | unit: python import matrix |
| IDX3-002 | P0 | IDX2-003 | Java import resolver | package import maps to `.java` path | unit: java package mapping |
| IDX3-003 | P0 | IDX2-004 | TypeScript resolver | relative + extension fallback supported | unit: ts path resolution |
| IDX3-004 | P0 | IDX2-005 | Go resolver using `go.mod` | module-relative imports resolved | unit: go module mapping |
| IDX3-005 | P1 | IDX3-001 | External dep capture | unresolved imports inserted in `external_deps` | unit: external dep insert |

### Epic IDX4: Pass 4 (Call Graph)
| ID | Priority | Depends On | Deliverable | Acceptance Criteria | Tests |
|---|---|---|---|---|---|
| IDX4-001 | P0 | IDX2-006 | Callsite extraction per language | call expressions extracted with line numbers | unit: callsite extraction |
| IDX4-002 | P0 | IDX4-001, IDX3-005 | Name-based resolution with scope ranking | same-file/import/global fallback ordering works | unit: resolution priority |
| IDX4-003 | P1 | IDX4-002 | Confidence scoring for ambiguous links | ambiguous edges have `confidence < 1.0` | unit: ambiguity scenarios |

### Epic QRY: Query Engine + Ranking
| ID | Priority | Depends On | Deliverable | Acceptance Criteria | Tests |
|---|---|---|---|---|---|
| QRY-001 | P0 | IDX4-002 | PageRank job over symbol graph | non-zero scores for connected symbols | unit: pagerank convergence |
| QRY-002 | P0 | QRY-001 | `search_symbols` backend | name/kind/file filtering + limit work | integration: search API |
| QRY-003 | P0 | IDX4-002 | `get_references` backend | callers/callees/both with depth traversal | integration: references traversal |
| QRY-004 | P0 | QRY-001, QRY-002 | `get_context` seed selection + expansion | PPR ranking and depth expansion implemented | integration: context ranking |
| QRY-005 | P0 | QRY-004 | Token-budget packing | never exceeds `token_budget`; fallback to signature-only | integration: budget enforcement |
| QRY-006 | P1 | QRY-001 | `get_structure` backend | hierarchy output and top-rank markers | integration: structure map |
| QRY-007 | P1 | QRY-003 | `get_blast_radius` backend | direct/transitive dependents and risk summary | integration: blast radius |

### Epic MCP: Server + Tool Surface
| ID | Priority | Depends On | Deliverable | Acceptance Criteria | Tests |
|---|---|---|---|---|---|
| MCP-001 | P0 | QRY-002..QRY-007 | MCP server bootstrap in `server.py` | server advertises 5 tools with schemas | integration: MCP discovery |
| MCP-002 | P0 | MCP-001 | Tool handlers + serialization | response payloads match spec contracts | integration: JSON contract tests |
| MCP-003 | P1 | MCP-002 | Error handling and diagnostics | invalid params return structured errors | integration: bad input cases |

### Epic INC: Incremental Indexing
| ID | Priority | Depends On | Deliverable | Acceptance Criteria | Tests |
|---|---|---|---|---|---|
| INC-001 | P0 | IDX1-003 | Git diff change detector in `watcher/git_diff.py` | A/M/D/R statuses parsed correctly | unit: git diff parser |
| INC-002 | P0 | INC-001, IDX4-002 | Incremental orchestration in pipeline | only changed files are reprocessed | integration: single-file update |
| INC-003 | P1 | INC-002 | Reverse-edge rebuild and pagerank refresh | graph consistency after rename/delete | integration: rename/delete cases |

### Epic PRF: Performance + Hardening
| ID | Priority | Depends On | Deliverable | Acceptance Criteria | Tests |
|---|---|---|---|---|---|
| PRF-001 | P1 | MCP-002 | Query benchmarks script | latency report for each tool | benchmark run output |
| PRF-002 | P1 | INC-003 | Index benchmarks script | full + incremental timing report | benchmark run output |
| PRF-003 | P1 | PRF-001 | WAL mode + batch write tuning | measurable write/query improvement | perf comparison log |

## Module Contracts (Implementation Interfaces)

### `src/bombe/store/database.py`
```python
from pathlib import Path
from typing import Iterable, Sequence
from bombe.models import FileRecord, SymbolRecord, EdgeRecord, ExternalDepRecord

class Database:
    def __init__(self, db_path: Path) -> None: ...
    def init_schema(self) -> None: ...
    def upsert_files(self, records: Sequence[FileRecord]) -> None: ...
    def replace_file_symbols(self, file_path: str, symbols: Sequence[SymbolRecord]) -> None: ...
    def replace_file_edges(self, file_path: str, edges: Sequence[EdgeRecord]) -> None: ...
    def replace_external_deps(self, file_path: str, deps: Sequence[ExternalDepRecord]) -> None: ...
    def delete_file_graph(self, file_path: str) -> None: ...
    def rename_file(self, old_path: str, new_path: str) -> None: ...
    def query(self, sql: str, params: tuple = ()) -> list[tuple]: ...
```

### `src/bombe/indexer/parser.py`
```python
from pathlib import Path
from bombe.models import ParsedUnit

def parse_file(path: Path, language: str) -> ParsedUnit: ...
```

### `src/bombe/indexer/symbols.py`
```python
from bombe.models import ParsedUnit, SymbolRecord, ImportRecord

def extract_symbols(parsed: ParsedUnit) -> tuple[list[SymbolRecord], list[ImportRecord]]: ...
```

### `src/bombe/indexer/imports.py`
```python
from bombe.models import FileRecord, ImportRecord, EdgeRecord, ExternalDepRecord

def resolve_imports(
    repo_root: str,
    source_file: FileRecord,
    imports: list[ImportRecord],
    all_files: dict[str, FileRecord],
) -> tuple[list[EdgeRecord], list[ExternalDepRecord]]: ...
```

### `src/bombe/indexer/callgraph.py`
```python
from bombe.models import ParsedUnit, SymbolRecord, EdgeRecord

def build_call_edges(
    parsed: ParsedUnit,
    file_symbols: list[SymbolRecord],
    candidate_symbols: list[SymbolRecord],
) -> list[EdgeRecord]: ...
```

### `src/bombe/indexer/pagerank.py`
```python
from bombe.store.database import Database

def recompute_pagerank(db: Database, damping: float = 0.85, epsilon: float = 1e-6) -> None: ...
```

### `src/bombe/indexer/pipeline.py`
```python
from pathlib import Path
from bombe.store.database import Database
from bombe.models import IndexStats, FileChange

def full_index(repo_root: Path, db: Database, workers: int = 4) -> IndexStats: ...
def incremental_index(repo_root: Path, db: Database, changes: list[FileChange]) -> IndexStats: ...
```

### `src/bombe/query/search.py`
```python
from bombe.models import SymbolSearchRequest, SymbolSearchResponse
from bombe.store.database import Database

def search_symbols(db: Database, req: SymbolSearchRequest) -> SymbolSearchResponse: ...
```

### `src/bombe/query/references.py`
```python
from bombe.models import ReferenceRequest, ReferenceResponse
from bombe.store.database import Database

def get_references(db: Database, req: ReferenceRequest) -> ReferenceResponse: ...
```

### `src/bombe/query/context.py`
```python
from bombe.models import ContextRequest, ContextResponse
from bombe.store.database import Database

def get_context(db: Database, req: ContextRequest) -> ContextResponse: ...
```

### `src/bombe/query/structure.py`
```python
from bombe.models import StructureRequest
from bombe.store.database import Database

def get_structure(db: Database, req: StructureRequest) -> str: ...
```

### `src/bombe/query/blast.py`
```python
from bombe.models import BlastRadiusRequest, BlastRadiusResponse
from bombe.store.database import Database

def get_blast_radius(db: Database, req: BlastRadiusRequest) -> BlastRadiusResponse: ...
```

### `src/bombe/watcher/git_diff.py`
```python
from pathlib import Path
from bombe.models import FileChange

def get_changed_files(repo_root: Path) -> list[FileChange]: ...
```

### `src/bombe/tools/definitions.py`
```python
from mcp.server import Server
from bombe.store.database import Database

def register_tools(server: Server, db: Database, repo_root: str) -> None: ...
```

## Test Plan (Concrete Cases)

### Unit tests
| File | Case | Pass Criteria |
|---|---|---|
| `tests/test_database.py` | schema init run twice | no exception, same table count |
| `tests/test_parser.py` | parse each language fixture | parse tree exists, no crash |
| `tests/test_symbols.py` | extract classes/functions/methods | expected symbol names + line ranges |
| `tests/test_imports.py` | local vs external import resolution | edges for local imports, external rows for others |
| `tests/test_callgraph.py` | same-file, imported, ambiguous callsites | correct edge priority + confidence score |
| `tests/test_pagerank.py` | small synthetic graph | scores converge and rank order stable |
| `tests/test_git_diff.py` | A/M/D/R parsing | returned `FileChange` list matches fixture output |

### Integration tests
| File | Case | Pass Criteria |
|---|---|---|
| `tests/test_indexer.py` | full index on each fixture repo | symbols + edges count > 0, no duplicates |
| `tests/test_incremental.py` | modify one file then incremental index | only affected file graph replaced |
| `tests/test_query_search.py` | `search_symbols` filters | query/kind/path filters return expected results |
| `tests/test_query_references.py` | depth-1 and depth-3 traversals | correct nodes and depth values |
| `tests/test_query_context.py` | context assembly under small budget | `tokens_used <= token_budget` and includes seed symbol |
| `tests/test_query_structure.py` | structure output rendering | includes hierarchy and rank markers |
| `tests/test_query_blast.py` | signature and delete impact | direct + transitive dependents present |
| `tests/test_mcp_contract.py` | call all 5 tools via MCP harness | response schema fields match spec |

### Performance tests
| File | Case | Target |
|---|---|---|
| `tests/perf/test_index_perf.py` | full index medium fixture | `< 30s` |
| `tests/perf/test_incremental_perf.py` | single-file incremental update | `< 500ms` |
| `tests/perf/test_query_perf.py` | p95 for each query type | meets section 6 targets |

## Recommended Week-1 Execution Sequence
1. Implement `FND-001..FND-003` and run schema + bootstrap tests.
2. Complete `IDX1-001..IDX1-003` and verify idempotent indexing.
3. Ship `IDX2-001..IDX2-005` with language fixture tests.
4. Add `IDX3-001..IDX3-005` and validate import edges.
5. Add `IDX4-001..IDX4-002`, then `QRY-001..QRY-003`.
6. Implement `QRY-004..QRY-005` (`get_context`) before `QRY-006..QRY-007`.
7. Wire MCP (`MCP-001..MCP-003`), then incremental updates (`INC-001..INC-003`).
8. Finish with `PRF-001..PRF-003` and document benchmark outputs.

## Risks to Track
- Tree-sitter query differences across grammar versions can break extraction.
- Name-based call resolution can introduce false positives in large repos.
- Token estimation (`chars / 3.5`) is approximate and may drift from model tokenization.
- SQLite recursive traversal may need query tuning for very large call graphs.

## Immediate Next Action
Start with `FND-001` by creating the package scaffold and wiring a minimal CLI entrypoint.
