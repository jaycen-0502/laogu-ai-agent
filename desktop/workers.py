from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    finished = Signal(object)
    error = Signal(str)
    progress = Signal(int, str)
    done = Signal()


class FunctionWorker(QRunnable):
    def __init__(self, function: Callable[..., Any], *args: Any, **kwargs: Any):
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.function(*self.args, **self.kwargs)
        except Exception as exc:
            details = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            self.signals.error.emit(details)
        else:
            self.signals.finished.emit(result)
        finally:
            self.signals.done.emit()
