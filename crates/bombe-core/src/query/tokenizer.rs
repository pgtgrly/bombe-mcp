//! Token estimation helpers.

use pyo3::prelude::*;

#[pyfunction]
#[pyo3(signature = (text, model=None))]
pub fn estimate_tokens(text: &str, model: Option<&str>) -> i64 {
    let _ = model; // Model parameter kept for API compat, always uses fallback
    if text.is_empty() {
        return 0;
    }
    (text.len() as f64 / 3.5).max(1.0) as i64
}
