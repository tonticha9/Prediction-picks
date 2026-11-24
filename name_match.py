"""
name_match.py - Ulinganishaji wa majina ya timu (v1, Nov 24 2026)

API (AllSportsAPI) na historia yetu wakati mwingine zinaandika jina la timu tofauti
(mfano 'Bradford City' na 'Bradford', 'Vasco da Gama' na 'Vasco', 'FK Bodo/Glimt' na 'Bodo/Glimt').
Faili hili linatoa:
  - tokens(name)                 -> jina lililosafishwa (herufi ndogo, bila alama, bila FC/AFC/CF n.k.)
  - score(a, b)                  -> 0..1 (1 = sawa kabisa)
  - find_fixture(home, away, candidates, get)  -> mechi moja iliyo bora zaidi, au None (hakuna / zaidi ya moja)
Pande ZOTE mbili (nyumbani na ugenini) lazima zilingane; hii inapunguza makosa sana.
Haitegemei Flask wala database.
"""
import difflib
import re
import unicodedata

ACCEPT = 0.85       # kima cha chini cha alama kwa kila upande
AMBIGUOUS_GAP = 0.04  # mechi mbili zikikaribiana hivi, hatuchagui (hatuna uhakika)

_TRANS = str.maketrans({'ø': 'o', 'Ø': 'o', 'æ': 'ae', 'Æ': 'ae', 'ß': 'ss', 'đ': 'd', 'ł': 'l', 'ð': 'd',
                        'þ': 'th', 'œ': 'oe'})
# maneno ya jumla yanayoachwa (si sehemu ya utambulisho)
_DROP = {'fc', 'afc', 'cf', 'sc', 'fk', 'ik', 'if', 'bk', 'sk', 'il', 'cd', 'ud', 'ac', 'as', 'sd', 'cs',
         'the', 'club', 'clube', 'de', 'da', 'do', 'del', 'la', 'el', 'rj', 'sp', 'mg', 'go', 'pr', 'rs', 'ba'}
# maneno yanayobadilishwa kuwa umbo moja
_TOKEN_MAP = {'utd': 'united', 'weds': 'wednesday', 'wed': 'wednesday', 'st': 'saint', 'intl': 'international'}
# majina maalum ambayo hayafikiwi kwa kanuni za jumla (ufunguo = jina lililosafishwa kwa tokens)
_ALIAS = {
    'mk dons': 'milton keynes dons',
    'l orient': 'leyton orient',
    'hamarkameratene': 'hamkam',
    'kfum kameratene oslo': 'kfum oslo',
    'sarpsborg': 'sarpsborg 08',
    'atletico mineiro': 'atletico mg',
    'red bull bragantino': 'bragantino',
    'america': 'america',
}


def tokens(name):
    s = str(name or '').translate(_TRANS)
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace('&', ' and ')
    s = re.sub(r'[^a-z0-9]+', ' ', s).strip()
    raw = s.split()
    if not raw:
        return ()
    joined = ' '.join(_TOKEN_MAP.get(t, t) for t in raw)
    joined = _ALIAS.get(joined, joined)
    toks = [_TOKEN_MAP.get(t, t) for t in joined.split()]
    kept = [t for t in toks if t not in _DROP]
    return tuple(kept or toks)


def score(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    sa, sb = ' '.join(ta), ' '.join(tb)
    if sa == sb:
        return 1.0
    short, long_ = (ta, tb) if len(sa) <= len(sb) else (tb, ta)
    if set(short) <= set(long_) and max(len(t) for t in short) >= 4:
        return 0.93
    ss, sl = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    if len(ss) >= 5 and sl.startswith(ss):
        return 0.90
    return difflib.SequenceMatcher(None, sa, sb).ratio()


def pair_score(h1, a1, h2, a2):
    return min(score(h1, h2), score(a1, a2))


def find_fixture(home, away, candidates, get=lambda c: (c['home'], c['away'])):
    """Tafuta mechi inayolingana na (home, away) kati ya `candidates`. `get(c)` inarudisha (home, away) ya mgombea.
    Inarudisha mgombea bora tu ikiwa alama yake >= ACCEPT na hakuna mwingine aliye karibu sana; vinginevyo None."""
    scored = []
    for c in candidates:
        h, a = get(c)
        s = pair_score(home, away, h, a)
        if s >= ACCEPT:
            scored.append((s, c))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    if len(scored) > 1 and scored[0][0] - scored[1][0] < AMBIGUOUS_GAP:
        return None
    return scored[0][1]