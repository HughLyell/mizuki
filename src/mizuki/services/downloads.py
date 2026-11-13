"""Download queue with progress reporting."""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from gi.repository import GLib

from .. import net
from ..models import Post

log = logging.getLogger(__name__)

DownloadListener = Callable[[str, "DownloadTask"], None]


@dataclass
class DownloadTask:
    post: Post
    directory: Path
    path: Path
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    status: str = "queued"  # queued | downloading | done | failed | cancelled
    progress: float = 0.0
    error: str = ""
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def active(self) -> bool:
        return self.status in ("queued", "downloading")

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def status_label(self) -> str:
        if self.status == "queued":
            return "Waiting…"
        if self.status == "downloading":
            return f"{int(self.progress * 100)}%"
        if self.status == "done":
            return "Downloaded"
        if self.status == "cancelled":
            return "Cancelled"
        return self.error or "Failed"


class DownloadManager:
    def __init__(self, settings, *, workers: int = 3) -> None:
        self.settings = settings
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mizuki-download")
        self._tasks: list[DownloadTask] = []
        self._listeners: list[DownloadListener] = []
        self._lock = threading.Lock()
        self._reserved_paths: set[str] = set()

    def add_listener(self, listener: DownloadListener) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: DownloadListener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    @property
    def tasks(self) -> list[DownloadTask]:
        return list(self._tasks)

    @property
    def active_count(self) -> int:
        return sum(1 for task in self._tasks if task.active)

    def enqueue(self, post: Post) -> DownloadTask:
        directory = Path(str(self.settings.get("download_dir"))).expanduser()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            directory = Path.home() / "Downloads"
            log.warning("Falling back to %s: %s", directory, exc)
            directory.mkdir(parents=True, exist_ok=True)
        path = self._unique_path(directory, post.filename())
        task = DownloadTask(post=post, directory=directory, path=path)
        with self._lock:
            self._tasks.insert(0, task)
            self._reserved_paths.add(str(path))
        self._emit("added", task)
        self._executor.submit(self._run, task)
        return task

    def cancel(self, task: DownloadTask) -> None:
        if task.active:
            task.cancel_event.set()
            task.status = "cancelled"
            self._emit("updated", task)

    def clear_finished(self) -> int:
        with self._lock:
            finished = [task for task in self._tasks if not task.active]
            self._tasks = [task for task in self._tasks if task.active]
            for task in finished:
                self._reserved_paths.discard(str(task.path))
        for task in finished:
            self._emit("removed", task)
        return len(finished)

    def shutdown(self) -> None:
        for task in self._tasks:
            if task.active:
                task.cancel_event.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    # -- internals -------------------------------------------------------
    def _run(self, task: DownloadTask) -> None:
        if task.cancel_event.is_set():
            self._update(task, status="cancelled")
            return
        self._update(task, status="downloading", progress=0.0)
        last_update = 0.0

        def report(fraction: float) -> None:
            nonlocal last_update
            fraction = max(0.0, min(1.0, fraction))
            # Throttle UI wakeups while downloading.
            if fraction < 1.0 and fraction - last_update < 0.02:
                return
            last_update = fraction
            GLib.idle_add(self._progress, task, fraction)

        try:
            headers = {"Referer": task.post.referer} if task.post.referer else None
            net.download(
                task.post.file_url,
                task.path,
                headers=headers,
                progress=report,
                cancel=task.cancel_event,
            )
        except net.Cancelled:
            # net.download already removed its .part file; nothing else to do.
            self._update(task, status="cancelled")
        except net.NetworkError as exc:
            self._update(task, status="failed", error=str(exc))
        except Exception as exc:  # pragma: no cover - defensive worker
            log.exception("Download failed")
            self._update(task, status="failed", error=str(exc))
        else:
            self._update(task, status="done", progress=1.0)

    def _progress(self, task: DownloadTask, fraction: float):
        if task.status == "downloading":
            task.progress = fraction
            self._emit("updated", task)
        return False

    def _update(self, task: DownloadTask, **changes) -> None:
        for key, value in changes.items():
            setattr(task, key, value)
        GLib.idle_add(self._emit_idle, "updated", task)

    def _emit_idle(self, event: str, task: DownloadTask):
        self._emit(event, task)
        return False

    def _emit(self, event: str, task: DownloadTask) -> None:
        for listener in tuple(self._listeners):
            try:
                listener(event, task)
            except Exception:
                log.exception("Download listener failed")

    def _unique_path(self, directory: Path, filename: str) -> Path:
        candidate = directory / filename
        stem, suffix = candidate.stem, candidate.suffix
        counter = 1
        while True:
            with self._lock:
                if str(candidate) not in self._reserved_paths and not candidate.exists():
                    self._reserved_paths.add(str(candidate))
                    return candidate
            counter += 1
            candidate = directory / f"{stem}-{counter}{suffix}"
