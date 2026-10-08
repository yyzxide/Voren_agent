const STORAGE_KEY = "voren.web.session.v1";
const state = {
  run: null,
  source: null,
  events: new Map(),
  submission: null,
  decision: null,
  renderedMessages: new Set(),
  knowledgeSearch: null,
  knowledgeBusy: false,
};

const $ = (id) => document.getElementById(id);
const form = $("request-form");
const input = $("request-input");
const sendButton = $("send-button");

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof body.detail === "string"
    ? body.detail : (response.status === 422 ? "请求参数不符合约束" : `HTTP ${response.status}`));
  return body;
}

function loadSession() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {};
  } catch (_error) {
    return {};
  }
}

function saveSession() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    run_id: state.run?.run_id || null,
    submission: state.submission,
    decision: state.decision,
  }));
}

function appendMessage(role, text) {
  $("empty-state").classList.add("hidden");
  const messages = $("messages");
  messages.classList.add("active");
  const item = document.createElement("div");
  item.className = `message ${role}`;
  item.textContent = text;
  messages.appendChild(item);
  messages.scrollTop = messages.scrollHeight;
}

function renderRun(run) {
  if (state.run?.run_id !== run.run_id) state.renderedMessages.clear();
  state.run = run;
  if (state.decision && state.decision.proposal_digest !== run.proposal?.digest) {
    state.decision = null;
  }
  saveSession();
  renderMemoryContext(run.memory_versions || []);
  renderSkillRoute(run.skill_routing);
  renderKnowledgeContext(run.knowledge_corpus, run.recovery_reason);
  if (run.final_text) appendOnce("agent", run.final_text);
  if (run.error_code) {
    appendOnce("system", `运行停止：${run.error_code}${run.error_detail_code ? ` / ${run.error_detail_code}` : ""}`);
  }
  if (run.receipt) {
    appendOnce(
      "system",
      `动作回执：${run.receipt.status} · 精确副作用核验 ${run.receipt.verification.passed ? "通过" : "失败"}`,
    );
  }
  if (run.decision_approved === false) {
    appendOnce("system", "你已拒绝本次提议；该动作没有执行，先前已完成的动作保留在回执中。");
  }
  renderHistory(run.action_history || []);
  $("resume-card").classList.toggle("hidden", run.recovery_required || !(
    run.status === "running" || (run.status === "completed" && !run.final_text)
  ));
  renderApproval(run);
  openEvents(run);
}

function appendOnce(role, text) {
  const key = `${role}:${text}`;
  if (state.renderedMessages.has(key)) return;
  state.renderedMessages.add(key);
  appendMessage(role, text);
}

function renderHistory(history) {
  $("history-card").classList.toggle("hidden", !history.length);
  const list = $("action-history");
  list.replaceChildren();
  history.forEach((action, index) => {
    const item = document.createElement("article");
    item.className = "effect";
    const title = document.createElement("strong");
    title.textContent = `${index + 1}. ${action.proposal.action_name}`;
    const detail = document.createElement("p");
    detail.textContent = action.receipt
      ? `${action.receipt.status} · 精确副作用核验 ${action.receipt.verification.passed ? "通过" : "未通过"}`
      : (action.decision_approved === false ? "已拒绝" : "等待审批");
    const identity = document.createElement("code");
    identity.textContent = action.proposal.operation_id;
    item.append(title, detail, identity);
    list.appendChild(item);
  });
}

function renderMemoryContext(versions) {
  const label = $("memory-context");
  if (!versions.length) {
    label.textContent = "Memory：无 Active Profile";
    return;
  }
  const selected = versions
    .map((ref) => `${ref.memory_id}@${ref.version_id.slice(0, 12)}`)
    .join(", ");
  label.textContent = `Memory：${selected}`;
}

function renderSkillRoute(route) {
  const label = $("skill-route");
  if (!route) {
    label.textContent = "Skill：旧运行未记录路由信息";
    return;
  }
  if (route.selected_versions.length) {
    const selected = route.selected_versions
      .map((ref) => `${ref.name}@${ref.version_id.slice(0, 12)}`)
      .join(", ");
    label.textContent = `Skill：${selected} · ${route.mode}`;
    return;
  }
  const suffix = route.ambiguous_skills.length
    ? ` · 歧义：${route.ambiguous_skills.join(", ")}`
    : "";
  label.textContent = `Skill：no_skill · ${route.mode}${suffix}`;
}

function renderKnowledgeContext(corpus, recoveryReason = null) {
  const label = $("knowledge-context");
  if (!corpus) {
    label.textContent = recoveryReason === "knowledge_evidence_unavailable"
      ? "知识版本：原任务的资料快照无法确认，请恢复冻结证据"
      : "知识版本：旧运行未保存快照，沿用活动资料检索";
  } else if (!corpus.refs.length) {
    label.textContent = "知识版本：已冻结空资料集，后续新增资料不进入此任务";
  } else {
    label.textContent = `知识版本：${corpus.refs.map((ref) => `${ref.document_id}@${ref.version_id}`).join(", ")}`;
  }
}

function renderApproval(run) {
  const card = $("approval-card");
  const reconciling = run.status === "needs_reconciliation";
  const knowledgeBlocked = continuationConfigurationBlocked(run);
  if (knowledgeBlocked) {
    const reasons = {
      knowledge_configuration_changed: "知识检索配置与任务原配置不一致，请恢复原检索模式和模型配置。",
      knowledge_evidence_unavailable: "原任务的知识版本或向量索引不可用，请恢复冻结的资料和索引。",
      model_configuration_changed: "生成模型配置与任务原配置不一致，请恢复原模型及 Provider 配置。",
      transcript_unavailable: "原任务的加密模型检查点无法读取，请恢复原密钥与检查点。",
      workspace_contract_changed: "工作区适配器或动作契约与任务原版本不一致，请恢复原适配器和动作契约。",
    };
    appendOnce("system", `${reasons[run.recovery_reason]}${reconciling
      ? "恢复后可核对已执行动作，系统不会重发该动作。"
      : "恢复前任务暂停；尚未执行的提议仍可拒绝。"}`);
  }
  const mismatch = reconciling && (
    run.receipt?.verification.unexpected_effect_ids.length
    || run.receipt?.verification.mismatched_effect_ids.length
  );
  if (mismatch) {
    card.classList.add("hidden");
    appendOnce("system", "结果与批准内容不符，需要人工核对；系统不会重发该动作。");
    return;
  }
  if ((!reconciling && run.status !== "waiting_approval") || !run.proposal || (run.recovery_required && !knowledgeBlocked)) {
    card.classList.add("hidden");
    if (run.recovery_required && !knowledgeBlocked) {
      appendOnce("system", run.workspace === "google"
        ? "当前 Google 连接无法恢复此任务，请检查原账号、日历、时区和连接配置。已完成动作仍保留在回执中。"
        : "当前任务的原工作区无法恢复，任务已暂停。请先核对已完成的动作；不要重复执行。AgentDojo 演示的外部状态仅保存在原进程中。");
    }
    return;
  }
  card.classList.remove("hidden");
  $("approval-label").textContent = knowledgeBlocked ? "请恢复原任务配置或证据" : (reconciling ? "等待结果核对" : "等待你的决定");
  $("approve-button").textContent = reconciling ? "重新核对结果" : "批准这些副作用";
  $("approve-button").disabled = knowledgeBlocked;
  $("reject-button").disabled = false;
  $("reject-button").classList.toggle("hidden", reconciling || (knowledgeBlocked && run.decision_approved === true));
  $("proposal-digest").textContent = run.proposal.digest;
  const effects = $("effects");
  effects.replaceChildren();
  for (const effect of run.proposal.effects) {
    const item = document.createElement("article");
    item.className = "effect";
    const title = document.createElement("strong");
    title.textContent = `${effect.kind} · ${effect.resource}`;
    const list = document.createElement("dl");
    for (const [key, value] of Object.entries(effect.attributes)) {
      const term = document.createElement("dt");
      term.textContent = key;
      const description = document.createElement("dd");
      description.textContent = typeof value === "string" ? value : JSON.stringify(value);
      list.append(term, description);
    }
    item.append(title, list);
    effects.appendChild(item);
  }
}

function openEvents(run) {
  if (state.source) state.source.close();
  state.events.clear();
  renderEvents();
  const source = new EventSource(`/api/runs/${encodeURIComponent(run.run_id)}/events/stream`);
  state.source = source;
  source.onmessage = receiveEvent;
  const names = [
    "run.created", "run.started", "memory_context.assembled", "skill_context.assembled",
    "model.requested", "model.responded", "tool.called", "tool.observed",
    "action.proposed", "run.waiting_approval", "approval.accepted", "approval.rejected",
    "action.receipt", "action.reconciled", "run.completed", "run.failed", "run.cancelled",
    "action.result_consumed", "run.continued", "run.needs_reconciliation",
  ];
  for (const name of names) source.addEventListener(name, receiveEvent);
  source.onerror = () => {
    if (state.run && ["completed", "failed", "cancelled", "limit_exceeded", "needs_reconciliation"].includes(state.run.status)) {
      source.close();
    }
  };
}

function receiveEvent(event) {
  const payload = JSON.parse(event.data);
  state.events.set(payload.sequence, payload);
  renderEvents();
}

function renderEvents() {
  const list = $("event-list");
  const events = [...state.events.values()].sort((a, b) => a.sequence - b.sequence);
  $("event-count").textContent = String(events.length);
  list.replaceChildren();
  if (!events.length) {
    const item = document.createElement("li");
    item.className = "placeholder";
    item.textContent = "等待运行事件…";
    list.appendChild(item);
    return;
  }
  for (const event of events) {
    const item = document.createElement("li");
    const name = document.createElement("span");
    name.className = "event-name";
    name.textContent = event.event_type;
    const meta = document.createElement("span");
    meta.textContent = `#${event.sequence}`;
    item.append(name, meta);
    list.appendChild(item);
  }
}

async function submitRequest(event) {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  setBusy(true);
  appendMessage("user", text);
  input.value = "";
  if (!state.submission || state.submission.request !== text) {
    state.submission = { client_request_id: crypto.randomUUID(), request: text };
    saveSession();
  }
  try {
    const run = await api("/api/runs", {
      method: "POST",
      body: JSON.stringify(state.submission),
    });
    state.submission = null;
    renderRun(run);
  } catch (error) {
    input.value = text;
    saveSession();
    appendMessage("system", `请求失败：${error.message}`);
  } finally {
    setBusy(false);
  }
}

async function decide(approved) {
  if (!state.run?.proposal) return;
  if (approved && continuationConfigurationBlocked(state.run)) return;
  setDecisionBusy(true);
  if (
    !state.decision
    || state.decision.run_id !== state.run.run_id
    || state.decision.proposal_digest !== state.run.proposal.digest
    || state.decision.approved !== approved
  ) {
    state.decision = {
      run_id: state.run.run_id,
      decision_id: state.run.decision_id || crypto.randomUUID(),
      proposal_digest: state.run.proposal.digest,
      approved,
    };
    saveSession();
  }
  try {
    const run = await api(`/api/runs/${encodeURIComponent(state.run.run_id)}/decision`, {
      method: "POST",
      body: JSON.stringify({
        decision_id: state.decision.decision_id,
        proposal_digest: state.decision.proposal_digest,
        approved: state.decision.approved,
      }),
    });
    state.decision = null;
    renderRun(run);
  } catch (error) {
    saveSession();
    appendMessage("system", `决定未生效：${error.message}`);
  } finally {
    setDecisionBusy(false);
  }
}

async function resumeRun() {
  if (!state.run) return;
  $("resume-button").disabled = true;
  try {
    renderRun(await api(`/api/runs/${encodeURIComponent(state.run.run_id)}/resume`, { method: "POST" }));
  } catch (error) {
    appendMessage("system", `继续执行失败：${error.message}`);
  } finally {
    $("resume-button").disabled = false;
  }
}

function setBusy(busy) {
  sendButton.disabled = busy;
  sendButton.textContent = busy ? "运行中…" : "开始执行";
}

function setDecisionBusy(busy) {
  $("approve-button").disabled = busy || continuationConfigurationBlocked(state.run);
  $("reject-button").disabled = busy;
}

function continuationConfigurationBlocked(run) {
  return Boolean(run?.recovery_required && [
    "knowledge_configuration_changed", "knowledge_evidence_unavailable",
    "model_configuration_changed", "transcript_unavailable",
    "workspace_contract_changed",
  ].includes(run.recovery_reason));
}

function knowledgeNode(tag, text, className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function knowledgeSource(source, quote, start, end, hitId) {
  const item = knowledgeNode("article", "", "knowledge-source");
  item.append(
    knowledgeNode("strong", source.title),
    knowledgeNode("p", `来源：${source.source_uri}`, "context-state"),
    knowledgeNode("code", `${source.ref.document_id}@${source.ref.version_id}`),
    knowledgeNode("p", `字符位置：[${start}, ${end})`, "context-state"),
    knowledgeNode("blockquote", quote),
  );
  if (hitId) item.append(knowledgeNode("code", `hit_id: ${hitId}`));
  return item;
}

function updateKnowledgeButton() {
  const online = $("knowledge-online").checked;
  const searched = state.knowledgeSearch;
  const current = $("knowledge-question").value.trim();
  $("knowledge-draft").disabled = online || state.knowledgeBusy;
  $("knowledge-search-button").disabled = state.knowledgeBusy;
  $("knowledge-online").disabled = state.knowledgeBusy;
  $("knowledge-answer-button").textContent = online ? "发送一次在线问答请求" : "验证本地提案";
  $("knowledge-answer-button").disabled = state.knowledgeBusy || !searched
    || searched.question !== current || (!online && !$("knowledge-draft").value.trim());
}

async function searchKnowledge(event) {
  event.preventDefault();
  const question = $("knowledge-question").value.trim();
  if (!question || state.knowledgeBusy) return;
  state.knowledgeBusy = true;
  state.knowledgeSearch = null;
  $("knowledge-answer").replaceChildren();
  $("knowledge-sources").replaceChildren();
  $("knowledge-search-status").textContent = "正在检索原文…";
  updateKnowledgeButton();
  try {
    const result = await api("/api/knowledge/search", {
      method: "POST", body: JSON.stringify({ question, limit: 5 }),
    });
    state.knowledgeSearch = result;
    $("knowledge-search-status").textContent = result.hits.length
      ? `检索到 ${result.hits.length} 个片段，问答将使用这份资料快照。排序不代表答案充分。`
      : "本次资料快照没有匹配片段；问答会拒答，不调用模型。";
    for (const hit of result.hits) {
      $("knowledge-sources").append(knowledgeSource(hit, hit.snippet, hit.chunk_start, hit.chunk_end, hit.chunk_id));
    }
  } catch (error) {
    $("knowledge-search-status").textContent = `检索失败：${error.message}`;
  } finally {
    state.knowledgeBusy = false;
    updateKnowledgeButton();
  }
}

function renderKnowledgeAnswer(result) {
  const target = $("knowledge-answer");
  target.replaceChildren();
  const statuses = {
    answered: "回答提案通过引用校验", abstained: "拒答：未输出回答声明",
    rejected: "提案被拒绝：格式或引用不合法", failed: "问答失败：不能解释为资料中没有答案",
    cancelled: "问答已取消",
  };
  target.append(
    knowledgeNode("h3", statuses[result.status] || result.status),
    knowledgeNode("p", result.proposal_mode === "operator_draft"
      ? "结果来自你输入的本地提案，没有请求在线模型。"
      : (result.usage.requests_attempted ? "已尝试一次在线模型请求。" : "未请求在线模型。")),
    knowledgeNode("p", "语义支持尚未验证（semantic_support=unverified）；真实引用仍可能不能支撑声明。", "knowledge-boundary"),
  );
  if (result.reason) target.append(knowledgeNode("p", `原因：${result.reason}`));
  if (result.error_code) target.append(knowledgeNode("p", `错误状态：${result.error_code}`));
  for (const claim of result.claims) {
    target.append(knowledgeNode("p", claim.text, "knowledge-claim"));
    for (const citation of claim.citations) {
      target.append(knowledgeSource(citation, citation.quote, citation.start, citation.end));
    }
  }
}

async function askKnowledge(event) {
  event.preventDefault();
  const searched = state.knowledgeSearch;
  if (state.knowledgeBusy || !searched || searched.question !== $("knowledge-question").value.trim()) return;
  const online = $("knowledge-online").checked;
  const draft = $("knowledge-draft").value;
  if (!online && !draft.trim()) return;
  state.knowledgeBusy = true;
  updateKnowledgeButton();
  $("knowledge-answer").replaceChildren(knowledgeNode("p", online ? "正在请求模型并校验引用…" : "正在校验本地提案…"));
  try {
    const result = await api("/api/knowledge/ask", {
      method: "POST", body: JSON.stringify({
        question: searched.question, limit: 5, corpus: searched.corpus,
        draft: online ? null : draft, allow_model_api: online,
      }),
    });
    renderKnowledgeAnswer(result);
  } catch (error) {
    $("knowledge-answer").replaceChildren(knowledgeNode("p", `问答请求未完成：${error.message}。这不是资料无答案的结论。`));
  } finally {
    state.knowledgeBusy = false;
    updateKnowledgeButton();
  }
}

async function loadHealth() {
  try {
    const health = await api("/api/health");
    $("health-dot").classList.add("ok");
    $("health-text").textContent = `Runtime 已连接 · ${health.mode} · ${health.workspace} · Profiles ${health.active_profile_count} · Skills ${health.active_skill_count}`;
    if (health.mode === "demo") {
      $("mode-banner").textContent = "确定性 AgentDojo 演示：无需 API Key，不代表真实模型质量";
    } else {
      const modelState = health.live_model_configured ? "模型已配置" : "缺少模型配置";
      const workspaceState = health.workspace_configured ? "Workspace 已配置" : "缺少 Workspace 配置";
      $("mode-banner").textContent = `Live · ${health.workspace}：${modelState}；${workspaceState}`;
    }
  } catch (error) {
    $("health-text").textContent = "Runtime 不可用";
    $("mode-banner").textContent = error.message;
  }
}

async function restoreSession() {
  const saved = loadSession();
  state.submission = saved.submission || null;
  state.decision = saved.decision || null;
  if (state.submission) input.value = state.submission.request;
  if (!saved.run_id) return;
  try {
    const run = await api(`/api/runs/${encodeURIComponent(saved.run_id)}`);
    renderRun(run);
  } catch (_error) {
    localStorage.removeItem(STORAGE_KEY);
  }
}

form.addEventListener("submit", submitRequest);
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) form.requestSubmit();
});
$("example-button").addEventListener("click", () => {
  input.value = "根据徒步邮件安排日程，再发确认邮件，每个动作都让我确认。";
  input.focus();
});
$("approve-button").addEventListener("click", () => decide(true));
$("reject-button").addEventListener("click", () => decide(false));
$("resume-button").addEventListener("click", resumeRun);
$("knowledge-search-form").addEventListener("submit", searchKnowledge);
$("knowledge-answer-form").addEventListener("submit", askKnowledge);
$("knowledge-question").addEventListener("input", updateKnowledgeButton);
$("knowledge-draft").addEventListener("input", updateKnowledgeButton);
$("knowledge-online").addEventListener("change", updateKnowledgeButton);
loadHealth();
restoreSession();
