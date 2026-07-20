"""FaceFX-style link (response) functions — closed-form curves that reshape how a
driver value maps to a target's output, beyond the linear gain+offset (#68).

FaceFX Face Graph links carry a *link function* that reshapes one node's value as
it drives another. OpenFaceFX already applies the LINEAR case everywhere a channel
value is tuned — ``retarget.apply_adjust`` and ``mapping.Target`` both compute
``clamp(gain*v + offset, lo, hi)``, i.e. ``linear(v, m=gain, b=offset)``. This
module is the rest of the family, so an integrator gets FaceFX-grade control over a
target's response. Applied exactly where that clamp is applied today; the output is
clamped to the target's range afterwards, as before.

Formulas (``x`` is the input value; the output is clamped downstream):

  * ``linear``          — ``m*x + b``                     (m=1, b=0)
  * ``quadratic``       — ``m*x^2 + b``                   (m=1, b=0)
  * ``cubic``           — ``m*x^3 + b``                   (m=1, b=0)
  * ``sqrt``            — ``m*sqrt(max(x, 0)) + b``       (m=1, b=0)
  * ``negate``          — ``-x``                          (FaceFX: linear m=-1, b=0)
  * ``constant``        — ``c``                           (c=0; ignores x)
  * ``clamped_linear``  — a line of slope ``m`` through ``(clampx, clampy)``, held
    constant at ``clampy`` on one side of ``clampx`` (``clampdir`` = ``"right"``
    holds the right side constant, ``"left"`` the left).

FaceFX documents exact formulas only for linear/negate/constant and the
``clamped_linear`` parameter names; for quadratic/cubic/sqrt it publishes graphs,
not equations, so these closed forms follow the documented ``m*shape(x)+b``
convention and match those graphs. FaceFX's ``inverse`` (no published formula;
reciprocal semantics ill-suited to ``[0, 1]`` weights) and the two-input
``corrective`` / deprecated ``one-clamp`` links are intentionally out of scope.

Source: https://facefx.github.io/documentation/doc/link-functions
Values work on Python scalars and numpy arrays alike, so both the per-keyframe
(:func:`retarget.apply_adjust`) and per-column (:func:`curves.reduce_to_track`)
call sites use the same functions. Deterministic on py3.9/3.13.
"""

from __future__ import annotations

import math
from typing import Dict, Tuple

import numpy as np


def _linear(x, m=1.0, b=0.0):
    return m * x + b


def _quadratic(x, m=1.0, b=0.0):
    return m * np.square(x) + b


def _cubic(x, m=1.0, b=0.0):
    return m * np.power(x, 3) + b


def _sqrt(x, m=1.0, b=0.0):
    return m * np.sqrt(np.maximum(x, 0.0)) + b


def _negate(x):
    return -x


def _constant(x, c=0.0):
    return c + 0.0 * x                      # scalar -> c; array -> c-filled array


def _clamped_linear(x, m=1.0, clampx=1.0, clampy=1.0, clampdir="right"):
    line = clampy + m * (x - clampx)
    if clampdir == "right":                 # constant clampy to the RIGHT of clampx
        return np.where(x <= clampx, line, clampy)
    return np.where(x >= clampx, line, clampy)   # constant to the LEFT


_FUNCS = {
    "linear": _linear, "quadratic": _quadratic, "cubic": _cubic, "sqrt": _sqrt,
    "negate": _negate, "constant": _constant, "clamped_linear": _clamped_linear,
}

#: Each link function's accepted parameters and their defaults (for validation).
LINK_PARAMS: Dict[str, Dict[str, object]] = {
    "linear": {"m": 1.0, "b": 0.0},
    "quadratic": {"m": 1.0, "b": 0.0},
    "cubic": {"m": 1.0, "b": 0.0},
    "sqrt": {"m": 1.0, "b": 0.0},
    "negate": {},
    "constant": {"c": 0.0},
    "clamped_linear": {"m": 1.0, "clampx": 1.0, "clampy": 1.0, "clampdir": "right"},
}
LINK_FUNCTIONS: Tuple[str, ...] = tuple(LINK_PARAMS)


def normalize_link(spec: Dict) -> Tuple[str, Dict]:
    """Validate a ``{"function": name, ...params}`` dict, returning
    ``(name, params)`` with every parameter filled from its default. Raises
    ``ValueError`` on an unknown function, an unknown/mistyped parameter, a
    non-finite number, or a bad ``clampdir`` — so callers validate once, here."""
    if not isinstance(spec, dict) or "function" not in spec:
        raise ValueError("link spec must be an object with a 'function' key")
    name = spec["function"]
    if name not in LINK_PARAMS:
        raise ValueError(f"unknown link function {name!r}; choose from "
                         f"{list(LINK_FUNCTIONS)}")
    allowed = LINK_PARAMS[name]
    extra = set(spec) - {"function"} - set(allowed)
    if extra:
        raise ValueError(f"link {name!r} has unknown parameter(s) {sorted(extra)}; "
                         f"allowed: {sorted(allowed) or '(none)'}")
    params: Dict[str, object] = {}
    for key, default in allowed.items():
        val = spec.get(key, default)
        if key == "clampdir":
            if val not in ("left", "right"):
                raise ValueError(f"link {name!r} clampdir must be 'left' or 'right', "
                                 f"got {val!r}")
        elif isinstance(val, bool) or not isinstance(val, (int, float)) \
                or not math.isfinite(val):
            raise ValueError(f"link {name!r} parameter {key!r} must be a finite "
                             f"number, got {val!r}")
        else:
            val = float(val)
        params[key] = val
    return name, params


def apply_link(x, name: str, params: Dict):
    """Evaluate link function ``name`` on ``x`` (scalar or numpy array) with the
    validated ``params`` from :func:`normalize_link`."""
    return _FUNCS[name](x, **params)
