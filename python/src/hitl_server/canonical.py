"""Deterministic serialization shared (by specification) with the TypeScript twin.

The audit chain is only comparable across implementations if both produce the same
bytes for the same entry. Python's json module and JavaScript's JSON.stringify agree on
string escaping (with ensure_ascii=False) and both emit the shortest round-trip digits
for a float, but they lay those digits out differently: Python writes "1.0" and "1e-07"
where JavaScript writes "1" and "1e-7", and they switch to exponent notation at different
magnitudes. fmt_num therefore re-implements ECMAScript's Number-to-String layout on top
of Python's shortest digits. spec/fixtures/canonical_vectors.json pins the result.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from typing import Any


def round4(x: float) -> float:
    # floor(x + 0.5) rather than round(): Python rounds half-to-even, JavaScript's
    # Math.round rounds half-up. Using the same IEEE operations in the same order gives
    # bit-identical results in both runtimes.
    return math.floor(x * 10000 + 0.5) / 10000


def fmt_num(x: int | float) -> str:
    """Render a number exactly as JavaScript's String(x) does (ECMA-262 Number::toString)."""
    if isinstance(x, bool):
        raise TypeError("bool is not a number here")
    if isinstance(x, int):
        return str(x)
    if not math.isfinite(x):
        raise ValueError("non-finite numbers cannot be canonicalized")
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    # repr() yields the shortest digit string that round-trips; Decimal splits it.
    _, digit_tuple, exp = Decimal(repr(abs(x))).normalize().as_tuple()
    digits = "".join(str(d) for d in digit_tuple)
    k = len(digits)
    n = k + int(exp)  # value = 0.digits * 10**n
    if k <= n <= 21:
        body = digits + "0" * (n - k)
    elif 0 < n <= 21:
        body = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        body = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        mantissa = digits if k == 1 else digits[0] + "." + digits[1:]
        body = f"{mantissa}e{'+' if e >= 0 else '-'}{abs(e)}"
    return sign + body


def canonical_json(value: Any) -> str:
    """Sorted keys, no insignificant whitespace, UTF-8 strings unescaped.

    Keys sort by code point here and by UTF-16 code unit in JavaScript; the two orders
    agree for every key outside the astral planes, which covers all keys this system emits.
    """
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return fmt_num(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(v) for v in value) + "]"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: kv[0])
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + canonical_json(v) for k, v in items) + "}"
    raise TypeError(f"cannot canonicalize {type(value).__name__}")
