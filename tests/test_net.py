"""Tests for the HTTP helpers in mizuki.net."""

from __future__ import annotations

import email.message
import unittest
import urllib.error

from mizuki import net


def make_http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://example.test/", code, "error", hdrs=headers, fp=None)


class RetryDelayTests(unittest.TestCase):
    def test_retry_after_numeric_header_is_used(self) -> None:
        self.assertEqual(net._retry_delay(make_http_error(429, "5"), 0), 5.0)

    def test_retry_after_is_capped(self) -> None:
        self.assertEqual(net._retry_delay(make_http_error(503, "120"), 1), 30.0)

    def test_retry_after_zero_is_allowed(self) -> None:
        self.assertEqual(net._retry_delay(make_http_error(429, "0"), 0), 0.0)

    def test_missing_header_falls_back_to_backoff(self) -> None:
        self.assertAlmostEqual(net._retry_delay(make_http_error(500), 0), 0.7)
        self.assertAlmostEqual(net._retry_delay(make_http_error(500), 2), 2.1)

    def test_unparseable_header_falls_back_to_backoff(self) -> None:
        self.assertAlmostEqual(net._retry_delay(make_http_error(429, "soon"), 0), 0.7)


class UrlWithParamsTests(unittest.TestCase):
    def test_none_values_are_dropped(self) -> None:
        url = net._url_with_params(
            "https://example.test/api",
            {"tags": "sky", "page": 2, "ignored": None},
        )
        self.assertEqual(url, "https://example.test/api?tags=sky&page=2")

    def test_existing_query_string_is_extended(self) -> None:
        url = net._url_with_params("https://example.test/api?page=1", {"limit": 5})
        self.assertEqual(url, "https://example.test/api?page=1&limit=5")

    def test_no_params_returns_url(self) -> None:
        self.assertEqual(net._url_with_params("https://example.test", None), "https://example.test")


if __name__ == "__main__":
    unittest.main()
