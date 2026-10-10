# tests/test_core.py
#
# Deterministic tests for rfc_2231_continuation_decoder.
# Run: PYTHONPATH=src python -m unittest discover -s tests

import unittest

from rfc_2231_continuation_decoder import (
    decode_rfc2231_param,
    decode_segments,
    join_continuation,
    parse_param_segments,
    unpercent,
)


class TestUnpercent(unittest.TestCase):
    def test_plain_text(self):
        self.assertEqual(unpercent("hello"), b"hello")

    def test_percent_octets(self):
        self.assertEqual(unpercent("caf%e9"), b"caf\xe9")

    def test_double_percent(self):
        # `%%` is not an escape sequence in RFC 2231 percent-encoding;
        # each stray `%` is left as a literal `%` byte.
        self.assertEqual(unpercent("100%%"), b"100%%")

    def test_stray_percent_at_end_is_literal(self):
        # Trailing `%` with no hex after it stays as `%` byte.
        self.assertEqual(unpercent("a%"), b"a%")

    def test_invalid_hex_left_literal(self):
        # `%zz` is not valid hex, so `%` stays and `zz` stays.
        self.assertEqual(unpercent("a%zzb"), b"a%zzb")


class TestParseParamSegments(unittest.TestCase):
    def test_single_encoded(self):
        result = parse_param_segments({"filename*": "utf-8'en'%63%61%66.txt"})
        self.assertIn("filename", result)
        entry = result["filename"][0]
        self.assertEqual(entry[0], 0)        # index
        self.assertTrue(entry[1])             # encoded flag
        self.assertEqual(entry[2], "utf-8")   # charset
        self.assertEqual(entry[3], "en")       # language
        self.assertEqual(entry[4], "%63%61%66.txt")  # remainder

    def test_plain_param_ignored(self):
        result = parse_param_segments({"filename": "plain.txt"})
        self.assertEqual(result, {})

    def test_bare_continuation_grouped(self):
        result = parse_param_segments({
            "name*0": "part1",
            "name*1": "part2",
        })
        self.assertEqual(len(result["name"]), 2)
        self.assertEqual(result["name"][0][:2], (0, False))
        self.assertEqual(result["name"][1][:2], (1, False))

    def test_segments_sorted_by_index(self):
        result = parse_param_segments({
            "name*2": "c",
            "name*0": "a",
            "name*1": "b",
        })
        indices = [e[0] for e in result["name"]]
        self.assertEqual(indices, [0, 1, 2])


class TestJoinContinuation(unittest.TestCase):
    def test_encoded_single(self):
        segments = [(0, True, "utf-8", "en", "caf%C3%A9")]
        charset, _lang, raw = join_continuation(segments)
        self.assertEqual(charset, "utf-8")
        self.assertEqual(raw, b"caf\xc3\xa9")

    def test_bare_segments_concatenated(self):
        segments = [(0, False, "", "", "hel"), (1, False, "", "", "lo")]
        _charset, _lang, raw = join_continuation(segments)
        self.assertEqual(raw, b"hello")

    def test_mixed_encoded_then_bare(self):
        # First segment encoded with charset, second bare literal.
        segments = [
            (0, True, "utf-8", "", "caf%C3%A9"),
            (1, False, "", "", ".txt"),
        ]
        charset, _lang, raw = join_continuation(segments)
        self.assertEqual(charset, "utf-8")
        self.assertEqual(raw, b"caf\xc3\xa9.txt")

    def test_duplicate_index_raises(self):
        segments = [
            (0, True, "utf-8", "", "a"),
            (0, True, "", "", "b"),
        ]
        with self.assertRaises(ValueError):
            join_continuation(segments)

    def test_encoded_segment_zero_without_charset_raises(self):
        # Encoded flag set but no charset/language tag on segment 0.
        segments = [(0, True, "", "", "abc")]
        with self.assertRaises(ValueError):
            join_continuation(segments)

    def test_bare_zero_without_charset_ok(self):
        # Bare (non-encoded) segment 0 needs no charset.
        segments = [(0, False, "", "", "abc")]
        _c, _l, raw = join_continuation(segments)
        self.assertEqual(raw, b"abc")


class TestDecodeSegments(unittest.TestCase):
    def test_utf8_decoded_to_str(self):
        segments = [(0, True, "utf-8", "", "caf%C3%A9")]
        self.assertEqual(decode_segments(segments), "caf\xe9")

    def test_no_charset_latin1(self):
        segments = [(0, False, "", "", "caf\xe9")]
        self.assertEqual(decode_segments(segments), "caf\xe9")

    def test_unknown_charset_falls_back(self):
        segments = [(0, True, "nonexistent-charset", "", "abc")]
        self.assertEqual(decode_segments(segments), "abc")

    def test_continuation_utf8(self):
        segments = [
            (0, True, "utf-8", "", "caf%C3%A9"),
            (1, False, "", "", ".txt"),
        ]
        self.assertEqual(decode_segments(segments), "caf\xe9.txt")

    def test_empty_language_tag(self):
        # `charset'rest` (one quote) is valid; language is empty.
        segments = [(0, True, "utf-8", "", "hi")]
        self.assertEqual(decode_segments(segments), "hi")

    def test_bare_segment_no_charset_ok(self):
        # Bare (non-encoded) segment with no charset decodes as latin-1.
        segments = [(0, False, "", "", "plain")]
        self.assertEqual(decode_segments(segments), "plain")


class TestDecodeRFC2231Param(unittest.TestCase):
    def test_full_flow(self):
        params = {"filename*": "utf-8'en'%63%61%66.txt"}
        self.assertEqual(decode_rfc2231_param(params, "filename"), "caf.txt")

    def test_continuation_full_flow(self):
        params = {
            "name*0*": "utf-8'en'caf%C3%A9",
            "name*1*": ".txt",
        }
        self.assertEqual(decode_rfc2231_param(params, "name"), "caf\xe9.txt")

    def test_missing_param_returns_none(self):
        self.assertIsNone(decode_rfc2231_param({}, "filename"))

    def test_plain_param_not_decoded(self):
        # `filename` without `*` is not RFC 2231 → None (not decoded).
        params = {"filename": "caf.txt"}
        self.assertIsNone(decode_rfc2231_param(params, "filename"))

    def test_bare_continuation_full_flow(self):
        params = {
            "name*0": "hello",
            "name*1": " world",
        }
        self.assertEqual(decode_rfc2231_param(params, "name"), "hello world")

    def test_encoded_takes_precedence_over_plain(self):
        # Per design choice #2: if `filename*` exists, plain `filename` ignored.
        params = {
            "filename": "plain.txt",
            "filename*": "utf-8'en'encoded.txt",
        }
        self.assertEqual(decode_rfc2231_param(params, "filename"), "encoded.txt")

    def test_realistic_attachment(self):
        params = {
            "filename*0*": "utf-8'en'report%20",
            "filename*1*": "Q4%20draft.pdf",
        }
        self.assertEqual(
            decode_rfc2231_param(params, "filename"),
            "report Q4 draft.pdf",
        )


if __name__ == "__main__":
    unittest.main()
