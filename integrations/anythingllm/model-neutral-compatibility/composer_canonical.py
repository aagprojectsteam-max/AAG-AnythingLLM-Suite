#!/usr/bin/env python3
"""RFC 8785-style canonical JSON for signed AAG Composer envelopes.

This module is deliberately scoped to the cross-runtime Composer trust
boundary. Generic AAG hashes continue to use compatibility.stable_json.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from typing import Any


COMPOSER_CANONICALIZATION = "RFC8785-JCS"
MAX_SAFE_INTEGER = 9_007_199_254_740_991


class ComposerCanonicalizationError(ValueError):
    """The value cannot be represented safely as I-JSON/JCS."""


def _validate_unicode(value: str) -> None:
    # RFC 8785 requires invalid Unicode data (including lone surrogates) to
    # terminate canonicalization. Python strings can contain such code points.
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError as error:
        raise ComposerCanonicalizationError("Composer JSON contains invalid Unicode.") from error


def _canonical_string(value: str) -> str:
    _validate_unicode(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _canonical_number(value: int | float) -> str:
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise ComposerCanonicalizationError("Composer integer exceeds the I-JSON safe range.")
        return str(value)

    if not math.isfinite(value):
        raise ComposerCanonicalizationError("Composer JSON contains a non-finite number.")
    if value == 0:
        return "0"

    negative = value < 0
    decimal = Decimal(repr(abs(value)))
    _sign, raw_digits, exponent = decimal.as_tuple()
    digits = "".join(str(digit) for digit in raw_digits)
    while len(digits) > 1 and digits.endswith("0"):
        digits = digits[:-1]
        exponent += 1

    decimal_point = len(digits) + exponent
    scientific_exponent = decimal_point - 1
    if -6 <= scientific_exponent < 21:
        if decimal_point <= 0:
            encoded = "0." + ("0" * -decimal_point) + digits
        elif decimal_point >= len(digits):
            encoded = digits + ("0" * (decimal_point - len(digits)))
        else:
            encoded = digits[:decimal_point] + "." + digits[decimal_point:]
    else:
        mantissa = digits[0] + (("." + digits[1:]) if len(digits) > 1 else "")
        exponent_sign = "+" if scientific_exponent >= 0 else ""
        encoded = f"{mantissa}e{exponent_sign}{scientific_exponent}"
    return ("-" if negative else "") + encoded


def composer_canonical_json(value: Any) -> str:
    """Serialize one Composer value with a cross-runtime JCS contract.

    Object keys are ordered by UTF-16 code units, strings use JSON escaping,
    and numbers use ECMAScript-compatible shortest double formatting. Values
    outside the I-JSON domain fail closed.
    """

    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return _canonical_number(value)
    if isinstance(value, str):
        return _canonical_string(value)
    if isinstance(value, list):
        return "[" + ",".join(composer_canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ComposerCanonicalizationError("Composer object keys must be strings.")
        keys = sorted(value, key=lambda key: key.encode("utf-16-be", "strict"))
        return "{" + ",".join(
            _canonical_string(key) + ":" + composer_canonical_json(value[key]) for key in keys
        ) + "}"
    raise ComposerCanonicalizationError(f"Unsupported Composer JSON type: {type(value).__name__}.")
