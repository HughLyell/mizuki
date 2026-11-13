"""Background search orchestration for one or more sources."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor

from gi.repository import GLib

from ..models import Post, SearchQuery
from ..sources import Source, SourceError

log = logging.getLogger(__name__)

# posts, has_more, errors, done
ResultsCallback = Callable[[list[Post], bool, list[str], bool], None]
ErrorCallback = Callable[[str], None]
SimpleCallback = Callable[[], None]
NoticeCallback = Callable[[str], None]


class _SearchState:
    """Mutable per-search bookkeeping shared between worker callbacks."""

    __slots__ = ("pending", "pages_received", "has_more", "errors", "seen")

    def __init__(self, pending: int) -> None:
        self.pending = pending
        self.pages_received = 0
        self.has_more = False
        self.errors: list[str] = []
        self.seen: set[str] = set()


class SearchController:
    """Run searches off the UI thread and stream results back to it.

    Each source's page is delivered as soon as it arrives instead of waiting
    for the slowest source to finish. A generation counter drops late results
    when the user starts another search before the previous one completes.
    """

    def __init__(self, workers: int = 6) -> None:
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mizuki-search")
        self._generation = 0
        self._lock = threading.Lock()

    def search(
        self,
        sources: Sequence[Source],
        query: SearchQuery,
        *,
        on_start: SimpleCallback | None = None,
        on_results: ResultsCallback,
        on_error: ErrorCallback,
        on_finished: SimpleCallback | None = None,
        on_notice: NoticeCallback | None = None,
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

        state = _SearchState(pending=len(sources))
        for source in sources:
            future = self._executor.submit(source.search, query)
            future.add_done_callback(
                lambda completed, src=source: self._on_source_done(
                    generation, state, src, completed,
                    on_results, on_error, on_finished, on_notice,
                )
            )

    def _on_source_done(
        self,
        generation: int,
        state: _SearchState,
        source: Source,
        future,
        on_results: ResultsCallback,
        on_error: ErrorCallback,
        on_finished: SimpleCallback | None,
        on_notice: NoticeCallback | None,
    ) -> None:
        page = None
        error: str | None = None
        try:
            page = future.result()
        except SourceError as exc:
            log.warning("Source %s failed: %s", source.id, exc)
            error = f"{source.name}: {exc}"
        except Exception as exc:  # pragma: no cover - third-party sources
            log.exception("Source %s failed", source.id)
            error = f"{source.name}: {exc}"
        else:
            log.info(
                "Source %s returned %d posts (has_more=%s)",
                source.id, len(page.posts), page.has_more,
            )
        GLib.idle_add(
            self._deliver, generation, state, source, page, error,
            on_results, on_error, on_finished, on_notice,
        )

    def _deliver(
        self,
        generation: int,
        state: _SearchState,
        source: Source,
        page,
        error: str | None,
        on_results: ResultsCallback,
        on_error: ErrorCallback,
        on_finished: SimpleCallback | None,
        on_notice: NoticeCallback | None,
    ):
        with self._lock:
            if generation != self._generation:
                return False
        if error is not None:
            state.errors.append(error)
        if page is not None and page.notice:
            log.warning("Source %s notice: %s", source.id, page.notice)
            if on_notice is not None:
                try:
                    on_notice(page.notice)
                except Exception:
                    log.exception("Search notice callback failed")
        new_posts: list[Post] = []
        if page is not None:
            state.pages_received += 1
            state.has_more = state.has_more or page.has_more
            for post in page.posts:
                if post.key in state.seen:
                    continue
                state.seen.add(post.key)
                new_posts.append(post)
        state.pending -= 1
        done = state.pending == 0
        try:
            if done and state.pages_received == 0:
                on_error(
                    state.errors[0]
                    if state.errors
                    else "Every source failed. Check your connection and try again."
                )
            else:
                if done:
                    log.info(
                        "Search finished: %d unique posts from %d source(s) (%d errors)",
                        len(state.seen), state.pages_received, len(state.errors),
                    )
                on_results(new_posts, state.has_more, list(state.errors), done)
        except Exception:  # pragma: no cover - defensive UI boundary
            log.exception("Could not deliver search results")
        finally:
            if done and on_finished is not None:
                try:
                    on_finished()
                except Exception:
                    log.exception("Search finish callback failed")
        return False

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
