from __future__ import annotations

from collections.abc import AsyncIterator

import orjson
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, ORJSONResponse
from sse_starlette.sse import EventSourceResponse

from router_app.config_source import ConfigSourceError
from router_app.core.errors import PlannerModelError, PlannerRejectedError, SessionOwnershipError
from router_app.core.schemas import BusinessFrame, CompletionRequest, HandoffRequest, MessageRequest
from router_app.core.service import RouterService

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> ORJSONResponse:
    service = _service(request)
    payload = await service.ready()
    return ORJSONResponse(payload, status_code=200 if payload["status"] == "ready" else 503)


@router.get("/debug", response_class=HTMLResponse)
async def debug_page() -> str:
    return DEBUG_PAGE_HTML


@router.post("/api/v1/message")
async def message(request_body: MessageRequest, request: Request):
    service = _service(request)
    try:
        frame = await service.handle_message(request_body)
    except SessionOwnershipError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConfigSourceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PlannerModelError as exc:
        # 模型服务、鉴权、网络等外部依赖失败属于临时不可用，统一映射为 503。
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PlannerRejectedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if request_body.stream:
        return EventSourceResponse(_frame_events(frame, debug=request_body.debug_trace))
    return frame


@router.post("/api/v1/task/handoff")
async def task_handoff(request_body: HandoffRequest, request: Request):
    service = _service(request)
    try:
        return await service.handle_handoff(request_body)
    except SessionOwnershipError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConfigSourceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/v1/task/completion")
async def task_completion(request_body: CompletionRequest, request: Request):
    service = _service(request)
    try:
        frame = await service.handle_completion(request_body)
    except SessionOwnershipError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConfigSourceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if request_body.stream:
        return EventSourceResponse(_frame_events(frame, debug=request_body.debug_trace))
    return frame


def _service(request: Request) -> RouterService:
    return request.app.state.router_service


async def _frame_events(frame: BusinessFrame, *, debug: bool) -> AsyncIterator[dict[str, str]]:
    business_frame = frame.model_copy(update={"trace": None})
    yield {"event": "message", "data": _json(business_frame)}
    if debug and frame.trace:
        for event in frame.trace:
            yield {"event": "trace", "data": event.model_dump_json(by_alias=True)}
    yield {"event": "done", "data": _json(business_frame)}


def _json(frame: BusinessFrame) -> str:
    return orjson.dumps(frame.model_dump(by_alias=True, mode="json", exclude_none=True)).decode("utf-8")


DEBUG_PAGE_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>Router Debug</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 24px; max-width: 1180px; }
    label { display: block; margin: 8px 0 4px; }
    input, textarea, button { font: inherit; box-sizing: border-box; }
    input, textarea { width: 100%; padding: 6px; }
    textarea { min-height: 84px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    button { margin: 8px 8px 8px 0; padding: 6px 10px; cursor: pointer; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .panel { border: 1px solid #bbb; padding: 12px; }
    pre { background: #111; color: #eee; padding: 12px; overflow: auto; min-height: 260px; }
  </style>
</head>
<body>
  <h1>Router Debug</h1>
  <div class="grid">
    <div class="panel">
      <label>routerBase</label>
      <input id="routerBase" value="" placeholder="默认当前站点" />
      <label>configBase</label>
      <input id="configBase" value="http://127.0.0.1:18080" />
      <label>sessionId</label>
      <input id="sessionId" value="demo-1" />
      <label>cust_no</label>
      <input id="custNo" value="c1" />
      <label>用户输入</label>
      <textarea id="txt">给小明转 200 元，然后查一下余额</textarea>
      <label><input id="debugTrace" type="checkbox" /> debugTrace</label>
      <label><input id="autoRun" type="checkbox" checked /> 自动执行 TODO</label>
      <button onclick="sendMessage()">POST /api/v1/message</button>
      <button onclick="healthz()">GET /healthz</button>
      <button onclick="readyz()">GET /readyz</button>

      <label>taskId</label>
      <input id="taskId" placeholder="从返回 tasks 中复制，或点下方按钮自动取 currentTaskId" />
      <button onclick="useCurrentTask()">使用 currentTaskId</button>
      <button onclick="sendCompletion(1)">阶段完成</button>
      <button onclick="sendCompletion(2)">最终完成</button>
      <button onclick="runTodoList()">执行 TODO 列表</button>

      <label>子智能体 endpointUrl</label>
      <input id="endpointUrl" placeholder="http://127.0.0.1:9000/agent/task" />
      <label><input id="mockDispatch" type="checkbox" checked /> 无 endpoint 时 mock 子智能体执行</label>
      <label>extraPayload JSON</label>
      <textarea id="extraPayload">{}</textarea>
      <button onclick="prepareHandoff(false)">准备 handoff payload</button>
      <button onclick="prepareHandoff(true)">调用子智能体接口</button>

      <label>skillId / version / referenceKey</label>
      <input id="skillId" value="skill_transfer" />
      <input id="skillVersion" value="v1" />
      <input id="referenceKey" value="transfer/limits" />
      <button onclick="loadSkillIndex()">GET /v1/router/skills/index</button>
      <button onclick="loadSkillBody()">GET /v1/router/skills/{skillId}/body</button>
      <button onclick="loadSkillRaw()">GET /v1/router/skills/{skillId}/raw</button>
      <button onclick="loadReference()">GET /v1/router/references/{referenceKey}</button>
    </div>
    <div class="panel">
      <button onclick="clearOutput()">清空输出</button>
      <pre id="output"></pre>
    </div>
  </div>
  <script>
    let lastFrame = null;
    let lastTodoList = [];

    function base() {
      return {
        sessionId: document.getElementById('sessionId').value,
        cust_no: document.getElementById('custNo').value,
        debugTrace: document.getElementById('debugTrace').checked
      };
    }

    function routerUrl(path) {
      const baseUrl = document.getElementById('routerBase').value.trim();
      return baseUrl ? `${baseUrl.replace(/\\/$/, '')}${path}` : path;
    }

    function configUrl(path) {
      return `${document.getElementById('configBase').value.trim().replace(/\\/$/, '')}${path}`;
    }

    async function call(method, url, body) {
      const options = { method, headers: {} };
      if (body) {
        options.headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(body);
      }
      const res = await fetch(url, options);
      const text = await res.text();
      let data;
      try { data = JSON.parse(text); } catch { data = text; }
      print({ status: res.status, data });
      if (data && data.todoList) {
        lastFrame = data;
        lastTodoList = data.todoList;
        printTodo(lastTodoList);
      } else if (lastTodoList.length) {
        printTodo(lastTodoList);
      }
      return data;
    }

    async function healthz() {
      await call('GET', routerUrl('/healthz'));
    }

    async function readyz() {
      await call('GET', routerUrl('/readyz'));
    }

    async function sendMessage() {
      const body = { ...base(), txt: document.getElementById('txt').value };
      const data = await call('POST', routerUrl('/api/v1/message'), body);
      if (data.currentTaskId) document.getElementById('taskId').value = data.currentTaskId;
      if (document.getElementById('autoRun').checked) {
        await runTodoList();
      }
    }

    function useCurrentTask() {
      if (lastFrame && lastFrame.currentTaskId) {
        document.getElementById('taskId').value = lastFrame.currentTaskId;
      }
    }

    async function sendCompletion(signal) {
      return call('POST', routerUrl('/api/v1/task/completion'), {
        ...base(),
        taskId: document.getElementById('taskId').value,
        completionSignal: signal
      });
    }

    async function prepareHandoff(dispatch) {
      let extraPayload = {};
      try { extraPayload = JSON.parse(document.getElementById('extraPayload').value || '{}'); }
      catch (err) { print({ error: 'extraPayload 不是合法 JSON' }); return; }
      return call('POST', routerUrl('/api/v1/task/handoff'), {
        ...base(),
        taskId: document.getElementById('taskId').value,
        dispatch,
        mockDispatch: dispatch && document.getElementById('mockDispatch').checked && !document.getElementById('endpointUrl').value.trim(),
        endpointUrl: document.getElementById('endpointUrl').value || null,
        extraPayload
      });
    }

    async function runTodoList() {
      let guard = 0;
      while (lastFrame && lastFrame.todoList && guard < 20) {
        guard += 1;
        const current = lastFrame.currentTask || lastFrame.todoList.find(item => item.current) || lastFrame.todoList[0];
        if (!current) {
          print({ autoRun: '没有当前 TODO' });
          return;
        }
        document.getElementById('taskId').value = current.taskId;
        printTodo(lastFrame.todoList);
        const missingSlots = current.missingSlots || [];
        if (lastFrame.status === 'collecting_slots' || missingSlots.length) {
          print({ autoRun: '当前任务仍需补槽', taskId: current.taskId, missingSlots: current.missingSlots || [] });
          return;
        }
        if (lastFrame.status === 'handoff_ready' && current.status === 'waiting') {
          const hasEndpoint = Boolean(document.getElementById('endpointUrl').value.trim());
          const shouldDispatch = hasEndpoint || document.getElementById('mockDispatch').checked;
          const handoff = await prepareHandoff(shouldDispatch);
          if (document.getElementById('debugTrace').checked) print({ autoRunHandoff: handoff });
          if (handoff && handoff.status === 'doing' && handoff.accepted) {
            await sendCompletion(2);
            continue;
          }
          if (handoff && handoff.status === 'excepted') {
            print({ autoRun: '子智能体接口调用异常，已停止', taskId: current.taskId });
            return;
          }
          print({ autoRun: '已生成 handoff payload，等待子智能体执行或 completion 回调', taskId: current.taskId });
          return;
        }
        if (current.status === 'doing') {
          await sendCompletion(2);
          continue;
        }
        if (current.status === 'excepted') {
          print({ autoRun: '当前任务异常，已停止', taskId: current.taskId });
          return;
        }
        if (lastFrame.status === 'completed') {
          print({ autoRun: 'TODO 已全部完成' });
          return;
        }
        break;
      }
    }

    async function loadSkillIndex() {
      return call('GET', configUrl('/v1/router/skills/index'));
    }

    async function loadSkillBody() {
      const skillId = document.getElementById('skillId').value;
      const version = document.getElementById('skillVersion').value;
      return call('GET', configUrl(`/v1/router/skills/${encodeURIComponent(skillId)}/body?version=${encodeURIComponent(version)}`));
    }

    async function loadSkillRaw() {
      const skillId = document.getElementById('skillId').value;
      return call('GET', configUrl(`/v1/router/skills/${encodeURIComponent(skillId)}/raw`));
    }

    async function loadReference() {
      const key = document.getElementById('referenceKey').value;
      const version = document.getElementById('skillVersion').value;
      return call('GET', configUrl(`/v1/router/references/${encodeURIComponent(key)}?version=${encodeURIComponent(version)}`));
    }

    function print(value) {
      const output = document.getElementById('output');
      output.textContent += JSON.stringify(value, null, 2) + '\\n\\n';
      output.scrollTop = output.scrollHeight;
    }

    function printTodo(todoList) {
      const lines = todoList.map(item => {
        const mark = item.current ? '>' : ' ';
        const missing = item.missingSlots && item.missingSlots.length ? ` missing=${item.missingSlots.join(',')}` : '';
        return `${mark} ${item.order}. ${item.name} [${item.status}]${missing}`;
      });
      print({ todoListText: lines.join('\\n') });
    }

    function clearOutput() {
      document.getElementById('output').textContent = '';
    }
  </script>
</body>
</html>
"""
