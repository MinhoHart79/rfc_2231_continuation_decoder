# rfc_2231_continuation_decoder/core.py
#
# Pure-Python decoder for RFC 2231 \"MIME Parameter Value and Encoded Word
# Extensions\".  The standard library's `email` package already understands
# RFC 2231 when it parses full messages, but if you have a raw parameter dict
# in hand (e.g. from a `multipart/form-data` Content-Disposition header that
# you parsed yourself) the standard library offers no public helper to
# reassemble the `name*0`, `name*1*`, `name*=us-ascii'en'...` triplets.
#
# Design choices stated up front so the tests can lock them down:
#
#   1. We split parameters on `;` ONLY at the top level.  We do not attempt
#      quoted-string value parsing; callers are expected to pass a mapping of
#      name->raw-value.  This keeps the surface small and avoids reimplementing
#      RFC 2045 quoted-string semantics.  If a value contains a `;` the caller
#      is responsible for having unquoted it first.
#
#   2. A parameter that has NO asterisk (e.g. `filename` without `*`) is passed
#      through unchanged and is NOT decoded as RFC 2231, even if a sibling
#      `filename*0` exists.  RFC 2231 encoding is opt-in via the trailing `*`.
#
#   3. We reject duplicate continuation indices (e.g. two `name*0` entries)
#      instead of silently picking one; ambiguous input should be loud.
#
#   4. If a charset is declared but decoding fails we fall back to the raw
#      (percent-decoded) bytes decoded as latin-1.  This matches what most
#      mail clients do in practice and never raises on malformed input.
#
#   5. Language tags are NOT returned as a separate field; they are parsed
#      only so we can skip them when decoding.  If you need the language you
#      can read it off the raw parameter before calling us.

from __future__ import annotations

from typing import Dict, List, Mapping, Tuple


__all__ = [
    "decode_rfc2231_param",
    "decode_segments",
    "join_continuation",
    "parse_param_segments",
    "unpercent",
]


def _is_continuation_key(key: str) -> bool:
    """True when `key` looks like `name*N` (bare numeric suffix)."""
    star = key.rfind("*")
    if star <= 0:
        return False
    suffix = key[star + 1 :]
    return suffix.isdigit() and suffix != ""


def _is_encoded_key(key: str) -> bool:
    """True when `key` looks like `name*` or `name*N*` (encoded form)."""
    star = key.rfind("*")
    if star <= 0:
        return False
    if key[star + 1 :] != "":
        return False
    prev_star = key.rfind("*", 0, star)
    if prev_star <= 0:
        return True  # `name*` single encoded segment
    between = key[prev_star + 1 : star]
    return between.isdigit() and between != ""


def _base_name(key: str) -> str:
    """Strip trailing `*`, `*N`, or `*N*` to get the logical parameter name."""
    star = key.rfind("*")
    if star <= 0:
        return key
    rest = key[:star]
    prev_star = rest.rfind("*")
    if prev_star > 0:
        between = rest[prev_star + 1 :]
        if between.isdigit() and between != "":
            return rest[:prev_star]
    return rest


def _parse_language_tag(value: str) -> Tuple[str, str, str]:
    """Split `charset'language'rest` per RFC 2231 §4.

    Both charset and language may be empty.  We return (charset, language,
    remainder) where remainder still needs percent-decoding.
    """
    first = value.find("'")
    if first == -1:
        return "", "", value
    second = value.find("'", first + 1)
    if second == -1:
        charset = value[:first]
        return charset, "", value[first + 1 :]
    charset = value[:first]
    language = value[first + 1 : second]
    rest = value[second + 1 :]
    return charset, language, rest


def unpercent(value: str) -> bytes:
    """Percent-decode `value` into raw bytes.

    Unlike `urllib.parse.unquote` this returns bytes, which is essential because
    RFC 2231 percent-escapes encode octets of the charset, not Unicode code
    points.  A stray `%` is left as a literal `%` byte rather than raising.
    """
    out = bytearray()
    i = 0
    n = len(value)
    while i < n:
        c = value[i]
        if c == "%" and i + 2 < n:
            hexpart = value[i + 1 : i + 3]
            try:
                out.append(int(hexpart, 16))
                i += 3
                continue
            except ValueError:
                pass
        out.append(ord(c))
        i += 1
    return bytes(out)


def parse_param_segments(
    params: Mapping[str, str],
) -> Dict[str, List[Tuple[int, bool, str, str, str]]]:
    """Group RFC 2231 parameter entries by logical name.

    Returns a dict mapping logical name -> sorted list of
    ``(index, encoded_flag, charset, language, value)`` tuples.  Parameters
    without a `*` in the key are ignored; they are not RFC 2231.
    """
    grouped: Dict[str, List[Tuple[int, bool, str, str, str]]] = {}
    for key, raw_value in params.items():
        if _is_continuation_key(key):
            star = key.rfind("*")
            idx = int(key[star + 1 :])
            base = _base_name(key)
            grouped.setdefault(base, []).append(
                (idx, False, "", "", raw_value)
            )
        elif _is_encoded_key(key):
            star = key.rfind("*")
            rest = key[:star]
            prev_star = rest.rfind("*")
            if prev_star > 0 and rest[prev_star + 1 : star].isdigit():
                idx = int(rest[prev_star + 1 : star])
            else:
                idx = 0
            base = _base_name(key)
            charset, language, remainder = _parse_language_tag(raw_value)
            grouped.setdefault(base, []).append(
                (idx, True, charset, language, remainder)
            )
        # else: plain parameter, not RFC 2231 — skip.

    for entries in grouped.values():
        entries.sort(key=lambda e: e[0])
    return grouped


def join_continuation(
    segments: List[Tuple[int, bool, str, str, str]],
) -> Tuple[str, str, bytes]:
    """Join grouped segments into (charset, language, raw_bytes).

    `segments` is the list produced by :func:`parse_param_segments` for a single
    logical name.  Raises `ValueError` on duplicate indices or on an encoded
    segment missing the `charset'lang'` prefix when no earlier segment set one.
    """
    seen = set()
    charset = ""
    language = ""
    parts: List[bytes] = []

    for idx, encoded, seg_charset, seg_lang, value in segments:
        if idx in seen:
            raise ValueError(
                "duplicate RFC 2231 continuation index %d" % idx
            )
        seen.add(idx)

        if encoded:
            if seg_charset or seg_lang or idx == 0:
                # First encoded segment carries the charset/language tag;
                # later encoded segments must not repeat it but we tolerate
                # it if present (some encoders are sloppy).
                if seg_charset:
                    charset = seg_charset
                if seg_lang:
                    language = seg_lang
                if idx == 0 and not seg_charset and not seg_lang:
                    raise ValueError(
                        "encoded RFC 2231 segment 0 lacks charset tag"
                    )
            parts.append(unpercent(value))
        else:
            # Bare continuation segment: literal text, not percent-encoded.
            parts.append(value.encode("latin-1", "replace"))

    return charset, language, b"".join(parts)


def decode_segments(
    segments: List[Tuple[int, bool, str, str, str]],
) -> str:
    """Decode joined segments into a `str`.

    If no charset was declared we decode as latin-1 (i.e. raw octets).  If the
    declared charset is unknown to Python we fall back to latin-1 rather than
    raising, matching the lenient behaviour of real mail clients.
    """
    charset, _language, raw = join_continuation(segments)
    if not charset:
        return raw.decode("latin-1")
    try:
        return raw.decode(charset)
    except (LookupError, UnicodeDecodeError):
        return raw.decode("latin-1")


def decode_rfc2231_param(
    params: Mapping[str, str],
    name: str,
) -> str:
    """Decode the RFC 2231 parameter `name` from `params`.

    `params` is a flat mapping as produced by a header parser.  If `name` is
    not present as an RFC 2231 parameter, `None` is returned.  If `name` IS
    present but also has a plain (non-`*`) variant, the plain variant is
    ignored — RFC 2231 encoding takes precedence per our stated choice #2.
    """
    grouped = parse_param_segments(params)
    if name not in grouped:
        return None  # type: ignore[return-value]
    return decode_segments(grouped[name])
