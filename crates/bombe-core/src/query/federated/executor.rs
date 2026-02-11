//! Federated query executor for cross-repo shard groups.

use std::time::Instant;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

#[pyclass]
pub struct FederatedQueryExecutor {
    #[allow(dead_code)]
    catalog: PyObject,
    router: PyObject,
    planner: PyObject,
}

#[pymethods]
impl FederatedQueryExecutor {
    #[new]
    fn new(catalog: PyObject, router: PyObject, planner: PyObject) -> Self {
        Self {
            catalog,
            router,
            planner,
        }
    }

    #[pyo3(signature = (query, kind, file_pattern=None, limit=20))]
    fn execute_search(
        &self,
        py: Python<'_>,
        query: &str,
        kind: &str,
        file_pattern: Option<&str>,
        limit: i64,
    ) -> PyResult<PyObject> {
        let started = Instant::now();
        let plan = self
            .planner
            .call_method1(py, "plan_search", (query, kind, limit))?;
        let shard_ids: Vec<String> = plan.getattr(py, "shard_ids")?.extract(py)?;

        let mut all_results: Vec<PyObject> = Vec::new();
        let mut shard_reports: Vec<PyObject> = Vec::new();
        let mut shards_failed = 0i64;

        for shard_id in &shard_ids {
            let shard_started = Instant::now();
            let report = PyDict::new(py);
            report.set_item("shard_id", shard_id)?;

            match self.execute_on_shard(py, shard_id, |py, db| {
                let search_mod = py.import("bombe.query.search")?;
                let models_mod = py.import("bombe.models")?;
                let req = models_mod.getattr("SymbolSearchRequest")?.call1((
                    query,
                    kind,
                    file_pattern,
                    limit,
                ))?;
                let response = search_mod.call_method1("search_symbols", (db, req))?;
                Ok(response.into())
            }) {
                Ok(result) => {
                    report.set_item("status", "ok")?;
                    report.set_item("latency_ms", shard_started.elapsed().as_millis() as i64)?;
                    if let Ok(symbols) = result.getattr(py, "symbols") {
                        if let Ok(list) = symbols.downcast_bound::<PyList>(py) {
                            for item in list.iter() {
                                all_results.push(item.into());
                            }
                        }
                    }
                }
                Err(e) => {
                    report.set_item("status", "error")?;
                    report.set_item("error", e.to_string())?;
                    report.set_item("latency_ms", shard_started.elapsed().as_millis() as i64)?;
                    shards_failed += 1;
                }
            }
            shard_reports.push(report.into());
        }

        let elapsed_ms = started.elapsed().as_millis() as i64;
        let total_matches = all_results.len() as i64;

        let result = PyDict::new(py);
        let results_list = PyList::new(py, &all_results)?;
        result.set_item("results", results_list)?;
        result.set_item("shard_reports", PyList::new(py, &shard_reports)?)?;
        result.set_item("total_matches", total_matches)?;
        result.set_item("shards_queried", shard_ids.len() as i64)?;
        result.set_item("shards_failed", shards_failed)?;
        result.set_item("elapsed_ms", elapsed_ms)?;

        Ok(result.into())
    }

    fn execute_references(
        &self,
        py: Python<'_>,
        symbol_name: &str,
        direction: &str,
        depth: i64,
        include_source: bool,
    ) -> PyResult<PyObject> {
        let started = Instant::now();
        let plan =
            self.planner
                .call_method1(py, "plan_references", (symbol_name, direction, depth))?;
        let shard_ids: Vec<String> = plan.getattr(py, "shard_ids")?.extract(py)?;

        let mut all_results: Vec<PyObject> = Vec::new();
        let mut shard_reports: Vec<PyObject> = Vec::new();
        let mut shards_failed = 0i64;

        for shard_id in &shard_ids {
            let shard_started = Instant::now();
            let report = PyDict::new(py);
            report.set_item("shard_id", shard_id)?;

            match self.execute_on_shard(py, shard_id, |py, db| {
                let refs_mod = py.import("bombe.query.references")?;
                let models_mod = py.import("bombe.models")?;
                let req = models_mod.getattr("ReferenceRequest")?.call1((
                    symbol_name,
                    direction,
                    depth,
                    include_source,
                ))?;
                let response = refs_mod.call_method1("get_references", (db, req))?;
                Ok(response.into())
            }) {
                Ok(result) => {
                    report.set_item("status", "ok")?;
                    report.set_item("latency_ms", shard_started.elapsed().as_millis() as i64)?;
                    all_results.push(result);
                }
                Err(_) => {
                    report.set_item("status", "error")?;
                    report.set_item("latency_ms", shard_started.elapsed().as_millis() as i64)?;
                    shards_failed += 1;
                }
            }
            shard_reports.push(report.into());
        }

        let elapsed_ms = started.elapsed().as_millis() as i64;

        let result = PyDict::new(py);
        result.set_item("results", PyList::new(py, &all_results)?)?;
        result.set_item("shard_reports", PyList::new(py, &shard_reports)?)?;
        result.set_item("total_matches", all_results.len() as i64)?;
        result.set_item("shards_queried", shard_ids.len() as i64)?;
        result.set_item("shards_failed", shards_failed)?;
        result.set_item("elapsed_ms", elapsed_ms)?;

        Ok(result.into())
    }

    fn execute_blast_radius(
        &self,
        py: Python<'_>,
        symbol_name: &str,
        change_type: &str,
        max_depth: i64,
    ) -> PyResult<PyObject> {
        let started = Instant::now();
        let plan = self
            .planner
            .call_method1(py, "plan_blast_radius", (symbol_name, max_depth))?;
        let shard_ids: Vec<String> = plan.getattr(py, "shard_ids")?.extract(py)?;

        let mut all_results: Vec<PyObject> = Vec::new();
        let mut shard_reports: Vec<PyObject> = Vec::new();
        let mut shards_failed = 0i64;

        for shard_id in &shard_ids {
            let shard_started = Instant::now();
            let report = PyDict::new(py);
            report.set_item("shard_id", shard_id)?;

            match self.execute_on_shard(py, shard_id, |py, db| {
                let blast_mod = py.import("bombe.query.blast")?;
                let models_mod = py.import("bombe.models")?;
                let req = models_mod.getattr("BlastRadiusRequest")?.call1((
                    symbol_name,
                    change_type,
                    max_depth,
                ))?;
                let response = blast_mod.call_method1("get_blast_radius", (db, req))?;
                Ok(response.into())
            }) {
                Ok(result) => {
                    report.set_item("status", "ok")?;
                    report.set_item("latency_ms", shard_started.elapsed().as_millis() as i64)?;
                    all_results.push(result);
                }
                Err(_) => {
                    report.set_item("status", "error")?;
                    report.set_item("latency_ms", shard_started.elapsed().as_millis() as i64)?;
                    shards_failed += 1;
                }
            }
            shard_reports.push(report.into());
        }

        let elapsed_ms = started.elapsed().as_millis() as i64;

        let result = PyDict::new(py);
        result.set_item("results", PyList::new(py, &all_results)?)?;
        result.set_item("shard_reports", PyList::new(py, &shard_reports)?)?;
        result.set_item("total_matches", all_results.len() as i64)?;
        result.set_item("shards_queried", shard_ids.len() as i64)?;
        result.set_item("shards_failed", shards_failed)?;
        result.set_item("elapsed_ms", elapsed_ms)?;

        Ok(result.into())
    }
}

impl FederatedQueryExecutor {
    fn execute_on_shard<F>(
        &self,
        py: Python<'_>,
        shard_id: &str,
        operation: F,
    ) -> PyResult<PyObject>
    where
        F: FnOnce(Python<'_>, &Bound<'_, PyAny>) -> PyResult<PyObject>,
    {
        let db = self.router.call_method1(py, "get_shard_db", (shard_id,))?;
        if db.is_none(py) {
            return Err(pyo3::exceptions::PyRuntimeError::new_err(
                "shard database not accessible",
            ));
        }
        operation(py, db.bind(py))
    }
}
