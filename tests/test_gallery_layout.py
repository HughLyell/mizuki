"""Tests for the justified gallery row packing."""

from __future__ import annotations

import unittest

from mizuki.models import Post
from mizuki.ui.gallery_layout import (
    MAX_ASPECT,
    MIN_ASPECT,
    build_justified_rows,
    display_aspect,
)


def make_post(number: int, width: int, height: int) -> Post:
    return Post(
        id=str(number),
        source="test",
        source_name="Test",
        thumb_url="https://example.test/thumb.jpg",
        preview_url="https://example.test/preview.jpg",
        file_url="https://example.test/full.jpg",
        page_url="https://example.test/post",
        width=width,
        height=height,
    )


class GalleryLayoutTests(unittest.TestCase):
    def test_empty_gallery(self) -> None:
        self.assertEqual(build_justified_rows([], 1200, 220), [])

    def test_posts_are_preserved_in_order(self) -> None:
        posts = [
            make_post(1, 1600, 900),
            make_post(2, 900, 1600),
            make_post(3, 1200, 1200),
            make_post(4, 2000, 800),
        ]
        rows = build_justified_rows(posts, 1000, 220, gap=12, min_height=160, max_height=300)
        laid_out = [tile.post for row in rows for tile in row.tiles]
        self.assertEqual(laid_out, posts)

    def test_first_row_is_justified_to_available_width(self) -> None:
        posts = [make_post(index, 1200, 900) for index in range(12)]
        rows = build_justified_rows(posts, 1000, 220, gap=12, min_height=160, max_height=300)
        row = rows[0]
        total = sum(tile.width for tile in row.tiles) + 12 * (len(row.tiles) - 1)
        self.assertTrue(row.justified)
        self.assertEqual(total, 1000)

    def test_card_chrome_is_accounted_for(self) -> None:
        posts = [make_post(index, 1200, 900) for index in range(12)]
        rows = build_justified_rows(
            posts, 1000, 220, gap=12, min_height=160, max_height=300, chrome=2
        )
        row = rows[0]
        total = (
            sum(tile.width for tile in row.tiles)
            + 12 * (len(row.tiles) - 1)
            + 2 * len(row.tiles)
        )
        self.assertEqual(total, 1000)

    def test_tiles_keep_rough_aspect_ratio(self) -> None:
        posts = [
            make_post(1, 1600, 900),
            make_post(2, 900, 1600),
            make_post(3, 1200, 1200),
            make_post(4, 1400, 1000),
            make_post(5, 1000, 1500),
        ]
        rows = build_justified_rows(posts, 1200, 230, gap=12, min_height=170, max_height=300)
        for row in rows:
            for tile in row.tiles:
                expected = display_aspect(tile.post)
                actual = tile.width / tile.height
                self.assertAlmostEqual(actual, expected, delta=0.04)

    def test_landscapes_are_wider_than_portraits(self) -> None:
        landscape = make_post(1, 1920, 1080)
        portrait = make_post(2, 1080, 1920)
        rows = build_justified_rows(
            [landscape, portrait], 800, 220, gap=12, min_height=160, max_height=300
        )
        tiles = [tile for row in rows for tile in row.tiles]
        self.assertGreater(tiles[0].width, tiles[1].width)

    def test_extreme_aspects_are_gently_cropped(self) -> None:
        panorama = make_post(1, 4000, 1000)
        tall = make_post(2, 800, 3200)
        self.assertEqual(display_aspect(panorama), MAX_ASPECT)
        self.assertEqual(display_aspect(tall), MIN_ASPECT)

    def test_incomplete_last_row_is_not_stretched(self) -> None:
        posts = [make_post(1, 1200, 900), make_post(2, 1200, 900)]
        rows = build_justified_rows(posts, 1000, 220, gap=12, min_height=160, max_height=300)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        total = sum(tile.width for tile in row.tiles) + 12 * (len(row.tiles) - 1)
        self.assertFalse(row.justified)
        self.assertLess(total, 1000)

    def test_widths_are_positive_in_a_narrow_window(self) -> None:
        posts = [make_post(1, 4000, 1000), make_post(2, 800, 3200)]
        rows = build_justified_rows(posts, 240, 220, gap=12, min_height=160, max_height=300)
        self.assertTrue(rows)
        for row in rows:
            for tile in row.tiles:
                self.assertGreater(tile.width, 0)
                self.assertGreater(tile.height, 0)

    def test_row_heights_respect_bounds(self) -> None:
        posts = [make_post(index, 1200, 900 + index * 40) for index in range(1, 21)]
        rows = build_justified_rows(posts, 1100, 240, gap=12, min_height=170, max_height=300)
        for row in rows:
            self.assertGreaterEqual(row.height, 170)
            self.assertLessEqual(row.height, 300)


if __name__ == "__main__":
    unittest.main()
