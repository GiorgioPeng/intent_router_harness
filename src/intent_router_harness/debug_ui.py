from __future__ import annotations


def validator_html() -> str:
    """Return the lightweight browser UI for streaming assistant validation."""
    return _VALIDATOR_HTML


_VALIDATOR_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Intent Router 验证台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --surface: #ffffff;
      --surface-strong: #f0f5ff;
      --line: #d7dde8;
      --text: #172033;
      --muted: #647084;
      --blue: #2563eb;
      --teal: #0f766e;
      --amber: #b45309;
      --red: #b91c1c;
      --green: #15803d;
      --shadow: 0 10px 28px rgba(23, 32, 51, 0.08);
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }

    button,
    input,
    select,
    textarea {
      font: inherit;
    }

    button {
      border: 1px solid transparent;
      border-radius: 8px;
      padding: 0 14px;
      min-height: 38px;
      background: var(--blue);
      color: #ffffff;
      cursor: pointer;
    }

    button.secondary {
      background: #ffffff;
      color: var(--text);
      border-color: var(--line);
    }

    button.success {
      background: var(--teal);
    }

    button.danger {
      background: #ffffff;
      color: var(--red);
      border-color: #fecaca;
    }

    button:disabled {
      opacity: 0.48;
      cursor: not-allowed;
    }

    label {
      display: grid;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
    }

    input,
    select,
    textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
      color: var(--text);
      padding: 10px 12px;
      outline: none;
    }

    input:focus,
    select:focus,
    textarea:focus {
      border-color: var(--blue);
      box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.14);
    }

    textarea {
      min-height: 76px;
      resize: vertical;
      line-height: 1.5;
    }

    .app {
      display: grid;
      grid-template-rows: auto 1fr;
      min-height: 100vh;
    }

    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 14px 18px;
      background: var(--surface);
      border-bottom: 1px solid var(--line);
    }

    .brand {
      display: grid;
      gap: 2px;
    }

    .brand strong {
      font-size: 17px;
      font-weight: 700;
    }

    .brand span,
    .status-line {
      color: var(--muted);
      font-size: 12px;
    }

    .status-line {
      display: flex;
      align-items: center;
      gap: 8px;
      white-space: nowrap;
    }

    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--muted);
    }

    .dot.busy {
      background: var(--amber);
    }

    .dot.ready {
      background: var(--green);
    }

    .dot.error {
      background: var(--red);
    }

    .workspace {
      display: grid;
      grid-template-columns: minmax(0, 1.5fr) minmax(340px, 0.8fr);
      gap: 16px;
      padding: 16px;
      min-height: 0;
    }

    .main,
    .side {
      display: grid;
      gap: 16px;
      align-content: start;
      min-width: 0;
    }

    .panel {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      min-width: 0;
    }

    .panel-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
    }

    .panel-header h2 {
      margin: 0;
      font-size: 14px;
      font-weight: 700;
    }

    .panel-body {
      padding: 14px;
    }

    .controls {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }

    .input-row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: end;
      margin-top: 12px;
    }

    .toggle-row,
    .actions {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 10px;
    }

    .toggle-row label {
      display: flex;
      grid-template-columns: none;
      align-items: center;
      gap: 8px;
      color: var(--text);
      font-size: 13px;
    }

    .toggle-row input {
      width: auto;
    }

    .conversation {
      display: grid;
      gap: 10px;
      max-height: calc(100vh - 330px);
      overflow: auto;
      padding-right: 4px;
    }

    .empty {
      color: var(--muted);
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 18px;
      text-align: center;
      background: #fbfcfe;
    }

    .bubble {
      display: grid;
      gap: 8px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #ffffff;
    }

    .bubble.user {
      border-color: #bfdbfe;
      background: #eff6ff;
    }

    .bubble.message {
      border-color: #c7d2fe;
    }

    .bubble.trace {
      border-color: #fde68a;
      background: #fffbeb;
    }

    .bubble.error {
      border-color: #fecaca;
      background: #fef2f2;
    }

    .bubble-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      color: var(--muted);
      font-size: 12px;
    }

    .bubble-title {
      color: var(--text);
      font-weight: 700;
    }

    .bubble-text {
      line-height: 1.55;
      word-break: break-word;
    }

    .kv {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }

    .tag {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      border-radius: 6px;
      padding: 2px 8px;
      background: #eef2ff;
      color: #3730a3;
      font-size: 12px;
      word-break: break-word;
    }

    .tag.ok {
      background: #dcfce7;
      color: #166534;
    }

    .tag.warn {
      background: #fef3c7;
      color: #92400e;
    }

    .tag.err {
      background: #fee2e2;
      color: #991b1b;
    }

    pre {
      margin: 0;
      overflow: auto;
      max-height: 280px;
      border-radius: 8px;
      background: #101827;
      color: #dbeafe;
      padding: 10px;
      font-size: 12px;
      line-height: 1.45;
      white-space: pre-wrap;
      word-break: break-word;
    }

    details summary {
      cursor: pointer;
      color: var(--blue);
      font-size: 12px;
    }

    .runtime-grid {
      display: grid;
      gap: 10px;
    }

    .runtime-box {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fbfcfe;
    }

    .runtime-box h3 {
      margin: 0 0 8px;
      font-size: 13px;
    }

    .task-row {
      border-top: 1px solid var(--line);
      padding: 8px 0;
    }

    .task-row:first-child {
      border-top: 0;
      padding-top: 0;
    }

    .task-row:last-child {
      padding-bottom: 0;
    }

    .muted {
      color: var(--muted);
    }

    @media (max-width: 900px) {
      .workspace,
      .controls,
      .input-row {
        grid-template-columns: 1fr;
      }

      .topbar {
        align-items: flex-start;
        flex-direction: column;
      }

      .conversation {
        max-height: none;
      }
    }
  </style>
</head>
<body>
  <div class="app">
    <header class="topbar">
      <div class="brand">
        <strong>Intent Router 验证台</strong>
        <span>同源调用 /api/v1/message 和 /api/v1/task/completion，按 SSE 增量展示。</span>
      </div>
      <div class="status-line">
        <span id="statusDot" class="dot ready"></span>
        <span id="statusText">ready</span>
      </div>
    </header>

    <main class="workspace">
      <section class="main">
        <div class="panel">
          <div class="panel-header">
            <h2>请求参数</h2>
            <div class="actions">
              <button id="newSessionBtn" type="button" class="secondary">新会话</button>
              <button id="clearBtn" type="button" class="danger">清空输出</button>
            </div>
          </div>
          <div class="panel-body">
            <div class="controls">
              <label>
                Session ID
                <input id="sessionId" autocomplete="off">
              </label>
              <label>
                用户标识 custID
                <input id="custID" value="C0001" autocomplete="off">
              </label>
              <label>
                执行模式
                <select id="executionMode">
                  <option value="router_only">router_only</option>
                  <option value="execute">execute</option>
                </select>
              </label>
              <label>
                快速样例
                <select id="sampleSelect">
                  <option value="">选择后填入输入框</option>
                  <option value="我要转账">我要转账</option>
                  <option value="给小明转账200元">给小明转账200元</option>
                  <option value="我先给王阳明转账，然后再给李正义转账">多任务转账</option>
                  <option value="第一次给100元">第一次给100元</option>
                  <option value="第二次给200元">第二次给200元</option>
                </select>
              </label>
            </div>
            <div class="input-row">
              <label>
                用户输入
                <textarea id="messageText" placeholder="输入要发送给 router 的消息，例如：我先给王阳明转账，然后再给李正义转账"></textarea>
              </label>
              <button id="sendBtn" type="button">发送消息</button>
            </div>
            <div class="toggle-row" style="margin-top: 12px;">
              <label><input id="debugTrace" type="checkbox" checked> 输出 trace 流</label>
              <label><input id="showDetails" type="checkbox"> 默认展开 JSON</label>
            </div>
          </div>
        </div>

        <div class="panel">
          <div class="panel-header">
            <h2>流式对话</h2>
            <div class="actions">
              <button id="completeBtn" type="button" class="success" disabled>模拟完成当前任务</button>
            </div>
          </div>
          <div class="panel-body">
            <div id="conversation" class="conversation">
              <div class="empty">还没有事件。发送消息后，这里会按 SSE 到达顺序显示 trace、message 和 done。</div>
            </div>
          </div>
        </div>
      </section>

      <aside class="side">
        <div class="panel">
          <div class="panel-header">
            <h2>当前运行态</h2>
          </div>
          <div class="panel-body runtime-grid">
            <div class="runtime-box">
              <h3>当前任务</h3>
              <div id="currentTaskView" class="muted">暂无 current_task</div>
            </div>
            <div class="runtime-box">
              <h3>任务列表</h3>
              <div id="taskListView" class="muted">暂无 task_list</div>
            </div>
            <div class="runtime-box">
              <h3>上下文生命周期</h3>
              <div id="contextLifecycleView" class="muted">暂无上下文加载/释放事件</div>
            </div>
            <div class="runtime-box">
              <h3>最近业务帧</h3>
              <pre id="lastFrameView">{}</pre>
            </div>
          </div>
        </div>

        <div class="panel">
          <div class="panel-header">
            <h2>原始请求</h2>
          </div>
          <div class="panel-body">
            <pre id="lastRequestView">{}</pre>
          </div>
        </div>
      </aside>
    </main>
  </div>

  <script>
    const state = {
      currentTask: null,
      taskList: [],
      lastFrame: null,
      contextEvents: [],
      busy: false,
    };

    const els = {
      statusDot: document.getElementById("statusDot"),
      statusText: document.getElementById("statusText"),
      sessionId: document.getElementById("sessionId"),
      custID: document.getElementById("custID"),
      executionMode: document.getElementById("executionMode"),
      sampleSelect: document.getElementById("sampleSelect"),
      messageText: document.getElementById("messageText"),
      debugTrace: document.getElementById("debugTrace"),
      showDetails: document.getElementById("showDetails"),
      sendBtn: document.getElementById("sendBtn"),
      completeBtn: document.getElementById("completeBtn"),
      newSessionBtn: document.getElementById("newSessionBtn"),
      clearBtn: document.getElementById("clearBtn"),
      conversation: document.getElementById("conversation"),
      currentTaskView: document.getElementById("currentTaskView"),
      taskListView: document.getElementById("taskListView"),
      contextLifecycleView: document.getElementById("contextLifecycleView"),
      lastFrameView: document.getElementById("lastFrameView"),
      lastRequestView: document.getElementById("lastRequestView"),
    };

    function newSessionId() {
      return "ui_" + Date.now().toString(36);
    }

    function initSession() {
      const existing = localStorage.getItem("intent_router_validator_session");
      els.sessionId.value = existing || newSessionId();
      localStorage.setItem("intent_router_validator_session", els.sessionId.value);
    }

    function setStatus(kind, text) {
      els.statusDot.className = "dot " + kind;
      els.statusText.textContent = text;
    }

    function setBusy(value) {
      state.busy = value;
      els.sendBtn.disabled = value;
      updateRuntime();
    }

    function clearEmpty() {
      const empty = els.conversation.querySelector(".empty");
      if (empty) {
        empty.remove();
      }
    }

    function appendBubble(kind, title, text, payload) {
      clearEmpty();
      const node = document.createElement("article");
      node.className = "bubble " + kind;
      const time = new Date().toLocaleTimeString();
      const shouldOpen = els.showDetails.checked ? " open" : "";
      const details = payload === undefined ? "" : `
        <details${shouldOpen}>
          <summary>JSON</summary>
          <pre>${escapeHtml(JSON.stringify(payload, null, 2))}</pre>
        </details>
      `;
      node.innerHTML = `
        <div class="bubble-head">
          <span class="bubble-title">${escapeHtml(title)}</span>
          <span>${escapeHtml(time)}</span>
        </div>
        <div class="bubble-text">${escapeHtml(text || "")}</div>
        ${payload ? renderTags(payload) : ""}
        ${details}
      `;
      els.conversation.appendChild(node);
      els.conversation.scrollTop = els.conversation.scrollHeight;
    }

    function renderTags(payload) {
      if (!payload || typeof payload !== "object") {
        return "";
      }
      const tags = [];
      if (payload.status) tags.push(["status", payload.status, tagClassForStatus(payload.status)]);
      if (payload.intent_code) tags.push(["intent", payload.intent_code, ""]);
      if (payload.completion_reason) tags.push(["reason", payload.completion_reason, ""]);
      if (payload.stage) tags.push(["stage", payload.stage, ""]);
      if (payload.current_task && payload.current_task.taskId) tags.push(["task", payload.current_task.taskId, ""]);
      if (!tags.length) {
        return "";
      }
      return `<div class="kv">${tags.map(([key, value, cls]) => `<span class="tag ${cls}">${escapeHtml(key)}=${escapeHtml(String(value))}</span>`).join("")}</div>`;
    }

    function tagClassForStatus(status) {
      if (status === "failed" || status === "cancelled") return "err";
      if (status === "waiting_user_input" || status === "running") return "warn";
      return "ok";
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    function buildMessagePayload(text) {
      const sessionId = els.sessionId.value.trim() || newSessionId();
      els.sessionId.value = sessionId;
      localStorage.setItem("intent_router_validator_session", sessionId);
      const custID = els.custID.value.trim() || "C0001";
      return {
        sessionId,
        txt: text,
        custID,
        config_variables: [
          { name: "currentDisplay", value: "validator_page" },
        ],
        executionMode: els.executionMode.value,
        stream: true,
        debugTrace: els.debugTrace.checked,
      };
    }

    function buildCompletionPayload() {
      if (!state.currentTask || !state.currentTask.taskId) {
        return null;
      }
      return {
        sessionId: els.sessionId.value.trim(),
        custID: els.custID.value.trim() || "C0001",
        taskId: state.currentTask.taskId,
        completionSignal: 2,
        stream: true,
        debugTrace: els.debugTrace.checked,
      };
    }

    async function sendMessage() {
      const text = els.messageText.value.trim();
      if (!text) {
        setStatus("error", "请输入消息");
        return;
      }
      const payload = buildMessagePayload(text);
      appendBubble("user", "user", text, payload);
      els.messageText.value = "";
      await postSse("/api/v1/message", payload);
    }

    async function completeTask() {
      const payload = buildCompletionPayload();
      if (!payload) {
        setStatus("error", "没有可完成的 current_task");
        return;
      }
      appendBubble("user", "task completion", "模拟下游完成：" + payload.taskId, payload);
      await postSse("/api/v1/task/completion", payload);
    }

    async function postSse(path, payload) {
      setBusy(true);
      setStatus("busy", "streaming " + path);
      els.lastRequestView.textContent = JSON.stringify({ path, payload }, null, 2);
      try {
        const response = await fetch(path, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
          },
          body: JSON.stringify(payload),
        });
        if (!response.body) {
          throw new Error("当前浏览器未暴露 ReadableStream");
        }
        if (!response.ok) {
          appendBubble("error", "http error", "HTTP " + response.status, await safeJson(response));
          setStatus("error", "HTTP " + response.status);
          return;
        }
        await readSse(response.body);
        setStatus("ready", "ready");
      } catch (error) {
        appendBubble("error", "request error", error.message || String(error));
        setStatus("error", error.message || "error");
      } finally {
        setBusy(false);
      }
    }

    async function safeJson(response) {
      try {
        return await response.json();
      } catch (_error) {
        return { text: await response.text() };
      }
    }

    async function readSse(body) {
      const reader = body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary = buffer.indexOf("\\n\\n");
        while (boundary >= 0) {
          const rawFrame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          handleSseFrame(rawFrame);
          boundary = buffer.indexOf("\\n\\n");
        }
      }
      if (buffer.trim()) {
        handleSseFrame(buffer);
      }
    }

    function handleSseFrame(rawFrame) {
      const frame = parseSseFrame(rawFrame);
      if (!frame) return;
      if (frame.event === "done" || frame.data === "[DONE]") {
        appendBubble("message", "done", "[DONE]");
        return;
      }
      let payload = frame.data;
      try {
        payload = JSON.parse(frame.data);
      } catch (_error) {
        payload = { data: frame.data };
      }
      if (frame.event === "trace") {
        const title = payload.title || payload.stage || "trace";
        handleTraceFrame(payload);
        appendBubble("trace", title, payload.summary || payload.stage || "", payload);
        return;
      }
      if (frame.event === "error") {
        appendBubble("error", "error", payload.error ? payload.error.message : "stream error", payload);
        return;
      }
      handleBusinessFrame(payload);
    }

    function handleTraceFrame(payload) {
      if (!payload || typeof payload !== "object") {
        return;
      }
      const trackedStages = new Set([
        "skill_body_loaded",
        "reference_body_loaded",
        "prompt_context_released",
        "context_lease_released",
        "context_released",
      ]);
      if (!trackedStages.has(payload.stage)) {
        return;
      }
      state.contextEvents.push({
        stage: payload.stage,
        title: payload.title || payload.stage,
        summary: payload.summary || "",
        data: payload.data || {},
        time: new Date().toLocaleTimeString(),
      });
      if (state.contextEvents.length > 20) {
        state.contextEvents = state.contextEvents.slice(-20);
      }
      updateRuntime();
    }

    function parseSseFrame(rawFrame) {
      const lines = rawFrame.split(/\\r?\\n/);
      let event = "message";
      const data = [];
      for (const line of lines) {
        if (!line || line.startsWith(":")) continue;
        if (line.startsWith("event:")) {
          event = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          data.push(line.slice(5).trimStart());
        }
      }
      if (!data.length && !event) return null;
      return { event, data: data.join("\\n") };
    }

    function handleBusinessFrame(payload) {
      state.lastFrame = payload;
      if (Array.isArray(payload.task_list)) {
        state.taskList = payload.task_list;
      }
      if (payload.current_task) {
        state.currentTask = payload.current_task;
      } else if (payload.status === "completed" || payload.status === "cancelled" || payload.status === "failed") {
        state.currentTask = null;
      }
      const title = payload.stage === "intent_recognition" ? "intent recognition" : "message";
      const text = payload.message || payload.completion_reason || payload.status || "";
      appendBubble("message", title, text, payload);
      updateRuntime();
    }

    function updateRuntime() {
      const task = state.currentTask;
      if (task) {
        els.currentTaskView.innerHTML = `
          <div class="kv">
            <span class="tag">taskId=${escapeHtml(task.taskId || "")}</span>
            <span class="tag">intent=${escapeHtml(task.intent_code || "")}</span>
            <span class="tag ${tagClassForStatus(task.status || "")}">status=${escapeHtml(task.status || "")}</span>
          </div>
          <pre style="margin-top: 8px;">${escapeHtml(JSON.stringify(task.slot_memory || {}, null, 2))}</pre>
        `;
      } else {
        els.currentTaskView.textContent = "暂无 current_task";
      }
      if (state.taskList.length) {
        els.taskListView.innerHTML = state.taskList.map((taskItem, index) => `
          <div class="task-row">
            <div class="kv">
              <span class="tag">#${index + 1}</span>
              <span class="tag">${escapeHtml(taskItem.taskId || "")}</span>
              <span class="tag ${tagClassForStatus(taskItem.status || "")}">${escapeHtml(taskItem.status || "")}</span>
            </div>
            <div style="margin-top: 6px;">${escapeHtml(taskItem.title || taskItem.intent_code || "")}</div>
          </div>
        `).join("");
      } else {
        els.taskListView.textContent = "暂无 task_list";
      }
      renderContextLifecycle();
      els.lastFrameView.textContent = JSON.stringify(state.lastFrame || {}, null, 2);
      const canComplete = task && !state.busy && ["ready_for_dispatch", "waiting_assistant_completion"].includes(task.status);
      els.completeBtn.disabled = !canComplete;
    }

    function renderContextLifecycle() {
      if (!state.contextEvents.length) {
        els.contextLifecycleView.textContent = "暂无上下文加载/释放事件";
        return;
      }
      els.contextLifecycleView.innerHTML = state.contextEvents.map((event) => `
        <div class="task-row">
          <div class="kv">
            <span class="tag ${event.stage.includes("released") ? "ok" : "warn"}">${escapeHtml(event.stage)}</span>
            <span class="tag">${escapeHtml(event.time)}</span>
          </div>
          <div style="margin-top: 6px;">${escapeHtml(event.summary || event.title)}</div>
          ${renderContextEventDetail(event)}
        </div>
      `).join("");
    }

    function renderContextEventDetail(event) {
      const data = event.data || {};
      const values = [];
      if (data.skill) values.push("skill=" + data.skill);
      if (data.reference_id) values.push("reference=" + data.reference_id);
      if (Array.isArray(data.released_skill_bodies) && data.released_skill_bodies.length) {
        values.push("released skill bodies=" + data.released_skill_bodies.join(", "));
      }
      if (Array.isArray(data.released_reference_bodies) && data.released_reference_bodies.length) {
        values.push("released reference bodies=" + data.released_reference_bodies.join(", "));
      }
      if (Array.isArray(data.released_leases) && data.released_leases.length) {
        values.push("released leases=" + data.released_leases.map((lease) => lease.task_id || lease.intent_code || "lease").join(", "));
      }
      if (!values.length) {
        return "";
      }
      return `<div class="muted" style="margin-top: 6px; font-size: 12px;">${escapeHtml(values.join(" | "))}</div>`;
    }

    els.sendBtn.addEventListener("click", sendMessage);
    els.completeBtn.addEventListener("click", completeTask);
    els.newSessionBtn.addEventListener("click", () => {
      els.sessionId.value = newSessionId();
      localStorage.setItem("intent_router_validator_session", els.sessionId.value);
      state.currentTask = null;
      state.taskList = [];
      state.lastFrame = null;
      state.contextEvents = [];
      updateRuntime();
      setStatus("ready", "new session");
    });
    els.clearBtn.addEventListener("click", () => {
      els.conversation.innerHTML = '<div class="empty">已清空。新的 SSE 事件会继续显示在这里。</div>';
      setStatus("ready", "ready");
    });
    els.sampleSelect.addEventListener("change", () => {
      if (els.sampleSelect.value) {
        els.messageText.value = els.sampleSelect.value;
        els.messageText.focus();
      }
      els.sampleSelect.value = "";
    });
    els.messageText.addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        sendMessage();
      }
    });

    initSession();
    updateRuntime();
  </script>
</body>
</html>
"""
