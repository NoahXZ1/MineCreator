"""Structured operation feedback and cooperative cancellation, independent of UI."""

from dataclasses import dataclass, field
from threading import Event
from typing import Callable


class OperationCancelled(Exception):
    """Stop at a safe checkpoint, preserving any completed work."""


@dataclass(frozen=True)
class OperationEvent:
    operation: str
    kind: str
    message: str
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return dict(operation=self.operation, kind=self.kind, message=self.message, data=self.data)


def console_event(event: OperationEvent) -> None:
    print(event.message, flush=True)


class OperationContext:
    def __init__(self, operation: str, sink: Callable[[OperationEvent], None] = console_event):
        self.operation = operation
        self.sink = sink
        self._cancel = Event()

    def report(self, message: str, *, kind: str = 'status', **data) -> None:
        self.sink(OperationEvent(self.operation, kind, message, data))

    def cancel(self) -> None:
        self._cancel.set()

    def check(self) -> None:
        if self._cancel.is_set():
            raise OperationCancelled('Operation cancelled; completed changes remain in place.')

    def pause(self, seconds: float) -> None:
        self._cancel.wait(seconds)
        self.check()


def operation_context(context: OperationContext | None, operation: str) -> OperationContext:
    return context if context is not None else OperationContext(operation)
