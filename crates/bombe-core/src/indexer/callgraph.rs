//! Call graph construction from parsed source and symbol tables.
//!
//! Port of the Python `callgraph.py` (594 LOC). Constructs call-graph edges
//! from source text, file-level symbols, and a global candidate symbol table.
//! Resolution is cascading: class-scoped > type-hinted > alias > receiver >
//! qualified-name > same-file > import-scoped > global.

use std::collections::{HashMap, HashSet};
use std::sync::LazyLock;

use regex::Regex;

use crate::indexer::symbols::ExtractedSymbol;

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/// A single call-site extracted from source text.
#[derive(Debug, Clone)]
pub struct CallSite {
    pub callee_name: String,
    pub line_number: i64,
    pub receiver_name: Option<String>,
}

/// A resolved call edge between two symbols.
#[derive(Debug, Clone)]
pub struct CallEdge {
    pub source_id: i64,
    pub target_id: i64,
    pub source_type: String,
    pub target_type: String,
    pub relationship: String,
    pub file_path: String,
    pub line_number: i64,
    pub confidence: f64,
}

// ---------------------------------------------------------------------------
// Regex patterns (compiled once via LazyLock)
// ---------------------------------------------------------------------------

static CALL_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\b(?:([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*\(").unwrap()
});

static PY_FROM_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"from\s+([A-Za-z0-9_\.]+)\s+import").unwrap());

static PY_IMPORT_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"import\s+([A-Za-z0-9_\.]+)").unwrap());

static PY_FROM_ALIAS_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^\s*from\s+([A-Za-z0-9_\.]+)\s+import\s+(.+)$").unwrap());

static PY_IMPORT_ALIAS_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^\s*import\s+([A-Za-z0-9_\.]+)(?:\s+as\s+([A-Za-z_][A-Za-z0-9_]*))?\s*$").unwrap()
});

static PY_ASSIGN_TYPE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(").unwrap()
});

static JAVA_NEW_TYPE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^\s*([A-Za-z_][A-Za-z0-9_<>?,\s]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*new\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
    )
    .unwrap()
});

static TS_NEW_TYPE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^\s*(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*([A-Za-z_][A-Za-z0-9_<>]*))?\s*=\s*new\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
    )
    .unwrap()
});

static GO_SHORT_DECL_TYPE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:=\s*&?([A-Za-z_][A-Za-z0-9_]*)\s*\{").unwrap()
});

// Additional import patterns used in _import_hints
static TS_IMPORT_HINT_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"import(?:\s+type)?\s+.*?\s+from\s+['"]([^'"]+)['"]"#).unwrap());

static JAVA_IMPORT_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"import\s+([A-Za-z0-9_.*]+);").unwrap());

static GO_IMPORT_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r#""([^"]+)""#).unwrap());

// Additional import patterns used in _import_aliases
static TS_NAMED_IMPORT_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"^\s*import(?:\s+type)?\s+\{([^}]*)\}\s+from\s+['"][^'"]+['"]"#).unwrap()
});

static TS_DEFAULT_IMPORT_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"^\s*import(?:\s+type)?\s+([A-Za-z_][A-Za-z0-9_]*)\s+from\s+['"][^'"]+['"]"#)
        .unwrap()
});

// ---------------------------------------------------------------------------
// Call keywords to skip
// ---------------------------------------------------------------------------

/// Language keywords that look like function calls but are not.
fn is_call_keyword(name: &str) -> bool {
    matches!(
        name,
        "if" | "for" | "while" | "switch" | "return" | "new" | "function" | "class" | "catch"
    )
}

// ---------------------------------------------------------------------------
// CRC32 based symbol ID (matches Python zlib.crc32 & 0x7FFFFFFF)
// ---------------------------------------------------------------------------

/// Compute a CRC32 hash of the qualified name, masked to a positive i64.
///
/// This matches the Python `int(zlib.crc32(qualified_name.encode('utf-8')) & 0x7FFFFFFF)`.
fn symbol_id(qualified_name: &str) -> i64 {
    // IEEE CRC32 — same table as zlib
    let crc = crc32_ieee(qualified_name.as_bytes());
    (crc & 0x7FFF_FFFF) as i64
}

/// IEEE CRC32 computation (same polynomial as zlib / Python zlib.crc32).
fn crc32_ieee(data: &[u8]) -> u32 {
    let mut crc: u32 = 0xFFFF_FFFF;
    for &byte in data {
        crc ^= byte as u32;
        for _ in 0..8 {
            if crc & 1 != 0 {
                crc = (crc >> 1) ^ 0xEDB8_8320;
            } else {
                crc >>= 1;
            }
        }
    }
    !crc
}

// ---------------------------------------------------------------------------
// Call extraction
// ---------------------------------------------------------------------------

/// Extract call-sites from source text using regex scanning.
///
/// Skips language keywords and lines that look like definitions (prefixed
/// with `def`, `function`, `func`, `class`, or `new`).
fn extract_regex_calls(source: &str, _language: &str) -> Vec<CallSite> {
    let mut callsites = Vec::new();
    for (index, line) in source.lines().enumerate() {
        let line_number = (index as i64) + 1;
        for caps in CALL_RE.captures_iter(line) {
            let receiver = caps.get(1).map(|m| m.as_str().to_string());
            let name = match caps.get(2) {
                Some(m) => m.as_str(),
                None => continue,
            };

            if is_call_keyword(name) {
                continue;
            }

            // Check the prefix before the match to skip definitions
            let match_start = caps.get(0).unwrap().start();
            let prefix = line[..match_start].trim_end();
            if prefix.ends_with("def")
                || prefix.ends_with("function")
                || prefix.ends_with("func")
                || prefix.ends_with("class")
                || prefix.ends_with("new")
            {
                continue;
            }

            callsites.push(CallSite {
                callee_name: name.to_string(),
                line_number,
                receiver_name: receiver,
            });
        }
    }
    callsites
}

// ---------------------------------------------------------------------------
// Import hints
// ---------------------------------------------------------------------------

/// Extract module name hints from all import styles in source.
///
/// Returns a set of module names and their trailing components (e.g.
/// `foo.bar` yields both `foo.bar` and `bar`).
fn import_hints(source: &str) -> HashSet<String> {
    let mut hints = HashSet::new();

    for line in source.lines() {
        let normalized = line.trim();

        // Python: from X import ...
        if let Some(caps) = PY_FROM_RE.captures(normalized) {
            if let Some(m) = caps.get(1) {
                let value = m.as_str().trim();
                hints.insert(value.to_string());
                if let Some(last) = value.rsplit('.').next() {
                    hints.insert(last.to_string());
                }
            }
        }

        // Python: import X (only if line starts with "import ")
        if let Some(caps) = PY_IMPORT_RE.captures(normalized) {
            if normalized.starts_with("import ") {
                if let Some(m) = caps.get(1) {
                    let value = m.as_str().trim();
                    hints.insert(value.to_string());
                    if let Some(last) = value.rsplit('.').next() {
                        hints.insert(last.to_string());
                    }
                }
            }
        }

        // TypeScript: import ... from '...'
        if let Some(caps) = TS_IMPORT_HINT_RE.captures(normalized) {
            if let Some(m) = caps.get(1) {
                let value = m.as_str().trim();
                hints.insert(value.to_string());
                if let Some(last) = value.rsplit('/').next() {
                    hints.insert(last.to_string());
                }
            }
        }

        // Java: import X;
        if let Some(caps) = JAVA_IMPORT_RE.captures(normalized) {
            if let Some(m) = caps.get(1) {
                let value = m.as_str().trim().trim_end_matches(".*");
                hints.insert(value.to_string());
                if let Some(last) = value.rsplit('.').next() {
                    hints.insert(last.to_string());
                }
            }
        }

        // Go: import "..."
        if normalized.starts_with("import ") && normalized.contains('"') {
            if let Some(caps) = GO_IMPORT_RE.captures(normalized) {
                if let Some(m) = caps.get(1) {
                    let value = m.as_str().trim();
                    hints.insert(value.to_string());
                    if let Some(last) = value.rsplit('/').next() {
                        hints.insert(last.to_string());
                    }
                }
            }
        }
    }

    hints
}

// ---------------------------------------------------------------------------
// Import aliases
// ---------------------------------------------------------------------------

/// Extract import aliases — maps alias name to a set of possible original names.
fn import_aliases(source: &str) -> HashMap<String, HashSet<String>> {
    let mut aliases: HashMap<String, HashSet<String>> = HashMap::new();

    for raw_line in source.lines() {
        let normalized = raw_line.trim();
        if normalized.is_empty() {
            continue;
        }

        // Python: from X import a, b as c, ...
        if let Some(caps) = PY_FROM_ALIAS_RE.captures(normalized) {
            if let Some(items_match) = caps.get(2) {
                let items = items_match.as_str();
                for chunk in items.split(',') {
                    let token = chunk.trim();
                    if token.is_empty() {
                        continue;
                    }
                    let parts: Vec<&str> = token.splitn(2, " as ").map(|s| s.trim()).collect();
                    let imported = parts[0];
                    let alias = if parts.len() > 1 { parts[1] } else { imported };
                    let last = imported.rsplit('.').next().unwrap_or(imported);
                    aliases
                        .entry(alias.to_string())
                        .or_default()
                        .insert(last.to_string());
                }
            }
            continue;
        }

        // Python: import X (as Y)?
        if let Some(caps) = PY_IMPORT_ALIAS_RE.captures(normalized) {
            if let Some(module_match) = caps.get(1) {
                let imported_module = module_match.as_str();
                let alias = caps.get(2).map(|m| m.as_str()).unwrap_or_else(|| {
                    imported_module
                        .rsplit('.')
                        .next()
                        .unwrap_or(imported_module)
                });
                let last = imported_module
                    .rsplit('.')
                    .next()
                    .unwrap_or(imported_module);
                aliases
                    .entry(alias.to_string())
                    .or_default()
                    .insert(last.to_string());
            }
            continue;
        }

        // TypeScript: import { a, b as c } from '...'
        if let Some(caps) = TS_NAMED_IMPORT_RE.captures(normalized) {
            if let Some(items_match) = caps.get(1) {
                let items = items_match.as_str();
                for chunk in items.split(',') {
                    let token = chunk.trim();
                    if token.is_empty() {
                        continue;
                    }
                    let parts: Vec<&str> = token.splitn(2, " as ").map(|s| s.trim()).collect();
                    let imported = parts[0];
                    let alias = if parts.len() > 1 { parts[1] } else { imported };
                    aliases
                        .entry(alias.to_string())
                        .or_default()
                        .insert(imported.to_string());
                }
            }
            continue;
        }

        // TypeScript: import X from '...'
        if let Some(caps) = TS_DEFAULT_IMPORT_RE.captures(normalized) {
            if let Some(m) = caps.get(1) {
                let alias = m.as_str();
                aliases
                    .entry(alias.to_string())
                    .or_default()
                    .insert(alias.to_string());
            }
        }
    }

    aliases
}

// ---------------------------------------------------------------------------
// Lexical receiver type hints
// ---------------------------------------------------------------------------

/// Scan backwards from `line_number` (up to `window` lines) looking for
/// variable assignments that reveal the type of `receiver_name`.
fn lexical_receiver_type_hints(
    source: &str,
    receiver_name: Option<&str>,
    line_number: i64,
    window: usize,
) -> HashSet<String> {
    let receiver = match receiver_name {
        Some(r) => r.trim(),
        None => return HashSet::new(),
    };
    if receiver.is_empty() {
        return HashSet::new();
    }

    let lines: Vec<&str> = source.lines().collect();
    let end_index = (line_number - 1).max(0) as usize;
    let end_index = end_index.min(lines.len());
    let begin_index = end_index.saturating_sub(window);

    let mut hints = HashSet::new();

    for index in (begin_index..end_index).rev() {
        let line = lines[index];

        // Python: receiver = TypeName(...)
        if let Some(caps) = PY_ASSIGN_TYPE_RE.captures(line) {
            if caps.get(1).map(|m| m.as_str()) == Some(receiver) {
                if let Some(m) = caps.get(2) {
                    hints.insert(m.as_str().to_string());
                }
            }
        }

        // Java: TypeName receiver = new ConstructorName(...)
        if let Some(caps) = JAVA_NEW_TYPE_RE.captures(line) {
            if caps.get(2).map(|m| m.as_str()) == Some(receiver) {
                if let Some(m) = caps.get(1) {
                    let declared = m
                        .as_str()
                        .trim()
                        .split('<')
                        .next()
                        .unwrap_or("")
                        .to_string();
                    if !declared.is_empty() {
                        hints.insert(declared);
                    }
                }
                if let Some(m) = caps.get(3) {
                    hints.insert(m.as_str().trim().to_string());
                }
            }
        }

        // TypeScript: const/let/var receiver (: Type)? = new Constructor(...)
        if let Some(caps) = TS_NEW_TYPE_RE.captures(line) {
            if caps.get(1).map(|m| m.as_str()) == Some(receiver) {
                if let Some(m) = caps.get(2) {
                    let declared = m
                        .as_str()
                        .trim()
                        .split('<')
                        .next()
                        .unwrap_or("")
                        .to_string();
                    if !declared.is_empty() {
                        hints.insert(declared);
                    }
                }
                if let Some(m) = caps.get(3) {
                    hints.insert(m.as_str().trim().to_string());
                }
            }
        }

        // Go: receiver := &?TypeName{...}
        if let Some(caps) = GO_SHORT_DECL_TYPE_RE.captures(line) {
            if caps.get(1).map(|m| m.as_str()) == Some(receiver) {
                if let Some(m) = caps.get(2) {
                    hints.insert(m.as_str().to_string());
                }
            }
        }
    }

    hints
}

// ---------------------------------------------------------------------------
// Symbol helpers
// ---------------------------------------------------------------------------

/// Find the smallest containing symbol for a given line number.
fn caller_for_line(line_number: i64, file_symbols: &[ExtractedSymbol]) -> Option<&ExtractedSymbol> {
    let mut best: Option<&ExtractedSymbol> = None;
    for symbol in file_symbols {
        if symbol.start_line <= line_number && line_number <= symbol.end_line {
            match best {
                None => best = Some(symbol),
                Some(current) => {
                    let current_span = current.end_line - current.start_line;
                    let next_span = symbol.end_line - symbol.start_line;
                    if next_span < current_span {
                        best = Some(symbol);
                    }
                }
            }
        }
    }
    best
}

/// Extract the owner (class) name from a method's qualified name.
///
/// For `"foo.bar.MyClass.my_method"`, returns `"MyClass"`.
fn method_owner_name(symbol: &ExtractedSymbol) -> String {
    let parts: Vec<&str> = symbol.qualified_name.split('.').collect();
    if parts.len() < 2 {
        return String::new();
    }
    parts[parts.len() - 2].to_string()
}

/// Break a type name into lowercase tokens, splitting on `.`, `::`, and `/`.
fn type_name_tokens(type_name: &str) -> HashSet<String> {
    let value = type_name.trim();
    if value.is_empty() {
        return HashSet::new();
    }
    let lowered = value.to_lowercase();
    let mut tokens = HashSet::new();
    tokens.insert(lowered.clone());
    for separator in [".", "::", "/"] {
        if value.contains(separator) {
            if let Some(last) = value.split(separator).last() {
                tokens.insert(last.to_lowercase());
            }
        }
    }
    tokens
}

// ---------------------------------------------------------------------------
// Target resolution
// ---------------------------------------------------------------------------

/// Resolve call-site targets using cascading strategies.
///
/// Returns matched symbols and a confidence score. Strategies tried in order:
///
/// 1. Class-scoped methods (same class as caller, self/cls/this receiver)
/// 2. Combined type hints (receiver_type + lexical + semantic)
/// 3. Alias-based type hints
/// 4. Direct receiver name match
/// 5. Receiver in qualified_name
/// 6. Same file
/// 7. Import-scoped
#[allow(clippy::too_many_arguments)]
/// 8. All matches (fallback)
fn resolve_targets<'a>(
    callsite: &CallSite,
    caller: &ExtractedSymbol,
    candidate_symbols: &'a [ExtractedSymbol],
    import_hint_set: &HashSet<String>,
    alias_hints: &HashMap<String, HashSet<String>>,
    receiver_type_hints: &HashSet<String>,
    lexical_hints: &HashSet<String>,
    semantic_hints: &HashSet<String>,
) -> (Vec<&'a ExtractedSymbol>, f64) {
    let callee_name = &callsite.callee_name;

    // Build the set of candidate names (including aliases)
    let mut candidate_names: HashSet<&str> = HashSet::new();
    candidate_names.insert(callee_name.as_str());
    if let Some(alias_set) = alias_hints.get(callee_name.as_str()) {
        for a in alias_set {
            candidate_names.insert(a.as_str());
        }
    }

    // Filter candidates by name
    let matches: Vec<&ExtractedSymbol> = candidate_symbols
        .iter()
        .filter(|s| candidate_names.contains(s.name.as_str()))
        .collect();

    if matches.is_empty() {
        return (Vec::new(), 0.0);
    }

    let receiver = callsite
        .receiver_name
        .as_deref()
        .unwrap_or("")
        .trim()
        .to_lowercase();

    // Strategy (a): Class-scoped methods
    if caller.kind == "method" {
        let class_prefix = match caller.qualified_name.rsplit_once('.') {
            Some((prefix, _)) => prefix,
            None => "",
        };
        if !class_prefix.is_empty() {
            let prefix_dot = format!("{class_prefix}.");
            let class_scoped: Vec<&ExtractedSymbol> = matches
                .iter()
                .filter(|s| s.kind == "method" && s.qualified_name.starts_with(&prefix_dot))
                .copied()
                .collect();
            if !class_scoped.is_empty()
                && (receiver.is_empty()
                    || receiver == "self"
                    || receiver == "cls"
                    || receiver == "this")
            {
                let confidence = if class_scoped.len() == 1 { 1.0 } else { 0.78 };
                return (class_scoped, confidence);
            }
        }
    }

    // Strategy (b): Combined type hints
    let mut combined_type_hints: HashSet<String> = receiver_type_hints.clone();
    combined_type_hints.extend(lexical_hints.iter().cloned());
    combined_type_hints.extend(semantic_hints.iter().cloned());

    if !combined_type_hints.is_empty() {
        let mut type_tokens: HashSet<String> = HashSet::new();
        for hint in &combined_type_hints {
            type_tokens.extend(type_name_tokens(hint));
        }
        let typed_matches: Vec<&ExtractedSymbol> = matches
            .iter()
            .filter(|s| {
                if s.kind != "method" {
                    return false;
                }
                let owner = method_owner_name(s);
                let owner_tokens = type_name_tokens(&owner);
                !owner_tokens.is_disjoint(&type_tokens)
            })
            .copied()
            .collect();
        if !typed_matches.is_empty() {
            let confidence = if typed_matches.len() == 1 { 1.0 } else { 0.84 };
            return (typed_matches, confidence);
        }
    }

    // Strategy (c): Alias-based type hints
    let alias_receiver_hints = alias_hints
        .get(callsite.receiver_name.as_deref().unwrap_or(""))
        .cloned()
        .unwrap_or_default();
    if !alias_receiver_hints.is_empty() {
        let mut alias_tokens: HashSet<String> = HashSet::new();
        for hint in &alias_receiver_hints {
            alias_tokens.extend(type_name_tokens(hint));
        }
        let alias_typed_matches: Vec<&ExtractedSymbol> = matches
            .iter()
            .filter(|s| {
                if s.kind != "method" {
                    return false;
                }
                let owner = method_owner_name(s);
                !type_name_tokens(&owner).is_disjoint(&alias_tokens)
            })
            .copied()
            .collect();
        if !alias_typed_matches.is_empty() {
            let confidence = if alias_typed_matches.len() == 1 {
                1.0
            } else {
                0.83
            };
            return (alias_typed_matches, confidence);
        }
    }

    // Strategy (d): Direct receiver name match
    if !receiver.is_empty() && receiver != "self" && receiver != "cls" && receiver != "this" {
        let class_receiver: Vec<&ExtractedSymbol> = matches
            .iter()
            .filter(|s| {
                if s.kind != "method" {
                    return false;
                }
                let parts: Vec<&str> = s.qualified_name.split('.').collect();
                let owner = if parts.len() >= 2 {
                    parts[parts.len() - 2]
                } else {
                    ""
                };
                owner == receiver
            })
            .copied()
            .collect();
        if !class_receiver.is_empty() {
            let confidence = if class_receiver.len() == 1 { 1.0 } else { 0.79 };
            return (class_receiver, confidence);
        }

        // Strategy (e): Receiver in qualified_name
        let needle = format!(".{receiver}.");
        let receiver_scoped: Vec<&ExtractedSymbol> = matches
            .iter()
            .filter(|s| s.kind == "method" && s.qualified_name.contains(&needle))
            .copied()
            .collect();
        if !receiver_scoped.is_empty() {
            let confidence = if receiver_scoped.len() == 1 {
                1.0
            } else {
                0.75
            };
            return (receiver_scoped, confidence);
        }
    }

    // Strategy (f): Same file
    let same_file: Vec<&ExtractedSymbol> = matches
        .iter()
        .filter(|s| s.file_path == caller.file_path)
        .copied()
        .collect();
    if !same_file.is_empty() {
        let confidence = if same_file.len() == 1 { 1.0 } else { 0.8 };
        return (same_file, confidence);
    }

    // Strategy (g): Import-scoped
    let import_scoped: Vec<&ExtractedSymbol> = matches
        .iter()
        .filter(|s| {
            import_hint_set.iter().any(|hint| {
                if hint.is_empty() {
                    return false;
                }
                hint.contains(&s.qualified_name)
                    || s.qualified_name.contains(hint.as_str())
                    || s.file_path.ends_with(&format!("/{hint}.py"))
                    || s.file_path.ends_with(&format!("/{hint}.ts"))
                    || s.file_path.ends_with(&format!("/{hint}.go"))
            })
        })
        .copied()
        .collect();
    if !import_scoped.is_empty() {
        let confidence = if import_scoped.len() == 1 { 1.0 } else { 0.7 };
        return (import_scoped, confidence);
    }

    // Strategy (h): All matches (fallback)
    let confidence = if matches.len() == 1 { 1.0 } else { 0.5 };
    (matches, confidence)
}

// ---------------------------------------------------------------------------
// Main entry point
// ---------------------------------------------------------------------------

/// Build call-graph edges for a single source file.
///
/// # Arguments
///
/// * `source` - The raw source text of the file.
/// * `file_path` - The relative (POSIX) path of the file.
/// * `language` - Language identifier (`"python"`, `"java"`, `"typescript"`, `"go"`).
/// * `file_symbols` - Symbols extracted from this file (used to find callers).
/// * `candidate_symbols` - All symbols in the repository (used to resolve targets).
/// * `symbol_id_lookup` - Optional map from `(qualified_name, file_path)` to numeric id.
///   When `None`, ids are derived from `crc32(qualified_name) & 0x7FFFFFFF`.
/// * `semantic_receiver_type_hints` - Optional map from `(line_number, receiver_name)`
///   to a set of possible type names, supplied by the semantic hints layer.
///
/// # Returns
///
/// A deduplicated list of `CallEdge` values, sorted by `(line_number, source_id, target_id)`.
pub fn build_call_edges(
    source: &str,
    file_path: &str,
    language: &str,
    file_symbols: &[ExtractedSymbol],
    candidate_symbols: &[ExtractedSymbol],
    symbol_id_lookup: Option<&HashMap<(String, String), i64>>,
    semantic_receiver_type_hints: Option<&HashMap<(i64, String), HashSet<String>>>,
) -> Vec<CallEdge> {
    let callsites = extract_regex_calls(source, language);
    let hints = import_hints(source);
    let alias_hint_map = import_aliases(source);

    let mut edges: Vec<CallEdge> = Vec::new();
    let mut seen: HashSet<(i64, i64, i64)> = HashSet::new();

    for callsite in &callsites {
        let caller = match caller_for_line(callsite.line_number, file_symbols) {
            Some(c) => c,
            None => continue,
        };

        // Gather lexical receiver type hints for this call-site
        let lexical_hints = lexical_receiver_type_hints(
            source,
            callsite.receiver_name.as_deref(),
            callsite.line_number,
            60,
        );

        // Gather semantic receiver type hints for this call-site
        let semantic_hints = match semantic_receiver_type_hints {
            Some(map) => {
                let key = (
                    callsite.line_number,
                    callsite
                        .receiver_name
                        .as_deref()
                        .unwrap_or("")
                        .trim()
                        .to_string(),
                );
                map.get(&key).cloned().unwrap_or_default()
            }
            None => HashSet::new(),
        };

        let (targets, confidence) = resolve_targets(
            callsite,
            caller,
            candidate_symbols,
            &hints,
            &alias_hint_map,
            &HashSet::new(), // receiver_type_hints from Python AST (not available in regex path)
            &lexical_hints,
            &semantic_hints,
        );

        for target in targets {
            let (source_id, target_id) = if let Some(lookup) = symbol_id_lookup {
                let src_key = (caller.qualified_name.clone(), caller.file_path.clone());
                let tgt_key = (target.qualified_name.clone(), target.file_path.clone());
                match (lookup.get(&src_key), lookup.get(&tgt_key)) {
                    (Some(&sid), Some(&tid)) => (sid, tid),
                    _ => continue,
                }
            } else {
                (
                    symbol_id(&caller.qualified_name),
                    symbol_id(&target.qualified_name),
                )
            };

            let dedupe_key = (source_id, target_id, callsite.line_number);
            if seen.contains(&dedupe_key) {
                continue;
            }
            seen.insert(dedupe_key);

            edges.push(CallEdge {
                source_id,
                target_id,
                source_type: "symbol".to_string(),
                target_type: "symbol".to_string(),
                relationship: "CALLS".to_string(),
                file_path: file_path.to_string(),
                line_number: callsite.line_number,
                confidence,
            });
        }
    }

    edges.sort_by(|a, b| {
        a.line_number
            .cmp(&b.line_number)
            .then(a.source_id.cmp(&b.source_id))
            .then(a.target_id.cmp(&b.target_id))
    });

    edges
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    // Helper to build a minimal ExtractedSymbol
    fn make_symbol(
        name: &str,
        qualified_name: &str,
        kind: &str,
        file_path: &str,
        start_line: i64,
        end_line: i64,
    ) -> ExtractedSymbol {
        ExtractedSymbol {
            name: name.to_string(),
            qualified_name: qualified_name.to_string(),
            kind: kind.to_string(),
            file_path: file_path.to_string(),
            start_line,
            end_line,
            signature: None,
            return_type: None,
            visibility: None,
            is_async: false,
            is_static: false,
            docstring: None,
            parameters: Vec::new(),
        }
    }

    #[test]
    fn test_symbol_id_deterministic() {
        let id1 = symbol_id("foo.bar.baz");
        let id2 = symbol_id("foo.bar.baz");
        assert_eq!(id1, id2);
        assert!(id1 >= 0);
    }

    #[test]
    fn test_symbol_id_positive() {
        // Ensure the masking works for various inputs
        for name in &["a", "hello.world", "com.example.MyClass.myMethod"] {
            let id = symbol_id(name);
            assert!(
                id >= 0,
                "symbol_id({name}) should be non-negative, got {id}"
            );
        }
    }

    #[test]
    fn test_crc32_matches_zlib() {
        // Python: zlib.crc32(b"hello") == 907060870
        let crc = crc32_ieee(b"hello");
        assert_eq!(crc, 907060870);
    }

    #[test]
    fn test_extract_regex_calls_basic() {
        let source = "x = foo()\ny = bar.baz()\n";
        let calls = extract_regex_calls(source, "python");
        assert_eq!(calls.len(), 2);
        assert_eq!(calls[0].callee_name, "foo");
        assert_eq!(calls[0].line_number, 1);
        assert!(calls[0].receiver_name.is_none());
        assert_eq!(calls[1].callee_name, "baz");
        assert_eq!(calls[1].line_number, 2);
        assert_eq!(calls[1].receiver_name.as_deref(), Some("bar"));
    }

    #[test]
    fn test_extract_regex_calls_skips_keywords() {
        let source = "if (x):\n  for(i in range(10)):\n    while(true):\n";
        let calls = extract_regex_calls(source, "python");
        // "if", "for", "while" should be skipped; "range" should be found
        let names: Vec<&str> = calls.iter().map(|c| c.callee_name.as_str()).collect();
        assert!(!names.contains(&"if"));
        assert!(!names.contains(&"for"));
        assert!(!names.contains(&"while"));
        assert!(names.contains(&"range"));
    }

    #[test]
    fn test_extract_regex_calls_skips_def() {
        let source = "def foo(x):\n  pass\nfunction bar() {\n}\n";
        let calls = extract_regex_calls(source, "python");
        let names: Vec<&str> = calls.iter().map(|c| c.callee_name.as_str()).collect();
        assert!(!names.contains(&"foo"));
        assert!(!names.contains(&"bar"));
    }

    #[test]
    fn test_import_hints_python() {
        let source = "from os.path import join\nimport collections\n";
        let hints = import_hints(source);
        assert!(hints.contains("os.path"));
        assert!(hints.contains("path"));
        assert!(hints.contains("collections"));
    }

    #[test]
    fn test_import_hints_java() {
        let source = "import com.example.MyClass;\nimport java.util.*;\n";
        let hints = import_hints(source);
        assert!(hints.contains("com.example.MyClass"));
        assert!(hints.contains("MyClass"));
        assert!(hints.contains("java.util"));
        assert!(hints.contains("util"));
    }

    #[test]
    fn test_import_aliases_python() {
        let source = "from os.path import join as pjoin\nimport numpy as np\n";
        let aliases = import_aliases(source);
        assert!(aliases.contains_key("pjoin"));
        assert!(aliases["pjoin"].contains("join"));
        assert!(aliases.contains_key("np"));
        assert!(aliases["np"].contains("numpy"));
    }

    #[test]
    fn test_import_aliases_typescript() {
        let source = "import { Foo as Bar, Baz } from './module'\nimport Default from './other'\n";
        let aliases = import_aliases(source);
        assert!(aliases.contains_key("Bar"));
        assert!(aliases["Bar"].contains("Foo"));
        assert!(aliases.contains_key("Baz"));
        assert!(aliases["Baz"].contains("Baz"));
        assert!(aliases.contains_key("Default"));
    }

    #[test]
    fn test_lexical_receiver_type_hints_python() {
        let source = "x = MyClass()\nx.do_thing()\n";
        let hints = lexical_receiver_type_hints(source, Some("x"), 2, 60);
        assert!(hints.contains("MyClass"));
    }

    #[test]
    fn test_lexical_receiver_type_hints_java() {
        let source = "MyClass x = new MyClass();\nx.doThing();\n";
        let hints = lexical_receiver_type_hints(source, Some("x"), 2, 60);
        assert!(hints.contains("MyClass"));
    }

    #[test]
    fn test_lexical_receiver_type_hints_ts() {
        let source = "const x: Foo = new Bar();\nx.doThing();\n";
        let hints = lexical_receiver_type_hints(source, Some("x"), 2, 60);
        assert!(hints.contains("Foo"));
        assert!(hints.contains("Bar"));
    }

    #[test]
    fn test_lexical_receiver_type_hints_go() {
        let source = "x := &MyStruct{}\nx.DoThing()\n";
        let hints = lexical_receiver_type_hints(source, Some("x"), 2, 60);
        assert!(hints.contains("MyStruct"));
    }

    #[test]
    fn test_type_name_tokens() {
        let tokens = type_name_tokens("com.example.MyClass");
        assert!(tokens.contains("com.example.myclass"));
        assert!(tokens.contains("myclass"));
    }

    #[test]
    fn test_type_name_tokens_empty() {
        assert!(type_name_tokens("").is_empty());
        assert!(type_name_tokens("  ").is_empty());
    }

    #[test]
    fn test_method_owner_name() {
        let sym = make_symbol("do_thing", "com.Foo.do_thing", "method", "a.py", 1, 5);
        assert_eq!(method_owner_name(&sym), "Foo");
    }

    #[test]
    fn test_method_owner_name_short() {
        let sym = make_symbol("do_thing", "do_thing", "method", "a.py", 1, 5);
        assert_eq!(method_owner_name(&sym), "");
    }

    #[test]
    fn test_caller_for_line_picks_smallest() {
        let outer = make_symbol("outer", "m.outer", "function", "a.py", 1, 20);
        let inner = make_symbol("inner", "m.inner", "function", "a.py", 5, 10);
        let symbols = vec![outer, inner];
        let caller = caller_for_line(7, &symbols);
        assert!(caller.is_some());
        assert_eq!(caller.unwrap().name, "inner");
    }

    #[test]
    fn test_caller_for_line_none() {
        let sym = make_symbol("foo", "m.foo", "function", "a.py", 10, 20);
        assert!(caller_for_line(5, &[sym]).is_none());
    }

    #[test]
    fn test_build_call_edges_basic() {
        let source = "def caller():\n  callee()\n";
        let file_symbols = vec![make_symbol(
            "caller",
            "mod.caller",
            "function",
            "mod.py",
            1,
            2,
        )];
        let candidate_symbols = vec![make_symbol(
            "callee",
            "other.callee",
            "function",
            "other.py",
            1,
            3,
        )];
        let edges = build_call_edges(
            source,
            "mod.py",
            "python",
            &file_symbols,
            &candidate_symbols,
            None,
            None,
        );
        assert_eq!(edges.len(), 1);
        assert_eq!(edges[0].relationship, "CALLS");
        assert_eq!(edges[0].line_number, 2);
        assert_eq!(edges[0].confidence, 1.0);
    }

    #[test]
    fn test_build_call_edges_dedup() {
        // Two calls to the same symbol on the same line should produce one edge
        let source = "def caller():\n  foo() or foo()\n";
        let file_symbols = vec![make_symbol(
            "caller",
            "mod.caller",
            "function",
            "mod.py",
            1,
            2,
        )];
        let candidate_symbols = vec![make_symbol(
            "foo",
            "other.foo",
            "function",
            "other.py",
            1,
            3,
        )];
        let edges = build_call_edges(
            source,
            "mod.py",
            "python",
            &file_symbols,
            &candidate_symbols,
            None,
            None,
        );
        assert_eq!(edges.len(), 1);
    }

    #[test]
    fn test_build_call_edges_sorted() {
        let source = "def caller():\n  b()\n  a()\n";
        let file_symbols = vec![make_symbol(
            "caller",
            "mod.caller",
            "function",
            "mod.py",
            1,
            3,
        )];
        let candidate_symbols = vec![
            make_symbol("b", "mod.b", "function", "other.py", 1, 2),
            make_symbol("a", "mod.a", "function", "other.py", 4, 6),
        ];
        let edges = build_call_edges(
            source,
            "mod.py",
            "python",
            &file_symbols,
            &candidate_symbols,
            None,
            None,
        );
        assert_eq!(edges.len(), 2);
        assert!(edges[0].line_number <= edges[1].line_number);
    }

    #[test]
    fn test_build_call_edges_with_lookup() {
        let source = "def caller():\n  callee()\n";
        let file_symbols = vec![make_symbol(
            "caller",
            "mod.caller",
            "function",
            "mod.py",
            1,
            2,
        )];
        let candidate_symbols = vec![make_symbol(
            "callee",
            "other.callee",
            "function",
            "other.py",
            1,
            3,
        )];

        let mut lookup: HashMap<(String, String), i64> = HashMap::new();
        lookup.insert(("mod.caller".to_string(), "mod.py".to_string()), 100);
        lookup.insert(("other.callee".to_string(), "other.py".to_string()), 200);

        let edges = build_call_edges(
            source,
            "mod.py",
            "python",
            &file_symbols,
            &candidate_symbols,
            Some(&lookup),
            None,
        );
        assert_eq!(edges.len(), 1);
        assert_eq!(edges[0].source_id, 100);
        assert_eq!(edges[0].target_id, 200);
    }

    #[test]
    fn test_build_call_edges_lookup_missing_skips() {
        let source = "def caller():\n  callee()\n";
        let file_symbols = vec![make_symbol(
            "caller",
            "mod.caller",
            "function",
            "mod.py",
            1,
            2,
        )];
        let candidate_symbols = vec![make_symbol(
            "callee",
            "other.callee",
            "function",
            "other.py",
            1,
            3,
        )];

        // Only provide source_id — missing target should cause skip
        let mut lookup: HashMap<(String, String), i64> = HashMap::new();
        lookup.insert(("mod.caller".to_string(), "mod.py".to_string()), 100);

        let edges = build_call_edges(
            source,
            "mod.py",
            "python",
            &file_symbols,
            &candidate_symbols,
            Some(&lookup),
            None,
        );
        assert!(edges.is_empty());
    }

    #[test]
    fn test_resolve_class_scoped() {
        let caller = make_symbol("do_thing", "mod.MyClass.do_thing", "method", "a.py", 1, 10);
        let target = make_symbol("helper", "mod.MyClass.helper", "method", "a.py", 12, 20);
        let other = make_symbol("helper", "mod.Other.helper", "method", "b.py", 1, 5);
        let candidates = vec![target, other];

        let callsite = CallSite {
            callee_name: "helper".to_string(),
            line_number: 5,
            receiver_name: Some("self".to_string()),
        };

        let (targets, confidence) = resolve_targets(
            &callsite,
            &caller,
            &candidates,
            &HashSet::new(),
            &HashMap::new(),
            &HashSet::new(),
            &HashSet::new(),
            &HashSet::new(),
        );
        assert_eq!(targets.len(), 1);
        assert_eq!(targets[0].qualified_name, "mod.MyClass.helper");
        assert_eq!(confidence, 1.0);
    }

    #[test]
    fn test_resolve_same_file_fallback() {
        let caller = make_symbol("main", "mod.main", "function", "a.py", 1, 20);
        let target_same = make_symbol("helper", "mod.helper", "function", "a.py", 22, 30);
        let target_other = make_symbol("helper", "other.helper", "function", "b.py", 1, 5);
        let candidates = vec![target_same, target_other];

        let callsite = CallSite {
            callee_name: "helper".to_string(),
            line_number: 10,
            receiver_name: None,
        };

        let (targets, confidence) = resolve_targets(
            &callsite,
            &caller,
            &candidates,
            &HashSet::new(),
            &HashMap::new(),
            &HashSet::new(),
            &HashSet::new(),
            &HashSet::new(),
        );
        assert_eq!(targets.len(), 1);
        assert_eq!(targets[0].file_path, "a.py");
        assert_eq!(confidence, 1.0);
    }

    #[test]
    fn test_is_call_keyword() {
        assert!(is_call_keyword("if"));
        assert!(is_call_keyword("for"));
        assert!(is_call_keyword("new"));
        assert!(!is_call_keyword("foo"));
        assert!(!is_call_keyword("println"));
    }
}
