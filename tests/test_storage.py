from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mizuki.favorites import FavoriteStore
from mizuki.models import Post
from mizuki.settings import Settings


def make_post(post_id: str = "1") -> Post:
    return Post(
        id=post_id,
        source="danbooru",
        source_name="Danbooru",
        thumb_url="https://example.test/t.jpg",
        preview_url="https://example.test/p.jpg",
        file_url="https://example.test/f.jpg",
        page_url="https://example.test/1",
        tags=("1girl",),
    )


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "settings.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_defaults(self) -> None:
        settings = Settings(self.path)
        self.assertTrue(settings.get("safe_search"))
        self.assertTrue(settings.get("sidebar_visible"))
        self.assertEqual(settings.get("grid_size"), "comfortable")
        self.assertFalse(self.path.exists())

    def test_persist_and_reload(self) -> None:
        settings = Settings(self.path)
        settings.set("safe_search", False)
        settings.set("grid_size", "large")
        reloaded = Settings(self.path)
        self.assertFalse(reloaded.get("safe_search"))
        self.assertEqual(reloaded.get("grid_size"), "large")

    def test_validation_clamps_bad_values(self) -> None:
        self.path.write_text(
            json.dumps({"grid_size": "enormous", "sort": "sideways", "accent": "neon"})
        )
        settings = Settings(self.path)
        self.assertEqual(settings.get("grid_size"), "comfortable")
        self.assertEqual(settings.get("sort"), "newest")
        self.assertEqual(settings.get("accent"), "mauve")

    def test_subscription_and_no_op(self) -> None:
        settings = Settings(self.path)
        calls = []
        settings.subscribe("safe_search", lambda key, value: calls.append((key, value)))
        settings.set("safe_search", False)
        settings.set("safe_search", False)
        self.assertEqual(calls, [("safe_search", False)])

    def test_recent_searches_are_unique_and_capped(self) -> None:
        settings = Settings(self.path)
        for index in range(20):
            settings.add_recent_search(f"tag {index}")
        settings.add_recent_search("TAG 5")
        recent = settings.get("recent_searches")
        self.assertEqual(len(recent), 12)
        self.assertEqual(recent[0], "TAG 5")
        self.assertEqual(sum(1 for item in recent if item.lower() == "tag 5"), 1)

    def test_remove_recent_search(self) -> None:
        settings = Settings(self.path)
        settings.add_recent_search("landscape")
        settings.add_recent_search("Zenless Zone Zero")
        settings.remove_recent_search("landscape")
        self.assertEqual(settings.get("recent_searches"), ["Zenless Zone Zero"])
        settings.remove_recent_search("missing")
        self.assertEqual(settings.get("recent_searches"), ["Zenless Zone Zero"])

    def test_unknown_key_rejected(self) -> None:
        settings = Settings(self.path)
        with self.assertRaises(KeyError):
            settings.set("definitely_not_a_setting", 1)

    def test_set_validates_value_immediately(self) -> None:
        settings = Settings(self.path)
        settings.set("grid_size", "enormous")
        self.assertEqual(settings.get("grid_size"), "comfortable")
        settings.set("accent", "neon")
        self.assertEqual(settings.get("accent"), "mauve")
        reloaded = Settings(self.path)
        self.assertEqual(reloaded.get("grid_size"), "comfortable")

    def test_set_coerces_boolean_flags(self) -> None:
        settings = Settings(self.path)
        settings.set("safe_search", "yes")
        self.assertIs(settings.get("safe_search"), True)

    def test_defaults_are_not_shared_with_instances(self) -> None:
        settings = Settings(self.path)
        settings.get("enabled_sources").append("mutant")
        settings.get("recent_searches").append("mutant")
        self.assertNotIn("mutant", Settings(self.path).get("enabled_sources"))
        self.assertNotIn("mutant", Settings(self.path).get("recent_searches"))

    def test_set_notifies_with_the_validated_value(self) -> None:
        settings = Settings(self.path)
        seen = []
        settings.subscribe("grid_size", lambda key, value: seen.append(value))
        settings.set("grid_size", "bogus")
        self.assertEqual(seen, [])
        settings.set("grid_size", "large")
        self.assertEqual(seen, ["large"])

    def test_source_filters_round_trip(self) -> None:
        settings = Settings(self.path)
        settings.set_source_filter("danbooru", "1girl, landscape")
        settings.set_source_filter("zerochan", "genshin impact")
        reloaded = Settings(self.path)
        self.assertEqual(reloaded.get_source_filter("danbooru"), "1girl, landscape")
        self.assertEqual(reloaded.get_source_filter("zerochan"), "genshin impact")
        settings.set_source_filter("danbooru", "")
        self.assertEqual(settings.get_source_filter("danbooru"), "")
        self.assertNotIn("danbooru", settings.get("source_filters"))


class FavoriteStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "favorites.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_toggle_add_remove(self) -> None:
        store = FavoriteStore(self.path)
        post = make_post()
        self.assertFalse(store.contains(post))
        self.assertTrue(store.toggle(post))
        self.assertTrue(store.contains(post))
        self.assertEqual(store.count(), 1)
        self.assertFalse(store.toggle(post))
        self.assertEqual(store.count(), 0)

    def test_persistence(self) -> None:
        store = FavoriteStore(self.path)
        store.toggle(make_post("7"))
        reloaded = FavoriteStore(self.path)
        self.assertEqual([post.id for post in reloaded.all()], ["7"])

    def test_keys(self) -> None:
        store = FavoriteStore(self.path)
        store.toggle(make_post("7"))
        store.toggle(make_post("9"))
        self.assertEqual(store.keys(), {"danbooru:7", "danbooru:9"})

    def test_clear(self) -> None:
        store = FavoriteStore(self.path)
        store.toggle(make_post("1"))
        store.toggle(make_post("2"))
        store.clear()
        self.assertEqual(store.count(), 0)
        reloaded = FavoriteStore(self.path)
        self.assertEqual(reloaded.count(), 0)


if __name__ == "__main__":
    unittest.main()
