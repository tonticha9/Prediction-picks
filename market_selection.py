"""
market_selection.py — Uchaguzi wa masoko kwa kila mechi.
"""
import re

MATCH_RESULT_DIRECT_PAIR = {'home_win': 'away_win', 'away_win': 'home_win'}
THRESHOLD_RE = re.compile(r'^(?P<stat>[a-z_]+?)_(?P<direction>over|under)_(?P<num>\d{2,3})$')

# Kigezo cha 'Pro Confidence' ya model - kinatumika kuvunja usawa wa
# probability zilizokaribiana (point 1-2), badala ya namba pekee.
TIER_RANK = {'PRO_STRONG': 2, 'PRO_MEDIUM': 1}


def is_market_allowed(market):
    if 'goals' in market and (market.endswith('_05') or market.endswith('_45')):
        return False
    if market.startswith('home_goals_under_') or market.startswith('away_goals_under_'):
        return False
    if '_and_' in market and 'under' in market:
        return False
    return True


def _parse_threshold(market):
    m = THRESHOLD_RE.match(market)
    if not m:
        return None
    return m.group('stat'), m.group('direction'), m.group('num')


def _pair_key(market):
    if market in MATCH_RESULT_DIRECT_PAIR:
        return 'result_pair'
    parsed = _parse_threshold(market)
    if parsed:
        stat, _direction, num = parsed
        return f'{stat}_{num}'
    return None


def _ev(probability, real_odds):
    if real_odds is None:
        return None
    return (probability or 0) / 100 * real_odds - 1


def _score_hybrid(probability, real_odds, pro_tier=None):
    ev = _ev(probability, real_odds)
    if ev is not None:
        if ev > 0:
            return (2, ev)
        return (0, probability or 0)
    return (1, probability or 0)


def _score_probability(probability, real_odds, pro_tier=None):
    """Probability formula - SASA inatumia pro_tier kwanza (kigezo cha
    Pro Confidence ya model) kama tiebreaker, kabla ya namba ya probability
    yenyewe - tofauti ndogo za point 1-2 hazitaamua peke yake."""
    return (TIER_RANK.get(pro_tier, 0), probability or 0)


def _score_ev(probability, real_odds, pro_tier=None):
    ev = _ev(probability, real_odds)
    return (ev,) if ev is not None else (-999,)


FORMULA_SCORERS = {
    'probability': _score_probability,
    'ev': _score_ev,
    'hybrid': _score_hybrid,
}


def select_markets(entries, threshold_pct, formula='hybrid'):
    """entries: orodha ya tuple (market, probability, real_odds, pro_tier, original)
    kwa MECHI MOJA pekee."""
    groups = {}
    passthrough = []
    for market, probability, real_odds, pro_tier, original in entries:
        key = _pair_key(market)
        if key is None:
            passthrough.append((market, probability, real_odds, pro_tier, original))
        else:
            groups.setdefault(key, []).append((market, probability, real_odds, pro_tier, original))

    survivors = list(passthrough)
    for _key, group in groups.items():
        best = max(group, key=lambda e: e[1] or 0)
        survivors.append(best)

    survivors = [e for e in survivors if is_market_allowed(e[0])]
    survivors = [e for e in survivors if (e[1] or 0) >= threshold_pct]

    scorer = FORMULA_SCORERS.get(formula, _score_hybrid)
    survivors.sort(key=lambda e: scorer(e[1], e[2], e[3]), reverse=True)

    return [e[4] for e in survivors]
