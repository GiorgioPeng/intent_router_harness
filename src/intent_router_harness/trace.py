from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from intent_router_harness.contracts import AssistantTraceEvent


TraceSink = Callable[[AssistantTraceEvent], None]

_trace_sink: ContextVar[TraceSink | None] = ContextVar("intent_router_trace_sink", default=None)


def emit_trace(event: AssistantTraceEvent) -> None:
    """Emit a trace event to the active request stream, when one exists."""
    sink = _trace_sink.get()
    if sink is not None:
        sink(event)


@contextmanager
def trace_sink(sink: TraceSink | None) -> Iterator[None]:
    """Install a per-request trace sink for synchronous code paths."""
    token = _trace_sink.set(sink)
    try:
        yield
    finally:
        _trace_sink.reset(token)
