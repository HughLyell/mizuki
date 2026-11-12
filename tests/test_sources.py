from __future__ import annotations

import unittest

from mizuki.models import SearchQuery
from mizuki.sources.base import merge_pages
from mizuki.models import Post, SourcePage
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
