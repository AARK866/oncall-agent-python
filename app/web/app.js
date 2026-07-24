const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const state = {
  apiBase: sessionStorage.getItem("oncall.apiBase") || "",
  apiToken: sessionStorage.getItem("oncall.apiToken") || "",
  apps: [],
  selectedApp: null,
  draft: null,
  graph: null,
  dirty: false,
  selectedNodeId: null,
  versions: [],
  runs: [],
  metrics: null,
  audit: [],
  activeTab: "designer",
  runTargetVersion: null,
  decision: null,
  rollbackVersion: null,
  drag: null,
};

const nodeVisuals = {
  start: { icon: "circle-play", label: "Start" },
  agent: { icon: "bot", label: "Agent" },
  knowledge_retrieval: { icon: "book-open-text", label: "Knowledge" },
  tool: { icon: "wrench", label: "Tool" },
  human_review: { icon: "user-check", label: "Human Review" },
  end: { icon: "circle-stop", label: "End" },
};

document.addEventListener("DOMContentLoaded", initialize);

async function initialize() {
  bindEvents();
  refreshIcons();
  $("#apiBaseInput").value = state.apiBase;
  $("#apiTokenInput").value = state.apiToken;
  await loadApplications();
}

function bindEvents() {
  $("#sidebarToggle").addEventListener("click", () => {
    $("#sidebar").classList.toggle("open");
  });
  $("#connectionButton").addEventListener("click", () => {
    $("#apiBaseInput").value = state.apiBase;
    $("#apiTokenInput").value = state.apiToken;
    $("#connectionDialog").showModal();
  });
  $("#connectionForm").addEventListener("submit", saveConnection);

  ["#createAppButton", "#emptyCreateButton"].forEach((selector) => {
    $(selector).addEventListener("click", openCreateAppDialog);
  });
  $("#createAppForm").addEventListener("submit", createApplication);
  $("#refreshAppsButton").addEventListener("click", loadApplications);
  $("#appSearch").addEventListener("input", renderApplicationList);
  $("#appList").addEventListener("click", (event) => {
    const button = event.target.closest("[data-app-id]");
    if (button) selectApplication(button.dataset.appId);
  });

  $("#workspaceTabs").addEventListener("click", (event) => {
    const tab = event.target.closest("[data-tab]");
    if (tab) switchTab(tab.dataset.tab);
  });
  $$(".inspector-tab").forEach((tab) => {
    tab.addEventListener("click", () => switchInspectorTab(tab.dataset.inspectorTab));
  });

  $("#addNodeButton").addEventListener("click", openAddNodeDialog);
  $("#addNodeForm").addEventListener("submit", addNode);
  $("#newNodeType").addEventListener("change", suggestNodeName);
  $("#addEdgeButton").addEventListener("click", openAddEdgeDialog);
  $("#addEdgeForm").addEventListener("submit", addEdge);
  $("#deleteNodeButton").addEventListener("click", deleteSelectedNode);
  $("#edgeList").addEventListener("click", deleteEdgeFromList);

  $("#nodeNameInput").addEventListener("input", updateSelectedNodeName);
  $("#nodeTypeInput").addEventListener("change", updateSelectedNodeType);
  $("#nodeConfigInput").addEventListener("change", updateSelectedNodeConfig);
  $("#graphVariablesInput").addEventListener("change", updateGraphVariables);
  $("#graphSettingsInput").addEventListener("change", updateGraphSettings);

  $("#validateButton").addEventListener("click", validateDraft);
  $("#saveDraftButton").addEventListener("click", saveDraft);
  $("#publishButton").addEventListener("click", openPublishDialog);
  $("#publishForm").addEventListener("submit", publishVersion);
  $("#runDraftButton").addEventListener("click", () => openRunDialog(null));
  $("#runForm").addEventListener("submit", runWorkflow);
  $("#closeValidationButton").addEventListener("click", () => {
    $("#validationStrip").classList.add("hidden");
  });

  $("#refreshVersionsButton").addEventListener("click", loadVersions);
  $("#versionsTable").addEventListener("click", handleVersionAction);
  $("#rollbackForm").addEventListener("submit", rollbackVersion);
  $("#refreshRunsButton").addEventListener("click", loadRunsAndMetrics);
  $("#runStatusFilter").addEventListener("change", loadRuns);
  $("#runsTable").addEventListener("click", (event) => {
    const row = event.target.closest("[data-run-id]");
    if (row) openRunDetail(row.dataset.runId);
  });
  $("#closeRunDetailButton").addEventListener("click", () => {
    $("#runDetailDialog").close();
  });
  $("#runReviews").addEventListener("click", openReviewDecision);
  $("#decisionForm").addEventListener("submit", submitReviewDecision);
  $("#refreshAuditButton").addEventListener("click", loadAudit);

  $("#workflowCanvas").addEventListener("click", (event) => {
    const node = event.target.closest("[data-node-id]");
    if (node) selectNode(node.dataset.nodeId);
  });
  $("#workflowCanvas").addEventListener("pointerdown", beginNodeDrag);
  window.addEventListener("pointermove", moveNode);
  window.addEventListener("pointerup", endNodeDrag);
  window.addEventListener("resize", renderEdges);

  $$("dialog form").forEach((form) => {
    form.addEventListener("click", (event) => {
      const button = event.target.closest("button[value='cancel']");
      if (button) {
        event.preventDefault();
        form.closest("dialog").close();
      }
    });
  });
}

async function request(path, options = {}) {
  const base = state.apiBase.replace(/\/+$/, "");
  const headers = new Headers(options.headers || {});
  if (options.body && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  if (state.apiToken) {
    headers.set("x-api-key", state.apiToken);
  }
  const response = await fetch(`${base}${path}`, { ...options, headers });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    const rawBody = await response.text();
    if (rawBody) {
      try {
        const payload = JSON.parse(rawBody);
        detail = formatErrorDetail(payload.detail ?? payload);
      } catch {
        detail = rawBody;
      }
    }
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  if (response.status === 204) return null;
  return response.json();
}

function formatErrorDetail(detail) {
  if (typeof detail === "string") return detail;
  if (detail?.message) return detail.message;
  if (detail?.issues) {
    return detail.issues.map((issue) => issue.message).join("; ");
  }
  return JSON.stringify(detail);
}

async function loadApplications() {
  setConnection("loading", "正在连接");
  try {
    await request("/health");
    state.apps = await request("/api/workflow-apps?limit=100&include_archived=true");
    setConnection("connected", "已连接");
    renderApplicationList();
    const remembered = localStorage.getItem("oncall.selectedApp");
    const nextId = state.apps.some((app) => app.app_id === remembered)
      ? remembered
      : state.apps[0]?.app_id;
    if (nextId) {
      await selectApplication(nextId);
    } else {
      clearWorkspace("暂无工作流应用");
    }
  } catch (error) {
    setConnection("failed", "连接失败");
    clearWorkspace("无法加载工作流应用");
    toast(error.message, "error");
  }
}

function renderApplicationList() {
  const query = $("#appSearch").value.trim().toLowerCase();
  const apps = state.apps.filter((app) => {
    const text = `${app.name} ${app.description}`.toLowerCase();
    return !query || text.includes(query);
  });
  $("#appList").innerHTML = apps.length
    ? apps
        .map(
          (app) => `
            <button class="app-list-item ${app.app_id === state.selectedApp?.app_id ? "active" : ""}"
                    type="button" data-app-id="${escapeHtml(app.app_id)}">
              <span class="app-list-icon"><i data-lucide="workflow"></i></span>
              <span class="app-list-copy">
                <strong>${escapeHtml(app.name)}</strong>
                <span>${escapeHtml(app.status)} · ${formatDate(app.updated_at)}</span>
              </span>
            </button>
          `,
        )
        .join("")
    : '<div class="inspector-empty">无匹配应用</div>';
  refreshIcons();
}

async function selectApplication(appId) {
  if (state.dirty && !window.confirm("当前草稿有未保存修改，仍要切换应用吗？")) return;
  const app = state.apps.find((item) => item.app_id === appId);
  if (!app) return;
  state.selectedApp = app;
  state.dirty = false;
  state.selectedNodeId = null;
  localStorage.setItem("oncall.selectedApp", appId);
  $("#emptyWorkspace").classList.add("hidden");
  $("#appWorkspace").classList.remove("hidden");
  $("#sidebar").classList.remove("open");
  renderApplicationList();
  setWorkspaceLoading(true);
  try {
    const [draft, versions, runs, metrics, audit] = await Promise.all([
      request(`/api/workflow-apps/${appId}/draft`),
      request(`/api/workflow-apps/${appId}/versions?limit=100`),
      request(`/api/workflow-apps/${appId}/runs?limit=100`),
      request(`/api/workflow-apps/${appId}/runs/metrics?window_hours=24`),
      request(`/api/workflow-apps/${appId}/audit-events?limit=200`),
    ]);
    state.draft = draft;
    state.graph = structuredClone(draft.graph);
    normalizeNodePositions();
    state.versions = versions;
    state.runs = runs;
    state.metrics = metrics;
    state.audit = audit;
    renderWorkspace();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    setWorkspaceLoading(false);
  }
}

function renderWorkspace() {
  const app = state.selectedApp;
  if (!app || !state.draft) return;
  $("#appTitle").textContent = app.name;
  $("#appDescription").textContent = app.description || " ";
  $("#appStatus").textContent = app.status;
  $("#appStatus").className = `status-badge ${app.status}`;
  $("#draftRevision").textContent = `草稿 r${state.draft.revision}`;
  updateSaveState();
  renderGraph();
  renderVersions();
  renderRuns();
  renderMetrics();
  renderAudit();
}

function clearWorkspace(message) {
  state.selectedApp = null;
  state.draft = null;
  state.graph = null;
  $("#appWorkspace").classList.add("hidden");
  $("#emptyWorkspace").classList.remove("hidden");
  $("#emptyMessage").textContent = message;
  renderApplicationList();
}

function switchTab(tabName) {
  state.activeTab = tabName;
  $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === tabName));
  $$(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.panel === tabName);
  });
  if (tabName === "designer") {
    requestAnimationFrame(renderEdges);
  }
}

function switchInspectorTab(tabName) {
  $$(".inspector-tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.inspectorTab === tabName);
  });
  $("#nodeInspector").classList.toggle("hidden", tabName !== "node");
  $("#graphInspector").classList.toggle("hidden", tabName !== "graph");
}

function renderGraph() {
  if (!state.graph) return;
  const nodes = state.graph.nodes || [];
  $("#nodeLayer").innerHTML = nodes
    .map((node) => {
      const visual = nodeVisuals[node.node_type] || nodeVisuals.agent;
      const x = Number(node.position?.x || 0);
      const y = Number(node.position?.y || 0);
      return `
        <button class="workflow-node ${node.node_id === state.selectedNodeId ? "selected" : ""}"
                type="button"
                data-node-id="${escapeHtml(node.node_id)}"
                data-node-type="${escapeHtml(node.node_type)}"
                style="left:${x}px;top:${y}px">
          <span class="node-icon"><i data-lucide="${visual.icon}"></i></span>
          <span class="node-copy">
            <strong>${escapeHtml(node.name)}</strong>
            <span>${escapeHtml(visual.label)}</span>
          </span>
        </button>
      `;
    })
    .join("");
  renderInspector();
  renderEdgeList();
  refreshIcons();
  requestAnimationFrame(renderEdges);
}

function renderEdges() {
  if (!state.graph) return;
  const svg = $("#edgeLayer");
  const paths = (state.graph.edges || [])
    .map((edge) => {
      const source = getNode(edge.source_node_id);
      const target = getNode(edge.target_node_id);
      if (!source || !target) return "";
      const startX = Number(source.position?.x || 0) + 190;
      const startY = Number(source.position?.y || 0) + 34;
      const endX = Number(target.position?.x || 0);
      const endY = Number(target.position?.y || 0) + 34;
      const bend = Math.max(70, Math.abs(endX - startX) * 0.45);
      const control1X = startX + bend;
      const control2X = endX - bend;
      return `<path class="edge-path" d="M ${startX} ${startY} C ${control1X} ${startY}, ${control2X} ${endY}, ${endX} ${endY}"></path>`;
    })
    .join("");
  const defs = $("defs", svg)?.outerHTML || "";
  svg.innerHTML = `${defs}${paths}`;
}

function renderInspector() {
  const node = getNode(state.selectedNodeId);
  $("#nodeInspectorEmpty").classList.toggle("hidden", Boolean(node));
  $("#nodeInspectorForm").classList.toggle("hidden", !node);
  if (node) {
    $("#nodeNameInput").value = node.name;
    $("#nodeIdInput").value = node.node_id;
    $("#nodeTypeInput").value = node.node_type;
    $("#nodeConfigInput").value = prettyJson(node.config || {});
  }
  $("#graphVariablesInput").value = prettyJson(state.graph?.variables || {});
  $("#graphSettingsInput").value = prettyJson(state.graph?.settings || {});
}

function renderEdgeList() {
  const edges = state.graph?.edges || [];
  $("#edgeCount").textContent = edges.length;
  $("#edgeList").innerHTML = edges.length
    ? edges
        .map(
          (edge) => `
            <div class="edge-item">
              <span title="${escapeHtml(edge.edge_id)}">${escapeHtml(edge.source_node_id)} → ${escapeHtml(edge.target_node_id)}</span>
              <button class="icon-button small" type="button" data-delete-edge="${escapeHtml(edge.edge_id)}" aria-label="删除连线" title="删除连线">
                <i data-lucide="x"></i>
              </button>
            </div>
          `,
        )
        .join("")
    : '<div class="inspector-empty">暂无连线</div>';
  refreshIcons();
}

function selectNode(nodeId) {
  state.selectedNodeId = nodeId;
  switchInspectorTab("node");
  $$(".workflow-node").forEach((node) => {
    node.classList.toggle("selected", node.dataset.nodeId === nodeId);
  });
  renderInspector();
}

function beginNodeDrag(event) {
  const nodeElement = event.target.closest(".workflow-node");
  if (!nodeElement || event.button !== 0) return;
  const node = getNode(nodeElement.dataset.nodeId);
  if (!node) return;
  event.preventDefault();
  selectNode(node.node_id);
  state.drag = {
    node,
    element: nodeElement,
    startClientX: event.clientX,
    startClientY: event.clientY,
    startX: Number(node.position?.x || 0),
    startY: Number(node.position?.y || 0),
    moved: false,
  };
  nodeElement.setPointerCapture?.(event.pointerId);
}

function moveNode(event) {
  if (!state.drag) return;
  const dx = event.clientX - state.drag.startClientX;
  const dy = event.clientY - state.drag.startClientY;
  if (Math.abs(dx) + Math.abs(dy) > 2) state.drag.moved = true;
  const x = Math.max(20, Math.min(1390, state.drag.startX + dx));
  const y = Math.max(20, Math.min(800, state.drag.startY + dy));
  state.drag.node.position = { x: Math.round(x), y: Math.round(y) };
  state.drag.element.style.left = `${x}px`;
  state.drag.element.style.top = `${y}px`;
  renderEdges();
}

function endNodeDrag() {
  if (!state.drag) return;
  if (state.drag.moved) markDirty();
  state.drag = null;
}

function openAddNodeDialog() {
  if (!state.graph) return;
  $("#newNodeType").value = "agent";
  suggestNodeName();
  $("#addNodeDialog").showModal();
  $("#newNodeName").focus();
}

function suggestNodeName() {
  const type = $("#newNodeType").value;
  const defaults = {
    start: "Start",
    agent: "Diagnosis Agent",
    knowledge_retrieval: "Search Runbook",
    tool: "Query Metrics",
    human_review: "Human Review",
    end: "End",
  };
  $("#newNodeName").value = defaults[type] || "New Node";
}

function addNode(event) {
  event.preventDefault();
  if (event.submitter?.value === "cancel") return;
  const type = $("#newNodeType").value;
  const name = $("#newNodeName").value.trim();
  if (!name) return;
  const id = uniqueNodeId(slugify(name) || type);
  const viewport = $("#canvasViewport");
  const node = {
    node_id: id,
    node_type: type,
    name,
    config: defaultNodeConfig(type),
    position: {
      x: Math.max(30, viewport.scrollLeft + 80),
      y: Math.max(30, viewport.scrollTop + 80),
    },
  };
  state.graph.nodes.push(node);
  markDirty();
  state.selectedNodeId = id;
  $("#addNodeDialog").close();
  renderGraph();
}

function openAddEdgeDialog() {
  const nodes = state.graph?.nodes || [];
  if (nodes.length < 2) {
    toast("至少需要两个节点", "error");
    return;
  }
  const options = nodes
    .map((node) => `<option value="${escapeHtml(node.node_id)}">${escapeHtml(node.name)} · ${escapeHtml(node.node_id)}</option>`)
    .join("");
  $("#edgeSourceInput").innerHTML = options;
  $("#edgeTargetInput").innerHTML = options;
  $("#edgeTargetInput").selectedIndex = Math.min(1, nodes.length - 1);
  $("#addEdgeDialog").showModal();
}

function addEdge(event) {
  event.preventDefault();
  if (event.submitter?.value === "cancel") return;
  const source = $("#edgeSourceInput").value;
  const target = $("#edgeTargetInput").value;
  if (source === target) {
    toast("起点和终点不能相同", "error");
    return;
  }
  if (state.graph.edges.some((edge) => edge.source_node_id === source && edge.target_node_id === target)) {
    toast("该连线已经存在", "error");
    return;
  }
  state.graph.edges.push({
    edge_id: uniqueEdgeId(`${source}-${target}`),
    source_node_id: source,
    target_node_id: target,
    condition: null,
    priority: 0,
  });
  markDirty();
  $("#addEdgeDialog").close();
  renderGraph();
}

function deleteSelectedNode() {
  const node = getNode(state.selectedNodeId);
  if (!node || !window.confirm(`删除节点“${node.name}”及其连线？`)) return;
  state.graph.nodes = state.graph.nodes.filter((item) => item.node_id !== node.node_id);
  state.graph.edges = state.graph.edges.filter(
    (edge) => edge.source_node_id !== node.node_id && edge.target_node_id !== node.node_id,
  );
  state.selectedNodeId = null;
  markDirty();
  renderGraph();
}

function deleteEdgeFromList(event) {
  const button = event.target.closest("[data-delete-edge]");
  if (!button) return;
  state.graph.edges = state.graph.edges.filter((edge) => edge.edge_id !== button.dataset.deleteEdge);
  markDirty();
  renderGraph();
}

function updateSelectedNodeName() {
  const node = getNode(state.selectedNodeId);
  if (!node) return;
  node.name = $("#nodeNameInput").value;
  const nodeElement = $(`[data-node-id="${cssEscape(node.node_id)}"]`);
  if (nodeElement) $("strong", nodeElement).textContent = node.name;
  markDirty();
}

function updateSelectedNodeType() {
  const node = getNode(state.selectedNodeId);
  if (!node) return;
  node.node_type = $("#nodeTypeInput").value;
  if (!Object.keys(node.config || {}).length) {
    node.config = defaultNodeConfig(node.node_type);
  }
  markDirty();
  renderGraph();
}

function updateSelectedNodeConfig() {
  const node = getNode(state.selectedNodeId);
  if (!node) return;
  try {
    node.config = parseJson($("#nodeConfigInput").value, "节点配置");
    $("#nodeConfigInput").closest(".field").classList.remove("invalid");
    markDirty();
  } catch (error) {
    $("#nodeConfigInput").closest(".field").classList.add("invalid");
    toast(error.message, "error");
  }
}

function updateGraphVariables() {
  updateGraphJsonField("variables", "#graphVariablesInput", "输入变量");
}

function updateGraphSettings() {
  updateGraphJsonField("settings", "#graphSettingsInput", "全局设置");
}

function updateGraphJsonField(field, selector, label) {
  try {
    state.graph[field] = parseJson($(selector).value, label);
    $(selector).closest(".field").classList.remove("invalid");
    markDirty();
  } catch (error) {
    $(selector).closest(".field").classList.add("invalid");
    toast(error.message, "error");
  }
}

function syncJsonEditors() {
  const node = getNode(state.selectedNodeId);
  if (node) node.config = parseJson($("#nodeConfigInput").value, "节点配置");
  state.graph.variables = parseJson($("#graphVariablesInput").value, "输入变量");
  state.graph.settings = parseJson($("#graphSettingsInput").value, "全局设置");
}

async function saveDraft() {
  if (!state.selectedApp || !state.draft) return null;
  try {
    syncJsonEditors();
    const updated = await request(`/api/workflow-apps/${state.selectedApp.app_id}/draft`, {
      method: "PUT",
      body: JSON.stringify({
        expected_revision: state.draft.revision,
        graph: state.graph,
      }),
    });
    state.draft = updated;
    state.graph = structuredClone(updated.graph);
    state.dirty = false;
    $("#draftRevision").textContent = `草稿 r${updated.revision}`;
    updateSaveState();
    toast(`草稿已保存为 r${updated.revision}`, "success");
    return updated;
  } catch (error) {
    toast(error.status === 409 ? `保存冲突：${error.message}` : error.message, "error");
    throw error;
  }
}

async function validateDraft() {
  if (!state.selectedApp) return;
  try {
    if (state.dirty) await saveDraft();
    const report = await request(`/api/workflow-apps/${state.selectedApp.app_id}/draft/validate`, {
      method: "POST",
    });
    renderValidation(report);
    if (report.valid) toast("工作流校验通过", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderValidation(report) {
  const strip = $("#validationStrip");
  strip.classList.remove("hidden", "invalid");
  strip.classList.toggle("invalid", !report.valid);
  $("#validationSummary").textContent = report.valid
    ? `校验通过 · ${report.node_count} 个节点 · ${report.edge_count} 条连线`
    : `发现 ${report.issues.length} 个问题`;
  $("#validationIssues").innerHTML = report.issues
    .map(
      (issue) => `
        <span class="validation-issue">
          <code>${escapeHtml(issue.code)}</code>
          <span>${escapeHtml(issue.message)}</span>
        </span>
      `,
    )
    .join("");
}

function openPublishDialog() {
  if (!state.draft) return;
  $("#releaseNotesInput").value = "";
  $("#publishDialog").showModal();
}

async function publishVersion(event) {
  event.preventDefault();
  if (event.submitter?.value === "cancel") return;
  try {
    if (state.dirty) await saveDraft();
    const version = await request(`/api/workflow-apps/${state.selectedApp.app_id}/publish`, {
      method: "POST",
      body: JSON.stringify({
        expected_revision: state.draft.revision,
        published_by: $("#publisherInput").value.trim(),
        release_notes: $("#releaseNotesInput").value.trim(),
      }),
    });
    $("#publishDialog").close();
    toast(`版本 v${version.version_number} 已发布`, "success");
    await Promise.all([loadVersions(), loadAudit()]);
    switchTab("versions");
  } catch (error) {
    toast(error.message, "error");
  }
}

function openRunDialog(versionNumber) {
  state.runTargetVersion = versionNumber;
  $("#runTargetLabel").textContent = versionNumber ? `已发布版本 v${versionNumber}` : `当前草稿 r${state.draft?.revision}`;
  $("#runInputsInput").value = suggestedRunInputs();
  $("#runDialog").showModal();
}

async function runWorkflow(event) {
  event.preventDefault();
  if (event.submitter?.value === "cancel") return;
  try {
    if (state.runTargetVersion === null && state.dirty) await saveDraft();
    const inputs = parseJson($("#runInputsInput").value, "运行输入");
    const appId = state.selectedApp.app_id;
    const path = state.runTargetVersion
      ? `/api/workflow-apps/${appId}/versions/${state.runTargetVersion}/run`
      : `/api/workflow-apps/${appId}/draft/run`;
    const result = await request(path, {
      method: "POST",
      body: JSON.stringify({
        inputs,
        requested_by: $("#runRequestedBy").value.trim(),
      }),
    });
    $("#runDialog").close();
    toast(result.status === "waiting_review" ? "运行等待人工审批" : "运行成功", result.status === "waiting_review" ? "info" : "success");
    await Promise.all([loadRunsAndMetrics(), loadAudit()]);
    switchTab("runs");
    await openRunDetail(result.run_id);
  } catch (error) {
    toast(error.message, "error");
    await loadRunsAndMetrics();
  }
}

async function loadVersions() {
  if (!state.selectedApp) return;
  try {
    state.versions = await request(`/api/workflow-apps/${state.selectedApp.app_id}/versions?limit=100`);
    renderVersions();
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderVersions() {
  $("#versionCount").textContent = `${state.versions.length} 个版本`;
  $("#versionsTable").innerHTML = state.versions.length
    ? state.versions
        .map(
          (version) => `
            <tr>
              <td><strong>v${version.version_number}</strong></td>
              <td>r${version.source_draft_revision}</td>
              <td>${escapeHtml(version.published_by)}</td>
              <td><span class="mono truncate-cell" title="${escapeHtml(version.graph_sha256)}">${escapeHtml(version.graph_sha256.slice(0, 16))}</span></td>
              <td>${formatDateTime(version.created_at)}</td>
              <td>
                <div class="table-actions">
                  <button class="secondary-button" type="button" data-version-action="run" data-version="${version.version_number}">
                    <i data-lucide="play"></i>运行
                  </button>
                  <button class="secondary-button" type="button" data-version-action="rollback" data-version="${version.version_number}">
                    <i data-lucide="rotate-ccw"></i>恢复
                  </button>
                </div>
              </td>
            </tr>
          `,
        )
        .join("")
    : emptyTableRow(6, "暂无已发布版本");
  refreshIcons();
}

function handleVersionAction(event) {
  const button = event.target.closest("[data-version-action]");
  if (!button) return;
  const version = Number(button.dataset.version);
  if (button.dataset.versionAction === "run") openRunDialog(version);
  if (button.dataset.versionAction === "rollback") openRollbackDialog(version);
}

function openRollbackDialog(versionNumber) {
  state.rollbackVersion = versionNumber;
  $("#rollbackTarget").textContent = `历史版本 v${versionNumber} 将复制为新的草稿修订。`;
  $("#rollbackReason").value = "";
  $("#rollbackDialog").showModal();
}

async function rollbackVersion(event) {
  event.preventDefault();
  if (event.submitter?.value === "cancel") return;
  try {
    const appId = state.selectedApp.app_id;
    const response = await request(
      `/api/workflow-apps/${appId}/versions/${state.rollbackVersion}/rollback`,
      {
        method: "POST",
        body: JSON.stringify({
          expected_revision: state.draft.revision,
          requested_by: $("#rollbackRequestedBy").value.trim(),
          reason: $("#rollbackReason").value.trim(),
        }),
      },
    );
    state.draft = response.draft;
    state.graph = structuredClone(response.draft.graph);
    state.dirty = false;
    state.selectedNodeId = null;
    normalizeNodePositions();
    $("#rollbackDialog").close();
    toast(`v${state.rollbackVersion} 已恢复为草稿 r${response.draft.revision}`, "success");
    renderWorkspace();
    await loadAudit();
    switchTab("designer");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function loadRunsAndMetrics() {
  await Promise.all([loadRuns(), loadMetrics()]);
}

async function loadRuns() {
  if (!state.selectedApp) return;
  const filter = $("#runStatusFilter").value;
  const query = new URLSearchParams({ limit: "100" });
  if (filter) query.set("status", filter);
  try {
    state.runs = await request(`/api/workflow-apps/${state.selectedApp.app_id}/runs?${query}`);
    renderRuns();
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderRuns() {
  $("#runCount").textContent = `${state.runs.length} 条记录`;
  $("#runsTable").innerHTML = state.runs.length
    ? state.runs
        .map(
          (run) => `
            <tr class="clickable" data-run-id="${escapeHtml(run.run_id)}">
              <td><span class="mono truncate-cell" title="${escapeHtml(run.run_id)}">${escapeHtml(shortId(run.run_id))}</span></td>
              <td>${run.execution_source === "published" ? `v${run.version_number}` : `草稿 r${run.draft_revision}`}</td>
              <td><span class="run-status ${escapeHtml(run.status)}">${escapeHtml(run.status)}</span></td>
              <td>${escapeHtml(run.started_by)}</td>
              <td>${formatDateTime(run.created_at)}</td>
              <td>${formatDuration(run.created_at, run.finished_at)}</td>
            </tr>
          `,
        )
        .join("")
    : emptyTableRow(6, "暂无运行记录");
}

async function loadMetrics() {
  if (!state.selectedApp) return;
  try {
    state.metrics = await request(`/api/workflow-apps/${state.selectedApp.app_id}/runs/metrics?window_hours=24`);
    renderMetrics();
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderMetrics() {
  const metrics = state.metrics || {};
  $("#metricTotal").textContent = metrics.total_runs ?? 0;
  $("#metricSuccess").textContent = `${Math.round((metrics.success_rate || 0) * 100)}%`;
  $("#metricP95").textContent = formatMilliseconds(metrics.p95_duration_ms);
  $("#metricReviews").textContent = metrics.pending_reviews ?? 0;
}

async function openRunDetail(runId) {
  if (!runId) return;
  try {
    const appId = state.selectedApp.app_id;
    const [run, events, reviews] = await Promise.all([
      request(`/api/workflow-apps/${appId}/runs/${runId}`),
      request(`/api/workflow-apps/${appId}/runs/${runId}/events`),
      request(`/api/workflow-apps/${appId}/runs/${runId}/reviews`),
    ]);
    renderRunDetail(run, events, reviews);
    if (!$("#runDetailDialog").open) $("#runDetailDialog").showModal();
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderRunDetail(run, events, reviews) {
  $("#runDetailId").textContent = run.run_id;
  $("#runDetailSummary").innerHTML = [
    ["状态", run.status],
    ["来源", run.execution_source === "published" ? `v${run.version_number}` : `草稿 r${run.draft_revision}`],
    ["发起者", run.started_by],
    ["耗时", formatDuration(run.created_at, run.finished_at)],
  ]
    .map(
      ([label, value]) => `
        <div class="summary-cell">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(String(value))}</strong>
        </div>
      `,
    )
    .join("");
  $("#runReviews").innerHTML = reviews.length
    ? reviews
        .map(
          (review) => `
            <div class="review-row">
              <div class="review-copy">
                <strong>${escapeHtml(review.payload.title || review.node_id)}</strong>
                <span>${escapeHtml(review.payload.message || review.status)}</span>
              </div>
              ${
                review.status === "pending"
                  ? `
                    <div class="review-actions">
                      <button class="secondary-button" type="button" data-review-action="reject" data-review-id="${escapeHtml(review.review_id)}" data-run-id="${escapeHtml(run.run_id)}">
                        <i data-lucide="x"></i>拒绝
                      </button>
                      <button class="primary-button" type="button" data-review-action="approve" data-review-id="${escapeHtml(review.review_id)}" data-run-id="${escapeHtml(run.run_id)}">
                        <i data-lucide="check"></i>批准
                      </button>
                    </div>
                  `
                  : `<span class="run-status ${escapeHtml(review.status)}">${escapeHtml(review.status)}</span>`
              }
            </div>
          `,
        )
        .join("")
    : '<div class="inspector-empty">无审批记录</div>';
  $("#runTimeline").innerHTML = events
    .map(
      (event) => `
        <div class="timeline-item" data-event="${escapeHtml(event.event_type)}">
          <span class="timeline-dot"></span>
          <div class="timeline-copy">
            <strong>${escapeHtml(event.message)}</strong>
            <span>${escapeHtml(event.node_id || event.event_type)}${event.data?.elapsed_ms !== undefined ? ` · ${event.data.elapsed_ms}ms` : ""}</span>
          </div>
          <span class="timeline-time">${formatTime(event.created_at)}</span>
        </div>
      `,
    )
    .join("");
  $("#runOutput").textContent = prettyJson({
    output: run.output,
    error: run.error,
  });
  refreshIcons();
}

function openReviewDecision(event) {
  const button = event.target.closest("[data-review-action]");
  if (!button) return;
  state.decision = {
    action: button.dataset.reviewAction,
    reviewId: button.dataset.reviewId,
    runId: button.dataset.runId,
  };
  $("#decisionTitle").textContent = state.decision.action === "approve" ? "批准运行" : "拒绝运行";
  $("#confirmDecision").textContent = state.decision.action === "approve" ? "批准并继续" : "拒绝并终止";
  $("#confirmDecision").className = state.decision.action === "approve" ? "primary-button" : "danger-button";
  $("#decisionReason").value = "";
  $("#decisionDialog").showModal();
}

async function submitReviewDecision(event) {
  event.preventDefault();
  if (event.submitter?.value === "cancel" || !state.decision) return;
  try {
    const appId = state.selectedApp.app_id;
    const { action, reviewId, runId } = state.decision;
    const response = await request(
      `/api/workflow-apps/${appId}/runs/${runId}/reviews/${reviewId}/${action}`,
      {
        method: "POST",
        body: JSON.stringify({
          reviewer: $("#decisionReviewer").value.trim(),
          reason: $("#decisionReason").value.trim() || null,
        }),
      },
    );
    $("#decisionDialog").close();
    toast(action === "approve" ? "审批通过，运行已继续" : "运行已拒绝", action === "approve" ? "success" : "info");
    await Promise.all([loadRunsAndMetrics(), loadAudit()]);
    await openRunDetail(runId);
    return response;
  } catch (error) {
    toast(error.message, "error");
  }
}

async function loadAudit() {
  if (!state.selectedApp) return;
  try {
    state.audit = await request(`/api/workflow-apps/${state.selectedApp.app_id}/audit-events?limit=200`);
    renderAudit();
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderAudit() {
  $("#auditCount").textContent = `${state.audit.length} 条记录`;
  $("#auditTable").innerHTML = state.audit.length
    ? state.audit
        .map(
          (event) => `
            <tr>
              <td>${formatDateTime(event.created_at)}</td>
              <td>${escapeHtml(event.actor)}</td>
              <td><span class="mono">${escapeHtml(event.action)}</span></td>
              <td><span class="mono truncate-cell" title="${escapeHtml(event.resource_id)}">${escapeHtml(event.resource_type)} · ${escapeHtml(shortId(event.resource_id))}</span></td>
              <td><span class="mono truncate-cell" title="${escapeHtml(JSON.stringify(event.details))}">${escapeHtml(compactDetails(event.details))}</span></td>
            </tr>
          `,
        )
        .join("")
    : emptyTableRow(5, "暂无审计记录");
}

function openCreateAppDialog() {
  $("#createAppName").value = "";
  $("#createAppDescription").value = "";
  $("#createAppDialog").showModal();
  $("#createAppName").focus();
}

async function createApplication(event) {
  event.preventDefault();
  if (event.submitter?.value === "cancel") return;
  try {
    const application = await request("/api/workflow-apps", {
      method: "POST",
      body: JSON.stringify({
        name: $("#createAppName").value.trim(),
        description: $("#createAppDescription").value.trim(),
      }),
    });
    $("#createAppDialog").close();
    state.apps.unshift(application);
    renderApplicationList();
    toast("工作流应用已创建", "success");
    await selectApplication(application.app_id);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function saveConnection(event) {
  event.preventDefault();
  if (event.submitter?.value === "cancel") return;
  state.apiBase = $("#apiBaseInput").value.trim().replace(/\/+$/, "");
  state.apiToken = $("#apiTokenInput").value.trim();
  sessionStorage.setItem("oncall.apiBase", state.apiBase);
  sessionStorage.setItem("oncall.apiToken", state.apiToken);
  $("#connectionDialog").close();
  await loadApplications();
}

function setConnection(status, label) {
  const element = $("#connectionStatus");
  element.className = `topbar-status ${status}`;
  $("span:last-child", element).textContent = label;
}

function setWorkspaceLoading(loading) {
  $("#appWorkspace").classList.toggle("loading", loading);
}

function markDirty() {
  state.dirty = true;
  updateSaveState();
}

function updateSaveState() {
  $("#saveState").textContent = state.dirty ? "未保存" : "已保存";
  $("#saveState").classList.toggle("dirty", state.dirty);
}

function normalizeNodePositions() {
  const nodes = state.graph?.nodes || [];
  if (!nodes.length) return;
  const allAtOrigin = nodes.every((node) => Number(node.position?.x || 0) === 0 && Number(node.position?.y || 0) === 0);
  if (!allAtOrigin) return;
  nodes.forEach((node, index) => {
    node.position = {
      x: 70 + (index % 4) * 245,
      y: 70 + Math.floor(index / 4) * 145,
    };
  });
}

function getNode(nodeId) {
  return state.graph?.nodes?.find((node) => node.node_id === nodeId) || null;
}

function uniqueNodeId(base) {
  let candidate = base;
  let index = 2;
  while (getNode(candidate)) candidate = `${base}-${index++}`;
  return candidate;
}

function uniqueEdgeId(base) {
  const normalized = slugify(base) || "edge";
  let candidate = normalized;
  let index = 2;
  while (state.graph.edges.some((edge) => edge.edge_id === candidate)) {
    candidate = `${normalized}-${index++}`;
  }
  return candidate;
}

function defaultNodeConfig(type) {
  const configs = {
    start: {},
    agent: {
      prompt: "Diagnose ${inputs.question} for ${inputs.service}.",
      system_prompt: "You are an enterprise OnCall workflow agent.",
    },
    knowledge_retrieval: {
      query: "${inputs.question}",
      service: "${inputs.service}",
      top_k: 3,
    },
    tool: {
      tool_name: "query_metrics",
      arguments: { service: "${inputs.service}" },
      fail_on_error: true,
    },
    human_review: {
      title: "Approve operational action",
      message: "Approve action for ${inputs.service}?",
    },
    end: {},
  };
  return structuredClone(configs[type] || {});
}

function suggestedRunInputs() {
  const inputs = {};
  for (const [name, definition] of Object.entries(state.graph?.variables || {})) {
    if (definition.default !== undefined) {
      inputs[name] = definition.default;
    } else {
      inputs[name] = sampleValue(definition.type);
    }
  }
  return prettyJson(inputs);
}

function sampleValue(type) {
  return {
    string: "",
    integer: 0,
    number: 0,
    boolean: false,
    object: {},
    array: [],
  }[type] ?? "";
}

function parseJson(text, label) {
  try {
    const value = JSON.parse(text || "{}");
    if (!value || Array.isArray(value) || typeof value !== "object") {
      throw new Error(`${label}必须是 JSON 对象`);
    }
    return value;
  } catch (error) {
    if (error.message.endsWith("JSON 对象")) throw error;
    throw new Error(`${label} JSON 格式错误：${error.message}`);
  }
}

function refreshIcons() {
  if (window.lucide?.createIcons) window.lucide.createIcons();
}

function toast(message, type = "info") {
  const region = $("#toastRegion");
  const item = document.createElement("div");
  item.className = `toast ${type}`;
  const icon = type === "error" ? "circle-alert" : type === "success" ? "circle-check" : "info";
  item.innerHTML = `
    <i data-lucide="${icon}"></i>
    <span>${escapeHtml(message)}</span>
    <button class="icon-button small" type="button" aria-label="关闭通知"><i data-lucide="x"></i></button>
  `;
  $("button", item).addEventListener("click", () => item.remove());
  region.appendChild(item);
  refreshIcons();
  window.setTimeout(() => item.remove(), 5000);
}

function prettyJson(value) {
  return JSON.stringify(value ?? {}, null, 2);
}

function compactDetails(details) {
  const entries = Object.entries(details || {});
  if (!entries.length) return "--";
  return entries
    .slice(0, 3)
    .map(([key, value]) => `${key}=${typeof value === "object" ? JSON.stringify(value) : value}`)
    .join(" · ");
}

function shortId(value) {
  if (!value) return "--";
  return value.length > 22 ? `${value.slice(0, 12)}…${value.slice(-6)}` : value;
}

function formatDate(value) {
  if (!value) return "--";
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit" }).format(new Date(value));
}

function formatDateTime(value) {
  if (!value) return "--";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatTime(value) {
  if (!value) return "--";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function formatDuration(start, end) {
  if (!start || !end) return "--";
  return formatMilliseconds(Math.max(0, new Date(end) - new Date(start)));
}

function formatMilliseconds(value) {
  if (value === null || value === undefined) return "--";
  if (value < 1000) return `${Math.round(value)}ms`;
  return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)}s`;
}

function emptyTableRow(columns, message) {
  return `<tr class="empty-row"><td colspan="${columns}">${escapeHtml(message)}</td></tr>`;
}

function slugify(value) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function cssEscape(value) {
  if (window.CSS?.escape) return window.CSS.escape(value);
  return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
}
