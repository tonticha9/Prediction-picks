"""
market_selection.py — Uchaguzi wa masoko kwa kila mechi:
1) Ondoa jozi za moja-kwa-moja-zinazopingana (over_NN/under_NN wenye NN sawa,
   home_win/away_win) - kila jozi, chukua probability kubwa zaidi pekee.
2) Toa masoko yasiyoruhusiwa kabisa (is_market_allowed - kanuni ya KUDUMU,
   haibadiliki: goals_05/45, team-under, compound-under).
3) Chuja kwa probability_threshold (admin-adjustable, si fixed) - chini
   yake hayaonekani kabisa.
4) Panga kwa formula iliyochaguliwa admin: 'probability' / 'ev' / 'hybrid'.

Inafanya kazi na CHANZO CHOCHOTE (pandas row au PredictionRecord ORM) -
mwito lazima aandae orodha ya tuple (market, probability, real_odds, original).
"""
import re

# home_win na away_win ni jozi ya KWELI inayopingana moja kwa moja.
# dc_1x/dc_x2/dc_12 HAZIPINGANI nazo moja kwa moja (zina-overlap kwenye draw),
# kwa hiyo hazipo kwenye jozi - zinaruhusiwa kuonekana pamoja na home_win/away_win.
MATCH_RESULT_DIRECT_PAIR = {'home_win': 'away_win', 'away_win': 'home_win'}

THRESHOLD_RE = re.compile(r'^(?P<stat>[a-z_]+?)_(?P<direction>over|under)_(?P<num>\d{2,3})$')


def is_market_allowed(market):
    """Kanuni ya KUDUMU - uamuzi wa mwisho, HAIBADILIKI kamwe."""
    if 'goals' in market and (market.endswith('_05') or market.endswith('_45')):
        return False
    if market.startswith('home_goals_under_') or market.startswith('away_goals_under_'):
        return False
    if '_and_' in market and 'under' in market:
        return False
    return True


def _parse_threshold(market):
    """'corners_over_75' -> ('corners','over','75'). Rudisha None kama
    market si ya muundo huu (mfano dc_1x, btts, home_win, compound _and_)."""
    m = THRESHOLD_RE.match(market)
    if not m:
        return None
    return m.group('stat'), m.group('direction'), m.group('num')


def _pair_key(market):
    """Ufunguo wa jozi inayopingana moja kwa moja, au None (hakuna jozi -
    soko hili halishindani na lolote, linapita moja kwa moja hatua ya 1)."""
    if market in MATCH_RESULT_DIRECT_PAIR:
        return 'result_pair'
    parsed = _parse_threshold(market)
    if parsed:
        stat, _direction, num = parsed
        return f'{stat}_{num}'  # over_25 na under_25 -> 'goals_25' (jozi moja MOJA)
    return None


def _ev(probability, real_odds):
    if real_odds is None:
        return None
    return (probability or 0) / 100 * real_odds - 1


def _score_hybrid(probability, real_odds):
    """Formula ya sasa (default): odds+EV-chanya > hakuna-odds > odds+EV-hasi."""
    ev = _ev(probability, real_odds)
    if ev is not None:
        if ev > 0:
            return (2, ev)
        return (0, probability or 0)
    return (1, probability or 0)


def _score_probability(probability, real_odds):
    """Probability PEKEE - odds/EV havihusiki kabisa kwenye uchaguzi."""
    return (probability or 0,)


def _score_ev(probability, real_odds):
    """EV pekee - masoko yasiyo na odds yanashuka chini kabisa."""
    ev = _ev(probability, real_odds)
    return (ev,) if ev is not None else (-999,)


FORMULA_SCORERS = {
    'probability': _score_probability,
    'ev': _score_ev,
    'hybrid': _score_hybrid,
}


def select_markets(entries, threshold_pct, formula='hybrid'):
    """entries: orodha ya tuple (market:str, probability:float|None, real_odds:float|None, original:any)
    kwa MECHI MOJA pekee (kaa makini - mwito lazima achuje kwa mechi kabla ya kuita hii).

    Inarudisha: orodha ya 'original' iliyopangwa best-first (dedup+threshold
    tayari vimefanyika), au orodha TUPU kama hakuna soko lililopita threshold -
    mechi hiyo haipaswi kuonyeshwa kabisa katika hali hiyo."""
    groups = {}
    passthrough = []
    for market, probability, real_odds, original in entries:
        key = _pair_key(market)
        if key is None:
            passthrough.append((market, probability, real_odds, original))
        else:
            groups.setdefault(key, []).append((market, probability, real_odds, original))

    survivors = list(passthrough)
    for _key, group in groups.items():
        best = max(group, key=lambda e: e[1] or 0)
        survivors.append(best)

    survivors = [e for e in survivors if is_market_allowed(e[0])]
    survivors = [e for e in survivors if (e[1] or 0) >= threshold_pct]

    scorer = FORMULA_SCORERS.get(formula, _score_hybrid)
    survivors.sort(key=lambda e: scorer(e[1], e[2]), reverse=True)

    return [e[3] for e in survivors]
