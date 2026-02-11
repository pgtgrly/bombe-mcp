//! Symbol and import extraction from source code.
//!
//! Ports the Python `bombe.indexer.symbols` module (647 LOC) to Rust.
//! Java, TypeScript and Go extraction uses regex-based line scanning that
//! mirrors the Python implementation exactly.  Python symbol extraction
//! requires CPython's `ast` module, so it is left as a stub here — the
//! Python side continues to handle `.py` files.

use std::path::Path;
use std::sync::LazyLock;

use regex::Regex;

// ---------------------------------------------------------------------------
// Extracted types
// ---------------------------------------------------------------------------

/// A symbol extracted from source code (function, method, class, etc.).
#[derive(Clone, Debug)]
pub struct ExtractedSymbol {
    pub name: String,
    pub qualified_name: String,
    /// One of "function", "method", "class", "interface", "constant".
    pub kind: String,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub signature: Option<String>,
    pub return_type: Option<String>,
    pub visibility: Option<String>,
    pub is_async: bool,
    pub is_static: bool,
    pub docstring: Option<String>,
    pub parameters: Vec<ExtractedParameter>,
}

/// A single parameter of a function or method.
#[derive(Clone, Debug)]
pub struct ExtractedParameter {
    pub name: String,
    pub type_: Option<String>,
    pub position: i64,
}

/// An import statement extracted from source code.
#[derive(Clone, Debug)]
pub struct ExtractedImport {
    pub source_file_path: String,
    pub import_statement: String,
    pub module_name: String,
    pub imported_names: Vec<String>,
    pub line_number: i64,
}

// ---------------------------------------------------------------------------
// Helper functions
// ---------------------------------------------------------------------------

/// Convert a file path to a dotted module name.
///
/// Strips the file extension and joins path components with dots,
/// skipping any leading `/` or `.` segments.
pub fn to_module_name(path: &str) -> String {
    let p = Path::new(path);
    let without_ext = p.with_extension("");
    let parts: Vec<&str> = without_ext
        .components()
        .filter_map(|c| match c {
            std::path::Component::Normal(os) => os.to_str(),
            _ => None,
        })
        .filter(|s| !s.is_empty() && *s != ".")
        .collect();
    parts.join(".")
}

/// Determine visibility from a name: "private" if it starts with `_`, else "public".
pub fn visibility(name: &str) -> &'static str {
    if name.starts_with('_') {
        "private"
    } else {
        "public"
    }
}

/// Parse a raw comma-separated parameter string into `ExtractedParameter` entries.
///
/// The `language` argument controls splitting logic:
/// - `"typescript"`: split on `:` to separate name from type annotation
/// - `"go"`: first token is the name, remaining tokens form the type
/// - everything else (e.g. `"java"`): last token is the name, preceding tokens form the type
pub fn build_parameters(params_raw: &str, language: &str) -> Vec<ExtractedParameter> {
    let mut parameters = Vec::new();
    let trimmed = params_raw.trim();
    if trimmed.is_empty() {
        return parameters;
    }
    for (index, parameter) in trimmed.split(',').enumerate() {
        let chunk = parameter.trim();
        if chunk.is_empty() {
            continue;
        }
        let (name, param_type) = match language {
            "typescript" => {
                if let Some(colon_pos) = chunk.find(':') {
                    let before = chunk[..colon_pos].trim().to_string();
                    let after = chunk[colon_pos + 1..].trim().to_string();
                    (before, if after.is_empty() { None } else { Some(after) })
                } else {
                    (chunk.to_string(), None)
                }
            }
            "go" => {
                let parts: Vec<String> = chunk
                    .replace('\t', " ")
                    .split(' ')
                    .filter(|s| !s.is_empty())
                    .map(|s| s.to_string())
                    .collect();
                if parts.is_empty() {
                    continue;
                }
                let n = parts[0].replace("...", "");
                let t = if parts.len() > 1 {
                    Some(parts[1..].join(" "))
                } else {
                    None
                };
                (n, t)
            }
            _ => {
                // Java and others: last token is name, preceding tokens form type
                let parts: Vec<String> = chunk
                    .replace('\t', " ")
                    .split(' ')
                    .filter(|s| !s.is_empty())
                    .map(|s| s.to_string())
                    .collect();
                if parts.is_empty() {
                    continue;
                }
                let n = parts.last().unwrap().replace("...", "");
                let t = if parts.len() > 1 {
                    Some(parts[..parts.len() - 1].join(" "))
                } else {
                    None
                };
                (n, t)
            }
        };
        if !name.is_empty() {
            parameters.push(ExtractedParameter {
                name,
                type_: param_type,
                position: index as i64,
            });
        }
    }
    parameters
}

/// Normalize a type name: trim whitespace and trailing semicolons.
/// Returns `None` if the result is empty or the input is `None`.
pub fn normalize_type_name(type_name: Option<&str>) -> Option<String> {
    let raw = type_name?;
    let normalized = raw.trim().trim_end_matches(';');
    if normalized.is_empty() {
        None
    } else {
        Some(normalized.to_string())
    }
}

// ---------------------------------------------------------------------------
// Compiled regex patterns (LazyLock for one-time init)
// ---------------------------------------------------------------------------

// -- Java --

static JAVA_PACKAGE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^\s*package\s+([A-Za-z0-9_.]+)\s*;").unwrap());

static JAVA_IMPORT_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^\s*import\s+([A-Za-z0-9_.*]+)\s*;").unwrap());

static JAVA_CLASS_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^\s*(public|private|protected)?\s*(?:abstract\s+|final\s+)?(class|interface|enum)\s+([A-Za-z_][A-Za-z0-9_]*)",
    )
    .unwrap()
});

static JAVA_METHOD_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^\s*(public|private|protected)?\s*(static\s+)?(?:final\s+)?([A-Za-z0-9_<>\[\], ?]+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*\{",
    )
    .unwrap()
});

// -- TypeScript --

static TS_IMPORT_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"^\s*import(?:\s+type)?\s+.*?\s+from\s+['"]([^'"]+)['"];?"#).unwrap()
});

static TS_CLASS_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^\s*(?:export\s+)?(class|interface|type)\s+([A-Za-z_][A-Za-z0-9_]*)").unwrap()
});

static TS_FUNCTION_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*(?::\s*([^{]+))?",
    )
    .unwrap()
});

static TS_ARROW_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s+)?\(([^)]*)\)\s*(?::\s*([^=]+))?\s*=>",
    )
    .unwrap()
});

static TS_METHOD_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^\s*(?:public|private|protected)?\s*(?:async\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*(?::\s*([^=]+))?\s*\{?",
    )
    .unwrap()
});

static TS_CONST_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^\s*(?:export\s+)?const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*[^=].*;").unwrap()
});

// -- Go --

static GO_PACKAGE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^\s*package\s+([A-Za-z_][A-Za-z0-9_]*)").unwrap());

static GO_IMPORT_SINGLE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"^\s*import\s+"([^"]+)""#).unwrap());

static GO_IMPORT_BLOCK_START_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^\s*import\s*\(").unwrap());

static GO_IMPORT_BLOCK_LINE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"^\s*"([^"]+)""#).unwrap());

static GO_TYPE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^\s*type\s+([A-Za-z_][A-Za-z0-9_]*)\s+(struct|interface)\b").unwrap()
});

static GO_FUNCTION_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^\s*func\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*([A-Za-z0-9_*.\[\]]+)?")
        .unwrap()
});

static GO_METHOD_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^\s*func\s*\(([^)]*)\)\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*([A-Za-z0-9_*.\[\]]+)?",
    )
    .unwrap()
});

static GO_CONST_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^\s*const\s+([A-Za-z_][A-Za-z0-9_]*)\b").unwrap());

// ---------------------------------------------------------------------------
// Java extraction
// ---------------------------------------------------------------------------

/// Extract symbols and imports from Java source code.
///
/// Tracks a class stack with brace-depth for scope detection, exactly
/// matching the Python `_java_symbols` implementation.
fn java_symbols(source: &str, file_path: &str) -> (Vec<ExtractedSymbol>, Vec<ExtractedImport>) {
    let lines: Vec<&str> = source.lines().collect();
    let mut package_name = String::new();
    let mut imports: Vec<ExtractedImport> = Vec::new();
    let mut symbols: Vec<ExtractedSymbol> = Vec::new();
    // (symbol_index, class_name, brace_depth)
    let mut class_stack: Vec<(usize, String, i32)> = Vec::new();

    for (line_idx, line) in lines.iter().enumerate() {
        let index = (line_idx + 1) as i64; // 1-based

        // Package declaration
        if let Some(caps) = JAVA_PACKAGE_RE.captures(line) {
            package_name = caps[1].to_string();
        }

        // Import statement
        if let Some(caps) = JAVA_IMPORT_RE.captures(line) {
            let module_name = caps[1].to_string();
            imports.push(ExtractedImport {
                source_file_path: file_path.to_string(),
                import_statement: line.trim().to_string(),
                module_name,
                imported_names: Vec::new(),
                line_number: index,
            });
        }

        // Class / interface / enum declaration
        if let Some(caps) = JAVA_CLASS_RE.captures(line) {
            let vis = caps
                .get(1)
                .map(|m| m.as_str().to_string())
                .unwrap_or_else(|| "package".to_string());
            let raw_kind = &caps[2];
            let kind = if raw_kind == "interface" {
                "interface"
            } else {
                "class"
            };
            let class_name = caps[3].to_string();
            let qualified_name = if package_name.is_empty() {
                class_name.clone()
            } else {
                format!("{}.{}", package_name, class_name)
            };
            let symbol_index = symbols.len();
            let brace_depth = line.chars().filter(|&c| c == '{').count() as i32
                - line.chars().filter(|&c| c == '}').count() as i32;
            class_stack.push((symbol_index, class_name.clone(), brace_depth));
            symbols.push(ExtractedSymbol {
                name: class_name,
                qualified_name,
                kind: kind.to_string(),
                file_path: file_path.to_string(),
                start_line: index,
                end_line: index,
                signature: Some(line.trim().to_string()),
                return_type: None,
                visibility: Some(vis),
                is_async: false,
                is_static: false,
                docstring: None,
                parameters: Vec::new(),
            });
            // Brace tracking for this line happens below after method check,
            // but we already set the initial depth. The Python code uses `continue`
            // here, so skip method matching on this line.

            // Update brace depth for stacked classes (excluding current, which
            // was just pushed with the correct initial depth).
            // Note: the Python code does `continue` after pushing the class,
            // so the brace-tracking block at the end of the loop body does NOT
            // run for the class-declaration line. We replicate that by jumping
            // to the next iteration.
            continue;
        }

        // Method declaration (only inside a class)
        if let Some(caps) = JAVA_METHOD_RE.captures(line) {
            if !class_stack.is_empty() {
                let vis = caps
                    .get(1)
                    .map(|m| m.as_str().to_string())
                    .unwrap_or_else(|| "package".to_string());
                let is_static = caps.get(2).is_some();
                let return_type = caps[3].trim().to_string();
                let method_name = caps[4].to_string();
                let params_raw = caps[5].trim().to_string();
                let parameters = build_parameters(&params_raw, "java");
                let current_class = &class_stack.last().unwrap().1;
                let class_prefix = if package_name.is_empty() {
                    current_class.clone()
                } else {
                    format!("{}.{}", package_name, current_class)
                };
                symbols.push(ExtractedSymbol {
                    name: method_name.clone(),
                    qualified_name: format!("{}.{}", class_prefix, method_name),
                    kind: "method".to_string(),
                    file_path: file_path.to_string(),
                    start_line: index,
                    end_line: index,
                    signature: Some(line.trim().to_string()),
                    return_type: Some(return_type),
                    visibility: Some(vis),
                    is_async: false,
                    is_static,
                    docstring: None,
                    parameters,
                });
            }
        }

        // Brace-depth tracking for class end detection
        if !class_stack.is_empty() {
            let open = line.chars().filter(|&c| c == '{').count() as i32;
            let close = line.chars().filter(|&c| c == '}').count() as i32;
            let delta = open - close;

            // Update top-of-stack depth
            if let Some(top) = class_stack.last_mut() {
                top.2 += delta;
            }

            // Pop finished classes
            while let Some(top) = class_stack.last() {
                if top.2 <= 0 {
                    let finished_index = top.0;
                    class_stack.pop();
                    // Update end_line of the finished class symbol
                    symbols[finished_index].end_line = index;
                } else {
                    break;
                }
            }
        }
    }

    (symbols, imports)
}

// ---------------------------------------------------------------------------
// TypeScript extraction
// ---------------------------------------------------------------------------

/// Extract symbols and imports from TypeScript source code.
///
/// Mirrors the Python `_typescript_symbols` implementation exactly.
fn typescript_symbols(
    source: &str,
    file_path: &str,
) -> (Vec<ExtractedSymbol>, Vec<ExtractedImport>) {
    let lines: Vec<&str> = source.lines().collect();
    let module_name = to_module_name(file_path);
    let mut imports: Vec<ExtractedImport> = Vec::new();
    let mut symbols: Vec<ExtractedSymbol> = Vec::new();
    // (class_name, brace_depth)
    let mut class_stack: Vec<(String, i32)> = Vec::new();

    for (line_idx, line) in lines.iter().enumerate() {
        let index = (line_idx + 1) as i64;

        // Import
        if let Some(caps) = TS_IMPORT_RE.captures(line) {
            let import_module = caps[1].to_string();
            imports.push(ExtractedImport {
                source_file_path: file_path.to_string(),
                import_statement: line.trim().to_string(),
                module_name: import_module,
                imported_names: Vec::new(),
                line_number: index,
            });
        }

        // Class / interface / type
        if let Some(caps) = TS_CLASS_RE.captures(line) {
            let raw_kind = &caps[1];
            let kind = if raw_kind == "interface" || raw_kind == "type" {
                "interface"
            } else {
                "class"
            };
            let class_name = caps[2].to_string();
            symbols.push(ExtractedSymbol {
                name: class_name.clone(),
                qualified_name: format!("{}.{}", module_name, class_name),
                kind: kind.to_string(),
                file_path: file_path.to_string(),
                start_line: index,
                end_line: index,
                signature: Some(line.trim().to_string()),
                return_type: None,
                visibility: Some("public".to_string()),
                is_async: false,
                is_static: false,
                docstring: None,
                parameters: Vec::new(),
            });
            let brace_depth = line.chars().filter(|&c| c == '{').count() as i32
                - line.chars().filter(|&c| c == '}').count() as i32;
            class_stack.push((class_name, brace_depth));
            continue;
        }

        // Top-level function
        if let Some(caps) = TS_FUNCTION_RE.captures(line) {
            let function_name = caps[1].to_string();
            let parameters = build_parameters(&caps[2], "typescript");
            let return_type = caps
                .get(3)
                .and_then(|m| normalize_type_name(Some(m.as_str())));
            symbols.push(ExtractedSymbol {
                name: function_name.clone(),
                qualified_name: format!("{}.{}", module_name, function_name),
                kind: "function".to_string(),
                file_path: file_path.to_string(),
                start_line: index,
                end_line: index,
                signature: Some(line.trim().to_string()),
                return_type,
                visibility: Some("public".to_string()),
                is_async: line.contains("async "),
                is_static: false,
                docstring: None,
                parameters,
            });
            continue;
        }

        // Arrow function
        if let Some(caps) = TS_ARROW_RE.captures(line) {
            let function_name = caps[1].to_string();
            let parameters = build_parameters(&caps[2], "typescript");
            let return_type = caps
                .get(3)
                .and_then(|m| normalize_type_name(Some(m.as_str())));
            symbols.push(ExtractedSymbol {
                name: function_name.clone(),
                qualified_name: format!("{}.{}", module_name, function_name),
                kind: "function".to_string(),
                file_path: file_path.to_string(),
                start_line: index,
                end_line: index,
                signature: Some(line.trim().to_string()),
                return_type,
                visibility: Some("public".to_string()),
                is_async: line.contains("async "),
                is_static: false,
                docstring: None,
                parameters,
            });
            continue;
        }

        // Method inside a class
        if let Some(caps) = TS_METHOD_RE.captures(line) {
            if !class_stack.is_empty() {
                let method_name = caps[1].to_string();
                if method_name != "constructor" {
                    let parameters = build_parameters(&caps[2], "typescript");
                    let return_type = caps
                        .get(3)
                        .and_then(|m| normalize_type_name(Some(m.as_str())));
                    let current_class = &class_stack.last().unwrap().0;
                    symbols.push(ExtractedSymbol {
                        name: method_name.clone(),
                        qualified_name: format!(
                            "{}.{}.{}",
                            module_name, current_class, method_name
                        ),
                        kind: "method".to_string(),
                        file_path: file_path.to_string(),
                        start_line: index,
                        end_line: index,
                        signature: Some(line.trim().to_string()),
                        return_type,
                        visibility: Some("public".to_string()),
                        is_async: line.contains("async "),
                        is_static: false,
                        docstring: None,
                        parameters,
                    });
                }
            }
        }

        // Constant (only if not an arrow function — exclude `=>`)
        if let Some(caps) = TS_CONST_RE.captures(line) {
            if !line.contains("=>") {
                let const_name = caps[1].to_string();
                symbols.push(ExtractedSymbol {
                    name: const_name.clone(),
                    qualified_name: format!("{}.{}", module_name, const_name),
                    kind: "constant".to_string(),
                    file_path: file_path.to_string(),
                    start_line: index,
                    end_line: index,
                    signature: Some(line.trim().to_string()),
                    return_type: None,
                    visibility: Some("public".to_string()),
                    is_async: false,
                    is_static: false,
                    docstring: None,
                    parameters: Vec::new(),
                });
            }
        }

        // Brace-depth tracking for class stack
        if !class_stack.is_empty() {
            let open = line.chars().filter(|&c| c == '{').count() as i32;
            let close = line.chars().filter(|&c| c == '}').count() as i32;
            let delta = open - close;

            if let Some(top) = class_stack.last_mut() {
                top.1 += delta;
            }

            while let Some(top) = class_stack.last() {
                if top.1 <= 0 {
                    class_stack.pop();
                } else {
                    break;
                }
            }
        }
    }

    (symbols, imports)
}

// ---------------------------------------------------------------------------
// Go extraction
// ---------------------------------------------------------------------------

/// Go visibility: exported names start with an uppercase letter.
fn go_visibility(name: &str) -> &'static str {
    if name.starts_with(|c: char| c.is_ascii_uppercase()) {
        "public"
    } else {
        "private"
    }
}

/// Extract symbols and imports from Go source code.
///
/// Mirrors the Python `_go_symbols` implementation exactly, including
/// the import-block state machine.
fn go_symbols(source: &str, file_path: &str) -> (Vec<ExtractedSymbol>, Vec<ExtractedImport>) {
    let lines: Vec<&str> = source.lines().collect();
    let mut package_name = String::new();
    let mut imports: Vec<ExtractedImport> = Vec::new();
    let mut symbols: Vec<ExtractedSymbol> = Vec::new();
    let mut import_block = false;

    for (line_idx, line) in lines.iter().enumerate() {
        let index = (line_idx + 1) as i64;

        // Package declaration
        if let Some(caps) = GO_PACKAGE_RE.captures(line) {
            package_name = caps[1].to_string();
        }

        // Import block start
        if GO_IMPORT_BLOCK_START_RE.is_match(line) {
            import_block = true;
            continue;
        }

        // Inside import block
        if import_block {
            if line.trim() == ")" {
                import_block = false;
            } else if let Some(caps) = GO_IMPORT_BLOCK_LINE_RE.captures(line) {
                let module = caps[1].to_string();
                imports.push(ExtractedImport {
                    source_file_path: file_path.to_string(),
                    import_statement: line.trim().to_string(),
                    module_name: module,
                    imported_names: Vec::new(),
                    line_number: index,
                });
            }
            continue;
        }

        // Single-line import
        if let Some(caps) = GO_IMPORT_SINGLE_RE.captures(line) {
            let module = caps[1].to_string();
            imports.push(ExtractedImport {
                source_file_path: file_path.to_string(),
                import_statement: line.trim().to_string(),
                module_name: module,
                imported_names: Vec::new(),
                line_number: index,
            });
            continue;
        }

        // Type declaration (struct / interface)
        if let Some(caps) = GO_TYPE_RE.captures(line) {
            let type_name = caps[1].to_string();
            let type_kind = if &caps[2] == "interface" {
                "interface"
            } else {
                "class"
            };
            let qualified = if package_name.is_empty() {
                type_name.clone()
            } else {
                format!("{}.{}", package_name, type_name)
            };
            symbols.push(ExtractedSymbol {
                name: type_name.clone(),
                qualified_name: qualified,
                kind: type_kind.to_string(),
                file_path: file_path.to_string(),
                start_line: index,
                end_line: index,
                signature: Some(line.trim().to_string()),
                return_type: None,
                visibility: Some(go_visibility(&type_name).to_string()),
                is_async: false,
                is_static: false,
                docstring: None,
                parameters: Vec::new(),
            });
            continue;
        }

        // Method (func with receiver)
        if let Some(caps) = GO_METHOD_RE.captures(line) {
            let receiver_raw = caps[1].trim().to_string();
            let method_name = caps[2].to_string();
            let params_raw = &caps[3];
            let return_type = caps
                .get(4)
                .map(|m| m.as_str().trim().to_string())
                .filter(|s| !s.is_empty());
            let receiver_tokens: Vec<&str> =
                receiver_raw.split(' ').filter(|s| !s.is_empty()).collect();
            let receiver_type = if let Some(last) = receiver_tokens.last() {
                last.replace('*', "")
            } else {
                "Receiver".to_string()
            };
            let parameters = build_parameters(params_raw, "go");
            let class_prefix = if package_name.is_empty() {
                receiver_type
            } else {
                format!("{}.{}", package_name, receiver_type)
            };
            symbols.push(ExtractedSymbol {
                name: method_name.clone(),
                qualified_name: format!("{}.{}", class_prefix, method_name),
                kind: "method".to_string(),
                file_path: file_path.to_string(),
                start_line: index,
                end_line: index,
                signature: Some(line.trim().to_string()),
                return_type,
                visibility: Some(go_visibility(&method_name).to_string()),
                is_async: false,
                is_static: false,
                docstring: None,
                parameters,
            });
            continue;
        }

        // Top-level function
        if let Some(caps) = GO_FUNCTION_RE.captures(line) {
            let function_name = caps[1].to_string();
            let params_raw = &caps[2];
            let return_type = caps
                .get(3)
                .map(|m| m.as_str().trim().to_string())
                .filter(|s| !s.is_empty());
            let parameters = build_parameters(params_raw, "go");
            let qualified = if package_name.is_empty() {
                function_name.clone()
            } else {
                format!("{}.{}", package_name, function_name)
            };
            symbols.push(ExtractedSymbol {
                name: function_name.clone(),
                qualified_name: qualified,
                kind: "function".to_string(),
                file_path: file_path.to_string(),
                start_line: index,
                end_line: index,
                signature: Some(line.trim().to_string()),
                return_type,
                visibility: Some(go_visibility(&function_name).to_string()),
                is_async: false,
                is_static: false,
                docstring: None,
                parameters,
            });
        }

        // Constant
        if let Some(caps) = GO_CONST_RE.captures(line) {
            let const_name = caps[1].to_string();
            let qualified = if package_name.is_empty() {
                const_name.clone()
            } else {
                format!("{}.{}", package_name, const_name)
            };
            symbols.push(ExtractedSymbol {
                name: const_name.clone(),
                qualified_name: qualified,
                kind: "constant".to_string(),
                file_path: file_path.to_string(),
                start_line: index,
                end_line: index,
                signature: Some(line.trim().to_string()),
                return_type: None,
                visibility: Some(go_visibility(&const_name).to_string()),
                is_async: false,
                is_static: false,
                docstring: None,
                parameters: Vec::new(),
            });
        }
    }

    (symbols, imports)
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Extract symbols and imports from source code.
///
/// Dispatches to the language-specific extractor. Java, TypeScript and Go
/// are handled natively in Rust. Python extraction requires CPython's `ast`
/// module and should be performed on the Python side; this function returns
/// empty results for Python.
///
/// # Arguments
///
/// * `source`   - Source code text.
/// * `file_path` - Relative or absolute file path (used for module name derivation).
/// * `language`  - One of `"java"`, `"typescript"`, `"go"`, `"python"`.
pub fn extract_symbols(
    source: &str,
    file_path: &str,
    language: &str,
) -> (Vec<ExtractedSymbol>, Vec<ExtractedImport>) {
    match language {
        "java" => java_symbols(source, file_path),
        "typescript" => typescript_symbols(source, file_path),
        "go" => go_symbols(source, file_path),
        // Python extraction requires CPython's ast module; handled on
        // the Python side via PyO3 callback.
        "python" => (Vec::new(), Vec::new()),
        _ => (Vec::new(), Vec::new()),
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    // -- Helper tests -------------------------------------------------------

    #[test]
    fn test_to_module_name_simple() {
        assert_eq!(
            to_module_name("src/bombe/indexer/symbols.py"),
            "src.bombe.indexer.symbols"
        );
    }

    #[test]
    fn test_to_module_name_no_extension() {
        assert_eq!(to_module_name("foo/bar/baz"), "foo.bar.baz");
    }

    #[test]
    fn test_visibility_private() {
        assert_eq!(visibility("_helper"), "private");
        assert_eq!(visibility("__init__"), "private");
    }

    #[test]
    fn test_visibility_public() {
        assert_eq!(visibility("main"), "public");
        assert_eq!(visibility("MyClass"), "public");
    }

    #[test]
    fn test_normalize_type_name_trims() {
        assert_eq!(
            normalize_type_name(Some("  string; ")),
            Some("string".to_string())
        );
    }

    #[test]
    fn test_normalize_type_name_none() {
        assert_eq!(normalize_type_name(None), None);
    }

    #[test]
    fn test_normalize_type_name_empty() {
        assert_eq!(normalize_type_name(Some("  ")), None);
    }

    #[test]
    fn test_build_parameters_java() {
        let params = build_parameters("int count, String name", "java");
        assert_eq!(params.len(), 2);
        assert_eq!(params[0].name, "count");
        assert_eq!(params[0].type_.as_deref(), Some("int"));
        assert_eq!(params[0].position, 0);
        assert_eq!(params[1].name, "name");
        assert_eq!(params[1].type_.as_deref(), Some("String"));
        assert_eq!(params[1].position, 1);
    }

    #[test]
    fn test_build_parameters_typescript() {
        let params = build_parameters("name: string, age: number", "typescript");
        assert_eq!(params.len(), 2);
        assert_eq!(params[0].name, "name");
        assert_eq!(params[0].type_.as_deref(), Some("string"));
        assert_eq!(params[1].name, "age");
        assert_eq!(params[1].type_.as_deref(), Some("number"));
    }

    #[test]
    fn test_build_parameters_go() {
        let params = build_parameters("ctx context.Context, name string", "go");
        assert_eq!(params.len(), 2);
        assert_eq!(params[0].name, "ctx");
        assert_eq!(params[0].type_.as_deref(), Some("context.Context"));
        assert_eq!(params[1].name, "name");
        assert_eq!(params[1].type_.as_deref(), Some("string"));
    }

    #[test]
    fn test_build_parameters_empty() {
        let params = build_parameters("", "java");
        assert!(params.is_empty());
    }

    // -- Java extraction tests ----------------------------------------------

    #[test]
    fn test_java_package_and_import() {
        let src = "\
package com.example.app;

import java.util.List;
import java.io.*;

public class App {
}
";
        let (symbols, imports) = extract_symbols(src, "App.java", "java");
        assert_eq!(imports.len(), 2);
        assert_eq!(imports[0].module_name, "java.util.List");
        assert_eq!(imports[0].line_number, 3);
        assert_eq!(imports[1].module_name, "java.io.*");
        assert_eq!(symbols.len(), 1);
        assert_eq!(symbols[0].name, "App");
        assert_eq!(symbols[0].qualified_name, "com.example.app.App");
        assert_eq!(symbols[0].kind, "class");
    }

    #[test]
    fn test_java_method_extraction() {
        let src = "\
package com.example;

public class Service {
    public static void doWork(int count) {
    }
    private String helper(String name, int x) {
    }
}
";
        let (symbols, _imports) = extract_symbols(src, "Service.java", "java");
        // class + 2 methods
        assert_eq!(symbols.len(), 3);

        let class_sym = &symbols[0];
        assert_eq!(class_sym.name, "Service");
        assert_eq!(class_sym.kind, "class");

        let method1 = &symbols[1];
        assert_eq!(method1.name, "doWork");
        assert_eq!(method1.kind, "method");
        assert_eq!(method1.qualified_name, "com.example.Service.doWork");
        assert_eq!(method1.visibility.as_deref(), Some("public"));
        assert!(method1.is_static);
        assert_eq!(method1.return_type.as_deref(), Some("void"));
        assert_eq!(method1.parameters.len(), 1);
        assert_eq!(method1.parameters[0].name, "count");

        let method2 = &symbols[2];
        assert_eq!(method2.name, "helper");
        assert_eq!(method2.visibility.as_deref(), Some("private"));
        assert!(!method2.is_static);
        assert_eq!(method2.parameters.len(), 2);
    }

    #[test]
    fn test_java_interface() {
        let src = "\
public interface Runnable {
    void run();
}
";
        let (symbols, _) = extract_symbols(src, "Runnable.java", "java");
        assert_eq!(symbols.len(), 1);
        assert_eq!(symbols[0].kind, "interface");
        assert_eq!(symbols[0].name, "Runnable");
    }

    #[test]
    fn test_java_class_end_line() {
        let src = "\
public class Foo {
    public void bar() {
    }
}
";
        let (symbols, _) = extract_symbols(src, "Foo.java", "java");
        let class_sym = &symbols[0];
        assert_eq!(class_sym.name, "Foo");
        assert_eq!(class_sym.start_line, 1);
        assert_eq!(class_sym.end_line, 4);
    }

    // -- TypeScript extraction tests ----------------------------------------

    #[test]
    fn test_typescript_imports() {
        let src = "\
import { foo } from 'bar';
import type { Baz } from \"./baz\";
";
        let (_, imports) = extract_symbols(src, "src/index.ts", "typescript");
        assert_eq!(imports.len(), 2);
        assert_eq!(imports[0].module_name, "bar");
        assert_eq!(imports[1].module_name, "./baz");
    }

    #[test]
    fn test_typescript_function() {
        let src = "\
export async function fetchData(url: string): Promise<Response> {
}
";
        let (symbols, _) = extract_symbols(src, "src/api.ts", "typescript");
        assert_eq!(symbols.len(), 1);
        assert_eq!(symbols[0].name, "fetchData");
        assert_eq!(symbols[0].kind, "function");
        assert!(symbols[0].is_async);
        assert_eq!(symbols[0].parameters.len(), 1);
        assert_eq!(symbols[0].parameters[0].name, "url");
        assert_eq!(symbols[0].parameters[0].type_.as_deref(), Some("string"));
        assert!(symbols[0].return_type.is_some());
    }

    #[test]
    fn test_typescript_arrow_function() {
        let src = "\
export const add = (a: number, b: number): number => a + b;
";
        let (symbols, _) = extract_symbols(src, "src/utils.ts", "typescript");
        // Arrow match should fire; const should not (because `=>` is in the line)
        assert_eq!(symbols.len(), 1);
        assert_eq!(symbols[0].name, "add");
        assert_eq!(symbols[0].kind, "function");
    }

    #[test]
    fn test_typescript_class_and_method() {
        let src = "\
export class UserService {
    async getUser(id: string): Promise<User> {
    }
}
";
        let (symbols, _) = extract_symbols(src, "src/service.ts", "typescript");
        assert!(symbols.len() >= 2);
        assert_eq!(symbols[0].name, "UserService");
        assert_eq!(symbols[0].kind, "class");
        assert_eq!(symbols[1].name, "getUser");
        assert_eq!(symbols[1].kind, "method");
        assert!(symbols[1].is_async);
    }

    #[test]
    fn test_typescript_const() {
        let src = "\
export const MAX_RETRIES = 3;
";
        let (symbols, _) = extract_symbols(src, "src/config.ts", "typescript");
        assert_eq!(symbols.len(), 1);
        assert_eq!(symbols[0].name, "MAX_RETRIES");
        assert_eq!(symbols[0].kind, "constant");
    }

    #[test]
    fn test_typescript_interface() {
        let src = "\
export interface Config {
    host: string;
    port: number;
}
";
        let (symbols, _) = extract_symbols(src, "src/types.ts", "typescript");
        assert!(symbols.len() >= 1);
        assert_eq!(symbols[0].name, "Config");
        assert_eq!(symbols[0].kind, "interface");
    }

    // -- Go extraction tests ------------------------------------------------

    #[test]
    fn test_go_package_and_imports() {
        let src = "\
package main

import \"fmt\"

import (
    \"os\"
    \"strings\"
)
";
        let (_, imports) = extract_symbols(src, "main.go", "go");
        assert_eq!(imports.len(), 3);
        assert_eq!(imports[0].module_name, "fmt");
        assert_eq!(imports[1].module_name, "os");
        assert_eq!(imports[2].module_name, "strings");
    }

    #[test]
    fn test_go_function() {
        let src = "\
package main

func main() {
}

func helper(name string) error {
}
";
        let (symbols, _) = extract_symbols(src, "main.go", "go");
        assert_eq!(symbols.len(), 2);
        assert_eq!(symbols[0].name, "main");
        assert_eq!(symbols[0].qualified_name, "main.main");
        assert_eq!(symbols[0].kind, "function");
        assert_eq!(symbols[0].visibility.as_deref(), Some("private"));

        assert_eq!(symbols[1].name, "helper");
        assert_eq!(symbols[1].visibility.as_deref(), Some("private"));
        assert_eq!(symbols[1].parameters.len(), 1);
        assert_eq!(symbols[1].parameters[0].name, "name");
    }

    #[test]
    fn test_go_method() {
        let src = "\
package http

func (s *Server) ListenAndServe() error {
}
";
        let (symbols, _) = extract_symbols(src, "server.go", "go");
        assert_eq!(symbols.len(), 1);
        assert_eq!(symbols[0].name, "ListenAndServe");
        assert_eq!(symbols[0].kind, "method");
        assert_eq!(symbols[0].qualified_name, "http.Server.ListenAndServe");
        assert_eq!(symbols[0].visibility.as_deref(), Some("public"));
        assert_eq!(symbols[0].return_type.as_deref(), Some("error"));
    }

    #[test]
    fn test_go_type_struct() {
        let src = "\
package http

type Server struct {
}
";
        let (symbols, _) = extract_symbols(src, "server.go", "go");
        assert_eq!(symbols.len(), 1);
        assert_eq!(symbols[0].name, "Server");
        assert_eq!(symbols[0].kind, "class");
        assert_eq!(symbols[0].qualified_name, "http.Server");
    }

    #[test]
    fn test_go_type_interface() {
        let src = "\
package io

type Reader interface {
}
";
        let (symbols, _) = extract_symbols(src, "io.go", "go");
        assert_eq!(symbols.len(), 1);
        assert_eq!(symbols[0].name, "Reader");
        assert_eq!(symbols[0].kind, "interface");
    }

    #[test]
    fn test_go_const() {
        let src = "\
package main

const MaxRetries = 3
";
        let (symbols, _) = extract_symbols(src, "main.go", "go");
        assert_eq!(symbols.len(), 1);
        assert_eq!(symbols[0].name, "MaxRetries");
        assert_eq!(symbols[0].kind, "constant");
        assert_eq!(symbols[0].visibility.as_deref(), Some("public"));
    }

    // -- Unsupported / Python -----------------------------------------------

    #[test]
    fn test_python_returns_empty() {
        let (symbols, imports) = extract_symbols("def foo(): pass", "foo.py", "python");
        assert!(symbols.is_empty());
        assert!(imports.is_empty());
    }

    #[test]
    fn test_unknown_language_returns_empty() {
        let (symbols, imports) = extract_symbols("fn main() {}", "main.rs", "rust");
        assert!(symbols.is_empty());
        assert!(imports.is_empty());
    }
}
