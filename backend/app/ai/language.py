"""Lightweight language detection for English / Hindi / Hinglish.

This is a cheap heuristic used only as a *hint* to the model (and stored as
message metadata). The model itself still reads the message and decides; this
just helps it mirror the user's language consistently.
"""

import re

from app.models.enums import Language

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LATIN = re.compile(r"[A-Za-z]")
_WORD = re.compile(r"[a-z]+")

# Romanised Hindi words that are rare in English. Deliberately excludes ambiguous
# words that are also English or ambiguous ("main", "the", "to", "do", "ho", "hi", "tab", "mat").
_HINDI_ROMAN_MARKERS = frozenset(
    """
    yaar yar arre arey acha accha achha haan nahi nahin kya kyu kyun kyon kaise kaisa kaisi
    kaun kab kahan mujhe mujhko mera meri mere tera teri tere tum tumhe tumhara aap aapka
    hum humko hamara hai hain tha thi hoon hun raha rahi rahe karna karo karu karun
    kar karke karta karti samajh samjha samjho aaj kal abhi bahut bohot thoda zyada kuch
    sab sabse bhi bhai matlab chal chalo dekho dekh bata batao bolo suno pata lekin magar
    toh woh yeh wahi isliye kyunki jaldi ghar khana kaam padhai naukri dost achhi bura
    theek thik sahi galat bilkul shayad zaroor jab agar varna
    """.split()  # noqa: SIM905 - a word list reads better than a 100-item literal
)


# Conversational markers that signal a Hinglish register even in otherwise-English text
# ("Yaar, I'm really confused..."), so the reply can match that tone.
_HINGLISH_INTERJECTIONS = frozenset({"yaar", "yar", "arre", "arey", "bhai", "accha", "achha"})


def detect_language(text: str) -> Language | None:
    """Best-effort guess. Returns None when there is too little text to tell."""
    devanagari = len(_DEVANAGARI.findall(text))
    latin = len(_LATIN.findall(text))
    if devanagari + latin < 2:
        return None
    # Devanagari with a few English loanwords ("मेरा interview कल है") is still Hindi.
    if devanagari >= 0.3 * (devanagari + latin):
        return Language.HINDI

    words = _WORD.findall(text.lower())
    if not words:
        return Language.HINDI if devanagari else None
    markers = sum(1 for w in words if w in _HINDI_ROMAN_MARKERS)
    has_interjection = any(w in _HINGLISH_INTERJECTIONS for w in words)
    if devanagari or markers >= 2 or has_interjection or (markers == 1 and len(words) <= 6):
        return Language.HINGLISH
    return Language.ENGLISH
