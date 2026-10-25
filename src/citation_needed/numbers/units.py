"""Unit vocabulary for the numbers checker.

Each canonical unit maps to a dimension and, where the conversion is exact, a factor
to that dimension's base unit. Units in the same dimension with factors are compared
after conversion (0.5 g matches 500 mg). Units without a factor only match
themselves: months and years are not a fixed number of days, and concentrations are
left alone rather than guessed at.
"""
from __future__ import annotations

import re
from decimal import Decimal

# canonical -> (dimension, factor to base or None)
UNITS: dict[str, tuple[str, Decimal | None]] = {
    "%": ("percent", Decimal(1)),
    "pp": ("percentage_point", Decimal(1)),
    "fold": ("fold", Decimal(1)),
    "USD": ("usd", Decimal(1)),
    "EUR": ("eur", Decimal(1)),
    "GBP": ("gbp", Decimal(1)),
    "ng": ("mass", Decimal("1e-9")),
    "µg": ("mass", Decimal("1e-6")),
    "mg": ("mass", Decimal("1e-3")),
    "g": ("mass", Decimal(1)),
    "kg": ("mass", Decimal(1000)),
    "µL": ("volume", Decimal("1e-6")),
    "mL": ("volume", Decimal("1e-3")),
    "dL": ("volume", Decimal("0.1")),
    "L": ("volume", Decimal(1)),
    "s": ("time", Decimal(1)),
    "min": ("time", Decimal(60)),
    "h": ("time", Decimal(3600)),
    "day": ("time", Decimal(86400)),
    "week": ("time", Decimal(604800)),
    "month": ("month", None),
    "year": ("year", None),
    "mm": ("length", Decimal("1e-3")),
    "cm": ("length", Decimal("1e-2")),
    "m": ("length", Decimal(1)),
    "km": ("length", Decimal(1000)),
    "mg/dL": ("mg/dL", None),
    "mg/L": ("mg/L", None),
    "mg/kg": ("mg/kg", None),
    "ng/mL": ("ng/mL", None),
    "µg/mL": ("µg/mL", None),
    "mmol/L": ("mmol/L", None),
    "µmol/L": ("µmol/L", None),
    "nmol/L": ("nmol/L", None),
    "mEq/L": ("mEq/L", None),
    "IU": ("IU", None),
    "U/L": ("U/L", None),
    "mmHg": ("mmHg", None),
    "bpm": ("bpm", None),
    "kcal": ("kcal", None),
    "°C": ("celsius", None),
    "°F": ("fahrenheit", None),
}

# Written form -> canonical. Matched case-sensitively first, then (for word units
# only) case-insensitively, so "mL" and "ml" both work but "M" never becomes "m".
_ALIASES: dict[str, str] = {
    "%": "%", "percent": "%", "per cent": "%", "pct": "%",
    "percentage points": "pp", "percentage point": "pp", "pp": "pp", "p.p.": "pp",
    "fold": "fold", "x": "fold", "×": "fold",
    "USD": "USD", "dollars": "USD", "EUR": "EUR", "euros": "EUR", "GBP": "GBP", "pounds": "GBP",
    "ng": "ng", "nanograms": "ng",
    "µg": "µg", "μg": "µg", "ug": "µg", "mcg": "µg", "micrograms": "µg",
    "mg": "mg", "milligrams": "mg", "milligram": "mg",
    "g": "g", "grams": "g", "gram": "g",
    "kg": "kg", "kilograms": "kg", "kilogram": "kg",
    "µL": "µL", "μL": "µL", "uL": "µL", "microliters": "µL",
    "mL": "mL", "ml": "mL", "milliliters": "mL", "millilitres": "mL",
    "dL": "dL", "dl": "dL",
    "L": "L", "liters": "L", "litres": "L", "liter": "L", "litre": "L",
    "s": "s", "sec": "s", "secs": "s", "second": "s", "seconds": "s",
    "min": "min", "mins": "min", "minute": "min", "minutes": "min",
    "h": "h", "hr": "h", "hrs": "h", "hour": "h", "hours": "h",
    "day": "day", "days": "day", "d": "day",
    "week": "week", "weeks": "week", "wk": "week", "wks": "week",
    "month": "month", "months": "month", "mo": "month",
    "year": "year", "years": "year", "yr": "year", "yrs": "year", "y": "year",
    "mm": "mm", "millimeters": "mm", "cm": "cm", "centimeters": "cm",
    "m": "m", "meters": "m", "metres": "m", "km": "km", "kilometers": "km",
    "mg/dL": "mg/dL", "mg/dl": "mg/dL", "mg/L": "mg/L", "mg/l": "mg/L", "mg/kg": "mg/kg",
    "ng/mL": "ng/mL", "ng/ml": "ng/mL", "µg/mL": "µg/mL", "μg/mL": "µg/mL", "ug/mL": "µg/mL",
    "mmol/L": "mmol/L", "mmol/l": "mmol/L", "mM": "mmol/L",
    "µmol/L": "µmol/L", "μmol/L": "µmol/L", "umol/L": "µmol/L", "nmol/L": "nmol/L",
    "mEq/L": "mEq/L", "IU": "IU", "U/L": "U/L",
    "mmHg": "mmHg", "mm Hg": "mmHg", "bpm": "bpm", "kcal": "kcal",
    "°C": "°C", "° C": "°C", "°F": "°F", "° F": "°F",
}
_WORD_UNIT = re.compile(r"^[A-Za-z ]{3,}$")

# Single letters are only units when attached or followed by a clear boundary, and
# the regex below already requires a non-alphanumeric next character.
_ALIAS_PATTERN = "|".join(re.escape(a) for a in sorted(_ALIASES, key=len, reverse=True))
UNIT_RE = re.compile(
    rf"(?:[    ]|-)?(?P<unit>{_ALIAS_PATTERN})(?![A-Za-z0-9µμ/])",
    re.IGNORECASE,
)

# Attached letter scales ("8B", "3.5k", "2bn") and spelled-out scale words.
SCALE_WORDS = {"thousand": Decimal(1000), "million": Decimal(10) ** 6,
               "billion": Decimal(10) ** 9, "trillion": Decimal(10) ** 12}
SCALE_SUFFIX = {"k": Decimal(1000), "K": Decimal(1000), "M": Decimal(10) ** 6, "mn": Decimal(10) ** 6,
                "B": Decimal(10) ** 9, "bn": Decimal(10) ** 9, "b": Decimal(10) ** 9}


def canonical_unit(written: str) -> str | None:
    if written in _ALIASES:
        return _ALIASES[written]
    if _WORD_UNIT.match(written):
        low = written.lower()
        for alias, canon in _ALIASES.items():
            if alias.lower() == low and _WORD_UNIT.match(alias):
                return canon
    return None


def same_dimension(a: str, b: str) -> bool:
    return UNITS[a][0] == UNITS[b][0]


def factor(unit: str) -> Decimal | None:
    return UNITS[unit][1]


def convertible_units(unit: str) -> list[str]:
    """Every unit that `unit` can be converted to exactly (including itself)."""
    dim, f = UNITS[unit]
    if f is None:
        return [unit]
    return [u for u, (d, g) in UNITS.items() if d == dim and g is not None]
