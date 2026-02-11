//! Import resolution from language-specific import records to repository files.

use std::collections::HashMap;
use std::path::Path;

use crate::indexer::symbols::ExtractedImport;

pub struct ImportEdge {
    pub source_id: i64,
    pub target_id: i64,
    pub source_type: String,
    pub target_type: String,
    pub relationship: String,
    pub file_path: String,
    pub line_number: i64,
    pub confidence: f64,
}

pub struct ExternalDep {
    pub file_path: String,
    pub import_statement: String,
    pub module_name: String,
    pub line_number: Option<i64>,
}

fn file_id(path: &str) -> i64 {
    (crc32fast::hash(path.as_bytes()) & 0x7FFFFFFF) as i64
}

fn resolve_python(
    source_path: &str,
    module_name: &str,
    all_files: &HashMap<String, String>,
) -> Option<String> {
    if module_name.is_empty() {
        return None;
    }
    let base = if module_name.starts_with('.') {
        let levels = module_name.chars().take_while(|&c| c == '.').count();
        let suffix = &module_name[levels..];
        let source_dir = Path::new(source_path).parent().unwrap_or(Path::new(""));
        let mut base_dir = source_dir.to_path_buf();
        for _ in 0..levels.saturating_sub(1) {
            base_dir = base_dir.parent().unwrap_or(Path::new("")).to_path_buf();
        }
        if !suffix.is_empty() {
            base_dir
                .join(suffix.replace('.', "/"))
                .to_string_lossy()
                .replace('\\', "/")
        } else {
            base_dir.to_string_lossy().replace('\\', "/")
        }
    } else {
        module_name.replace('.', "/")
    };
    let candidates = [format!("{base}.py"), format!("{base}/__init__.py")];
    candidates.into_iter().find(|c| all_files.contains_key(c))
}

fn resolve_java(module_name: &str, all_files: &HashMap<String, String>) -> Option<String> {
    if let Some(stripped) = module_name.strip_suffix(".*") {
        let package_prefix = stripped.replace('.', "/");
        let mut candidates: Vec<String> = all_files
            .keys()
            .filter(|p| p.starts_with(&format!("{package_prefix}/")) && p.ends_with(".java"))
            .cloned()
            .collect();
        candidates.sort();
        return candidates.into_iter().next();
    }
    let candidate = format!("{}.java", module_name.replace('.', "/"));
    if all_files.contains_key(&candidate) {
        Some(candidate)
    } else {
        None
    }
}

fn resolve_typescript(
    source_path: &str,
    module_name: &str,
    all_files: &HashMap<String, String>,
) -> Option<String> {
    if !module_name.starts_with('.') {
        return None;
    }
    let source_dir = Path::new(source_path).parent().unwrap_or(Path::new(""));
    let joined = source_dir.join(module_name);
    let resolved_base = normalize_posix_path(&joined.to_string_lossy().replace('\\', "/"));

    let candidates = [
        resolved_base.clone(),
        format!("{resolved_base}.ts"),
        format!("{resolved_base}.tsx"),
        format!("{resolved_base}.js"),
        format!("{resolved_base}.jsx"),
        format!("{resolved_base}/index.ts"),
        format!("{resolved_base}/index.tsx"),
        format!("{resolved_base}/index.js"),
        format!("{resolved_base}/index.jsx"),
    ];
    for candidate in &candidates {
        let normalized = normalize_posix_path(candidate);
        if all_files.contains_key(&normalized) {
            return Some(normalized);
        }
    }
    None
}

fn resolve_go(
    repo_root: &str,
    source_path: &str,
    module_name: &str,
    all_files: &HashMap<String, String>,
) -> Option<String> {
    if module_name.starts_with('.') {
        let source_dir = Path::new(source_path).parent().unwrap_or(Path::new(""));
        let normalized = normalize_posix_path(
            &source_dir
                .join(module_name)
                .to_string_lossy()
                .replace('\\', "/"),
        );
        let mut candidates: Vec<String> = all_files
            .keys()
            .filter(|p| p.starts_with(&format!("{normalized}/")) && p.ends_with(".go"))
            .cloned()
            .collect();
        candidates.sort();
        return candidates.into_iter().next();
    }

    let root_module = read_go_module(repo_root)?;
    if !module_name.starts_with(&root_module) {
        return None;
    }
    let rel_pkg = module_name[root_module.len()..].trim_start_matches('/');
    let prefix = if rel_pkg.is_empty() {
        String::new()
    } else {
        format!("{rel_pkg}/")
    };
    let mut candidates: Vec<String> = all_files
        .keys()
        .filter(|p| p.starts_with(&prefix) && p.ends_with(".go"))
        .cloned()
        .collect();
    candidates.sort();
    candidates.into_iter().next()
}

fn read_go_module(repo_root: &str) -> Option<String> {
    let go_mod = Path::new(repo_root).join("go.mod");
    let content = std::fs::read_to_string(go_mod).ok()?;
    for line in content.lines() {
        let stripped = line.trim();
        if let Some(mod_name) = stripped.strip_prefix("module ") {
            return Some(mod_name.trim().to_string());
        }
    }
    None
}

fn normalize_posix_path(path: &str) -> String {
    let parts: Vec<&str> = path.split('/').collect();
    let mut stack: Vec<&str> = Vec::new();
    for part in parts {
        match part {
            "" | "." => {}
            ".." => {
                stack.pop();
            }
            _ => stack.push(part),
        }
    }
    stack.join("/")
}

pub fn resolve_imports(
    repo_root: &str,
    source_path: &str,
    language: &str,
    imports: &[ExtractedImport],
    all_files: &HashMap<String, String>,
    file_id_lookup: Option<&HashMap<String, i64>>,
) -> (Vec<ImportEdge>, Vec<ExternalDep>) {
    let mut edges = Vec::new();
    let mut external = Vec::new();
    let source_id = file_id_lookup
        .and_then(|m| m.get(source_path))
        .copied()
        .unwrap_or_else(|| file_id(source_path));

    for import in imports {
        let module_name = &import.module_name;
        let resolved_path = match language {
            "python" => resolve_python(source_path, module_name, all_files),
            "java" => resolve_java(module_name, all_files),
            "typescript" => resolve_typescript(source_path, module_name, all_files),
            "go" => resolve_go(repo_root, source_path, module_name, all_files),
            _ => None,
        };

        match resolved_path {
            None => {
                external.push(ExternalDep {
                    file_path: source_path.to_string(),
                    import_statement: import.import_statement.clone(),
                    module_name: module_name.clone(),
                    line_number: Some(import.line_number),
                });
            }
            Some(resolved) => {
                let target_id = file_id_lookup
                    .and_then(|m| m.get(&resolved))
                    .copied()
                    .unwrap_or_else(|| file_id(&resolved));
                edges.push(ImportEdge {
                    source_id,
                    target_id,
                    source_type: "file".to_string(),
                    target_type: "file".to_string(),
                    relationship: "IMPORTS".to_string(),
                    file_path: source_path.to_string(),
                    line_number: import.line_number,
                    confidence: 1.0,
                });
            }
        }
    }

    (edges, external)
}
