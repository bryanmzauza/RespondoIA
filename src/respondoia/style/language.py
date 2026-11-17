"""Tell the language of a message from common words. No dependency, three languages.

The word lists are content analysis, not interface text. They are kept disjoint, so a
word counts for one language only.
"""

import re
from collections import Counter
from collections.abc import Sequence

LANGUAGES = ("pt", "en", "es")


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


_MARKERS = {
    "pt": _words(
        "agora amanhã aqui beleza bem blz boa bom certeza com da dia do em então eu falou "
        "gente hoje isso já mas meu minha mto muito na nao noite não o obrigada obrigado "
        "oi olá os pra sim também tava tb tbm tmj tudo tá tô um uma vai vc vcs voce você "
        "vou é"
    ),
    "en": _words(
        "and are at be can cheers for going gonna good great have hey hi home i is it "
        "just my not of on see sounds thank thanks that the then there thinking this to "
        "today tomorrow tonight u wanna was we what will yes you your"
    ),
    "es": _words(
        "ahora aquí bueno cómo del el entonces es estoy está gracias hola hoy ir las los "
        "mañana muy nos pero por quiero qué sí también tú usted ustedes vas vemos yo"
    ),
}
_WORD = re.compile(r"[^\W\d_]+")


def detect(text: str) -> str | None:
    """None with fewer than 2 marker words or a tie."""
    words = _WORD.findall(text.casefold())
    counts = {lang: sum(word in markers for word in words) for lang, markers in _MARKERS.items()}
    ranked = sorted(counts.items(), key=lambda item: -item[1])
    best, runner_up = ranked[0], ranked[1]
    if best[1] < 2 or best[1] == runner_up[1]:
        return None
    return best[0]


def chat_language(texts: Sequence[str]) -> str | None:
    """The most frequent detected language; None if no text decides or two tie."""
    counts = Counter(lang for lang in map(detect, texts) if lang)
    ranked = counts.most_common(2)
    if not ranked or (len(ranked) == 2 and ranked[0][1] == ranked[1][1]):
        return None
    return ranked[0][0]
