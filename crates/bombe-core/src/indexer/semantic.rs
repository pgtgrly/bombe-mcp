//! Optional semantic hint integrations for call resolution.

use std::collections::{HashMap, HashSet};
use std::path::Path;

/// Load receiver type hints from sidecar JSON files and environment.
pub fn load_receiver_type_hints(
    repo_root: &Path,
    relative_path: &str,
) -> HashMap<(i64, String), HashSet<String>> {
    let mut hints: HashMap<(i64, String), HashSet<String>> = HashMap::new();
    let normalized = normalize_relative_path(relative_path);

    // Load sidecar hints
    let sidecar = repo_root
        .join(".bombe")
        .join("semantic")
        .join(format!("{normalized}.hints.json"));
    if let Some(payload) = load_json(&sidecar) {
        merge_hint_maps(&mut hints, &parse_hint_payload(&payload));
    }

    // Load global hints file
    if let Ok(global_path) = std::env::var("BOMBE_SEMANTIC_HINTS_FILE") {
        let global_path = global_path.trim().to_string();
        if !global_path.is_empty() {
            let expanded = Path::new(&global_path);
            if let Some(payload) = load_json(expanded) {
                if let Some(files) = payload.get("files").and_then(|v| v.as_object()) {
                    let candidates = [
                        normalized.clone(),
                        relative_path.to_string(),
                        relative_path
                            .replace('\\', "/")
                            .trim_start_matches('/')
                            .to_string(),
                    ];
                    for candidate in &candidates {
                        if let Some(file_payload) = files.get(candidate) {
                            if let Some(obj) = file_payload.as_object() {
                                let val = serde_json::Value::Object(obj.clone());
                                merge_hint_maps(&mut hints, &parse_hint_payload(&val));
                            }
                        }
                    }
                }
            }
        }
    }

    hints
}

fn normalize_relative_path(path: &str) -> String {
    path.trim().trim_start_matches('/').replace('\\', "/")
}

fn load_json(path: &Path) -> Option<serde_json::Value> {
    if !path.exists() {
        return None;
    }
    let content = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&content).ok()
}

fn parse_hint_payload(payload: &serde_json::Value) -> HashMap<(i64, String), HashSet<String>> {
    let mut hints: HashMap<(i64, String), HashSet<String>> = HashMap::new();
    let entries = match payload.get("receiver_hints").and_then(|v| v.as_array()) {
        Some(arr) => arr,
        None => return hints,
    };
    for item in entries {
        let obj = match item.as_object() {
            Some(o) => o,
            None => continue,
        };
        let receiver = obj
            .get("receiver")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        let owner_type = obj
            .get("owner_type")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        if receiver.is_empty() || owner_type.is_empty() {
            continue;
        }
        let line = obj.get("line").and_then(|v| v.as_i64());
        let line_start = obj
            .get("line_start")
            .and_then(|v| v.as_i64())
            .or(line)
            .unwrap_or(1);
        let line_end = obj
            .get("line_end")
            .and_then(|v| v.as_i64())
            .unwrap_or(line_start);
        let start = line_start.max(1);
        let end = line_end.max(start);
        for line_num in start..=(end.min(start + 512)) {
            hints
                .entry((line_num, receiver.clone()))
                .or_default()
                .insert(owner_type.clone());
        }
    }
    hints
}

fn merge_hint_maps(
    target: &mut HashMap<(i64, String), HashSet<String>>,
    source: &HashMap<(i64, String), HashSet<String>>,
) {
    for (key, values) in source {
        target
            .entry(key.clone())
            .or_default()
            .extend(values.iter().cloned());
    }
}
