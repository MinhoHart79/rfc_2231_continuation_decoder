# rfc_2231_continuation_decoder

Decodes MIME parameter values with RFC 2231 continuation segments and character-set encoding — the `name*0`, `name*1*`, `name*=charset'lang'...` form used in `Content-Disposition` and `Content-Type` headers.

```python
from rfc_2231_continuation_decoder import decode_rfc2231_param

params = {
    "filename*0*": "utf-8'en'report%20",
    "filename*1*": "Q4.pdf",
}
print(decode_rfc2231_param(params, "filename"))  # report Q4.pdf
```

The standard library's `email` package understands RFC 2231 when parsing whole messages, but offers no public helper when you already have a flat `name -> value` dict — which is what you get from most third-party multipart parsers. This library fills that gap with zero dependencies.

The trade-off: this library decodes the RFC 2231 layer only. It does not parse the surrounding header syntax (quoted strings, `;` splitting, parameter folding). You pass it a mapping of parameter keys to raw string values; it reassembles continuations, applies percent-decoding, and decodes the declared charset to a `str`.

Edge you will hit: if the declared charset is unknown to Python, the library falls back to latin-1 (raw octets) rather than raising, matching the lenient behaviour of real mail clients. A plain parameter like `filename` (no trailing `*`) is **not** treated as RFC 2231 and is returned as `None` from `decode_rfc2231_param` even if `filename*0*` siblings exist — encoding is opt-in via the asterisk.

## Exported names

- `decode_rfc2231_param(params: Mapping[str, str], name: str) -> str | None` — top-level entry point.
- `decode_segments(segments) -> str` — decode already-grouped segments.
- `join_continuation(segments) -> (charset, language, bytes)` — reassemble without charset decoding.
- `parse_param_segments(params) -> dict[str, list[tuple]]` — group a flat param dict by logical name.
- `unpercent(value: str) -> bytes` — percent-decode to raw bytes (octets, not code points).

## Install

```bash
pip install rfc_2231_continuation_decoder
```

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

## Design notes

The window stores values eagerly rather than keeping running aggregates. Running
sums drift with floating point over long streams, and recomputing from a small
buffer is cheap enough that the drift is not worth the speed.

