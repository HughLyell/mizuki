from __future__ import annotations

import unittest

from mizuki.models import (
    EXPLICIT,
    SAFE,
    SENSITIVE,
    Post,
    extension_from_url,
    normalize_rating,
    parse_filter_tags,
    parse_tags,
    post_matches_filter,
    sanitize_component,
)


def make_post(**overrides) -> Post:
    data = dict(
        id="12345",
        source="danbooru",
        source_name="Danbooru",
        thumb_url="https://example.test/thumb.jpg",
        preview_url="https://example.test/preview.jpg",
        file_url="https://example.test/full.png",
        page_url="https://example.test/posts/12345",
        width=1920,
        height=1080,
        tags=("1girl", "landscape"),
        rating=SAFE,
        artist="Some Artist",
        ext="png",
        score=42,
        referer="https://example.test/",
    )
    data.update(overrides)
    return Post(**data)


class TagsTests(unittest.TestCase):
    def test_parse_tags_simple(self) -> None:
        self.assertEqual(parse_tags("1girl blue_sky"), ["1girl", "blue_sky"])

    def test_parse_tags_quoted_and_exclusion(self) -> None:
        self.assertEqual(
            parse_tags('1girl "blue sky" -monochrome'),
            ["1girl", "blue sky", "-monochrome"],
        )

    def test_parse_tags_collapses_whitespace(self) -> None:
        self.assertEqual(parse_tags("  a\t b \n c "), ["a", "b", "c"])

    def test_parse_tags_empty(self) -> None:
        self.assertEqual(parse_tags("   "), [])


class RatingTests(unittest.TestCase):
    def test_aliases(self) -> None:
        self.assertEqual(normalize_rating("g"), SAFE)
        self.assertEqual(normalize_rating("safe"), SAFE)
        self.assertEqual(normalize_rating("s"), SENSITIVE)
        self.assertEqual(normalize_rating("q"), "questionable")
        self.assertEqual(normalize_rating("e"), EXPLICIT)
        self.assertEqual(normalize_rating("explicit"), EXPLICIT)
        self.assertEqual(normalize_rating(None), SAFE)
        self.assertEqual(normalize_rating("nonsense"), SAFE)

    def test_post_rating_label(self) -> None:
        self.assertEqual(make_post(rating=EXPLICIT).rating_label, "Explicit")
        self.assertTrue(make_post().is_safe)


class FilenameTests(unittest.TestCase):
    def test_sanitize(self) -> None:
        self.assertEqual(sanitize_component("Hatsune Miku"), "Hatsune_Miku")
        self.assertEqual(sanitize_component("../../etc/passwd"), "etc_passwd")
        self.assertEqual(sanitize_component(""), "artwork")
        self.assertLessEqual(len(sanitize_component("x" * 200)), 64)

    def test_filename_uses_source_id_artist(self) -> None:
        name = make_post().filename()
        self.assertEqual(name, "danbooru_12345_Some_Artist.png")

    def test_filename_rejects_unknown_extension(self) -> None:
        self.assertTrue(make_post(ext="webm").filename().endswith(".jpg"))

    def test_extension_from_url(self) -> None:
        self.assertEqual(extension_from_url("https://x/a.JPEG"), "jpg")
        self.assertEqual(extension_from_url("https://x/a.webp?x=1"), "webp")
        self.assertEqual(extension_from_url("https://x/a"), "jpg")
        self.assertEqual(extension_from_url("https://x/a.webm"), "jpg")


class RoundTripTests(unittest.TestCase):
    def test_post_round_trip(self) -> None:
        post = make_post()
        restored = Post.from_dict(post.to_dict())
        self.assertEqual(restored, post)

    def test_from_dict_tolerates_missing(self) -> None:
        post = Post.from_dict({"id": 1, "tags": None, "rating": "nope"})
        self.assertEqual(post.id, "1")
        self.assertEqual(post.tags, ())
        self.assertEqual(post.rating, SAFE)


class FilterTests(unittest.TestCase):
    def test_parse_filter_tags(self) -> None:
        self.assertEqual(parse_filter_tags(" 1girl, blue_sky ,, -monochrome "),
                         ["1girl", "blue sky", "-monochrome"])
        self.assertEqual(parse_filter_tags(""), [])
        self.assertEqual(parse_filter_tags("tag, tag"), ["tag"])

    def test_matches_all_tags(self) -> None:
        post = make_post(tags=("1girl", "blue_sky", "landscape"))
        self.assertTrue(post_matches_filter(post, "1girl, blue sky"))
        self.assertTrue(post_matches_filter(post, "LANDSCAPE"))
        self.assertFalse(post_matches_filter(post, "1girl, no_match"))
        self.assertFalse(post_matches_filter(post, "blue_sky, 1girl, city"))

    def test_empty_filter_matches(self) -> None:
        self.assertTrue(post_matches_filter(make_post(), ""))
        self.assertTrue(post_matches_filter(make_post(), " , "))

    def test_zerochan_space_tags(self) -> None:
        post = make_post(tags=("genshin impact", "landscape"))
        self.assertTrue(post_matches_filter(post, "genshin_impact"))


if __name__ == "__main__":
    unittest.main()
