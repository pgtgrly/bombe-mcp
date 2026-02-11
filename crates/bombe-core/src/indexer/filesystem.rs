//! Filesystem scanning helpers for indexing passes.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use pyo3::prelude::*;
use sha2::{Digest, Sha256};

const LANGUAGE_BY_EXTENSION: &[(&str, &str)] = &[
    (".py", "python"),
    (".java", "java"),
    (".ts", "typescript"),
    (".tsx", "typescript"),
    (".go", "go"),
];

const DEFAULT_SENSITIVE_EXCLUDE_PATTERNS: &[&str] = &[
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*secret*",
    "*secrets*",
    "*credential*",
    "id_rsa",
    "id_dsa",
];

const IMPLICIT_IGNORED_DIRS: &[&str] = &[".git", ".bombe"];

struct IgnoreRule {
    pattern: String,
    directory_only: bool,
}

fn load_ignore_file(path: &Path) -> Vec<IgnoreRule> {
    if !path.exists() {
        return vec![];
    }
    let content = match std::fs::read_to_string(path) {
        Ok(c) => c,
        Err(_) => return vec![],
    };
    content
        .lines()
        .filter_map(|line| {
            let stripped = line.trim();
            if stripped.is_empty() || stripped.starts_with('#') {
                return None;
            }
            let directory_only = stripped.ends_with('/');
            let mut pattern = if directory_only {
                stripped[..stripped.len() - 1].to_string()
            } else {
                stripped.to_string()
            };
            if pattern.starts_with("./") {
                pattern = pattern[2..].to_string();
            }
            Some(IgnoreRule {
                pattern,
                directory_only,
            })
        })
        .collect()
}

fn matches_pattern(rel_path: &str, pattern: &str) -> bool {
    let normalized = rel_path.replace('\\', "/");
    // Simple glob matching
    glob_match(&normalized, pattern)
        || glob_match(
            Path::new(&normalized)
                .file_name()
                .map(|f| f.to_string_lossy().to_string())
                .unwrap_or_default()
                .as_str(),
            pattern,
        )
}

fn glob_match(text: &str, pattern: &str) -> bool {
    // Simple glob match supporting * and ?
    let t_chars: Vec<char> = text.chars().collect();
    let p_chars: Vec<char> = pattern.chars().collect();
    let (tl, pl) = (t_chars.len(), p_chars.len());
    let mut dp = vec![vec![false; pl + 1]; tl + 1];
    dp[0][0] = true;
    for j in 1..=pl {
        if p_chars[j - 1] == '*' {
            dp[0][j] = dp[0][j - 1];
        }
    }
    for i in 1..=tl {
        for j in 1..=pl {
            if p_chars[j - 1] == '*' {
                dp[i][j] = dp[i][j - 1] || dp[i - 1][j];
            } else if p_chars[j - 1] == '?' || t_chars[i - 1] == p_chars[j - 1] {
                dp[i][j] = dp[i - 1][j - 1];
            }
        }
    }
    dp[tl][pl]
}

fn is_ignored(rel_path: &str, is_dir: bool, rules: &[IgnoreRule]) -> bool {
    let normalized = rel_path.replace('\\', "/");
    for rule in rules {
        if rule.directory_only && !is_dir {
            continue;
        }
        if matches_pattern(&normalized, &rule.pattern) {
            return true;
        }
        if normalized.starts_with(&format!("{}/", rule.pattern)) {
            return true;
        }
    }
    false
}

fn matches_any_include(rel_path: &str, include_patterns: &[String]) -> bool {
    if include_patterns.is_empty() {
        return true;
    }
    include_patterns
        .iter()
        .any(|p| matches_pattern(rel_path, p))
}

pub fn iter_repo_files(
    repo_root: &Path,
    include_patterns: Option<&[String]>,
    exclude_patterns: Option<&[String]>,
) -> Vec<PathBuf> {
    let mut rules: Vec<IgnoreRule> = Vec::new();
    rules.extend(load_ignore_file(&repo_root.join(".gitignore")));
    rules.extend(load_ignore_file(&repo_root.join(".bombeignore")));

    let exclude_sensitive = match std::env::var("BOMBE_EXCLUDE_SENSITIVE") {
        Ok(val) => {
            let v = val.trim().to_lowercase();
            !matches!(v.as_str(), "0" | "false" | "no" | "off")
        }
        Err(_) => true,
    };
    if exclude_sensitive {
        for pattern in DEFAULT_SENSITIVE_EXCLUDE_PATTERNS {
            rules.push(IgnoreRule {
                pattern: pattern.to_string(),
                directory_only: false,
            });
        }
    }

    let include: Vec<String> = include_patterns
        .unwrap_or(&[])
        .iter()
        .filter(|p| !p.trim().is_empty())
        .cloned()
        .collect();

    if let Some(excludes) = exclude_patterns {
        for pattern in excludes {
            let stripped = pattern.trim();
            if stripped.is_empty() {
                continue;
            }
            let directory_only = stripped.ends_with('/');
            let mut p = if directory_only {
                stripped[..stripped.len() - 1].to_string()
            } else {
                stripped.to_string()
            };
            if p.starts_with("./") {
                p = p[2..].to_string();
            }
            rules.push(IgnoreRule {
                pattern: p,
                directory_only,
            });
        }
    }

    let implicit_ignored: HashSet<&str> = IMPLICIT_IGNORED_DIRS.iter().copied().collect();
    let mut result = Vec::new();

    fn walk_dir(
        dir: &Path,
        repo_root: &Path,
        rules: &[IgnoreRule],
        include: &[String],
        implicit_ignored: &HashSet<&str>,
        result: &mut Vec<PathBuf>,
    ) {
        let entries = match std::fs::read_dir(dir) {
            Ok(e) => e,
            Err(_) => return,
        };

        let mut dirs = Vec::new();
        let mut files = Vec::new();

        for entry in entries.flatten() {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().to_string();

            if path.is_dir() {
                if implicit_ignored.contains(name.as_str()) {
                    continue;
                }
                let rel = path
                    .strip_prefix(repo_root)
                    .unwrap_or(&path)
                    .to_string_lossy()
                    .replace('\\', "/");
                if is_ignored(&rel, true, rules) {
                    continue;
                }
                dirs.push(path);
            } else {
                files.push(path);
            }
        }

        for file_path in files {
            let rel = file_path
                .strip_prefix(repo_root)
                .unwrap_or(&file_path)
                .to_string_lossy()
                .replace('\\', "/");
            if is_ignored(&rel, false, rules) {
                continue;
            }
            if !matches_any_include(&rel, include) {
                continue;
            }
            result.push(file_path);
        }

        for dir_path in dirs {
            walk_dir(
                dir_path.as_path(),
                repo_root,
                rules,
                include,
                implicit_ignored,
                result,
            );
        }
    }

    walk_dir(
        repo_root,
        repo_root,
        &rules,
        &include,
        &implicit_ignored,
        &mut result,
    );
    result
}

#[pyfunction]
pub fn detect_language(path: &str) -> Option<String> {
    let path = Path::new(path);
    let ext = path
        .extension()
        .map(|e| format!(".{}", e.to_string_lossy().to_lowercase()))?;
    LANGUAGE_BY_EXTENSION
        .iter()
        .find(|(e, _)| *e == ext.as_str())
        .map(|(_, lang)| lang.to_string())
}

#[pyfunction]
pub fn compute_content_hash(path: &str) -> PyResult<String> {
    let mut hasher = Sha256::new();
    let data =
        std::fs::read(path).map_err(|e| pyo3::exceptions::PyIOError::new_err(e.to_string()))?;
    hasher.update(&data);
    Ok(format!("{:x}", hasher.finalize()))
}
