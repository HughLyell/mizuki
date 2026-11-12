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

    def test_konachan_safe_rating(self) -> None:
        self.assertIn("rating:safe", KonachanSource().search_tags(SearchQuery(text="sky", safe=True)))

    def test_moebooru_rating_mapping(self) -> None:
        self.assertEqual(moebooru_rating("s"), "safe")
        self.assertEqual(moebooru_rating("q"), "questionable")
        self.assertEqual(moebooru_rating("e"), "explicit")


class ZerochanTests(unittest.TestCase):
    def test_url_multi_tag(self) -> None:
        source = ZerochanSource()
        query = SearchQuery(text="frieren landscape", page=2, sort="score")
        url = source.search_url(query)
        self.assertIn("/frieren,landscape", url)
        self.assertIn("p=2", url)
        self.assertIn("s=fav", url)

    def test_parser_extracts_posts(self) -> None:
        parser = _GalleryParser()
        parser.feed(ZEROCHAN_FIXTURE)
        self.assertEqual(len(parser.items), 2)
        first = parser.items[0]
        self.assertEqual(first["id"], "4424191")
        self.assertEqual(first["full"], "https://static.zerochan.net/Frieren.full.4424191.jpg")
        self.assertIn("frieren", first["tags"])
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


if __name__ == "__main__":
    unittest.main()
