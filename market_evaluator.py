"""
market_evaluator.py — Won/Lost/Void logic kwa kila 'market' inayotokea kwenye
predictions.csv/value_bets.csv.

Inatumia matokeo halisi ya mechi (safu sawa na zile fetch_recent_results()
kwenye hourly_catchup.py inazozileta kutoka AllSportsAPI):
  FTHG, FTAG          - bao la mwisho (nyumbani/ugenini)
  HTHG, HTAG          - bao la nusu ya kwanza
  HC, AC              - corners (nyumbani/ugenini)
  HY, AY, HR, AR      - kadi za njano/nyekundu (nyumbani/ugenini)
  HST, AST            - risasi zilizolenga lengo (shots on target)

UFAFANUZI WA MASOKO YA KADI (umeoanishwa na TRAINING — Okt 11 2026):
  - yellows_over_X, cards_range_lo_hi = NJANO TU (HY+AY). cards_range ni lo..hi pamoja.
  - booking_pts_over_X = POINTI: njano 10, nyekundu 25 (X = 35/45/55, pointi > X).
  - X_and_cards_over35 (home_win/away_win) = matokeo NA booking points > 35
    (jina lina 'cards' lakini training inatumia booking_pts_over_35).
  - any_red_card           = nyekundu 1+ (HR+AR >= 1)
  - no_cards_ft            = njano+nyekundu = 0
  - both_teams_carded      = (HY+HR) >= 1 na (AY+AR) >= 1
  - both_teams_2plus_cards = (HY+HR) >= 2 na (AY+AR) >= 2
  - home_more_cards        = (HY + 2*HR) nyumbani > (AY + 2*AR) ugenini
                             (nyekundu inahesabiwa MARA 2, kama training)

MASOKO YA NUSU (imeongezwa Okt 22 2026, yanalingana 100% na training):
  - home_win_both_halves   = nyumbani ashinde nusu ZOTE MBILI (1H na 2H)
  - away_win_both_halves   = ugenini ashinde nusu ZOTE MBILI
  - away_score_both_halves = ugenini afunge bao kwenye nusu zote mbili

Masoko ya BTTS: btts, home_win_btts, away_win_btts, na X_and_btts (home_win/away_win/dc_*).

v2 (Nov 2 2026):
  - MASOKO YA MCHANGANYIKO (X_and_Y): upande wowote unaweza kuwa soko lolote linalojulikana
    (si over/under au btts tu). Upande mmoja ukipotea kwa uhakika (False), mchanganyiko
    umepotea hata kama data ya upande mwingine haipo.
  - Masoko ya kadi yanatoa jibu hata data ya nyekundu ikikosekana, pale njano pekee zinatosha
    kuamua (mfano both_teams_carded na njano 1+ kila upande = WON).
  - UNKNOWN_MARKETS: masoko yasiyo na evaluator yanakusanywa (hourly_catchup inayaonyesha).
  - selftest(): ukaguzi wa ndani; hourly_catchup inakataa kusettle ikishindwa.

evaluate_market(market, row) inarudisha:
  True   -> soko lilishinda (WON)
  False  -> soko lilipotea (LOST)
  None   -> haiwezekani kuamua (VOID) - takwimu muhimu haipo (NaN/missing) au soko halijulikani
"""
import re
import math
from collections import Counter

# Masoko yasiyojulikana (hayana evaluator) yanakusanywa hapa.
UNKNOWN_MARKETS = Counter()
NAN = float('nan')


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


def _has_yellows(row):
    return _num(row, 'HY') is not None and _num(row, 'AY') is not None


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


def _total_yellows(row):
    """NJANO TU (HY+AY) - inatumika kwa 'yellows' na 'cards_range'."""
    if not _has_yellows(row):
        return None
    return _num(row, 'HY') + _num(row, 'AY')


def _booking_points(row):
    """Pointi za nidhamu: njano = 10, nyekundu = 25."""
    if not _has_cards(row):
        return None
    yellows = _num(row, 'HY') + _num(row, 'AY')
    reds = _num(row, 'HR') + _num(row, 'AR')
    return 10 * yellows + 25 * reds


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
    'yellows': _total_yellows,
    'booking_pts': _booking_points,
    # 'cards' kwenye masoko ya mchanganyiko (home_win_and_cards_over35) =
    # booking points > 35 kwa training, kwa hiyo inatumia pointi pia.
    'cards': _booking_points,
}

# Stats ambazo namba ya soko ni THAMANI KAMILI (35 = 35), si 10x (75 = 7.5)
RAW_THRESHOLD_STATS = ('booking_pts', 'cards')

# Masoko ya nusu (yanahitaji HTHG/HTAG)
HALF_MARKETS = (
    'home_win_1h', 'home_win_2h', 'away_win_1h', 'away_win_2h',
    'home_win_either_half', 'away_win_either_half',
    'home_score_both_halves',
    'home_win_both_halves', 'away_win_both_halves', 'away_score_both_halves',
)

SIMPLE_MARKETS = ('home_win', 'away_win', 'dc_1x', 'dc_x2', 'dc_12')


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
    if market == 'home_win_both_halves':
        return (h_ht > a_ht) and (h_2h > a_2h)
    if market == 'away_win_both_halves':
        return (h_ht < a_ht) and (h_2h < a_2h)
    if market == 'away_score_both_halves':
        return (a_ht > 0) and (a_2h > 0)
    return 'unknown'


BOOLEAN_MARKETS = (
    'btts',
    'home_win_btts',
    'away_win_btts',
    'both_teams_carded',
    'any_red_card',
    'no_cards_ft',
    'both_teams_2plus_cards',
    'home_more_cards',
)


def _boolean_market(market, row):
    if market == 'btts':
        if not _has_score(row):
            return None
        return _num(row, 'FTHG') > 0 and _num(row, 'FTAG') > 0

    if market in ('home_win_btts', 'away_win_btts'):
        if not _has_score(row):
            return None
        h, a = _num(row, 'FTHG'), _num(row, 'FTAG')
        both = h > 0 and a > 0
        return both and (h > a if market == 'home_win_btts' else h < a)

    hy, ay = _num(row, 'HY'), _num(row, 'AY')
    hr, ar = _num(row, 'HR'), _num(row, 'AR')
    have_y = hy is not None and ay is not None
    have_r = hr is not None and ar is not None
    full = have_y and have_r

    if market == 'any_red_card':
        if (hr is not None and hr >= 1) or (ar is not None and ar >= 1):
            return True
        if not have_r:
            return None
        return (hr + ar) >= 1

    if market == 'both_teams_carded':
        if have_y and hy >= 1 and ay >= 1:
            return True            # njano pekee zinatosha kuamua
        if not full:
            return None
        return (hy + hr) >= 1 and (ay + ar) >= 1

    if market == 'no_cards_ft':
        if (have_y and (hy + ay) > 0) or (have_r and (hr + ar) > 0):
            return False           # kadi yoyote iliyopo inatosha kuamua
        if not full:
            return None
        return (hy + ay + hr + ar) == 0

    if market == 'both_teams_2plus_cards':
        if have_y and hy >= 2 and ay >= 2:
            return True
        if not full:
            return None
        return (hy + hr) >= 2 and (ay + ar) >= 2

    if market == 'home_more_cards':
        if not full:
            return None
        # Training: (HY + 2*HR) > (AY + 2*AR)
        return (hy + 2 * hr) > (ay + 2 * ar)

    return 'unknown'


CARDS_RANGE_RE = re.compile(r'^cards_range_(\d+)_(\d+)$')


def _cards_range(market, row):
    """cards_range_lo_hi = idadi ya NJANO (HY+AY) iko kati ya lo na hi (pamoja)."""
    m = CARDS_RANGE_RE.match(market)
    if not m:
        return 'unknown'
    lo, hi = int(m.group(1)), int(m.group(2))
    total = _total_yellows(row)
    if total is None:
        return None
    return lo <= total <= hi


# ================================================================
# Masoko ya over/under (yenye kiwango, k.m. corners_over_75 -> zaidi ya 7.5)
# ================================================================
THRESHOLD_RE = re.compile(r'^(?P<stat>[a-z_]*?)_?(?P<direction>over|under)_?(?P<num>\d{2,3})$')


def _parse_threshold(suffix):
    """Chambua 'corners_over_75' -> ('corners','over',7.5)
    'booking_pts_over_35' -> ('booking_pts','over',35.0)  (pointi kamili)
    'cards_over35' (kwenye compound) -> ('cards','over',35.0)  (pointi kamili)
    au 'under95' (bila jina la stat, kwa ajili ya compound) -> ('','under',9.5)."""
    m = THRESHOLD_RE.match(suffix)
    if not m:
        return None
    stat = m.group('stat').strip('_')
    direction = m.group('direction')
    raw = int(m.group('num'))
    if stat in RAW_THRESHOLD_STATS:
        threshold = float(raw)      # pointi kamili: 35, 45, 55
    else:
        threshold = raw / 10.0      # 75 -> 7.5
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
# Kiingilio cha ndani: True / False / None (data haipo) / 'unknown' (soko halijulikani)
# ================================================================
def _evaluate(market, row):
    if '_and_' in market:
        return _compound_market(market, row)
    if market in SIMPLE_MARKETS:
        return _simple_result(market, row)
    if market in HALF_MARKETS:
        return _half_result(market, row)
    if market in BOOLEAN_MARKETS:
        return _boolean_market(market, row)
    if market.startswith('cards_range_'):
        return _cards_range(market, row)
    return _threshold_market(market, row)


def _compound_market(market, row):
    """'X_and_Y': pande zote mbili lazima ziwe WON. Upande wowote ni soko lolote linalojulikana.
    Upande mmoja ukipotea kwa uhakika (False), mchanganyiko umepotea hata data ya upande
    mwingine ikikosekana."""
    base, _, second = market.partition('_and_')
    if not base or not second:
        return 'unknown'
    a = _evaluate(base, row)
    if isinstance(a, str):
        return 'unknown'
    b = _evaluate(second, row)
    if isinstance(b, str):
        return 'unknown'
    if a is False or b is False:
        return False
    if a is None or b is None:
        return None
    return bool(a) and bool(b)


# ================================================================
# ENTRY POINT
# ================================================================
def evaluate_market(market, row):
    """market: jina la soko (string, mfano 'away_win_and_under_25')
    row: dict/Series yenye FTHG,FTAG,HTHG,HTAG,HC,AC,HY,AY,HR,AR,HST,AST

    Inarudisha True (WON) / False (LOST) / None (VOID - takwimu haipo au soko halijulikani)."""
    result = _evaluate(market, row)
    if isinstance(result, str):
        # Soko halijulikani kabisa - salama zaidi kurudisha None (VOID) badala
        # ya kubahatisha na kuandika matokeo ya uongo. Linakusanywa ili lionekane kwenye logi.
        UNKNOWN_MARKETS[market] += 1
        return None
    return result


def result_label(evaluated):
    """True/False/None -> 'WON'/'LOST'/'VOID' (kwa kuandika kwenye DB)."""
    if evaluated is None:
        return 'VOID'
    return 'WON' if evaluated else 'LOST'


# ================================================================
# UKAGUZI WA NDANI: hourly_catchup inaita selftest() kabla ya kusettle.
# ================================================================
def _row(**kw):
    base = {k: NAN for k in ('FTHG', 'FTAG', 'HTHG', 'HTAG', 'HC', 'AC',
                              'HY', 'AY', 'HR', 'AR', 'HST', 'AST')}
    base.update(kw)
    return base


def selftest():
    """Inarudisha orodha ya kasoro (tupu = kila kitu sawa)."""
    r1 = _row(FTHG=1, FTAG=2, HTHG=1, HTAG=0, HC=5, AC=5, HY=3, AY=2, HR=0, AR=0, HST=4, AST=6)
    r2 = _row(FTHG=0, FTAG=1)                                   # data nyingi haipo
    r3 = _row(FTHG=2, FTAG=2, HTHG=1, HTAG=1, HY=1, AY=1)       # njano zipo, nyekundu hazipo
    r4 = _row(FTHG=0, FTAG=0, HTHG=0, HTAG=0, HY=0, AY=0, HR=0, AR=0)
    r5 = _row(FTHG=2, FTAG=0, HTHG=1, HTAG=0)

    cases = [
        ('yellows_under_35', r1, False),        # njano 5 -> under 3.5 LAZIMA ipotee
        ('yellows_over_35', r1, True),
        ('cards_range_4_6', r1, True),
        ('cards_range_0_3', r1, False),
        ('booking_pts_over_45', r1, True),      # 50 > 45
        ('booking_pts_under_55', r1, True),     # 50 < 55
        ('corners_over_95', r1, True),
        ('goals_over_25', r1, True),
        ('goals_over_15', r1, True),
        ('home_goals_under_15', r1, True),
        ('away_goals_over_05', r1, True),
        ('away_win_either_half', r1, True),     # 2H = 0-2
        ('home_win_either_half', r1, True),     # 1H = 1-0
        ('home_win_both_halves', r1, False),
        ('away_score_both_halves', r1, False),
        ('away_win_and_over_15', r1, True),
        ('dc_1x_and_over_15', r1, False),
        ('btts', r1, True),
        ('away_win_and_btts', r1, True),
        ('both_teams_carded', r1, True),
        ('no_cards_ft', r1, False),
        ('any_red_card', r1, False),
        ('home_more_cards', r1, True),
        ('yellows_under_35', r2, None),         # data haipo -> VOID, si WON
        ('away_win_either_half', r2, None),
        ('corners_over_95', r2, None),
        ('home_win_and_cards_over35', r2, False),   # home_win imepotea kwa uhakika
        ('away_win_and_cards_over35', r2, None),
        ('both_teams_carded', r3, True),        # njano pekee zinatosha
        ('no_cards_ft', r3, False),
        ('any_red_card', r3, None),
        ('no_cards_ft', r4, True),
        ('home_more_cards', r4, False),
        ('home_win_both_halves', r5, False),    # 2H ni 1-0 lakini 1H ni 1-0 -> kweli ni True
        ('home_win_and_both_teams_carded', r1, False),
        ('foo_bar_baz', r1, None),
    ]
    # kesi ya r5: nyumbani 2-0, HT 1-0, 2H 1-0 => home_win_both_halves = True
    cases[33] = ('home_win_both_halves', r5, True)

    problems = []
    for market, row, expected in cases:
        try:
            got = evaluate_market(market, row)
        except Exception as e:
            got = f"KOSA {type(e).__name__}: {e}"
        if got is not expected:
            problems.append(f"{market}: nilitegemea {expected}, nimepata {got}")
    UNKNOWN_MARKETS.clear()
    return problems