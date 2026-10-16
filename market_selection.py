"""
market_selection.py — Uchaguzi wa masoko kwa kila mechi.

Step 8B (Okt 2026): kichujio cha ubora (quality gate) kimeongezwa. Kinatumia
market_quality.py (madaraja A/B/C/X kwa skill + base rate ya hivi karibuni).
Kudhibiti kupitia Render env var QUALITY_GATE:
   '1' (chaguo-msingi) = kamili: daraja + margin juu ya base rate
   'soft'              = daraja tu (bila margin)
   '0'                 = kimezimwa (tabia ya zamani)
"""
import os
import re

try:
    from market_quality import QUALITY, MIN_EDGE, BEST_TIERS
except Exception:                      # faili likikosekana, gate inazimika kimya
    QUALITY, MIN_EDGE, BEST_TIERS = {}, {}, set()

MATCH_RESULT_DIRECT_PAIR = {'home_win': 'away_win', 'away_win': 'home_win'}
THRESHOLD_RE = re.compile(r'^(?P<stat>[a-z_]+?)_(?P<direction>over|under)_(?P<num>\d{2,3})$')
OVER_05_RE = re.compile(r'^(home_|away_)?goals_over_05$')

# Masoko ya timu moja "under" (mf. home_goals_under_15): yanaruhusiwa kuonekana (Other Picks)
# na kwenye combos, lakini HAYAWI Best Pick kamwe.
TEAM_GOALS_UNDER_RE = re.compile(r'^(home|away)_goals_under_\d{2,3}$')

# Kigezo cha 'Pro Confidence' ya model - kinatumika kuvunja usawa wa
# probability zilizokaribiana (point 1-2), badala ya namba pekee.
TIER_RANK = {'PRO_STRONG': 2, 'PRO_MEDIUM': 1}


def is_market_allowed(market):
    """Sheria ya kudumu (inatumika pia na COMBOS). Over 0.5 inabaki marufuku hapa.
    home_goals_under_* / away_goals_under_* zimerudishwa (Oct 13 2026): zinazuiwa
    kuwa Best Pick tu (angalia is_best_pick_excluded)."""
    market = str(market)
    if 'goals' in market and (market.endswith('_05') or market.endswith('_45')):
        return False
    if '_and_' in market and 'under' in market:
        return False
    return True


def is_display_allowed(market):
    """Sheria ya KUONYESHA (Dashboard/Zijazo/History): kama hapo juu, lakini
    over 0.5 inaruhusiwa (inaonekana Other Picks tu, haiwi Best Pick)."""
    return is_market_allowed(market) or bool(OVER_05_RE.match(str(market)))


def is_recordable(market):
    """Soko linaloweza kumfikia mtumiaji (kuonyeshwa au kwenye combos) ndilo pekee
    linalorekodiwa na kuchambuliwa. Masoko yaliyozuiliwa kabisa hayarekodiwi
    wala hayaingii Market Analysis."""
    try:
        return is_display_allowed(str(market))
    except Exception:
        return True


def is_best_pick_excluded(market, allow_dc_best=False):
    """Masoko yasiyoruhusiwa kuwa Best Pick (yanaruhusiwa kuonekana Other Picks)."""
    m = str(market)
    if m == 'dc_12':
        return True                       # kamwe Best Pick
    if m in ('dc_1x', 'dc_x2') and not allow_dc_best:
        return True                       # Admin anaweza kuruhusu
    if OVER_05_RE.match(m):
        return True                       # over 0.5: Other Picks tu
    if TEAM_GOALS_UNDER_RE.match(m):
        return True                       # home/away goals under: Other Picks tu
    return False


# ---------------------------------------------------------------------------
# QUALITY GATE (Step 8B)
# ---------------------------------------------------------------------------
def _gate_mode():
    """'0' = imezimwa, 'soft' = daraja tu, '1' = kamili (chaguo-msingi)."""
    if not QUALITY:
        return '0'
    v = os.environ.get('QUALITY_GATE', '1').strip().lower()
    if v in ('0', 'off', 'false', 'no'):
        return '0'
    if v == 'soft':
        return 'soft'
    return '1'


def _quality_key(market):
    """Ramani jina la soko kwenda jina la jedwali. Rudisha (ufunguo, ni_under)."""
    m = str(market)
    if '_and_' in m:
        # 'home_win_and_over_15' -> 'home_win_and_over15'
        return re.sub(r'over_(\d+)', r'over\1', m), False
    if '_under_' in m:
        # 'corners_under_95' -> 'corners_over_95' (base = 100 - base ya over)
        return m.replace('_under_', '_over_'), True
    return m, False


def quality_info(market, probability):
    """Taarifa ya ubora wa soko kwa probability husika, au None kama soko halimo
    kwenye jedwali (masoko yasiyojulikana hayachujwi)."""
    try:
        key, is_under = _quality_key(market)
        q = QUALITY.get(key)
        if q is None:
            return None
        tier, skill, base = q
        base_eff = (100.0 - base) if is_under else base
        edge = float(probability or 0) - base_eff
        min_edge = MIN_EDGE.get(tier, 0.0)
        return {
            'tier': tier,
            'skill': skill,
            'base_rate': round(base_eff, 1),
            'edge': round(edge, 1),
            'min_edge': min_edge,
            'informative': (tier != 'X') and edge >= min_edge,
            'best_ok': (tier in BEST_TIERS) and edge >= min_edge,
        }
    except Exception:
        return None


def quality_label(market, probability):
    """Kwa UI (Step 8C): rudisha (msimbo, maandishi) au None.
       'base_rate' = pick haizidi kiwango cha kawaida cha soko
       'weak'      = soko lenye ishara dhaifu (daraja C)
       'ok'        = soko lenye ishara na pick inazidi base rate"""
    qi = quality_info(market, probability)
    if qi is None:
        return None
    if not qi['informative']:
        return ('base_rate', 'Base rate')
    if qi['tier'] == 'C':
        return ('weak', 'Ishara dhaifu')
    return ('ok', '')


def unknown_quality_markets(markets):
    """Orodha ya masoko (kutoka kwenye data) yasiyo kwenye jedwali la ubora."""
    return sorted({str(m) for m in markets if quality_info(m, 0) is None})


# ---------------------------------------------------------------------------
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


def _has_odds(real_odds):
    return real_odds is not None and real_odds == real_odds   # (NaN != NaN)


def _ev(probability, real_odds):
    if not _has_odds(real_odds):
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
    """Probability formula - inatumia pro_tier kwanza (kigezo cha
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
    # 'no_odds': masoko yasiyo na odds pekee, yanapangwa kwa probability (+ tier)
    'no_odds': _score_probability,
}


def select_markets(entries, threshold_pct, formula='hybrid', allow_dc_best=False):
    """entries: orodha ya tuple (market, probability, real_odds, pro_tier, original)
    kwa MECHI MOJA pekee.

    Formula:
      - 'hybrid'      : masoko yote (EV chanya kwanza, kisha yasiyo na odds, kisha EV hasi)
      - 'probability' : masoko yote, kwa probability (+ tier)
      - 'ev'          : MASOKO YENYE ODDS TU, kwa EV (yasiyo na odds yanafichwa)
      - 'no_odds'     : MASOKO YASIYO NA ODDS TU, kwa probability (yenye odds yanafichwa)

    Quality gate (QUALITY_GATE): masoko ya daraja X yanafichwa (isipokuwa over 0.5 ya
    magoli, Other Picks tu); Best Pick lazima iwe daraja A/B (na, kwenye mode '1',
    probability izidi base rate kwa margin ya daraja).

    Orodha inayorudishwa: kipengele cha KWANZA ni Best Pick (soko la kwanza
    lisilozuiwa kuwa Best Pick), vingine vyote ni Other Picks kwa mpangilio wa formula.
    Kama hakuna soko linaloruhusiwa kuwa Best Pick, orodha tupu (mechi inafichwa).
    """
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

    survivors = [e for e in survivors if is_display_allowed(e[0])]
    survivors = [e for e in survivors if (e[1] or 0) >= threshold_pct]

    if formula == 'ev':
        survivors = [e for e in survivors if _has_odds(e[2])]
    elif formula == 'no_odds':
        survivors = [e for e in survivors if not _has_odds(e[2])]

    mode = _gate_mode()
    if mode != '0':
        kept = []
        for e in survivors:
            qi = quality_info(e[0], e[1])
            if qi is not None and qi['tier'] == 'X' and not OVER_05_RE.match(str(e[0])):
                continue                      # hakuna ishara: ficha
            kept.append(e)
        survivors = kept

    scorer = FORMULA_SCORERS.get(formula, _score_hybrid)
    survivors.sort(key=lambda e: scorer(e[1], e[2], e[3]), reverse=True)

    def _best_ok(e):
        if is_best_pick_excluded(e[0], allow_dc_best):
            return False
        if mode == '0':
            return True
        qi = quality_info(e[0], e[1])
        if qi is None:
            return True                       # soko lisilojulikana: haligusiwi
        if qi['tier'] not in BEST_TIERS:
            return False
        if mode == '1' and qi['edge'] < qi['min_edge']:
            return False
        return True

    best_idx = next((i for i, e in enumerate(survivors) if _best_ok(e)), None)
    if best_idx is None:
        return []
    if best_idx > 0:
        survivors.insert(0, survivors.pop(best_idx))

    return [e[4] for e in survivors]