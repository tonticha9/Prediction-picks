"""
market_evaluator.py — Won/Lost/Void logic kwa kila 'market' inayotokea kwenye
predictions.csv/value_bets.csv (masoko 63 yaliyothibitishwa Sept 2026).

Inatumia matokeo halisi ya mechi (safu sawa na zile fetch_recent_results()
kwenye hourly_catchup.py inazozileta kutoka AllSportsAPI):
  FTHG, FTAG          - bao la mwisho (nyumbani/ugenini)
  HTHG, HTAG          - bao la nusu ya kwanza
  HC, AC              - corners (nyumbani/ugenini)
  HY, AY, HR, AR      - kadi za njano/nyekundu (nyumbani/ugenini)
  HS, AS              - risasi jumla (haitumiki kwenye masoko 63 ya sasa)
  HST, AST            - risasi zilizolenga lengo (shots on target)

MAAMUZI YALIYOTHIBITISHWA NA MTUMIAJI (Sept 22 2026):
  - 'booking_pts' = jumla ya kadi ZOTE (yellow+red), SAWA na 'cards'/'yellows'
    ya jumla ya idadi (SI points scale ya 10/25).
  - 'both_teams_carded' = timu ZOTE mbili zimepata angalau kadi MOJA
    (yellow au red), si vigumu zaidi ya hapo.

evaluate_market(market, row) inarudisha:
  True   -> soko lilishinda (WON)
  False  -> soko lilipotea (LOST)
  None   -> haiwezekani kuamua (VOID) - takwimu muhimu haipo (NaN/missing)
"""
import re
import math


def _num(row, key):
    """Pata thamani ya namba kutoka row (dict au pandas Series), NaN-safe."""
    val = row.get(key)
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    return f


def _has_score(row):
    return _num(row, 'FTHG') is not None and _num(row, 'FTAG') is not None


def _has_ht_score(row):
    return _num(row, 'HTHG') is not None and _num(row, 'HTAG') is not None


def _has_corners(row):
    return _num(row, 'HC') is not None and _num(row, 'AC') is not None


def _has_cards(row):
    return all(_num(row, k) is not None for k in ('HY', 'AY', 'HR', 'AR'))


def _has_sot(row):
    return _num(row, 'HST') is not None and _num(row, 'AST') is not None


# ================================================================
# Stat aggregators (kila moja inarudisha namba MOJA kutoka row, au None)
# ================================================================
def _total_goals(row):
    if not _has_score(row):
        return None
    return _num(row, 'FTHG') + _num(row, 'FTAG')


def _home_goals(row):
    return _num(row, 'FTHG')


def _away_goals(row):
    return _num(row, 'FTAG')


def _total_corners(row):
    if not _has_corners(row):
        return None
    return _num(row, 'HC') + _num(row, 'AC')


def _total_cards(row):
    """Jumla ya kadi ZOTE (yellow+red) - inatumika kwa 'cards', 'yellows',
    NA 'booking_pts' (kwa uamuzi wa mtumiaji, zote tatu ni sawa)."""
    if not _has_cards(row):
        return None
    return _num(row, 'HY') + _num(row, 'AY') + _num(row, 'HR') + _num(row, 'AR')


def _total_sot(row):
    if not _has_sot(row):
        return None
    return _num(row, 'HST') + _num(row, 'AST')


STAT_FUNCS = {
    'goals': _total_goals,
    'home_goals': _home_goals,
    'away_goals': _away_goals,
    'corners': _total_corners,
    'sot': _total_sot,
    'yellows': _total_cards,
    'cards': _total_cards,
    'booking_pts': _total_cards,
}


# ================================================================
# Masoko ya moja kwa moja (match result / half-time / booleans)
# ================================================================
def _simple_result(market, row):
    """home_win/away_win/dc_1x/dc_x2/dc_12 - inahitaji tu FTHG/FTAG."""
    if not _has_score(row):
        return None
    h, a = _num(row, 'FTHG'), _num(row, 'FTAG')
    if market == 'home_win':
        return h > a
    if market == 'away_win':
        return h < a
    if market == 'dc_1x':
        return h >= a
    if market == 'dc_x2':
        return h <= a
    if market == 'dc_12':
        return h != a
    return 'unknown'


def _half_result(market, row):
    if not (_has_score(row) and _has_ht_score(row)):
        return None
    h_ft, a_ft = _num(row, 'FTHG'), _num(row, 'FTAG')
    h_ht, a_ht = _num(row, 'HTHG'), _num(row, 'HTAG')
    h_2h, a_2h = h_ft - h_ht, a_ft - a_ht

    if market == 'home_win_1h':
        return h_ht > a_ht
    if market == 'home_win_2h':
        return h_2h > a_2h
    if market == 'away_win_1h':
        return h_ht < a_ht
    if market == 'away_win_2h':
        return h_2h < a_2h
    if market == 'home_win_either_half':
        return (h_ht > a_ht) or (h_2h > a_2h)
    if market == 'away_win_either_half':
        return (h_ht < a_ht) or (h_2h < a_2h)
    if market == 'home_score_both_halves':
        return (h_ht > 0) and (h_2h > 0)
    return 'unknown'


def _boolean_market(market, row):
    if market == 'btts':
        if not _has_score(row):
            return None
        return _num(row, 'FTHG') > 0 and _num(row, 'FTAG') > 0

    if market == 'both_teams_carded':
        # Uamuzi wa mtumiaji: kila timu angalau kadi 1 (yellow au red)
        if not _has_cards(row):
            return None
        home_cards = _num(row, 'HY') + _num(row, 'HR')
        away_cards = _num(row, 'AY') + _num(row, 'AR')
        return home_cards >= 1 and away_cards >= 1

    return 'unknown'


CARDS_RANGE_RE = re.compile(r'^cards_range_(\d+)_(\d+)$')


def _cards_range(market, row):
    m = CARDS_RANGE_RE.match(market)
    if not m:
        return 'unknown'
    lo, hi = int(m.group(1)), int(m.group(2))
    total = _total_cards(row)
    if total is None:
        return None
    return lo <= total <= hi


# ================================================================
# Masoko ya over/under (yenye kiwango, k.m. corners_over_75 -> zaidi ya 7.5)
# ================================================================
THRESHOLD_RE = re.compile(r'^(?P<stat>[a-z_]*?)_?(?P<direction>over|under)_?(?P<num>\d{2,3})$')


def _parse_threshold(suffix):
    """Chambua 'corners_over_75' -> ('corners','over',7.5)
    au 'under95' (bila jina la stat, kwa ajili ya compound) -> ('','under',9.5)."""
    m = THRESHOLD_RE.match(suffix)
    if not m:
        return None
    stat = m.group('stat').strip('_')
    direction = m.group('direction')
    threshold = int(m.group('num')) / 10.0
    return stat, direction, threshold


def _threshold_market(market, row):
    parsed = _parse_threshold(market)
    if not parsed:
        return 'unknown'
    stat, direction, threshold = parsed
    stat_key = stat if stat else 'goals'  # bare 'over_25'/'under_15' = jumla ya bao
    func = STAT_FUNCS.get(stat_key)
    if func is None:
        return 'unknown'
    value = func(row)
    if value is None:
        return None
    return value > threshold if direction == 'over' else value < threshold


# ================================================================
# Masoko ya mchanganyiko: "X_and_Y" (mfano home_win_and_over_25,
# away_win_and_cards_under35, dc_12_and_over_25)
# ================================================================
def _compound_market(market, row):
    if '_and_' not in market:
        return 'unknown'
    base, _, second = market.partition('_and_')

    base_result = evaluate_market(base, row)
    if base_result is None:
        return None
    if base_result == 'unknown':
        return 'unknown'

    second_result = _threshold_market(second, row)
    if second_result is None:
        return None
    if second_result == 'unknown':
        return 'unknown'

    return bool(base_result) and bool(second_result)


# ================================================================
# ENTRY POINT
# ================================================================
def evaluate_market(market, row):
    """market: jina la soko (string, mfano 'away_win_and_under_25')
    row: dict/Series yenye FTHG,FTAG,HTHG,HTAG,HC,AC,HY,AY,HR,AR,HST,AST

    Inarudisha True (WON) / False (LOST) / None (VOID - takwimu haipo)."""

    if '_and_' in market:
        result = _compound_market(market, row)
        if result != 'unknown':
            return result

    if market in ('home_win', 'away_win', 'dc_1x', 'dc_x2', 'dc_12'):
        return _simple_result(market, row)

    if market in ('home_win_1h', 'home_win_2h', 'away_win_1h', 'away_win_2h',
                  'home_win_either_half', 'away_win_either_half',
                  'home_score_both_halves'):
        return _half_result(market, row)

    if market in ('btts', 'both_teams_carded'):
        return _boolean_market(market, row)

    if market.startswith('cards_range_'):
        return _cards_range(market, row)

    threshold_result = _threshold_market(market, row)
    if threshold_result != 'unknown':
        return threshold_result

    # Soko halijulikani kabisa - salama zaidi kurudisha None (VOID) badala
    # ya kubahatisha na kuandika matokeo ya uongo.
    return None


def result_label(evaluated):
    """True/False/None -> 'WON'/'LOST'/'VOID' (kwa kuandika kwenye DB)."""
    if evaluated is None:
        return 'VOID'
    return 'WON' if evaluated else 'LOST'
