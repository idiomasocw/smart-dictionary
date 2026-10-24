"""Headword rules (the policy of 2026-10-24, docs/policies/categories-and-headwords.md §3.3).

A headword is what search matches, so it's written the way a student types it:
- **No object slot at the end.** *put up with*, not *put up with someone*: the object belongs
  in the entry's patterns ("put up with sb/sth"), and a search for *put up with* must match.
  A slot at the end counts after a verb or a preposition (*drop someone*, *down to someone*),
  not in a phrase like *or something*.
- **Slots inside an expression are spelled as Wiktionary spells them:** *someone*,
  *something*, *someone's*, *one's*, *oneself*, never *somebody*, *sb* or *sth*. (*yourself*
  stays in imperative formulas, as Wiktionary has them: *make yourself at home*.)
- **The dictionary form:** a verb without *to* (*break the ice*), an adjective without *be*
  (*fed up*). Phrases that start with the preposition *to* (*to date*) and phrasal verbs
  with *be* (*be into*, *be on the ball*) keep it, as Oxford Learner's has them.

The console's Add and Approve refuse a headword that breaks them, a curation file mends it,
and the audit raises any that slipped in before.
"""

SLOTS = ("someone", "somebody", "something", "sb", "sth", "sb/sth")
SPELLED = {"somebody": "someone", "somebody's": "someone's", "sb": "someone",
           "sb's": "someone's", "sth": "something", "sb/sth": "someone"}  # fmt: skip
# The words an object slot follows at the end of a headword: prepositions and particles.
BEFORE_OBJECT = frozenset({"about", "across", "after", "against", "along", "around", "at",
                           "away", "back", "by", "down", "for", "from", "in", "into", "off",
                           "of", "on", "onto", "out", "over", "round", "through", "to",
                           "towards", "under", "up", "upon", "with"})  # fmt: skip


def problems(lemma: str, pos: str | None = None) -> list[str]:
    """What breaks the headword rules, in words the owner can act on; empty when nothing."""
    words = lemma.lower().split()
    found = []
    tail = _slot_tail(words, pos)
    if tail:
        better, pattern = mended(lemma, pos)
        found.append(
            f"it ends in the object {' '.join(words[-tail:])!r}: write {better!r}, and give"
            f" the object as a pattern ({pattern})"
        )
    lead = _lead(words, pos)
    if lead:
        found.append(f"drop the leading {lead!r}: write {' '.join(lemma.split()[1:])!r}")
    inner = words[:-tail] if tail else words  # a slot at the end goes anyway
    odd = sorted({word for word in inner if word in SPELLED}) if len(words) > 1 else []
    if odd:
        better = ", ".join(f"{SPELLED[word]!r} for {word!r}" for word in odd)
        found.append(f"write the slot as Wiktionary does: {better}")
    return found


def mended(lemma: str, pos: str | None = None) -> tuple[str, str | None]:
    """The headword that keeps the rules, and the pattern that carries a slot taken off its
    end (or None): *hold out much hope for someone* → *hold out much hope for*, "hold out much
    hope for sb"."""
    words = lemma.split()
    pattern = None
    tail = _slot_tail([word.lower() for word in words], pos)
    if tail:
        slot = words[-1].lower()
        if tail == 3 or slot == "sb/sth":
            short = "sb/sth"
        else:
            short = "sth" if SPELLED.get(slot, slot) == "something" else "sb"
        words = words[:-tail]
        pattern = " ".join([*[SPELLED.get(word.lower(), word) for word in words], short])
    if _lead([word.lower() for word in words], pos):
        words = words[1:]
    words = [SPELLED.get(word.lower(), word) for word in words]
    return " ".join(words), pattern


def mended_kind(headword: str, kind: str) -> str:
    """A mended headword of one word is a plain word: *drop someone*, a phrasal verb, becomes
    *drop*, a verb."""
    return "word" if len(headword.split()) == 1 else kind


def _lead(words: list[str], pos: str | None) -> str | None:
    """A *to* before a verb or a *be* before an adjective, which the dictionary form drops."""
    if len(words) < 2:
        return None
    if words[0] == "to" and pos == "verb":
        return "to"
    if words[0] == "be" and pos == "adjective":
        return "be"
    return None


def _slot_tail(words: list[str], pos: str | None) -> int:
    """How many words at the end are an object slot: 1 (*put up with someone*), 3 (*keep
    away from someone or something*), or 0."""
    if len(words) >= 4 and words[-1] in SLOTS and words[-2] == "or" and words[-3] in SLOTS:
        size = 3
    elif len(words) >= 2 and words[-1] in SLOTS:
        size = 1
    else:
        return 0
    before = words[-size - 1]
    return size if pos == "verb" or before in BEFORE_OBJECT else 0
