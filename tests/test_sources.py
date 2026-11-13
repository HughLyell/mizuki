from __future__ import annotations

import time
import unittest

from mizuki import net
from mizuki.models import Post, SearchQuery, SourcePage
from mizuki.sources.base import SourceError, merge_pages
from mizuki.sources.danbooru import DanbooruSource
from mizuki.sources.moebooru import KonachanSource, moebooru_rating
from mizuki.sources.safebooru import SafebooruSource
from mizuki.sources.zerochan import ZerochanSource, _GalleryParser

ZEROCHAN_FIXTURE = """
<ul id="thumbs2" class="medium-thumbs">
<li class="" data-id="4424191" >
  <div style="position: relative; ">
    <a class="thumb" href="/4424191">
      <img data-src="https://s3.zerochan.net/240/41/33/4424191.avif"
        alt="Frieren"
        title="4800&#10005;3592 4891kb jpg&#10;GIBUN&#10;Uploaded by PhoenixNight"
        width="240" height="180">
    </a>
    <a data-id="4424191" class="fav"><b>59</b></a>
    <p>
      <a href="/Frieren">Frieren</a>
      <a href="https://static.zerochan.net/Frieren.full.4424191.jpg">download</a>
    </p>
  </div>
</li>
<li class="" data-id="4176792" >
  <div style="position: relative; ">
    <a class="thumb" href="/4176792">
      <img data-src="https://s3.zerochan.net/240/42/35/4176792.avif"
        alt="Cross-Over"
        title="1920&#10005;1080 1167kb jpg&#10;Sharpshooter52"
        width="240" height="135">
    </a>
    <p><a href="/Cross-Over">Cross-Over</a></p>
  </div>
</li>
</ul>
"""


class DanbooruTagTests(unittest.TestCase):
    def test_safe_search_injects_general_rating(self) -> None:
        source = DanbooruSource()
        query = SearchQuery(text="1girl landscape", safe=True)
        self.assertEqual(
            source.search_tags(query),
            ["1girl", "landscape", "rating:g"],
        )

    def test_nsfw_search_does_not_filter_rating(self) -> None:
        query = SearchQuery(text="1girl", safe=False)
        self.assertEqual(DanbooruSource().search_tags(query), ["1girl"])

    def test_user_rating_metatag_is_stripped(self) -> None:
        query = SearchQuery(text="1girl rating:explicit order:score", safe=True)
        self.assertNotIn("rating:explicit", DanbooruSource().search_tags(query))

    def test_sort_maps_to_order_metatag(self) -> None:
        query = SearchQuery(text="sky", sort="score")
        self.assertIn("order:score", DanbooruSource().search_tags(query))
        query = SearchQuery(text="sky", sort="random")
        self.assertIn("order:random", DanbooruSource().search_tags(query))

    def test_safebooru_always_filters(self) -> None:
        source = SafebooruSource()
        query = SearchQuery(text="sky", safe=False)
        self.assertEqual(source.search_tags(query), ["sky"])
        self.assertFalse(source.supports_nsfw)

    def test_safebooru_server_sort_tags(self) -> None:
        source = SafebooruSource()
        self.assertIn(
            "sort:score:desc",
            source.search_tags(SearchQuery(text="landscape", sort="score")),
        )
        self.assertIn(
            "sort:random",
            source.search_tags(SearchQuery(text="landscape", sort="random")),
        )

    def test_konachan_safe_rating(self) -> None:
        self.assertIn("rating:safe", KonachanSource().search_tags(SearchQuery(text="sky", safe=True)))

    def test_moebooru_rating_mapping(self) -> None:
        self.assertEqual(moebooru_rating("s"), "safe")
        self.assertEqual(moebooru_rating("q"), "questionable")
        self.assertEqual(moebooru_rating("e"), "explicit")

    def test_multiword_phrase_fallback(self) -> None:
        source = DanbooruSource()
        query = SearchQuery(text="Zenless Zone Zero")
        self.assertEqual(source.fallback_phrase_tags(query), ["Zenless_Zone_Zero"])
        self.assertIsNone(source.fallback_phrase_tags(SearchQuery(text="landscape")))
        self.assertIsNone(source.fallback_phrase_tags(SearchQuery(text="landscape, sky")))

    def test_comma_groups_become_explicit_tags(self) -> None:
        source = DanbooruSource()
        query = SearchQuery(text="Hatsune Miku, landscape")
        self.assertEqual(source.query_tags(query), ["Hatsune_Miku", "landscape"])
        self.assertEqual(
            source.display_tags("Hatsune Miku, landscape"),
            ["Hatsune Miku", "landscape"],
        )
        self.assertEqual(
            source.join_tags(["Hatsune Miku", "landscape"]),
            "Hatsune Miku, landscape",
        )

    def test_two_word_phrase_is_tried_first(self) -> None:
        source = DanbooruSource()
        variants = source.search_variants(SearchQuery(text="hatsune miku"))
        self.assertEqual(variants[0], ["hatsune_miku"])
        self.assertEqual(variants[1], ["hatsune", "miku"])

    def test_exclusions_do_not_become_phrase(self) -> None:
        source = DanbooruSource()
        variants = source.search_variants(SearchQuery(text="landscape -monochrome"))
        self.assertEqual(variants, [["landscape", "-monochrome"]])


class DanbooruTagBudgetTests(unittest.TestCase):
    """The anonymous API allows two terms per query; `order:` counts, `rating:` does not."""

    def test_order_term_dropped_with_two_tags(self) -> None:
        source = DanbooruSource()
        query = SearchQuery(text="1girl landscape", safe=True, sort="score")
        self.assertEqual(
            source.search_tags(query),
            ["1girl", "landscape", "rating:g"],
        )

    def test_order_term_kept_with_one_tag(self) -> None:
        source = DanbooruSource()
        query = SearchQuery(text="1girl", safe=True, sort="score")
        self.assertEqual(
            source.search_tags(query),
            ["1girl", "rating:g", "order:score"],
        )

    def test_order_term_dropped_with_two_tags_and_no_safe_filter(self) -> None:
        source = DanbooruSource()
        query = SearchQuery(text="1girl landscape", safe=False, sort="random")
        self.assertEqual(source.search_tags(query), ["1girl", "landscape"])

    def test_single_tag_random_keeps_order(self) -> None:
        source = DanbooruSource()
        query = SearchQuery(text="sky", safe=False, sort="random")
        self.assertEqual(source.search_tags(query), ["sky", "order:random"])


class SearchErrorPropagationTests(unittest.TestCase):
    """A hard error must not be hidden behind an earlier empty variant."""

    @staticmethod
    def make_post() -> Post:
        return Post(
            id="1",
            source="danbooru",
            source_name="Danbooru",
            thumb_url="",
            preview_url="",
            file_url="",
            page_url="",
        )

    def test_error_not_masked_by_empty_variant(self) -> None:
        source = DanbooruSource()

        def fake_search(query, tags):
            if len(tags) == 1:
                return SourcePage(posts=[], has_more=False)
            raise SourceError("HTTP 422: TagLimitError")

        source._search_tags = fake_search  # type: ignore[method-assign]
        with self.assertRaises(SourceError):
            source.search(SearchQuery(text="hatsune miku", safe=True))

    def test_empty_page_returned_when_no_error(self) -> None:
        source = DanbooruSource()
        source._search_tags = lambda query, tags: SourcePage(posts=[], has_more=False)  # type: ignore[method-assign]
        page = source.search(SearchQuery(text="nothing", safe=True))
        self.assertEqual(page.posts, [])

    def test_posts_returned_immediately(self) -> None:
        source = DanbooruSource()
        source._search_tags = lambda query, tags: SourcePage(posts=[self.make_post()], has_more=True)  # type: ignore[method-assign]
        page = source.search(SearchQuery(text="x", safe=True))
        self.assertEqual(len(page.posts), 1)

    def test_safebooru_error_not_masked(self) -> None:
        source = SafebooruSource()

        def fake_search(query, tags):
            if len(tags) == 1:
                return SourcePage(posts=[], has_more=False)
            raise SourceError("HTTP 503")

        source._search_tags = fake_search  # type: ignore[method-assign]
        with self.assertRaises(SourceError):
            source.search(SearchQuery(text="hatsune miku", safe=True))

    def test_moebooru_error_not_masked(self) -> None:
        source = KonachanSource()

        def fake_search(query, tags_override):
            if len(tags_override) == 1:
                return SourcePage(posts=[], has_more=False)
            raise SourceError("HTTP 503")

        source._search_tags = fake_search  # type: ignore[method-assign]
        with self.assertRaises(SourceError):
            source.search(SearchQuery(text="hatsune miku", safe=True))


class SourceTagHelpersTests(unittest.TestCase):
    def test_strip_meta_tags(self) -> None:
        source = DanbooruSource()
        self.assertEqual(
            source.strip_meta_tags(["1girl", "rating:g", "-order:score", "sky"]),
            ["1girl", "sky"],
        )

    def test_normalize_tag_uses_source_separator(self) -> None:
        self.assertEqual(DanbooruSource().normalize_tag("blue sky"), "blue_sky")
        self.assertEqual(DanbooruSource().normalize_tag("blue_sky"), "blue_sky")
        self.assertEqual(ZerochanSource().normalize_tag("blue_sky"), "blue sky")
        self.assertEqual(ZerochanSource().normalize_tag("blue  sky"), "blue sky")
        self.assertEqual(DanbooruSource().normalize_tag(""), "")


class ZerochanTests(unittest.TestCase):
    def test_url_multi_tag(self) -> None:
        source = ZerochanSource()
        query = SearchQuery(text="frieren, landscape", page=2, sort="score")
        url = source.search_url(query)
        self.assertIn("/frieren,landscape", url)
        self.assertIn("p=2", url)
        self.assertIn("s=fav", url)

    def test_multiword_tag_is_one_tag(self) -> None:
        source = ZerochanSource()
        query = SearchQuery(text="Zenless Zone Zero")
        self.assertEqual(source.query_tags(query), ["Zenless Zone Zero"])
        self.assertEqual(source.search_path(query), "/Zenless+Zone+Zero")
        self.assertEqual(source.display_tags("Zenless Zone Zero"), ["Zenless Zone Zero"])
        self.assertEqual(
            source.display_tags("landscape, Zenless Zone Zero"),
            ["landscape", "Zenless Zone Zero"],
        )
        self.assertEqual(
            source.join_tags(["landscape", "Zenless Zone Zero"]),
            "landscape, Zenless Zone Zero",
        )

    def test_space_separated_fallback_variants(self) -> None:
        source = ZerochanSource()
        variants = source._tag_variants(SearchQuery(text="landscape sky"))
        self.assertEqual(variants[0], ["landscape sky"])
        self.assertEqual(variants[1], ["landscape", "sky"])

    def test_parser_extracts_posts(self) -> None:
        parser = _GalleryParser()
        parser.feed(ZEROCHAN_FIXTURE)
        self.assertEqual(len(parser.items), 2)
        first = parser.items[0]
        self.assertEqual(first["id"], "4424191")
        self.assertEqual(first["full"], "https://static.zerochan.net/Frieren.full.4424191.jpg")
        self.assertIn("frieren", first["tags"])
        self.assertEqual(first["primary"], "Frieren")
        self.assertIn("4800", first["title"])

    def test_to_post(self) -> None:
        parser = _GalleryParser()
        parser.feed(ZEROCHAN_FIXTURE)
        source = ZerochanSource()
        post = source._to_post(parser.items[0])
        assert post is not None
        self.assertEqual(post.thumb_url, "https://s3.zerochan.net/240/41/33/4424191.jpg")
        self.assertEqual(post.preview_url, "https://static.zerochan.net/Frieren.600.4424191.jpg")
        self.assertEqual(post.file_url, "https://static.zerochan.net/Frieren.full.4424191.jpg")
        self.assertEqual((post.width, post.height), (4800, 3592))
        self.assertEqual(post.artist, "GIBUN")
        self.assertEqual(post.ext, "jpg")
        self.assertEqual(post.rating, "safe")
        self.assertEqual(post.page_url, "https://www.zerochan.net/4424191")

    def test_construct_full_for_similar_tile(self) -> None:
        source = ZerochanSource()
        item = {
            "id": "4034546",
            "thumb": "https://s3.zerochan.net/240/46/40/4034546.avif",
            "full": "https://s3.zerochan.net/240/46/40/4034546.avif",
            "primary": "Genshin Impact",
            "title": "1500✕1500 1234kb jpg\n326738\nUploaded by Doopliss",
            "tags": ["genshin impact"],
        }
        self.assertEqual(
            source._construct_full(item),
            "https://static.zerochan.net/Genshin.Impact.full.4034546.jpg",
        )
        post = source._to_post(item)
        assert post is not None
        self.assertEqual(post.file_url, "https://static.zerochan.net/Genshin.Impact.full.4034546.jpg")
        self.assertEqual(post.preview_url, "https://static.zerochan.net/Genshin.Impact.600.4034546.jpg")
        self.assertEqual(post.ext, "jpg")

    def test_extract_tag_data_tolerates_trailing_commas(self) -> None:
        source = ZerochanSource()
        html = (
            'tagData = {\n'
            '  "Shylily": {"total": 125, "thumbnails": ["4735525", "4648695",],},\n'
            '}\nparentTags = {}'
        )
        data = source._extract_tag_data(html)
        self.assertEqual(data["Shylily"]["total"], 125)
        self.assertEqual(data["Shylily"]["thumbnails"], ["4735525", "4648695"])

    def test_typo_candidates_collapse_duplicate_letters(self) -> None:
        self.assertIn("Shylily", ZerochanSource._typo_candidates("Shylilly"))
        self.assertEqual(ZerochanSource._typo_candidates("landscape"), [])

    def test_preview_preserves_full_extension(self) -> None:
        source = ZerochanSource()
        post = source._to_post(
            {
                "id": "123",
                "thumb": "https://s3.zerochan.net/240/12/34/123.jpg",
                "full": "https://static.zerochan.net/Artwork.full.123.png",
                "title": "",
                "alt": "",
                "tags": [],
                "primary": "",
                "width": 0,
                "height": 0,
            }
        )
        assert post is not None
        self.assertEqual(post.preview_url, "https://static.zerochan.net/Artwork.600.123.png")
        self.assertEqual(post.file_url, "https://static.zerochan.net/Artwork.full.123.png")

    def test_cache_put_prunes_expired_and_caps_size(self) -> None:
        source = ZerochanSource()
        cache: dict[str, tuple[float, str]] = {}
        now = time.time()
        cache["old"] = (now - 400, "stale")
        for index in range(10):
            cache[f"fresh-{index}"] = (now, str(index))
        with source._cache_lock:
            source._cache_put(cache, "new", (now, "new"), ttl=300, max_entries=10)
        self.assertNotIn("old", cache)
        self.assertIn("new", cache)
        self.assertLessEqual(len(cache), 10)


class ZerochanCorrectionTests(unittest.TestCase):
    """Regression: the typo-correction path must still run guest-limit
    supplementation (it previously broke out before member_only was set)."""

    MEMBER_ONLY_HTML = ZEROCHAN_FIXTURE + '\n<div class="members only">member area</div>'

    def setUp(self) -> None:
        self.source = ZerochanSource()
        self._clear_caches()

    def tearDown(self) -> None:
        self._clear_caches()

    def _clear_caches(self) -> None:
        with self.source._cache_lock:
            self.source._resolution_cache.clear()
            self.source._supplement_cache.clear()
            self.source._html_cache.clear()

    @staticmethod
    def _extra_post(post_id: str) -> Post:
        return Post(
            id=post_id,
            source="zerochan",
            source_name="Zerochan",
            thumb_url="",
            preview_url="",
            file_url="",
            page_url="",
        )

    def _arm_typo_404(self) -> None:
        def fake_fetch_page(url, timeout=25):
            raise net.NetworkError("HTTP 404: Not Found", status=404)

        self.source._fetch_page = fake_fetch_page  # type: ignore[method-assign]
        self.source._find_correction = lambda tag: (  # type: ignore[method-assign]
            self.MEMBER_ONLY_HTML,
            "shylily",
        )

    def test_corrected_search_runs_supplementation(self) -> None:
        self._arm_typo_404()
        supplemented_with: list[tuple[str, int]] = []

        def fake_supplement(tag, public_posts):
            supplemented_with.append((tag, len(public_posts)))
            return (
                [self._extra_post("9001"), self._extra_post("9002"), self._extra_post("9003")],
                "guest-limit notice",
            )

        self.source._supplement = fake_supplement  # type: ignore[method-assign]

        page = self.source.search(SearchQuery(text="shylilly", limit=100, safe=True))

        self.assertEqual(supplemented_with, [("shylily", 2)])
        self.assertEqual(len(page.posts), 5)
        self.assertFalse(page.has_more)
        self.assertIn("shylilly", page.notice)
        self.assertIn("shylily", page.notice)
        self.assertIn("guest-limit notice", page.notice)

    def test_corrected_search_without_member_only_skips_supplement(self) -> None:
        def fake_fetch_page(url, timeout=25):
            raise net.NetworkError("HTTP 404: Not Found", status=404)

        self.source._fetch_page = fake_fetch_page  # type: ignore[method-assign]
        self.source._find_correction = lambda tag: (ZEROCHAN_FIXTURE, "shylily")  # type: ignore[method-assign]
        called: list[str] = []
        self.source._supplement = lambda tag, posts: called.append(tag) or ([], "")  # type: ignore[method-assign]

        page = self.source.search(SearchQuery(text="shylilly", limit=100, safe=True))

        self.assertEqual(called, [])
        self.assertEqual(len(page.posts), 2)
        self.assertIn("shylilly", page.notice)
        self.assertIn("shylily", page.notice)

    def test_member_only_normal_path_still_supplements(self) -> None:
        self.source._fetch_page = lambda url, timeout=25: (  # type: ignore[method-assign]
            self.MEMBER_ONLY_HTML,
            "https://www.zerochan.net/shylily",
        )
        supplemented_with: list[tuple[str, int]] = []
        self.source._supplement = lambda tag, posts: (  # type: ignore[method-assign]
            supplemented_with.append((tag, len(posts))) or ([self._extra_post("9001")], "guest-limit notice")
        )

        page = self.source.search(SearchQuery(text="shylily", limit=100, safe=True))

        self.assertEqual(supplemented_with, [("shylily", 2)])
        self.assertEqual(len(page.posts), 3)
        self.assertFalse(page.has_more)
        self.assertIn("guest-limit notice", page.notice)


class MergeTests(unittest.TestCase):
    @staticmethod
    def post(source: str, post_id: str) -> Post:
        return Post(
            id=post_id,
            source=source,
            source_name=source,
            thumb_url="",
            preview_url="",
            file_url="",
            page_url="",
        )

    def test_round_robin_merge_deduplicates(self) -> None:
        a = SourcePage([self.post("a", "1"), self.post("a", "2")], has_more=True)
        b = SourcePage([self.post("b", "1"), self.post("a", "1")], has_more=False)
        posts, has_more = merge_pages([a, b], limit=10)
        self.assertEqual([post.key for post in posts], ["a:1", "b:1", "a:2"])
        self.assertTrue(has_more)

    def test_limit_is_respected(self) -> None:
        a = SourcePage([self.post("a", str(i)) for i in range(5)], has_more=True)
        posts, _ = merge_pages([a], limit=3)
        self.assertEqual(len(posts), 3)

    def test_empty_source_does_not_hide_other_results(self) -> None:
        populated = SourcePage([self.post("a", "1")], has_more=False)
        empty = SourcePage([], has_more=False)
        posts, _ = merge_pages([empty, populated, empty], limit=10)
        self.assertEqual([post.key for post in posts], ["a:1"])


if __name__ == "__main__":
    unittest.main()
