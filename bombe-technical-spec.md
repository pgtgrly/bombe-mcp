# Bombe: Structure-Aware Code Retrieval for AI Coding Agents

## Technical Specification v1.0

**Project:** Graph-based code navigation MCP server for AI coding agents
**Target Languages:** Java, Python, TypeScript, Go
**Runtime:** Local-first, single binary, zero external dependencies
**Protocol:** Model Context Protocol (MCP) over STDIO
**Implementation Language:** Python

---

## 1. Problem Statement

### 1.1 The Core Failure of Current Approaches

AI coding agents navigate codebases using one of three fundamentally limited approaches:

**Brute-force agentic search (Claude Code).** The agent iteratively calls grep, find, and cat to explore files, relying on LLM reasoning to decide what to read next. No persistent index exists — every session starts from scratch. This burns tokens on repeated file reads (often 30-50% of context window), misses non-textually-adjacent relationships, and scales poorly beyond ~50k LOC.

**Vector embedding search (Cursor).** Files are chunked, embedded, and stored in a remote vector database (Turbopuffer). The `@codebase` feature runs nearest-neighbor search against query embeddings. This fails for code because: semantic similarity ≠ structural relevance (a JSON parser and XML parser embed closely but are unrelated in the call graph), chunking destroys function boundaries, and call relationships are invisible to vector space. Cursor's architecture is also cloud-dependent — chunks are sent to remote servers for embedding.

**Static repo maps (Aider).** Tree-sitter parses every file, extracting definitions and references. A file-level dependency graph is built, PageRank identifies important symbols, and a token-budget-constrained map is sent with every request. This is the best existing approach, but it produces a static map regardless of the current task — the same map is sent whether you're debugging authentication or refactoring a data model.

### 1.2 What's Missing

No existing tool provides **query-driven, structurally-aware context assembly** — where the agent asks a specific question ("what do I need to understand to safely change function X?") and receives a precise context bundle assembled by traversing the code's dependency graph from relevant entry points. This is the gap Bombe fills.

### 1.3 Market Validation

The industry is converging on graph-based code intelligence:

- **Greptile** (raised ~$30M, 2,000+ customers including Stripe/Brex) uses graph-based codebase context as their core differentiator. Their research found semantic search performs 12% better when code is first translated to NL summaries — confirming raw embeddings fail for code.
- **Sourcegraph Cody** moved away from embeddings for enterprise, replacing them with their code graph (SCIP) + BM25 search — finding it more scalable and accurate.
- **Aider** never used embeddings — tree-sitter + PageRank outperformed vector approaches from the start.
- **Claude Code** experimented with LSP integration (December 2025) and abandoned it — symbol resolution failures, empty reference searches, 8.5% higher token consumption.
- The MCP ecosystem has 5,500+ servers, 97M monthly SDK downloads, and governance under the Linux Foundation with Anthropic, OpenAI, and Block as co-founders.
- 82% of developers use AI coding assistants daily/weekly. The AI coding tools market is $4-7B in 2025, growing at 25-27% CAGR.

---

## 2. Architecture Overview

### 2.1 High-Level Design

```
┌─────────────────────────────────────────────────────────┐
│                    AI Coding Agent                       │
│              (Claude Code / Cursor / Copilot)            │
└────────────────────┬────────────────────────────────────┘
                     │ MCP Protocol (STDIO)
                     ▼
┌─────────────────────────────────────────────────────────┐
│                  Bombe MCP Server                    │
│  ┌───────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │  MCP Tool  │  │   Query      │  │   Context        │ │
│  │  Interface │──│   Engine     │──│   Assembler      │ │
│  └───────────┘  └──────────────┘  └──────────────────┘ │
│         │              │                    │            │
│  ┌──────┴──────────────┴────────────────────┴────────┐  │
│  │              Graph Store (SQLite)                  │  │
│  │  ┌──────────┐ ┌───────────┐ ┌──────────────────┐  │  │
│  │  │  Symbols │ │   Edges   │ │  File Metadata   │  │  │
│  │  │  Table   │ │   Table   │ │  Table           │  │  │
│  │  └──────────┘ └───────────┘ └──────────────────┘  │  │
│  └───────────────────────────────────────────────────┘  │
│         │                                               │
│  ┌──────┴────────────────────────────────────────────┐  │
│  │              Indexing Pipeline                     │  │
│  │  ┌──────────┐ ┌───────────┐ ┌──────────────────┐  │  │
│  │  │Tree-sitter│ │  Graph    │ │  PageRank        │  │  │
│  │  │  Parser   │ │  Builder  │ │  Scorer          │  │  │
│  │  └──────────┘ └───────────┘ └──────────────────┘  │  │
│  └───────────────────────────────────────────────────┘  │
│         │                                               │
│  ┌──────┴────────────────────────────────────────────┐  │
│  │              Change Detection                     │  │
│  │  ┌──────────┐ ┌───────────┐                       │  │
│  │  │ Git Diff │ │ File Hash │                       │  │
│  │  │ Watcher  │ │ Tracker   │                       │  │
│  │  └──────────┘ └───────────┘                       │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
                     │
                     ▼
              Local File System
              (Git Repository)
```

### 2.2 Design Principles

1. **Zero external dependencies.** No Docker, no separate database service, no cloud APIs. SQLite is embedded. Tree-sitter grammars are bundled. The server is a single `pip install`.
2. **Query-driven, not map-driven.** Unlike Aider's static repo map, context is assembled specifically for each query by traversing the graph from relevant entry points.
3. **Incremental by default.** Initial indexing uses bulk operations; subsequent updates use git-diff detection to re-index only changed files.
4. **Token-budget-aware.** Every context response respects a configurable token budget, using PageRank-based importance scoring to prioritize what to include.
5. **Language-agnostic at the graph layer.** All languages produce the same node/edge types. Language-specific logic is isolated to tree-sitter query files.

---

## 3. Indexing Pipeline

### 3.1 Four-Pass Indexing Architecture

Inspired by GitNexus's proven approach, indexing operates in four sequential passes. Each pass produces a specific set of nodes and edges, and only affected passes are re-run on incremental updates.

#### Pass 1: File Structure Analysis

**Input:** Git repository file tree (respecting `.gitignore`)
**Output:** File and Directory nodes, CONTAINS edges

```
Nodes:
  - Directory(path, name)
  - File(path, name, language, size_bytes, content_hash)

Edges:
  - Directory -[CONTAINS]-> File
  - Directory -[CONTAINS]-> Directory
```

**Implementation:**
- Walk the file tree using `os.walk()`, skip entries matching `.gitignore` patterns
- Detect language via file extension mapping (`.py` → Python, `.java` → Java, `.ts/.tsx` → TypeScript, `.go` → Go)
- Compute content hash (SHA-256 of file contents) for change detection
- Store file metadata in SQLite `files` table

#### Pass 2: AST Symbol Extraction

**Input:** Source files from Pass 1
**Output:** Symbol nodes (Function, Class, Method, Interface, etc.), DEFINED_IN edges

For each file, parse with the appropriate tree-sitter grammar and run `tags.scm` queries to extract symbols.

**Symbol types extracted per language:**

| Symbol Type | Python | Java | TypeScript | Go |
|---|---|---|---|---|
| Function/Method | `def`, `async def` | methods | `function`, arrow functions | `func` |
| Class/Struct | `class` | `class`, `interface`, `enum` | `class`, `interface`, `type` | `struct`, `interface` |
| Module/Package | module (file) | `package` | `module`/`namespace` | `package` |
| Constants | `UPPER_CASE` assigns | `static final` fields | `const`, `enum` members | `const` |
| Imports | `import`, `from...import` | `import` | `import`/`require` | `import` |

```
Nodes:
  - Function(name, qualified_name, file_path, start_line, end_line, 
             signature, return_type, parameters[], visibility, is_async)
  - Class(name, qualified_name, file_path, start_line, end_line,
          superclasses[], interfaces[], visibility)
  - Method(name, qualified_name, file_path, start_line, end_line,
           signature, return_type, parameters[], visibility, is_static, 
           parent_class)
  - Interface(name, qualified_name, file_path, start_line, end_line)
  - Import(source, imported_names[], file_path, line)

Edges:
  - File -[DEFINES]-> Function|Class|Method|Interface
  - Class -[HAS_METHOD]-> Method
  - Class -[EXTENDS]-> Class
  - Class -[IMPLEMENTS]-> Interface
```

**Tree-sitter query examples:**

Python function extraction:
```scheme
(function_definition
  name: (identifier) @name
  parameters: (parameters) @params
  return_type: (type)? @return_type
  body: (block) @body) @definition.function
```

Java method extraction:
```scheme
(method_declaration
  (modifiers)? @modifiers
  type: (_) @return_type
  name: (identifier) @name
  parameters: (formal_parameters) @params) @definition.method
```

TypeScript function extraction:
```scheme
(function_declaration
  name: (identifier) @name
  parameters: (formal_parameters) @params
  return_type: (type_annotation)? @return_type) @definition.function

(arrow_function
  parameters: (formal_parameters) @params
  return_type: (type_annotation)? @return_type
  body: (_) @body) @definition.function
```

Go function extraction:
```scheme
(function_declaration
  name: (identifier) @name
  parameters: (parameter_list) @params
  result: (_)? @return_type) @definition.function

(method_declaration
  receiver: (parameter_list) @receiver
  name: (field_identifier) @name
  parameters: (parameter_list) @params
  result: (_)? @return_type) @definition.method
```

#### Pass 3: Import/Dependency Resolution

**Input:** Import nodes from Pass 2, file structure from Pass 1
**Output:** IMPORTS edges between files

This pass resolves import statements to actual files within the repository, creating file-level dependency edges.

```
Edges:
  - File -[IMPORTS]-> File
  - File -[IMPORTS_SYMBOL]-> Function|Class (when specific symbols imported)
```

**Resolution strategies per language:**

| Language | Import Pattern | Resolution |
|---|---|---|
| Python | `from foo.bar import Baz` | Map `foo/bar.py` or `foo/bar/__init__.py` |
| Java | `import com.example.MyClass` | Map `com/example/MyClass.java` |
| TypeScript | `import { X } from './utils'` | Resolve relative path + extensions |
| Go | `import "github.com/user/pkg"` | Match against `go.mod` module path |

**Handling unresolvable imports:**
External library imports (stdlib, third-party packages) are recorded as `ExternalDependency` nodes but not resolved further. This is intentional — we only graph the repository's own code.

#### Pass 4: Call Graph Construction

**Input:** Full AST of each file, symbol table from Pass 2
**Output:** CALLS edges between symbols

This is the most complex pass. For each function/method body, we identify all function call expressions and attempt to resolve them to known symbols.

```
Edges:
  - Function -[CALLS]-> Function|Method
  - Method -[CALLS]-> Function|Method
```

**Resolution approach (name-based with scope awareness):**

1. Parse function body AST for `call_expression` nodes
2. Extract the callee name (e.g., `foo()` → `foo`, `self.bar()` → `bar`, `obj.method()` → `method`)
3. Search symbol table for matches, prioritizing:
   a. Symbols defined in the same file
   b. Symbols from directly imported modules
   c. Methods on known class types (when receiver type is inferrable)
   d. Global symbol name match (fallback, may have false positives)

**Known limitations (acceptable for MVP):**
- Dynamic dispatch: `getattr(obj, method_name)()` — cannot resolve
- Higher-order functions: `map(func, items)` — resolves `func` if it's a direct reference
- Decorators: `@app.route("/")` — resolved as a call to `route`
- Reflection: Java `Method.invoke()` — cannot resolve target
- Method chaining: `a.b().c()` — resolves `b` and `c` independently, may not link to correct class

These limitations are shared by all static analysis tools short of full compilation. Aider, Sourcegraph's tree-sitter fallback, and GitNexus all have similar gaps. The tradeoff is acceptable: name-based resolution with import scoping covers 80-90% of practical navigation needs.

### 3.2 Incremental Update Pipeline

After initial indexing, updates are triggered by file changes detected via git:

```
git diff-index --name-status HEAD
```

This returns a list of added (A), modified (M), deleted (D), and renamed (R) files.

**Update algorithm:**

```python
def incremental_update(changed_files: List[FileChange]):
    for change in changed_files:
        if change.status == 'D':
            # Delete all symbols and edges for this file
            delete_file_graph(change.path)
        elif change.status in ('A', 'M'):
            # Re-run passes 2-4 for this file only
            old_hash = get_stored_hash(change.path)
            new_hash = compute_hash(change.path)
            if old_hash != new_hash:
                delete_file_graph(change.path)
                extract_symbols(change.path)        # Pass 2
                resolve_imports(change.path)         # Pass 3
                build_call_graph(change.path)        # Pass 4
        elif change.status == 'R':
            rename_file_in_graph(change.old_path, change.new_path)
    
    # Rebuild reverse edges for affected files
    rebuild_reverse_edges(changed_files)
    
    # Re-run PageRank (fast — operates on edge counts, not file contents)
    recompute_pagerank()
```

**Performance target:** Incremental update for a single file change should complete in <500ms.

### 3.3 PageRank Importance Scoring

Following Aider's proven approach, we compute PageRank over the file-level dependency graph to identify the most structurally important symbols.

**Graph construction for PageRank:**
- Nodes: All symbols (functions, classes, methods)
- Edges: CALLS + IMPORTS_SYMBOL + EXTENDS + IMPLEMENTS
- Edge weight: 1.0 for all edges (unweighted)

**Algorithm:** Standard PageRank with damping factor 0.85, converging at ε < 1e-6.

**Output:** Each symbol gets a `pagerank_score` stored in the symbols table, updated on every incremental reindex.

**Usage:** When assembling context under a token budget, symbols are included in order of descending PageRank score, ensuring the most structurally central code is always included first.

---

## 4. Graph Storage Schema

### 4.1 SQLite Database Design

SQLite is chosen over graph databases (KuzuDB, Memgraph, Neo4j) for the MVP because:
- Zero setup — embedded in the Python process
- Single file — easy to distribute, backup, and debug
- Sufficient performance — recursive CTEs handle graph traversal for codebases up to ~500k LOC
- No dependency management — no Docker, no server process

**Upgrade path:** If Cypher queries become necessary for complex traversals, KuzuDB can be swapped in as a drop-in replacement with minimal schema changes. The graph schema is designed to be portable.

### 4.2 Table Definitions

```sql
-- Repository metadata
CREATE TABLE repo_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Indexed files with content hashes for change detection
CREATE TABLE files (
    path TEXT PRIMARY KEY,
    language TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size_bytes INTEGER,
    last_indexed_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- All code symbols (functions, classes, methods, interfaces)
CREATE TABLE symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,       -- e.g., "mypackage.MyClass.my_method"
    kind TEXT NOT NULL,                 -- function|class|method|interface|constant
    file_path TEXT NOT NULL REFERENCES files(path),
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    signature TEXT,                     -- Full signature string
    return_type TEXT,
    visibility TEXT,                    -- public|private|protected|package
    is_async BOOLEAN DEFAULT FALSE,
    is_static BOOLEAN DEFAULT FALSE,
    parent_symbol_id INTEGER REFERENCES symbols(id),  -- For methods -> class
    docstring TEXT,
    pagerank_score REAL DEFAULT 0.0,
    
    UNIQUE(qualified_name, file_path)
);

-- Symbol parameters (separate table for queryability)
CREATE TABLE parameters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol_id INTEGER NOT NULL REFERENCES symbols(id),
    name TEXT NOT NULL,
    type TEXT,
    position INTEGER NOT NULL,         -- 0-indexed parameter position
    default_value TEXT,
    
    UNIQUE(symbol_id, position)
);

-- All relationships between symbols and files
CREATE TABLE edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,        -- Symbol or file ID
    target_id INTEGER NOT NULL,        -- Symbol or file ID
    source_type TEXT NOT NULL,          -- 'symbol' or 'file'
    target_type TEXT NOT NULL,          -- 'symbol' or 'file'
    relationship TEXT NOT NULL,         -- CALLS|IMPORTS|EXTENDS|IMPLEMENTS|DEFINES|HAS_METHOD
    file_path TEXT,                     -- Where this relationship occurs
    line_number INTEGER,               -- Line where the reference occurs
    confidence REAL DEFAULT 1.0,       -- 0.0-1.0, lower for ambiguous resolutions
    
    UNIQUE(source_id, target_id, source_type, target_type, relationship)
);

-- External dependencies (stdlib, third-party packages)
CREATE TABLE external_deps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL REFERENCES files(path),
    import_statement TEXT NOT NULL,
    module_name TEXT NOT NULL,
    line_number INTEGER
);

-- Indexes for fast traversal
CREATE INDEX idx_symbols_name ON symbols(name);
CREATE INDEX idx_symbols_qualified ON symbols(qualified_name);
CREATE INDEX idx_symbols_file ON symbols(file_path);
CREATE INDEX idx_symbols_kind ON symbols(kind);
CREATE INDEX idx_symbols_pagerank ON symbols(pagerank_score DESC);
CREATE INDEX idx_edges_source ON edges(source_id, source_type);
CREATE INDEX idx_edges_target ON edges(target_id, target_type);
CREATE INDEX idx_edges_relationship ON edges(relationship);
CREATE INDEX idx_files_hash ON files(content_hash);
```

### 4.3 Common Graph Queries via SQL

**Find all callers of a function:**
```sql
SELECT s.name, s.file_path, s.start_line, e.line_number
FROM edges e
JOIN symbols s ON e.source_id = s.id AND e.source_type = 'symbol'
WHERE e.target_id = ? AND e.relationship = 'CALLS';
```

**Find all callees of a function:**
```sql
SELECT s.name, s.file_path, s.start_line
FROM edges e
JOIN symbols s ON e.target_id = s.id AND e.target_type = 'symbol'
WHERE e.source_id = ? AND e.relationship = 'CALLS';
```

**Transitive call chain (2 hops):**
```sql
WITH RECURSIVE call_chain AS (
    SELECT target_id, 1 as depth
    FROM edges 
    WHERE source_id = ? AND relationship = 'CALLS'
    
    UNION ALL
    
    SELECT e.target_id, cc.depth + 1
    FROM edges e
    JOIN call_chain cc ON e.source_id = cc.target_id
    WHERE e.relationship = 'CALLS' AND cc.depth < 3
)
SELECT DISTINCT s.name, s.qualified_name, s.file_path, s.start_line, cc.depth
FROM call_chain cc
JOIN symbols s ON cc.target_id = s.id
ORDER BY cc.depth, s.pagerank_score DESC;
```

**Class hierarchy:**
```sql
WITH RECURSIVE hierarchy AS (
    SELECT id, name, 0 as depth FROM symbols WHERE id = ?
    UNION ALL
    SELECT s.id, s.name, h.depth + 1
    FROM edges e
    JOIN symbols s ON e.target_id = s.id
    JOIN hierarchy h ON e.source_id = h.id
    WHERE e.relationship IN ('EXTENDS', 'IMPLEMENTS')
)
SELECT * FROM hierarchy;
```

**Most important symbols by PageRank:**
```sql
SELECT name, qualified_name, kind, file_path, pagerank_score
FROM symbols
ORDER BY pagerank_score DESC
LIMIT 50;
```

---

## 5. MCP Tool Interface

### 5.1 Transport

STDIO transport over the MCP SDK (`mcp` Python package). The server starts with:

```bash
bombe --repo /path/to/repo
```

Claude Code configuration (`.claude/mcp.json`):
```json
{
  "mcpServers": {
    "bombe": {
      "command": "bombe",
      "args": ["--repo", "."]
    }
  }
}
```

### 5.2 Tool Definitions

The server exposes **five MCP tools**. Each is designed to answer a specific category of agent question.

#### Tool 1: `search_symbols`

**Purpose:** Find symbols (functions, classes, methods) by name, pattern, or kind.
**When agents use it:** "Find the authentication function," "What classes exist in the user module?"

```json
{
  "name": "search_symbols",
  "description": "Search for code symbols (functions, classes, methods) by name pattern, kind, or file path. Returns signatures, locations, and importance scores.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "Symbol name or glob pattern (e.g., 'authenticate', 'User*', '*.handler')"
      },
      "kind": {
        "type": "string",
        "enum": ["function", "class", "method", "interface", "constant", "any"],
        "default": "any",
        "description": "Filter by symbol kind"
      },
      "file_pattern": {
        "type": "string",
        "description": "Glob pattern for file paths (e.g., 'src/auth/**')"
      },
      "limit": {
        "type": "integer",
        "default": 20,
        "description": "Maximum results to return"
      }
    },
    "required": ["query"]
  }
}
```

**Response format:**
```json
{
  "symbols": [
    {
      "name": "authenticate_user",
      "qualified_name": "auth.service.authenticate_user",
      "kind": "function",
      "file_path": "src/auth/service.py",
      "start_line": 45,
      "end_line": 78,
      "signature": "def authenticate_user(username: str, password: str) -> Optional[User]",
      "visibility": "public",
      "importance_score": 0.0847,
      "callers_count": 12,
      "callees_count": 4
    }
  ],
  "total_matches": 3
}
```

#### Tool 2: `get_references`

**Purpose:** Find all callers, callees, implementations, or usages of a symbol.
**When agents use it:** "What calls this function?", "What does this method depend on?", "What implements this interface?"

```json
{
  "name": "get_references",
  "description": "Find all references to/from a symbol: callers, callees, implementations, parent classes, or dependents.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "symbol_name": {
        "type": "string",
        "description": "Symbol name or qualified name"
      },
      "direction": {
        "type": "string",
        "enum": ["callers", "callees", "both", "implementors", "supers"],
        "default": "both",
        "description": "Direction of references to find"
      },
      "depth": {
        "type": "integer",
        "default": 1,
        "minimum": 1,
        "maximum": 5,
        "description": "Traversal depth for transitive references"
      },
      "include_source": {
        "type": "boolean",
        "default": false,
        "description": "Include source code of each reference"
      }
    },
    "required": ["symbol_name"]
  }
}
```

**Response format:**
```json
{
  "target_symbol": {
    "name": "authenticate_user",
    "file_path": "src/auth/service.py",
    "signature": "def authenticate_user(username: str, password: str) -> Optional[User]"
  },
  "callers": [
    {
      "name": "login_endpoint",
      "file_path": "src/api/routes.py",
      "line": 23,
      "depth": 1,
      "source": "# only if include_source=true\ndef login_endpoint(request):\n    user = authenticate_user(request.username, request.password)\n    ..."
    }
  ],
  "callees": [
    {
      "name": "verify_password",
      "file_path": "src/auth/crypto.py",
      "line": 67,
      "depth": 1
    },
    {
      "name": "get_user_by_username",
      "file_path": "src/models/user.py",
      "line": 34,
      "depth": 1
    }
  ]
}
```

#### Tool 3: `get_context`

**Purpose:** Assemble a structurally-aware context bundle for a natural language question, respecting a token budget. **This is the primary differentiator over existing tools.**
**When agents use it:** "I need to understand how authentication works," "What context do I need to safely refactor the payment module?"

```json
{
  "name": "get_context",
  "description": "Assemble a context bundle for a task by finding relevant symbols and traversing the code graph to include their dependencies, callers, types, and related code. Returns the most important context within a token budget.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "Natural language description of what you need context for"
      },
      "entry_points": {
        "type": "array",
        "items": { "type": "string" },
        "description": "Optional: specific symbol names to start graph traversal from"
      },
      "token_budget": {
        "type": "integer",
        "default": 8000,
        "description": "Maximum approximate tokens for the context bundle"
      },
      "include_signatures_only": {
        "type": "boolean",
        "default": false,
        "description": "If true, include only signatures (not full source) for non-primary symbols"
      },
      "expansion_depth": {
        "type": "integer",
        "default": 2,
        "minimum": 1,
        "maximum": 4,
        "description": "How many hops in the graph to expand from entry points"
      }
    },
    "required": ["query"]
  }
}
```

**Context assembly algorithm:**

```
1. SEED SELECTION
   - Parse query for identifiable symbol names/patterns
   - Run BM25 keyword search over symbol names + docstrings
   - If entry_points provided, add those directly
   - Result: Set of seed symbol IDs

2. GRAPH EXPANSION (Personalized PageRank from seeds)
   - From each seed, BFS-expand outward through edges:
     - CALLS (both directions)
     - IMPORTS_SYMBOL
     - EXTENDS / IMPLEMENTS  
     - HAS_METHOD (for class context)
   - Score each reached node using Personalized PageRank
     (restart probability 0.15 at seed nodes)
   - Collect all nodes within expansion_depth hops

3. CONTEXT RANKING
   - Score = PPR_score * global_pagerank_score * proximity_bonus
   - proximity_bonus: 1.0 for depth-0, 0.7 for depth-1, 0.4 for depth-2
   - Sort all candidate symbols by score descending

4. TOKEN-BUDGET PACKING
   - For each symbol in ranked order:
     a. If it's a seed/entry_point: include full source code
     b. If include_signatures_only: include signature + docstring only
     c. Else: include full source if budget allows, otherwise signature only
   - Track token count (approximate: chars / 3.5)
   - Stop when budget exhausted

5. ASSEMBLY
   - Group included symbols by file
   - For each file, output: file path, then symbols in line-number order
   - Add a "structure summary" header showing the graph relationships
     between included symbols
```

**Response format:**
```json
{
  "query": "how does authentication work",
  "context_bundle": {
    "summary": "Authentication flows through 3 files: routes.py → service.py → crypto.py. The authenticate_user function is the central entry point, called by 3 API endpoints.",
    "relationship_map": "login_endpoint → authenticate_user → [verify_password, get_user_by_username, create_session]",
    "files": [
      {
        "path": "src/auth/service.py",
        "symbols": [
          {
            "name": "authenticate_user",
            "kind": "function",
            "lines": "45-78",
            "included_as": "full_source",
            "source": "def authenticate_user(username: str, password: str) -> Optional[User]:\n    ..."
          }
        ]
      },
      {
        "path": "src/auth/crypto.py",
        "symbols": [
          {
            "name": "verify_password",
            "kind": "function",
            "lines": "12-25",
            "included_as": "signature_only",
            "source": "def verify_password(password: str, hashed: str) -> bool"
          }
        ]
      }
    ],
    "tokens_used": 4200,
    "token_budget": 8000,
    "symbols_included": 8,
    "symbols_available": 15
  }
}
```

#### Tool 4: `get_structure`

**Purpose:** Return a hierarchical repo structure with the most important symbols, similar to Aider's repo map but query-focused.
**When agents use it:** First-time exploration of a codebase, understanding high-level architecture.

```json
{
  "name": "get_structure",
  "description": "Return a hierarchical map of the repository structure showing the most important files and symbols, ranked by structural importance. Optional: focus on a subdirectory.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "path": {
        "type": "string",
        "default": ".",
        "description": "Subdirectory to focus on (relative to repo root)"
      },
      "token_budget": {
        "type": "integer",
        "default": 4000,
        "description": "Maximum tokens for the structure map"
      },
      "include_signatures": {
        "type": "boolean",
        "default": true,
        "description": "Include function/method signatures in the map"
      }
    }
  }
}
```

**Response format (text, optimized for LLM consumption):**
```
src/
  auth/
    service.py
      ⚡ authenticate_user(username: str, password: str) -> Optional[User]  [rank: 1]
      ⚡ create_session(user: User) -> Session  [rank: 4]
      ⋮ 3 more functions
    crypto.py
      verify_password(password: str, hashed: str) -> bool  [rank: 7]
      hash_password(password: str) -> str  [rank: 12]
    middleware.py
      ⚡ require_auth(handler: Callable) -> Callable  [rank: 2]
  api/
    routes.py
      ⚡ login_endpoint(request: Request) -> Response  [rank: 3]
      register_endpoint(request: Request) -> Response  [rank: 8]
      ⋮ 5 more functions
  models/
    user.py
      class User  [rank: 5]
        get_user_by_username(username: str) -> Optional[User]  [rank: 6]
        ⋮ 8 more methods

⚡ = Top 10 by structural importance (PageRank)
```

#### Tool 5: `get_blast_radius`

**Purpose:** Given a symbol that's about to be changed, show everything that would be affected.
**When agents use it:** Before making a change, understanding impact: "What breaks if I change this function's signature?"

```json
{
  "name": "get_blast_radius",
  "description": "Analyze the impact of changing a symbol. Returns all direct and transitive dependents that would be affected by a signature or behavior change.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "symbol_name": {
        "type": "string",
        "description": "Symbol to analyze impact for"
      },
      "change_type": {
        "type": "string",
        "enum": ["signature", "behavior", "delete"],
        "default": "behavior",
        "description": "Type of change: signature (parameters/return type), behavior (logic), or delete"
      },
      "max_depth": {
        "type": "integer",
        "default": 3,
        "description": "Maximum traversal depth for impact analysis"
      }
    },
    "required": ["symbol_name"]
  }
}
```

**Response format:**
```json
{
  "target": {
    "name": "authenticate_user",
    "file_path": "src/auth/service.py"
  },
  "change_type": "signature",
  "impact": {
    "direct_callers": [
      { "name": "login_endpoint", "file": "src/api/routes.py", "line": 23 },
      { "name": "api_auth_middleware", "file": "src/api/middleware.py", "line": 45 },
      { "name": "test_authenticate", "file": "tests/test_auth.py", "line": 12 }
    ],
    "transitive_callers": [
      { "name": "main_app", "file": "src/app.py", "line": 78, "depth": 2 }
    ],
    "affected_files": [
      "src/api/routes.py",
      "src/api/middleware.py",
      "tests/test_auth.py",
      "src/app.py"
    ],
    "total_affected_symbols": 4,
    "total_affected_files": 4,
    "risk_assessment": "medium - 3 direct callers, 1 transitive dependent, 1 test file"
  }
}
```

---

## 6. Performance Targets

### 6.1 Indexing Performance

| Codebase Size | Initial Index Time | Incremental Update (1 file) |
|---|---|---|
| Small (1k files, 50k LOC) | < 5 seconds | < 100ms |
| Medium (10k files, 500k LOC) | < 30 seconds | < 300ms |
| Large (50k files, 2M LOC) | < 3 minutes | < 500ms |

**Optimization strategies:**
- Parallel tree-sitter parsing using `concurrent.futures.ProcessPoolExecutor`
- SQLite WAL mode for concurrent reads during writes
- Batch INSERT using `executemany()` with 1000-row batches
- Content hash comparison to skip unchanged files entirely

### 6.2 Query Performance

| Query Type | Target Latency |
|---|---|
| `search_symbols` (name lookup) | < 10ms |
| `get_references` (1-hop) | < 20ms |
| `get_references` (3-hop transitive) | < 100ms |
| `get_context` (full assembly) | < 500ms |
| `get_structure` (full repo map) | < 200ms |
| `get_blast_radius` (3-depth) | < 150ms |

### 6.3 Resource Usage

| Metric | Target |
|---|---|
| Memory (idle, 10k file repo) | < 50 MB |
| SQLite DB size (10k file repo) | < 20 MB |
| CPU during idle | ~0% |
| CPU during incremental update | < 1 core for < 1s |

---

## 7. Technology Decisions & Rationale

### 7.1 Why SQLite over Graph Databases

| Factor | SQLite | KuzuDB | Memgraph |
|---|---|---|---|
| Setup | Zero (embedded) | pip install | Docker required |
| Dependencies | None (stdlib) | C++ binary | Separate service |
| Deployment | Single file | Single file | Container |
| Graph queries | Recursive CTEs | Cypher (native) | Cypher (native) |
| Performance (10k nodes) | Sufficient | Better for complex traversals | Best for real-time |
| Maturity | Battle-tested | Newer, growing | Production-ready |

**Decision:** SQLite for MVP. The recursive CTE approach handles 2-3 hop traversals on graphs with <100k nodes in <100ms. If query complexity exceeds SQLite's capabilities (deep traversals, complex pattern matching), KuzuDB is a drop-in upgrade with the same schema adapted to property graph syntax.

### 7.2 Why Tree-sitter over LSP/SCIP

| Factor | Tree-sitter | LSP | SCIP |
|---|---|---|---|
| Build required | No | Sometimes | Yes (full compilation) |
| Works on broken code | Yes | Partially | No |
| Incremental parsing | Sub-millisecond | N/A | Full rebuild |
| Multi-language | Uniform API | Per-language server | Per-language indexer |
| Cross-file resolution | No (name-based) | Yes (precise) | Yes (precise) |
| Setup complexity | pip install | 4 language servers | 4 build environments |
| Accuracy | ~85% for calls | ~98% | ~99% |

**Decision:** Tree-sitter only for MVP. The 85% accuracy on call resolution is sufficient for context assembly — false positives are filtered by the LLM, and false negatives are caught by the agent's own file reading. LSP/SCIP can be added as optional enhancement layers for users who want higher precision and have build tools available.

### 7.3 Why Python

- Best tree-sitter bindings (`tree-sitter` package with pre-built wheels for all target grammars)
- Mature MCP SDK (`mcp` package)
- Fast prototyping for a hackathon timeline
- SQLite is in the Python stdlib
- Competitive performance for I/O-bound workloads (which this is — file reading and DB queries dominate)

---

## 8. Project Structure

```
bombe/
├── pyproject.toml              # Package config, entry points
├── README.md
├── src/
│   └── bombe/
│       ├── __init__.py
│       ├── server.py           # MCP server entry point
│       ├── indexer/
│       │   ├── __init__.py
│       │   ├── pipeline.py     # 4-pass indexing orchestrator
│       │   ├── parser.py       # Tree-sitter parsing logic
│       │   ├── symbols.py      # Symbol extraction per language
│       │   ├── imports.py      # Import resolution per language
│       │   ├── callgraph.py    # Call graph construction
│       │   └── pagerank.py     # PageRank computation
│       ├── store/
│       │   ├── __init__.py
│       │   ├── database.py     # SQLite schema and operations
│       │   └── queries.py      # Common graph queries
│       ├── query/
│       │   ├── __init__.py
│       │   ├── search.py       # Symbol search (BM25 + name matching)
│       │   ├── references.py   # Reference traversal
│       │   ├── context.py      # Context assembly algorithm
│       │   ├── structure.py    # Repo structure map generation
│       │   └── blast.py        # Blast radius analysis
│       ├── tools/
│       │   ├── __init__.py
│       │   └── definitions.py  # MCP tool definitions and handlers
│       └── watcher/
│           ├── __init__.py
│           └── git_diff.py     # Git-based change detection
├── queries/                    # Tree-sitter query files
│   ├── python/
│   │   └── tags.scm
│   ├── java/
│   │   └── tags.scm
│   ├── typescript/
│   │   └── tags.scm
│   └── go/
│       └── tags.scm
└── tests/
    ├── test_parser.py
    ├── test_indexer.py
    ├── test_queries.py
    └── fixtures/               # Test repositories
        ├── python_project/
        ├── java_project/
        ├── typescript_project/
        └── go_project/
```

---

## 9. Implementation Plan (1-Week Hackathon)

### Day 1: Foundation
- [ ] Project scaffolding, pyproject.toml, MCP server skeleton
- [ ] SQLite schema creation and basic CRUD operations
- [ ] Tree-sitter parsing for Python (the simplest grammar)
- [ ] Pass 1 (file structure) and Pass 2 (symbol extraction) for Python

### Day 2: Multi-Language + Graph
- [ ] Pass 2 for Java, TypeScript, Go
- [ ] Pass 3 (import resolution) for all 4 languages
- [ ] Pass 4 (call graph) for all 4 languages
- [ ] Bulk indexing pipeline with parallel parsing

### Day 3: Query Engine
- [ ] `search_symbols` tool implementation
- [ ] `get_references` tool with 1-hop and transitive traversal
- [ ] PageRank computation and scoring

### Day 4: Context Assembly (The Differentiator)
- [ ] `get_context` tool — seed selection via BM25
- [ ] Personalized PageRank from seeds
- [ ] Token-budget packing algorithm
- [ ] `get_structure` tool (repo map generation)

### Day 5: Polish + Incremental
- [ ] `get_blast_radius` tool
- [ ] Git-diff-based incremental updates
- [ ] Claude Code integration testing
- [ ] Performance benchmarking on a large OSS repo

### Day 6: Demo Preparation
- [ ] Test against Spring Framework (Java), FastAPI (Python), Next.js (TS), Kubernetes (Go)
- [ ] Record demo: Claude Code navigating 100k+ LOC repo via Bombe vs vanilla
- [ ] Token usage comparison metrics
- [ ] Edge case hardening

### Day 7: Documentation + Submission
- [ ] README with installation instructions
- [ ] Architecture diagram
- [ ] Performance benchmarks published
- [ ] Submission package

---

## 10. Success Metrics

### 10.1 Primary Metrics (for hackathon demo)

| Metric | Target | Measurement |
|---|---|---|
| Token reduction | 40-60% fewer tokens for equivalent task | Compare Claude Code with/without Bombe on same tasks |
| Navigation accuracy | >80% of context bundles contain the correct entry point | Manual evaluation on 20 test queries |
| Index time | <30s for 10k file repo | Benchmark on FastAPI, Spring Boot |
| Query latency | <500ms for get_context | p95 across test queries |

### 10.2 Demo Script

**The pitch:** "Watch Claude Code navigate a 100k-line repo it's never seen, using Bombe instead of brute-force file reading — fewer tokens, faster, more accurate edits."

**Demo flow:**
1. `bombe --repo ./spring-framework` — show indexing completes in <30s
2. Claude Code: "How does request authentication work in this project?"
   - Without Bombe: Claude reads 15+ files, burns 30k tokens exploring
   - With Bombe: `get_context` returns the exact auth chain, 4k tokens
3. Claude Code: "What's the blast radius of changing SecurityFilterChain?"
   - Without Bombe: Claude guesses based on grep
   - With Bombe: `get_blast_radius` returns 23 affected files with call chains
4. Show incremental update: edit a file, reindex in <200ms

---

## 11. Future Enhancements (Post-Hackathon)

### 11.1 Short-Term (1-2 months)
- KuzuDB backend for complex Cypher queries
- AI-generated code summaries cached per function (using the LLM to summarize each function's purpose)
- VS Code extension for visualization of the code graph
- File system watcher for real-time updates (not just git-diff)
- SCIP optional enhancement layer for users with build tools

### 11.2 Medium-Term (3-6 months)
- Cross-repository graphs (monorepo support)
- Stack graph integration for more accurate name resolution
- Runtime trace integration (link static graph to actual execution paths)
- Multi-agent coordination (multiple tools querying the same graph)
- Streaming MCP transport for remote deployments

### 11.3 Long-Term Research Directions
- Learning-to-rank for context assembly (use agent feedback to improve which symbols are included)
- Predictive context loading (pre-fetch likely-needed context based on current editing pattern)
- Cross-language call graph (e.g., Python calling Go via gRPC — linking the graph across language boundaries)
- Temporal graphs (how the code structure evolves over commits)
