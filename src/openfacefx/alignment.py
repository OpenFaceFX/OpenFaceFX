"""Forced alignment: producing time-stamped phonemes.

This is the model-heavy stage of the pipeline. Rather than reinvent a speech
recogniser, OpenFaceFX defines a small ``PhonemeSegment`` contract and provides:

  * ``NaiveAligner`` -- no acoustic model. Given the phonemes for an utterance
    and the utterance duration (or word timings), it distributes phonemes over
    time using per-phoneme duration priors. Good enough to see the pipeline end
    to end; not accurate lip-sync.

  * ``load_mfa_textgrid`` -- parse the output of the Montreal Forced Aligner
    (the recommended production aligner). MFA gives real acoustic alignment.

You can add adapters for Gentle, wav2vec2, or Whisper the same way: produce a
list of ``PhonemeSegment`` and the rest of the pipeline is unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from .phonemes import strip_stress, is_vowel, SILENCE


@dataclass
class PhonemeSegment:
    phoneme: str        # ARPAbet, may carry stress digit
    start: float        # seconds
    end: float          # seconds
    confidence: Optional[float] = None   # aligner score in [0,1], if provided

    @property
    def dur(self) -> float:
        return self.end - self.start


# Rough relative-duration priors (unitless). Vowels last longer than stops.
_DUR_PRIOR = {
    "AA": 1.6, "AE": 1.5, "AH": 1.0, "AO": 1.6, "AW": 1.8, "AY": 1.8,
    "EH": 1.3, "ER": 1.5, "EY": 1.6, "IH": 1.1, "IY": 1.4, "OW": 1.6,
    "OY": 1.8, "UH": 1.2, "UW": 1.5,
    "CH": 1.1, "JH": 1.0, "SH": 1.1, "ZH": 1.0, "S": 1.1, "Z": 1.0,
    "F": 1.0, "V": 0.9, "TH": 1.0, "DH": 0.8, "HH": 0.7,
    "P": 0.6, "B": 0.6, "T": 0.6, "D": 0.6, "K": 0.7, "G": 0.7,
    "M": 0.9, "N": 0.9, "NG": 0.9, "L": 0.9, "R": 1.0, "W": 0.8, "Y": 0.8,
    SILENCE: 1.0,
}


def _prior(ph: str) -> float:
    return _DUR_PRIOR.get(strip_stress(ph).upper(), 1.0)


class NaiveAligner:
    """Distribute phonemes across a time span using duration priors."""

    def align(
        self,
        phonemes: List[str],
        total_duration: float,
        start: float = 0.0,
    ) -> List[PhonemeSegment]:
        if not phonemes:
            return []
        weights = [_prior(p) for p in phonemes]
        wsum = sum(weights) or 1.0
        segs: List[PhonemeSegment] = []
        t = start
        for p, w in zip(phonemes, weights):
            d = total_duration * (w / wsum)
            segs.append(PhonemeSegment(p, t, t + d))
            t += d
        return segs

    def align_words(
        self,
        words: List[tuple],  # (phoneme_list, word_start, word_end)
    ) -> List[PhonemeSegment]:
        """When you already have word-level timings (common from ASR), align
        phonemes within each word span. Much better than utterance-level."""
        segs: List[PhonemeSegment] = []
        for phones, ws, we in words:
            segs.extend(self.align(phones, we - ws, start=ws))
        return segs


def load_mfa_textgrid(path: str, tier: str = "phones") -> List[PhonemeSegment]:
    """Parse a Praat TextGrid produced by the Montreal Forced Aligner.

    Only the interval tier named ``tier`` is read. Empty / silence intervals
    become the ``sil`` phoneme so the mouth relaxes between words.
    """
    text = open(path, "r", encoding="utf-8").read()
    # Find the requested interval tier block.
    tier_pat = re.compile(
        r'name = "%s".*?intervals: size = \d+(.*?)(?:item \[\d+\]:|$)' % re.escape(tier),
        re.DOTALL,
    )
    m = tier_pat.search(text)
    if not m:
        raise ValueError(f"tier {tier!r} not found in TextGrid")
    block = m.group(1)
    seg_pat = re.compile(
        r"xmin = ([\d.]+)\s*xmax = ([\d.]+)\s*text = \"([^\"]*)\"",
        re.DOTALL,
    )
    segs: List[PhonemeSegment] = []
    for xmin, xmax, label in seg_pat.findall(block):
        label = label.strip()
        ph = label if label else SILENCE
        segs.append(PhonemeSegment(ph, float(xmin), float(xmax)))
    return segs
