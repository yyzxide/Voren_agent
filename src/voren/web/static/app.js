const STORAGE_KEY = "voren.web.session.v1";
const state = {
  run: null,
  source: null,
  events: new Map(),
  submission: null,
  decision: null,
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
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
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
  state.run = run;
  saveSession();
  renderMemoryContext(run.memory_versions || []);
  renderSkillRoute(run.skill_routing);
  if (run.final_text) appendMessage("agent", run.final_text);
  if (run.error_code) {
    appendMessage("system", `运行停止：${run.error_code}${run.error_detail_code ? ` / ${run.error_detail_code}` : ""}`);
  }
  if (run.receipt) {
    appendMessage(
      "system",
      `动作回执：${run.receipt.status} · 精确副作用核验 ${run.receipt.verification.passed ? "通过" : "失败"}`,
    );
  } else if (run.decision_approved === false) {
    appendMessage("system", "你已拒绝本次提议；没有执行外部写操作。");
  }
  renderApproval(run);
  openEvents(run);
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

function renderApproval(run) {
  const card = $("approval-card");
  if (run.status !== "waiting_approval" || !run.proposal || run.recovery_required) {
    card.classList.add("hidden");
    if (run.recovery_required) {
      appendMessage("system", "进程已重启，受控 Workspace Handle 不再存在；系统不会自动重发动作，请重新发起任务。");
    }
    return;
  }
  card.classList.remove("hidden");
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
  setDecisionBusy(true);
  if (
    !state.decision
    || state.decision.run_id !== state.run.run_id
    || state.decision.approved !== approved
  ) {
    state.decision = {
      run_id: state.run.run_id,
      decision_id: crypto.randomUUID(),
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

function setBusy(busy) {
  sendButton.disabled = busy;
  sendButton.textContent = busy ? "运行中…" : "开始执行";
}

function setDecisionBusy(busy) {
  $("approve-button").disabled = busy;
  $("reject-button").disabled = busy;
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
  input.value = "根据徒步邮件安排日程，并在执行前让我确认。";
  input.focus();
});
$("approve-button").addEventListener("click", () => decide(true));
$("reject-button").addEventListener("click", () => decide(false));
loadHealth();
restoreSession();
