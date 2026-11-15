"""Optional structured progress for callers following a pipeline run."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal, Protocol

ProgressStatus = Literal["running", "completed", "failed", "info"]


class ProgressCallback(Protocol):
    def __call__(
        self, label: str, *, status: ProgressStatus = "info", detail: str | None = None
    ) -> None: ...


def report(
    callback: ProgressCallback | None,
    label: str,
    *,
    status: ProgressStatus = "info",
    detail: str | None = None,
) -> None:
    if callback is not None:
        callback(label, status=status, detail=detail)


@dataclass
class ProgressStep:
    status: Literal["completed", "failed"] = "completed"
    detail: str | None = None

    def completed(self, detail: str | None = None) -> None:
        self.status, self.detail = "completed", detail

    def failed(self, detail: str | None = None) -> None:
        self.status, self.detail = "failed", detail


@contextmanager
def progress_step(
    callback: ProgressCallback | None, label: str, *, detail: str | None = None
) -> Iterator[ProgressStep]:
    step = ProgressStep(detail=detail)
    report(callback, label, status="running", detail=detail)
    try:
        yield step
    except Exception:
        if step.status != "failed":
            step.failed("This step could not finish.")
        raise
    finally:
        report(callback, label, status=step.status, detail=step.detail)
