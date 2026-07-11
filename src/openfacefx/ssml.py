"""SSML input adapter (issue #52): drive lip-sync from the same SSML you feed TTS.

A thin front-end over the issue-#7 text-tag system -- **not** a new animation
path. Game / VO pipelines already author their TTS input as SSML (the W3C Speech
Synthesis Markup Language that Azure, Google and Amazon Polly consume), and
OpenFaceFX's text tags were deliberately modelled on it. :func:`parse_ssml`
parses an SSML document with the stdlib ``xml.etree.ElementTree`` and returns the
*same* ``(clean_text, List[Tag])`` the bracket-tag front-end
(:func:`openfacefx.texttags.parse_tagged_transcript`) yields, so the unchanged
naive pipeline lip-syncs it identically. Every supported construct maps onto an
existing #7 primitive -- nothing new is invented:

  * ``<break time="500ms"/>`` / ``strength`` -> a ``pause`` :class:`~openfacefx.
    texttags.Tag` (the ``[pause:S]`` path); ``time`` (ms/s) wins over ``strength``.
  * ``<emphasis level="strong">...</emphasis>`` -> an ``emphasis`` Tag, ``level``
    mapped to an issue-#18 dominance ``strength`` (see :data:`EMPHASIS_STRENGTH`);
    a level-less ``<emphasis>`` carries no strength, so the #7 default applies.
  * ``<sub alias="World Health Organization">WHO</sub>`` -> the spoken ``alias``
    is substituted into ``clean_text`` for G2P.
  * ``<mark name="hit"/>`` -> a named ``phrase`` (marker) Tag; ``<p>`` / ``<s>``
    -> a ``phrase`` boundary at the element start.
  * ``<say-as interpret-as=...>...</say-as>`` -> the enclosed text is routed
    through :func:`openfacefx.qa.normalize_transcript` (the #23 ASCII fold).

Deferred / out of scope: ``<phoneme alphabet="ipa" ph="...">`` pronunciation
override belongs to the multi-language framework (#8) + ``ipa.py`` -- its text is
passed through here and the ``ph`` ignored. Any unknown element degrades to its
text content and never crashes; malformed XML raises a clear ``ValueError`` at
the boundary. Deterministic and stdlib-only (``xml.etree`` / ``re``); numpy is
never imported, so ``<speak>hello world</speak>`` with no constructs yields
``("hello world", [])`` -- byte-identical to the plain naive path.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

from .qa import normalize_transcript
from .texttags import WORD_RE, Tag

#: SSML ``<emphasis level=...>`` -> issue-#18 dominance strength (gain
#: ``1 + strength``). ``moderate`` matches the bracket default; ``reduced`` /
#: ``none`` floor at 0 (this pass only amplifies, it cannot de-emphasise). A
#: level that is absent or unknown emits **no** strength param, so the #7 default
#: (:data:`openfacefx.texttags.DEFAULT_STRENGTH`) applies downstream.
EMPHASIS_STRENGTH: Dict[str, str] = {
    "strong": "1.0",
    "moderate": "0.5",
    "reduced": "0.0",
    "none": "0.0",
}

#: SSML ``<break strength=...>`` -> seconds of silence, used only when no explicit
#: ``time=`` is given. ``medium`` is the bare-``<break/>`` default.
BREAK_STRENGTH: Dict[str, float] = {
    "none": 0.0,
    "x-weak": 0.1,
    "weak": 0.25,
    "medium": 0.5,
    "strong": 0.75,
    "x-strong": 1.0,
}
_DEFAULT_BREAK = BREAK_STRENGTH["medium"]

# A document "looks like SSML" (the naive --text auto-detect) when it opens with
# a <speak> root, optionally behind an <?xml ...?> declaration.
_SPEAK_RE = re.compile(r"^\s*(?:<\?xml[^>]*\?>\s*)?<speak(?:[\s/>])", re.IGNORECASE)
# A DOCTYPE can smuggle entity-expansion ("billion laughs"); SSML never uses one,
# so reject it outright rather than hand it to expat.
_DOCTYPE_RE = re.compile(r"<!DOCTYPE", re.IGNORECASE)


def looks_like_ssml(text: str) -> bool:
    """True if ``text`` opens with a ``<speak>`` root -- the auto-detect the naive
    command uses to enable SSML parsing without an explicit ``--ssml`` flag."""
    return bool(text) and bool(_SPEAK_RE.match(text))


def _localname(tag) -> str:
    """The namespace-stripped, lower-cased element name (``{ns}emphasis`` ->
    ``emphasis``), so namespaced SSML dispatches the same as a bare document.
    Non-string tags (ET comment / PI callables) collapse to ``""``."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _break_time_seconds(value: str) -> float:
    """SSML break ``time`` -> seconds: ``"500ms"`` -> 0.5, ``"2s"`` / ``"2"`` ->
    2.0. An unparseable value falls back to 0 (a no-op pause), never raising."""
    s = (value or "").strip().lower()
    try:
        if s.endswith("ms"):
            return float(s[:-2]) / 1000.0
        if s.endswith("s"):
            return float(s[:-1])
        return float(s)
    except ValueError:
        return 0.0


def _break_seconds(elem) -> float:
    """Silence for a ``<break>``: ``time`` (ms/s) wins, else the ``strength``
    table, else the ``medium`` default."""
    t = elem.get("time")
    if t is not None:
        return _break_time_seconds(t)
    strength = elem.get("strength")
    if strength is not None:
        return BREAK_STRENGTH.get(strength.strip().lower(), _DEFAULT_BREAK)
    return _DEFAULT_BREAK


class _Walker:
    """Accumulates the clean spoken text and the anchored :class:`Tag` list while
    walking the SSML tree in document order -- mirroring how
    :func:`~openfacefx.texttags.parse_tagged_transcript` counts words between
    tags, so both front-ends produce identical output."""

    def __init__(self) -> None:
        self._parts: List[str] = []
        self.tags: List[Tag] = []
        self.nwords = 0

    def text(self, s) -> None:
        """Append spoken text, advancing the word count with the same
        ``[A-Za-z']+`` tokenizer #7 anchors tags with. ``None`` (an empty
        ``elem.text`` / ``tail``) is ignored."""
        if not s:
            return
        self._parts.append(s)
        self.nwords += len(WORD_RE.findall(s))

    def clean(self) -> str:
        """The spoken words with all whitespace collapsed to single spaces -- SSML
        is commonly pretty-printed across lines, and word indices were counted on
        the raw fragments so this never shifts a tag (``WORD_RE`` spans no
        whitespace)."""
        return " ".join("".join(self._parts).split())


def _walk(elem, w: _Walker) -> None:
    """Depth-first walk in document order: the element's leading text, then each
    child (dispatched by name) followed by that child's trailing ``tail``."""
    w.text(elem.text)
    for child in elem:
        _dispatch(child, w)
        w.text(child.tail)


def _dispatch(elem, w: _Walker) -> None:
    """Map one SSML element onto the #7 primitives, recursing for its content."""
    name = _localname(elem.tag)
    if name in ("s", "p"):                       # sentence / paragraph boundary
        w.tags.append(Tag("phrase", w.nwords, name="phrase"))
        _walk(elem, w)
    elif name == "break":                        # -> [pause:S] (void element)
        w.tags.append(Tag("pause", w.nwords, value=_break_seconds(elem)))
    elif name == "mark":                         # -> named phrase marker (void)
        w.tags.append(Tag("phrase", w.nwords, name=elem.get("name") or "phrase"))
    elif name == "emphasis":                     # -> [emphasis strength=..]word[/]
        start = w.nwords
        params: Dict[str, str] = {}
        level = elem.get("level")
        if level is not None:
            key = level.strip().lower()
            if key in EMPHASIS_STRENGTH:         # unknown level -> #7 default
                params["strength"] = EMPHASIS_STRENGTH[key]
        _walk(elem, w)
        w.tags.append(Tag("emphasis", start, end_word_index=w.nwords,
                          name="emphasis", params=params))
    elif name == "sub":                          # speak the alias, not the text
        alias = elem.get("alias")
        if alias is not None:
            w.text(alias)
        else:
            _walk(elem, w)
    elif name == "say-as":                       # normalize the enclosed text
        normalized, _ = normalize_transcript("".join(elem.itertext()))
        w.text(normalized)
    else:
        # <phoneme> (deferred to #8 + ipa.py) and any unknown element both degrade
        # to their text content -- never a crash, the ph/attributes are ignored.
        _walk(elem, w)


def parse_ssml(text: str) -> Tuple[str, List[Tag]]:
    """Parse an SSML document into ``(clean_text, tags)``.

    Returns the same pair :func:`openfacefx.texttags.parse_tagged_transcript`
    yields for the equivalent bracket transcript, ready for the unchanged naive
    pipeline: ``clean_text`` is the spoken words (aliases substituted, say-as
    normalized, tags removed) and ``tags`` the deterministic :class:`Tag` list
    anchored to word indices in ``clean_text``. Raises ``ValueError`` on malformed
    XML (or a DOCTYPE); unknown elements pass through as their text content."""
    if _DOCTYPE_RE.search(text or ""):
        raise ValueError("SSML with a DOCTYPE is not supported")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"malformed SSML: {exc}") from exc
    w = _Walker()
    # The root (<speak>) is a plain container: its text and children are walked,
    # but the root element itself is never a construct -- only <s>/<p> children
    # drop a phrase boundary.
    _walk(root, w)
    return w.clean(), w.tags
