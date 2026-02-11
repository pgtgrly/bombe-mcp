//! Error types for the Bombe core library.

use pyo3::exceptions::{PyIOError, PyRuntimeError, PyValueError};
use pyo3::PyErr;

/// Top-level error enum for the Bombe core library.
#[derive(Debug, thiserror::Error)]
pub enum BombeError {
    #[error("Database error: {0}")]
    Database(String),

    #[error("Index error: {0}")]
    Index(String),

    #[error("Query error: {0}")]
    Query(String),

    #[error("Parse error: {0}")]
    Parse(String),

    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),

    #[error("SQLite error: {0}")]
    Sqlite(#[from] rusqlite::Error),

    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),
}

impl From<BombeError> for PyErr {
    fn from(err: BombeError) -> PyErr {
        match &err {
            BombeError::Database(_) | BombeError::Sqlite(_) => {
                PyRuntimeError::new_err(err.to_string())
            }
            BombeError::Index(_) => PyRuntimeError::new_err(err.to_string()),
            BombeError::Query(_) => PyValueError::new_err(err.to_string()),
            BombeError::Parse(_) => PyValueError::new_err(err.to_string()),
            BombeError::Io(_) => PyIOError::new_err(err.to_string()),
            BombeError::Json(_) => PyValueError::new_err(err.to_string()),
        }
    }
}

pub type BombeResult<T> = Result<T, BombeError>;
