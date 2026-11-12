"""Melting temperature, nearest-neighbour (SantaLucia 1998 unified parameters).

Same convention SnapGene uses for its primer defaults: 0.25 µM oligo in 50 mM
Na+. A match shorter than the length threshold still counts when its Tm reaches
the user's minimum, which is how SnapGene's own HybridizationParams work.
"""
from __future__ import annotations

import math

R = 1.987  # cal/(K·mol)
DEFAULT_CONC = 0.25e-6   # M, oligo
DEFAULT_NA = 0.05        # M, monovalent cation

# dH (kcal/mol), dS (cal/(K·mol))
NN = {
    "AA": (-7.9, -22.2), "TT": (-7.9, -22.2),
    "AT": (-7.2, -20.4), "TA": (-7.2, -21.3),
    "CA": (-8.5, -22.7), "TG": (-8.5, -22.7),
    "GT": (-8.4, -22.4), "AC": (-8.4, -22.4),
    "CT": (-7.8, -21.0), "AG": (-7.8, -21.0),
    "GA": (-8.2, -22.2), "TC": (-8.2, -22.2),
    "CG": (-10.6, -27.2), "GC": (-9.8, -24.4),
    "GG": (-8.0, -19.9), "CC": (-8.0, -19.9),
}
INIT_GC = (0.1, -2.8)   # initiation with a terminal G·C
INIT_AT = (2.3, 4.1)    # initiation with a terminal A·T


def melting_temp(seq: str, conc: float = DEFAULT_CONC, na: float = DEFAULT_NA) -> float:
    """Tm in °C, or 0.0 for a sequence too short or with unknown letters."""
    s = (seq or "").upper()
    if len(s) < 2 or any(c not in "ACGT" for c in s):
        return 0.0
    dh, ds = 0.0, 0.0
    for a, b in ((INIT_AT if s[0] in "AT" else INIT_GC), (INIT_AT if s[-1] in "AT" else INIT_GC)):
        dh += a
        ds += b
    for i in range(len(s) - 1):
        pair = NN.get(s[i:i + 2])
        if pair is None:
            return 0.0
        dh += pair[0]
        ds += pair[1]
    try:
        tm = (dh * 1000.0) / (ds + R * math.log(conc / 4.0)) - 273.15
    except (ValueError, ZeroDivisionError):
        return 0.0
    return tm + 16.6 * math.log10(max(na, 1e-6))


def gc_percent(seq: str) -> float:
    s = (seq or "").upper()
    return 100.0 * sum(c in "GC" for c in s) / len(s) if s else 0.0
