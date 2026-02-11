//! Query planner with lightweight in-memory response caching.

use std::collections::HashMap;
use std::time::Instant;

use indexmap::IndexMap;
use parking_lot::Mutex;
use pyo3::prelude::*;
use pyo3::types::PyDict;

struct CacheEntry {
    value: PyObject,
    expires_at: Instant,
}

#[pyclass]
pub struct QueryPlanner {
    max_entries: usize,
    ttl_seconds: f64,
    cache: Mutex<IndexMap<String, CacheEntry>>,
}

#[pymethods]
impl QueryPlanner {
    #[new]
    #[pyo3(signature = (max_entries=512, ttl_seconds=15.0))]
    fn new(max_entries: i64, ttl_seconds: f64) -> Self {
        Self {
            max_entries: max_entries.max(1) as usize,
            ttl_seconds: ttl_seconds.max(0.1),
            cache: Mutex::new(IndexMap::new()),
        }
    }

    #[pyo3(signature = (tool_name, payload, version_token=None))]
    fn _cache_key(
        &self,
        tool_name: &str,
        payload: &Bound<'_, PyDict>,
        version_token: Option<&str>,
    ) -> PyResult<String> {
        let py = payload.py();
        let json_module = py.import("json")?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("sort_keys", true)?;
        let sep = pyo3::types::PyTuple::new(py, [",", ":"])?;
        kwargs.set_item("separators", sep)?;
        let normalized = json_module.call_method("dumps", (payload,), Some(&kwargs))?;
        let normalized_str: String = normalized.extract()?;
        let suffix = version_token.unwrap_or("default");
        Ok(format!("{tool_name}:{suffix}:{normalized_str}"))
    }

    fn _evict_expired(&self) {
        let mut cache = self.cache.lock();
        let now = Instant::now();
        let expired_keys: Vec<String> = cache
            .iter()
            .filter(|(_, entry)| entry.expires_at <= now)
            .map(|(key, _)| key.clone())
            .collect();
        for key in expired_keys {
            cache.shift_remove(&key);
        }
    }

    fn _evict_over_capacity(&self) {
        let mut cache = self.cache.lock();
        while cache.len() > self.max_entries {
            cache.shift_remove_index(0);
        }
    }

    #[pyo3(signature = (tool_name, payload, compute, version_token=None))]
    fn get_or_compute(
        &self,
        py: Python<'_>,
        tool_name: &str,
        payload: &Bound<'_, PyDict>,
        compute: &Bound<'_, PyAny>,
        version_token: Option<&str>,
    ) -> PyResult<(PyObject, String)> {
        let (result, mode, _) =
            self.get_or_compute_with_trace(py, tool_name, payload, compute, version_token)?;
        Ok((result, mode))
    }

    #[pyo3(signature = (tool_name, payload, compute, version_token=None))]
    fn get_or_compute_with_trace(
        &self,
        py: Python<'_>,
        tool_name: &str,
        payload: &Bound<'_, PyDict>,
        compute: &Bound<'_, PyAny>,
        version_token: Option<&str>,
    ) -> PyResult<(PyObject, String, PyObject)> {
        let cache_key = self._cache_key(tool_name, payload, version_token)?;
        let lookup_started = Instant::now();

        // Check cache
        self._evict_expired();
        {
            let mut cache = self.cache.lock();
            if let Some(entry) = cache.get(&cache_key) {
                if entry.expires_at > Instant::now() {
                    let value = entry.value.clone_ref(py);
                    // Move to end for LRU
                    let entry = cache.shift_remove(&cache_key).unwrap();
                    cache.insert(cache_key, entry);

                    let lookup_ms = lookup_started.elapsed().as_secs_f64() * 1000.0;
                    let trace = PyDict::new(py);
                    trace.set_item("lookup_ms", (lookup_ms * 1000.0).round() / 1000.0)?;
                    trace.set_item("compute_ms", 0.0)?;
                    trace.set_item("total_ms", (lookup_ms * 1000.0).round() / 1000.0)?;
                    trace.set_item("version_token", version_token.unwrap_or("default"))?;
                    return Ok((value, "cache_hit".to_string(), trace.into()));
                }
            }
        }

        // Compute
        let compute_started = Instant::now();
        let result = compute.call0()?;
        let compute_ms = compute_started.elapsed().as_secs_f64() * 1000.0;

        let expires_at = Instant::now() + std::time::Duration::from_secs_f64(self.ttl_seconds);
        {
            let mut cache = self.cache.lock();
            cache.insert(
                cache_key,
                CacheEntry {
                    value: result.clone().unbind(),
                    expires_at,
                },
            );
        }
        self._evict_over_capacity();

        let total_ms = lookup_started.elapsed().as_secs_f64() * 1000.0;
        let lookup_ms = (total_ms - compute_ms).max(0.0);
        let trace = PyDict::new(py);
        trace.set_item("lookup_ms", (lookup_ms * 1000.0).round() / 1000.0)?;
        trace.set_item("compute_ms", (compute_ms * 1000.0).round() / 1000.0)?;
        trace.set_item("total_ms", (total_ms * 1000.0).round() / 1000.0)?;
        trace.set_item("version_token", version_token.unwrap_or("default"))?;

        Ok((result.unbind(), "cache_miss".to_string(), trace.into()))
    }

    fn stats(&self) -> HashMap<String, i64> {
        let cache = self.cache.lock();
        let mut result = HashMap::new();
        result.insert("entries".to_string(), cache.len() as i64);
        result.insert("max_entries".to_string(), self.max_entries as i64);
        result
    }
}
