"""Tests for the threaded search controller.

`mizuki.services.search` imports ``gi.repository.GLib``, which is unavailable
in a headless test environment, so a minimal fake module is installed before
importing the controller. The fake ``idle_add`` runs callbacks synchronously,
which makes the streamed deliveries deterministic to assert.
"""

from __future__ import annotations

import sys
import threading
import types
import unittest


def _install_fake_gi() -> None:
    gi = types.ModuleType("gi")
    repository = types.ModuleType("gi.repository")
    glib = types.ModuleType("gi.repository.GLib")

    def idle_add(function, *args, **kwargs):
        function(*args, **kwargs)
        return 1

    glib.idle_add = idle_add
    repository.GLib = glib
    gi.repository = repository
    sys.modules.setdefault("gi", gi)
    sys.modules.setdefault("gi.repository", repository)
    sys.modules.setdefault("gi.repository.GLib", glib)


_install_fake_gi()

from mizuki.models import Post, SearchQuery, SourcePage  # noqa: E402
from mizuki.services.search import SearchController  # noqa: E402
from mizuki.sources.base import Source, SourceError  # noqa: E402


class FakeSource(Source):
    def __init__(self, source_id: str, posts=(), error: Exception | None = None,
                 notice: str = "", has_more: bool = False, delay: float = 0.0) -> None:
        self.id = source_id
        self.name = source_id
        self._posts = list(posts)
        self._error = error
        self._notice = notice
        self._has_more = has_more
        self._delay = delay

    def search(self, query: SearchQuery) -> SourcePage:
        if self._delay:
            import time

            time.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return SourcePage(posts=list(self._posts), has_more=self._has_more, notice=self._notice)


def make_post(source: str, post_id: str) -> Post:
    return Post(
        id=post_id,
        source=source,
        source_name=source,
        thumb_url="",
        preview_url="",
        file_url="",
        page_url="",
    )


class SearchControllerTests(unittest.TestCase):
    def run_search(self, sources, query=None):
        controller = SearchController(workers=4)
        batches: list[tuple[list[str], bool, list[str], bool]] = []
        errors: list[str] = []
        finished = threading.Event()
        notices: list[str] = []

        controller.search(
            sources,
            query or SearchQuery(text="sky", safe=True),
            on_results=lambda posts, has_more, errs, done: batches.append(
                ([post.key for post in posts], has_more, errs, done)
            ),
            on_error=errors.append,
            on_finished=finished.set,
            on_notice=notices.append,
        )
        self.assertTrue(finished.wait(timeout=10), "search never finished")
        controller.shutdown()
        return batches, errors, notices

    def test_results_stream_from_each_source(self) -> None:
        slow = FakeSource("slow", [make_post("slow", "1")], delay=0.3)
        fast = FakeSource("fast", [make_post("fast", "1")])
        batches, errors, notices = self.run_search([slow, fast])
        self.assertEqual(errors, [])
        self.assertEqual(notices, [])
        self.assertTrue(batches)
        # The fast source must be delivered first, without waiting for slow.
        self.assertEqual(batches[0][0], ["fast:1"])
        self.assertFalse(batches[0][3])  # not done yet
        # The final batch reports done=True.
        self.assertTrue(batches[-1][3])
        delivered = [key for batch in batches for key in batch[0]]
        self.assertCountEqual(delivered, ["slow:1", "fast:1"])

    def test_duplicate_posts_across_sources_are_dropped(self) -> None:
        a = FakeSource("a", [make_post("x", "1"), make_post("x", "2")])
        b = FakeSource("b", [make_post("x", "1")])
        batches, _, _ = self.run_search([a, b])
        delivered = [key for batch in batches for key in batch[0]]
        self.assertCountEqual(delivered, ["x:1", "x:2"])

    def test_has_more_is_true_if_any_source_has_more(self) -> None:
        a = FakeSource("a", [make_post("a", "1")], has_more=True)
        b = FakeSource("b", [make_post("b", "1")], has_more=False)
        batches, _, _ = self.run_search([a, b])
        self.assertTrue(batches[-1][1])

    def test_partial_failure_still_delivers_results(self) -> None:
        ok = FakeSource("ok", [make_post("ok", "1")])
        broken = FakeSource("broken", error=SourceError("boom"))
        batches, errors, _ = self.run_search([ok, broken])
        self.assertEqual(errors, [])
        delivered = [key for batch in batches for key in batch[0]]
        self.assertEqual(delivered, ["ok:1"])
        self.assertTrue(batches[-1][2])  # error list reached on_results
        self.assertTrue(any("boom" in message for message in batches[-1][2]))

    def test_all_sources_failing_calls_on_error(self) -> None:
        a = FakeSource("a", error=SourceError("first failure"))
        b = FakeSource("b", error=SourceError("second failure"))
        batches, errors, _ = self.run_search([a, b])
        # Intermediate batches carry no posts; the final state is an error.
        self.assertTrue(batches)
        self.assertTrue(all(not posts for posts, _, _, _ in batches))
        # One of the two failures is reported; which one finishes first is
        # nondeterministic.
        self.assertEqual(len(errors), 1)
        self.assertIn("failure", errors[0])

    def test_empty_pages_are_not_errors(self) -> None:
        a = FakeSource("a", [])
        batches, errors, _ = self.run_search([a])
        self.assertEqual(errors, [])
        self.assertTrue(batches[-1][3])  # done

    def test_notices_are_forwarded(self) -> None:
        a = FakeSource("a", [make_post("a", "1")], notice="guests see fewer results")
        _, _, notices = self.run_search([a])
        self.assertEqual(notices, ["guests see fewer results"])

    def test_no_sources_enabled_errors(self) -> None:
        batches, errors, _ = self.run_search([])
        self.assertEqual(batches, [])
        self.assertTrue(errors and "No sources are enabled" in errors[0])

    def test_stale_generation_results_are_dropped(self) -> None:
        controller = SearchController(workers=4)
        delivered: list[list[str]] = []
        finished = threading.Event()
        slow = FakeSource("slow", [make_post("slow", "1")], delay=0.4)
        controller.search(
            [slow],
            SearchQuery(text="first", safe=True),
            on_results=lambda posts, has_more, errs, done: delivered.append(
                [post.key for post in posts]
            ),
            on_error=lambda message: delivered.append([message]),
            on_finished=finished.set,
        )
        # A newer search supersedes the first one before it finishes.
        fast = FakeSource("fast", [make_post("fast", "1")])
        second_finished = threading.Event()
        controller.search(
            [fast],
            SearchQuery(text="second", safe=True),
            on_results=lambda posts, has_more, errs, done: delivered.append(
                [post.key for post in posts]
            ),
            on_error=lambda message: delivered.append([message]),
            on_finished=second_finished.set,
        )
        self.assertTrue(second_finished.wait(timeout=10))
        controller.shutdown()
        self.assertEqual(delivered, [["fast:1"]])


if __name__ == "__main__":
    unittest.main()
