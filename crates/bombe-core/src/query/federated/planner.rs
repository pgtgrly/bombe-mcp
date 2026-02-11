//! Federated query planner for cross-repo shard groups.

use pyo3::prelude::*;

use crate::query::guards::{MAX_CROSS_REPO_EDGES_PER_QUERY, MAX_SHARDS_PER_QUERY};

#[pyclass(frozen)]
pub struct ShardQueryPlan {
    #[pyo3(get)]
    pub shard_ids: Vec<String>,
    #[pyo3(get)]
    pub cross_repo_edges: Vec<PyObject>,
    #[pyo3(get)]
    pub fan_out_strategy: String,
    #[pyo3(get)]
    pub merge_strategy: String,
}

#[pymethods]
impl ShardQueryPlan {
    #[new]
    #[pyo3(signature = (shard_ids=vec![], cross_repo_edges=vec![], fan_out_strategy="all".to_string(), merge_strategy="score_sort".to_string()))]
    fn new(
        shard_ids: Vec<String>,
        cross_repo_edges: Vec<PyObject>,
        fan_out_strategy: String,
        merge_strategy: String,
    ) -> Self {
        Self {
            shard_ids,
            cross_repo_edges,
            fan_out_strategy,
            merge_strategy,
        }
    }
}

#[pyclass]
pub struct FederatedQueryPlanner {
    catalog: PyObject,
    router: PyObject,
}

#[pymethods]
impl FederatedQueryPlanner {
    #[new]
    fn new(catalog: PyObject, router: PyObject) -> Self {
        Self { catalog, router }
    }

    #[pyo3(signature = (_query, _kind="any", _limit=20))]
    fn plan_search(
        &self,
        py: Python<'_>,
        _query: &str,
        _kind: &str,
        _limit: i64,
    ) -> PyResult<ShardQueryPlan> {
        let shard_ids: Vec<String> = self.router.call_method0(py, "all_shard_ids")?.extract(py)?;
        Ok(ShardQueryPlan {
            shard_ids,
            cross_repo_edges: vec![],
            fan_out_strategy: "all".to_string(),
            merge_strategy: "score_sort".to_string(),
        })
    }

    #[pyo3(signature = (symbol_name, direction, _depth, source_repo_id=None))]
    fn plan_references(
        &self,
        py: Python<'_>,
        symbol_name: &str,
        direction: &str,
        _depth: i64,
        source_repo_id: Option<&str>,
    ) -> PyResult<ShardQueryPlan> {
        let _args = match source_repo_id {
            Some(rid) => (symbol_name, rid),
            None => (symbol_name, ""),
        };
        let shard_ids: Vec<String> = if source_repo_id.is_some() {
            self.router
                .call_method1(py, "route_reference_query", (symbol_name, source_repo_id))?
                .extract(py)?
        } else {
            self.router
                .call_method1(py, "route_reference_query", (symbol_name,))?
                .extract::<Vec<String>>(py)
                .unwrap_or_default()
        };

        let mut cross_edges: Vec<PyObject> = Vec::new();
        for sid in &shard_ids {
            if direction == "callers" || direction == "both" {
                let edges: Vec<PyObject> = self
                    .catalog
                    .call_method1(py, "get_cross_repo_edges_to", (sid.as_str(), symbol_name))?
                    .extract(py)
                    .unwrap_or_default();
                cross_edges.extend(edges);
            }
            if direction == "callees" || direction == "both" {
                let edges: Vec<PyObject> = self
                    .catalog
                    .call_method1(py, "get_cross_repo_edges_from", (sid.as_str(), symbol_name))?
                    .extract(py)
                    .unwrap_or_default();
                cross_edges.extend(edges);
            }
        }

        // Deduplicate and cap
        cross_edges.truncate(MAX_CROSS_REPO_EDGES_PER_QUERY as usize);

        let mut all_shard_ids = shard_ids;
        all_shard_ids.truncate(MAX_SHARDS_PER_QUERY as usize);

        Ok(ShardQueryPlan {
            shard_ids: all_shard_ids,
            cross_repo_edges: cross_edges,
            fan_out_strategy: "routed".to_string(),
            merge_strategy: "depth_merge".to_string(),
        })
    }

    fn plan_blast_radius(
        &self,
        py: Python<'_>,
        symbol_name: &str,
        max_depth: i64,
    ) -> PyResult<ShardQueryPlan> {
        self.plan_references(py, symbol_name, "callers", max_depth, None)
    }

    fn plan_context(
        &self,
        py: Python<'_>,
        _query: &str,
        entry_points: Vec<String>,
    ) -> PyResult<ShardQueryPlan> {
        let mut shard_set: std::collections::HashSet<String> = std::collections::HashSet::new();
        let mut shard_ids: Vec<String> = Vec::new();

        for ep in &entry_points {
            let ids: Vec<String> = self
                .router
                .call_method1(py, "route_symbol_query", (ep.as_str(),))?
                .extract(py)
                .unwrap_or_default();
            for sid in ids {
                if shard_set.insert(sid.clone()) {
                    shard_ids.push(sid);
                }
            }
        }

        if shard_ids.is_empty() {
            shard_ids = self.router.call_method0(py, "all_shard_ids")?.extract(py)?;
        }

        shard_ids.truncate(MAX_SHARDS_PER_QUERY as usize);

        Ok(ShardQueryPlan {
            shard_ids,
            cross_repo_edges: vec![],
            fan_out_strategy: "routed".to_string(),
            merge_strategy: "score_sort".to_string(),
        })
    }
}
