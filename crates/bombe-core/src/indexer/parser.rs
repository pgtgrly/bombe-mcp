//! Language parsing wrapper used by extraction passes.
//!
//! In the Rust port, we use native tree-sitter crates instead of
//! Python tree_sitter_languages. Python AST parsing is delegated
//! back to Python via PyO3 for the Python language.

use std::path::Path;

use pyo3::prelude::*;

const SUPPORTED_LANGUAGES: &[&str] = &["python", "java", "typescript", "go"];

/// Parsed source unit — mirrors the Python ParsedUnit but holds raw source.
/// Tree-sitter tree objects stay in Rust; for Python files, parsing
/// is done via Python's ast module through PyO3.
pub struct RustParsedUnit {
    pub path: String,
    pub language: String,
    pub source: String,
    /// For non-Python languages: native tree-sitter tree.
    pub tree: Option<tree_sitter::Tree>,
}

pub fn parse_file_native(path: &Path, language: &str) -> Result<RustParsedUnit, String> {
    if !SUPPORTED_LANGUAGES.contains(&language) {
        return Err(format!("Unsupported language: {language}"));
    }

    let source = std::fs::read_to_string(path)
        .map_err(|e| format!("Failed to read {}: {e}", path.display()))?;

    if language == "python" {
        // Python parsing is handled by the Python side via ast.parse
        return Ok(RustParsedUnit {
            path: path.to_string_lossy().to_string(),
            language: language.to_string(),
            source,
            tree: None,
        });
    }

    let ts_language = match language {
        "java" => tree_sitter_java::LANGUAGE,
        "typescript" => tree_sitter_typescript::LANGUAGE_TYPESCRIPT,
        "go" => tree_sitter_go::LANGUAGE,
        _ => return Err(format!("No tree-sitter grammar for: {language}")),
    };

    let mut parser = tree_sitter::Parser::new();
    parser
        .set_language(&ts_language.into())
        .map_err(|e| format!("Failed to set language: {e}"))?;

    let tree = parser
        .parse(source.as_bytes(), None)
        .ok_or_else(|| format!("Failed to parse {}", path.display()))?;

    Ok(RustParsedUnit {
        path: path.to_string_lossy().to_string(),
        language: language.to_string(),
        source,
        tree: Some(tree),
    })
}

#[pyfunction]
pub fn tree_sitter_capability_report(py: Python<'_>) -> PyResult<PyObject> {
    let required = vec!["python", "java", "typescript", "go"];

    let languages = pyo3::types::PyList::empty(py);
    let mut all_available = true;

    for lang in &required {
        let available = match *lang {
            "java" | "typescript" | "go" => true,
            "python" => true, // handled via Python ast
            _ => {
                all_available = false;
                false
            }
        };
        let entry = pyo3::types::PyDict::new(py);
        entry.set_item("language", *lang)?;
        entry.set_item("backend", *lang)?;
        entry.set_item("available", available)?;
        entry.set_item(
            "reason",
            if available {
                "ok"
            } else {
                "parser_unavailable"
            },
        )?;
        languages.append(entry)?;
    }

    let versions = pyo3::types::PyDict::new(py);
    versions.set_item("tree-sitter", "0.24")?;
    versions.set_item("tree-sitter-languages", "native-rust")?;

    let required_py = pyo3::types::PyList::new(py, ["python", "java", "typescript", "go"])?;

    let result = pyo3::types::PyDict::new(py);
    result.set_item("module_available", true)?;
    result.set_item("all_required_available", all_available)?;
    result.set_item("required_languages", required_py)?;
    result.set_item("versions", versions)?;
    result.set_item("languages", languages)?;

    Ok(result.into_any().unbind())
}
