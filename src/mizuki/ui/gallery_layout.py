"""Pure layout maths for the justified artwork gallery.

The gallery keeps a roughly constant row height while giving each tile a width
proportional to its image aspect ratio. This is the same idea as a photo
gallery: landscape images get wide tiles, portraits stay narrow, and rows line
up without forcing every thumbnail into one fixed crop.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..models import Post

# Extremely tall or wide images are gently cropped so one image cannot make a
# row microscopic or absurdly tall.
MIN_ASPECT = 0.52
MAX_ASPECT = 2.35

DEFAULT_ASPECT = 0.75


@dataclass(frozen=True, slots=True)
class TileLayout:
    post: Post
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class RowLayout:
    tiles: tuple[TileLayout, ...]
    height: int
    justified: bool


def display_aspect(post: Post) -> float:
    """Return the aspect ratio used for a tile, with a gentle crop limit."""
    try:
        aspect = float(post.aspect_ratio)
    except (TypeError, ValueError, ZeroDivisionError):
        aspect = DEFAULT_ASPECT
    if not math.isfinite(aspect) or aspect <= 0:
        aspect = DEFAULT_ASPECT
    return min(MAX_ASPECT, max(MIN_ASPECT, aspect))


def build_justified_rows(
    posts: list[Post] | tuple[Post, ...],
    available_width: int,
    target_height: int,
    *,
    gap: int = 12,
    min_height: int | None = None,
    max_height: int | None = None,
    chrome: int = 0,
) -> list[RowLayout]:
    """Pack posts into justified rows that fill ``available_width``.

    Full rows are scaled to the exact available width. The final, incomplete
    row keeps the target height and stays left-aligned, which avoids stretching
    one or two remaining images across the whole window.
    """
    items = list(posts)
    if not items:
        return []

    width = max(1, int(available_width))
    target = max(48, int(target_height))
    gap = max(0, int(gap))
    min_h = max(40, int(min_height if min_height is not None else target * 0.72))
    max_h = max(target, int(max_height if max_height is not None else target * 1.4))
    total = len(items)

    rows: list[RowLayout] = []
    index = 0
    while index < total:
        row_posts: list[Post] = []
        aspects: list[float] = []
        aspect_sum = 0.0

        while index < total:
            aspect = display_aspect(items[index])
            next_sum = aspect_sum + aspect
            count = len(row_posts) + 1
            natural_width = next_sum * target + gap * (count - 1)
            row_posts.append(items[index])
            aspects.append(aspect)
            aspect_sum = next_sum
            index += 1
            if natural_width >= width:
                break

        count = len(row_posts)
        natural_width = aspect_sum * target + gap * (count - 1)
        justified = natural_width >= width

        if justified:
            height = (width - gap * (count - 1)) / aspect_sum
            # If a row became too short, move its last images to the next row.
            while count > 1 and height < min_h:
                index -= 1
                row_posts.pop()
                aspect_sum -= aspects.pop()
                count -= 1
                height = (width - gap * (count - 1)) / aspect_sum
            if height < min_h:
                height = min_h
                justified = False
            elif height > max_h:
                height = max_h
                justified = False
        else:
            height = target

        height = max(min_h, min(max_h, height))
        rounded_height = max(1, int(round(height)))
        widths = _tile_widths(
            aspects,
            rounded_height,
            width,
            gap,
            fill=justified,
            chrome=chrome,
        )
        rows.append(
            RowLayout(
                tiles=tuple(
                    TileLayout(post=post, width=tile_width, height=rounded_height)
                    for post, tile_width in zip(row_posts, widths)
                ),
                height=rounded_height,
                justified=justified,
            )
        )
    return rows


def _tile_widths(
    aspects: list[float],
    height: int,
    available_width: int,
    gap: int,
    *,
    fill: bool,
    chrome: int,
) -> list[int]:
    if not aspects:
        return []
    raw = [max(1.0, aspect * height) for aspect in aspects]
    if fill:
        target = max(1, available_width - gap * (len(aspects) - 1) - chrome * len(aspects))
        scale = target / sum(raw)
        widths = [max(1, int(round(value * scale))) for value in raw]
        _distribute_rounding(widths, target)
    else:
        widths = [max(1, int(round(value))) for value in raw]
        # A single extreme tile in a very narrow window must not overflow.
        if len(widths) == 1 and widths[0] > available_width:
            widths[0] = available_width
    return widths


def _distribute_rounding(widths: list[int], target: int) -> None:
    """Make integer tile widths add up exactly to the row target."""
    difference = target - sum(widths)
    index = 0
    guard = 0
    while difference and widths and guard < 10_000:
        step = 1 if difference > 0 else -1
        slot = index % len(widths)
        if widths[slot] + step >= 1:
            widths[slot] += step
            difference -= step
        index += 1
        guard += 1
