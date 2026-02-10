/* --- Constants --- */
const GRAPH_PAGE_SIZE = 24;

/* --- State --- */
let currentBundle = null;
let graphPage = 0;

/* --- Data Loading --- */
async function loadBundle() {
  const response = await fetch("./bundle.json", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Unable to load bundle.json (${response.status})`);
  }
  return response.json();
}

/* --- Limits Warning (Large Graph Safeguard) --- */
function renderLimitsWarning(bundle) {
  const el = document.getElementById("limits-warning");
  const limits = bundle.limits || {};
  const nodesTotal = limits.nodes_total || 0;
  const edgesTotal = limits.edges_total || 0;
  const nodeLimit = limits.node_limit || 0;
  const edgeLimit = limits.edge_limit || 0;

  const warnings = [];
  if (nodesTotal > nodeLimit) {
    warnings.push(
      `Showing ${nodeLimit} of ${nodesTotal} symbols (${Math.round((nodeLimit / nodesTotal) * 100)}%).`
    );
  }
  if (edgesTotal > edgeLimit) {
    warnings.push(
      `Showing ${edgeLimit} of ${edgesTotal} edges (${Math.round((edgeLimit / edgesTotal) * 100)}%).`
    );
  }

  if (warnings.length > 0) {
    el.classList.remove("hidden");
    el.innerHTML =
      `<strong>⚠ Large Graph</strong> — ${warnings.join(" ")} ` +
      `Re-export with higher <code>--node-limit</code> / <code>--edge-limit</code> to see more.`;
  } else {
    el.classList.add("hidden");
  }
}

/* --- Summary --- */
function renderSummary(bundle) {
  const root = document.getElementById("summary");
  const nodes = Array.isArray(bundle.nodes) ? bundle.nodes.length : 0;
  const edges = Array.isArray(bundle.edges) ? bundle.edges.length : 0;
  const diagnostics = Array.isArray(bundle.diagnostics)
    ? bundle.diagnostics.length
    : 0;
  const explainerKeys = bundle.explainer
    ? Object.keys(bundle.explainer).length
    : 0;
  root.innerHTML = [
    `<span>Nodes ${nodes}</span>`,
    `<span>Edges ${edges}</span>`,
    `<span>Diagnostics ${diagnostics}</span>`,
    `<span>Explainable ${explainerKeys}</span>`,
  ].join("");
}

/* --- Hot Paths --- */
function renderHotPaths(bundle) {
  const root = document.getElementById("hot-paths");
  const items = Array.isArray(bundle.hot_paths) ? bundle.hot_paths : [];
  if (items.length === 0) {
    root.innerHTML = "<li class='empty'>No hot paths found.</li>";
    return;
  }
  root.innerHTML = items
    .slice(0, 12)
    .map(
      (item) =>
        `<li><strong>${esc(item.qualified_name)}</strong><br>${esc(item.file_path)}<br>score=${Number(item.pagerank_score || 0).toFixed(4)}</li>`
    )
    .join("");
}

/* --- Diagnostics --- */
function renderDiagnostics(bundle) {
  const root = document.getElementById("diagnostics");
  const items = Array.isArray(bundle.diagnostics) ? bundle.diagnostics : [];
  if (items.length === 0) {
    root.innerHTML = "<li class='empty'>No diagnostics recorded.</li>";
    return;
  }
  root.innerHTML = items
    .slice(0, 12)
    .map(
      (item) =>
        `<li><strong>${esc(item.stage)}</strong> ${esc(item.category)}<br>${esc(item.file_path || "unknown file")}<br>${esc(item.message)}</li>`
    )
    .join("");
}

/* --- Graph Preview with Pagination --- */
function renderGraph(bundle) {
  const root = document.getElementById("graph");
  const paginationEl = document.getElementById("graph-pagination");
  const items = Array.isArray(bundle.nodes) ? bundle.nodes : [];

  const totalPages = Math.max(1, Math.ceil(items.length / GRAPH_PAGE_SIZE));
  graphPage = Math.min(graphPage, totalPages - 1);
  const start = graphPage * GRAPH_PAGE_SIZE;
  const pageItems = items.slice(start, start + GRAPH_PAGE_SIZE);

  root.innerHTML = pageItems
    .map(
      (item) =>
        `<div class="node" data-id="${item.id}" onclick="showExplainer(${item.id})" role="button" tabindex="0" title="Click to explain ranking">` +
        `<strong>${esc(item.name)}</strong><br>${esc(item.kind)}<br>${esc(item.file_path)}<br>PR=${Number(item.pagerank_score || 0).toFixed(4)}` +
        `</div>`
    )
    .join("");

  if (totalPages > 1) {
    paginationEl.classList.remove("hidden");
    paginationEl.innerHTML =
      `<button ${graphPage === 0 ? "disabled" : ""} onclick="changePage(-1)">← Prev</button>` +
      `<span>Page ${graphPage + 1} of ${totalPages}</span>` +
      `<button ${graphPage >= totalPages - 1 ? "disabled" : ""} onclick="changePage(1)">Next →</button>`;
  } else {
    paginationEl.classList.add("hidden");
  }
}

function changePage(delta) {
  graphPage += delta;
  if (currentBundle) renderGraph(currentBundle);
}

/* --- Query Explainer --- */
function showExplainer(symbolId) {
  const root = document.getElementById("explainer");
  const explainer = currentBundle && currentBundle.explainer;

  if (!explainer || !explainer[String(symbolId)]) {
    root.innerHTML = `<div class="explainer-empty">No explanation available for symbol #${symbolId}.</div>`;
    return;
  }

  const data = explainer[String(symbolId)];
  const symbol = (currentBundle.nodes || []).find((n) => n.id === symbolId);
  const name = symbol ? symbol.qualified_name : `#${symbolId}`;
  const kind = symbol ? symbol.kind : "unknown";

  root.innerHTML = `
    <div class="explainer-card">
      <div class="explainer-header">
        <h3>${esc(name)}</h3>
        <span class="badge">${esc(kind)}</span>
      </div>
      <div class="explainer-stats">
        <div class="stat">
          <span class="stat-value">#${data.rank}</span>
          <span class="stat-label">Rank</span>
        </div>
        <div class="stat">
          <span class="stat-value">${Number(data.score).toFixed(4)}</span>
          <span class="stat-label">PageRank</span>
        </div>
        <div class="stat">
          <span class="stat-value">${data.inbound}</span>
          <span class="stat-label">Inbound</span>
        </div>
        <div class="stat">
          <span class="stat-value">${data.outbound}</span>
          <span class="stat-label">Outbound</span>
        </div>
      </div>
      <div class="explainer-reasons">
        <h4>Why this rank?</h4>
        <ul>
          ${data.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}
        </ul>
      </div>
    </div>
  `;

  // Scroll to explainer
  root.scrollIntoView({ behavior: "smooth", block: "nearest" });

  // Highlight the selected node
  document.querySelectorAll(".node").forEach((el) => el.classList.remove("selected"));
  const selectedNode = document.querySelector(`.node[data-id="${symbolId}"]`);
  if (selectedNode) selectedNode.classList.add("selected");
}

/* --- Utilities --- */
function esc(str) {
  const div = document.createElement("div");
  div.textContent = String(str || "");
  return div.innerHTML;
}

/* --- Main --- */
async function main() {
  try {
    currentBundle = await loadBundle();
    renderSummary(currentBundle);
    renderLimitsWarning(currentBundle);
    renderHotPaths(currentBundle);
    renderDiagnostics(currentBundle);
    renderGraph(currentBundle);
  } catch (error) {
    const summary = document.getElementById("summary");
    summary.textContent = String(error);
  }
}

main();
