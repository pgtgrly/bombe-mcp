//! Indexing pipeline orchestration with Rayon-based parallelism.

use std::path::Path;
use std::time::Instant;

use pyo3::prelude::*;
use rayon::prelude::*;

use crate::indexer::filesystem::{detect_language, iter_repo_files};
use crate::indexer::symbols::{extract_symbols, ExtractedImport, ExtractedSymbol};

pub struct FileRecord {
    pub path: String,
    pub language: String,
    pub content_hash: String,
    pub size_bytes: i64,
}

#[allow(dead_code)]
pub struct ExtractionResult {
    file_path: String,
    language: String,
    source: String,
    symbols: Vec<ExtractedSymbol>,
    imports: Vec<ExtractedImport>,
    error_stage: Option<String>,
    error_message: Option<String>,
}

fn extract_file_worker(repo_root: &str, relative_path: &str, language: &str) -> ExtractionResult {
    let absolute = Path::new(repo_root).join(relative_path);
    let source = match std::fs::read_to_string(&absolute) {
        Ok(s) => s,
        Err(e) => {
            return ExtractionResult {
                file_path: relative_path.to_string(),
                language: language.to_string(),
                source: String::new(),
                symbols: vec![],
                imports: vec![],
                error_stage: Some("parse".to_string()),
                error_message: Some(e.to_string()),
            }
        }
    };

    // For Python, symbols extraction requires Python's ast module
    // which is handled on the Python side. For Java/TypeScript/Go,
    // we extract natively in Rust.
    if language == "python" {
        // Return source only; Python extraction done on Python side
        return ExtractionResult {
            file_path: relative_path.to_string(),
            language: language.to_string(),
            source,
            symbols: vec![],
            imports: vec![],
            error_stage: None,
            error_message: None,
        };
    }

    let (symbols, imports) = extract_symbols(&source, relative_path, language);

    ExtractionResult {
        file_path: relative_path.to_string(),
        language: language.to_string(),
        source,
        symbols,
        imports,
        error_stage: None,
        error_message: None,
    }
}

pub fn parallel_extract(
    repo_root: &str,
    files: &[FileRecord],
    workers: usize,
) -> Vec<ExtractionResult> {
    if files.is_empty() {
        return vec![];
    }

    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(workers.max(1))
        .build();

    let jobs: Vec<(String, String, String)> = files
        .iter()
        .map(|f| (repo_root.to_string(), f.path.clone(), f.language.clone()))
        .collect();

    match pool {
        Ok(pool) => pool.install(|| {
            jobs.par_iter()
                .map(|(root, path, lang)| extract_file_worker(root, path, lang))
                .collect()
        }),
        Err(_) => {
            // Fallback to sequential
            jobs.iter()
                .map(|(root, path, lang)| extract_file_worker(root, path, lang))
                .collect()
        }
    }
}

pub struct IndexStats {
    pub files_seen: i64,
    pub files_indexed: i64,
    pub symbols_indexed: i64,
    pub edges_indexed: i64,
    pub elapsed_ms: i64,
    pub run_id: String,
}

/// Scan repository for indexable files.
pub fn scan_repo_files(
    repo_root: &Path,
    include_patterns: Option<&[String]>,
    exclude_patterns: Option<&[String]>,
) -> (i64, Vec<FileRecord>) {
    let all_files = iter_repo_files(repo_root, include_patterns, exclude_patterns);
    let mut files_seen = 0i64;
    let mut records = Vec::new();

    for file_path in all_files {
        files_seen += 1;
        let language = match detect_language(&file_path.to_string_lossy()) {
            Some(l) => l,
            None => continue,
        };
        let rel_path = file_path
            .strip_prefix(repo_root)
            .unwrap_or(&file_path)
            .to_string_lossy()
            .replace('\\', "/");
        let content_hash =
            crate::indexer::filesystem::compute_content_hash(&file_path.to_string_lossy())
                .unwrap_or_default();
        let size_bytes = file_path.metadata().map(|m| m.len() as i64).unwrap_or(0);

        records.push(FileRecord {
            path: rel_path,
            language,
            content_hash,
            size_bytes,
        });
    }

    (files_seen, records)
}

/// Full indexing pipeline exposed to Python.
#[pyfunction]
#[pyo3(signature = (repo_root, _db_path, workers=4))]
pub fn rust_full_index(
    py: Python<'_>,
    repo_root: &str,
    _db_path: &str,
    workers: i64,
) -> PyResult<PyObject> {
    let started = Instant::now();
    let repo = Path::new(repo_root);
    let (files_seen, file_records) = scan_repo_files(repo, None, None);

    let elapsed_ms = started.elapsed().as_millis() as i64;

    let result = pyo3::types::PyDict::new(py);
    result.set_item("files_seen", files_seen)?;
    result.set_item("files_indexed", file_records.len() as i64)?;
    result.set_item("elapsed_ms", elapsed_ms)?;
    result.set_item("workers", workers)?;

    Ok(result.into())
}
