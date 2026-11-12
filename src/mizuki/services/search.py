"""Background search orchestration for one or more sources."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Sequence

from gi.repository import GLib

from ..models import Post, SearchQuery
from ..sources import Source, SourceError, merge_pages

log = logging.getLogger(__name__)

ResultsCallback = Callable[[list[Post], bool, list[str]], None]
ErrorCallback = Callable[[str], None]
SimpleCallback = Callable[[], None]


class SearchController:
    """Run searches off the UI thread and marshal results back to it.

    A generation counter drops late results when the user starts another search
    or changes source before the previous request finishes.
    """

    def __init__(self, workers: int = 6) -> None:
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mizuki-search")
        self._generation = 0
        self._lock = threading.Lock()

    def cancel(self) -> None:
        with self._lock:
            self._generation += 1

    def search(
        self,
        sources: Sequence[Source],
        query: SearchQuery,
        *,
        on_start: SimpleCallback | None = None,
        on_results: ResultsCallback,
        on_error: ErrorCallback,
        on_finished: SimpleCallback | None = None,
    ) -> None:
        sources = [source for source in sources if source is not None]
        with self._lock:
            self._generation += 1
            generation = self._generation
        if on_start is not None:
            on_start()
        if not sources:
            on_error("No sources are enabled. Enable at least one in Preferences.")
            if on_finished is not None:
                on_finished()
            return

        futures = [(source, self._executor.submit(source.search, query)) for source in sources]

        def orchestrate() -> None:
            pages = []
            errors: list[str] = []
            for source, future in futures:
                try:
                    pages.append(future.result())
                except SourceError as exc:
                    log.warning("Source %s failed: %s", source.id, exc)
                    errors.append(f"{source.name}: {exc}")
                except Exception as exc:  # pragma: no cover - third-party sources
                    log.exception("Source %s failed", source.id)
                    errors.append(f"{source.name}: {exc}")
            GLib.idle_add(self._deliver, generation, pages, errors, on_results, on_error, on_finished)

        threading.Thread(target=orchestrate, name="mizuki-search-wait", daemon=True).start()

    def _deliver(
        self,
        generation: int,
        pages,
        errors: list[str],
        on_results: ResultsCallback,
        on_error: ErrorCallback,
        on_finished: SimpleCallback | None,
    ):
        with self._lock:
            if generation != self._generation:
                return False
        try:
            if not pages:
                on_error(errors[0] if errors else "Every source failed. Check your connection and try again.")
                return False
            posts, has_more = merge_pages(pages, limit=1000)
            on_results(posts, has_more, errors)
        except Exception:  # pragma: no cover - defensive UI boundary
            log.exception("Could not deliver search results")
        finally:
            if on_finished is not None:
                try:
                    on_finished()
                except Exception:
                    log.exception("Search finish callback failed")
        return False

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
