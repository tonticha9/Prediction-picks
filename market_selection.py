"""
market_selection.py — Uchaguzi wa masoko kwa kila mechi.

Step 8B (Okt 2026): kichujio cha ubora (quality gate) kimeongezwa. Kinatumia
market_quality.py (madaraja A/B/C/X kwa skill + base rate ya hivi karibuni).
Kudhibiti kupitia Render env var QUALITY_GATE:
   '1' (chaguo-msingi) = kamili: daraja + margin juu ya base rate
   'soft'              = daraja tu (bila margin)
   '0'                 = kimezimwa (tabia ya zamani)

v3 (Nov 3 2026): LIVE layer. Inasoma prediction_record (siku 90) na kupima kama soko
   linatofautisha washindi na walioshindwa (AUC). Soko lenye picks 100+ na AUC < 0.54
   haliwezi kuwa Best Pick (linabaki Other Picks). Masoko ya goals (over/under) hayaguswi.
   Pia inatoa honest_rate()/honest_text(): hit-rate halisi ya siku 60 karibu na probability.
   QUALITY_LIVE=0 inazima layer hii. Data ikikosekana, injini inarudi kwenye jedwali la tuli.

v4 (Nov 9 2026): dc_12 INARUHUSIWA kuwa Best Pick (marufuku ya kudumu imeondolewa).
   Admin anaweza kuizima kwa kitufe cha Best Pick switches (m:dc_12), au Render env
   BEST_ALLOW_DC12=0 inairudisha kuwa Other Picks tu. over 1.5 haijaguswa.

v5 (Nov 9 2026): MADARAJA YANAJIREKEBISHA (si ya kudumu tena). Jedwali la market_quality.py
   linabaki msingi, lakini daraja la kila soko linarekebishwa na matokeo halisi ya siku 90:
   - KUPANDA (C -> B tu, kamwe A): picks >= 80, AUC >= 0.55, win rate halisi isiwe chini ya
     probability ya wastani kwa zaidi ya pointi 3, na nusu mbili za dirisha hazijaharibika.
   - KUSHUKA (A -> B, B -> C): picks >= 100 na probability ya wastani iko juu ya win rate
     halisi kwa zaidi ya pointi 6 (overconfident). Kushuka hakufichi soko.
   - Hali ikibadilika, daraja linarudi lenyewe (kinahesabiwa upya kila dakika 30).
   - QUALITY_AUTO=0 inazima kurekebisha (jedwali la kudumu tu). Mabadiliko yanaandikwa kwenye logi.
   - Override ya kudumu ya dc_12 = B (v4) imeondolewa: dc_12 inapanda kwa ushahidi tu.
   - BEST_TIERS ikikosekana kwenye market_quality.py, thamani ya akiba ni {'A','B'}.
"""
import math
import os
import re
import time
from datetime import date, datetime, timedelta

try:
    import market_quality as _mq
    QUALITY = _mq.QUALITY
    MIN_EDGE = _mq.MIN_EDGE
    BEST_TIERS = getattr(_mq, 'BEST_TIERS', {'A', 'B'})
except Exception:                      # faili likikosekana, gate inazimika kimya
    QUALITY, MIN_EDGE, BEST_TIERS = {}, {}, set()

MATCH_RESULT_DIRECT_PAIR = {'home_win': 'away_win', 'away_win': 'home_win'}
THRESHOLD_RE = re.compile(r'^(?P<stat>[a-z_]+?)_(?P<direction>over|under)_(?P<num>\d{2,3})$')
OVER_05_RE = re.compile(r'^(home_|away_)?goals_over_05$')

# Masoko ya timu moja "under" (mf. home_goals_under_15): yanaruhusiwa kuonekana (Other Picks)
# na kwenye combos, lakini HAYAWI Best Pick kamwe.
TEAM_GOALS_UNDER_RE = re.compile(r'^(home|away)_goals_under_\d{2,3}$')

# Masoko ya goals (jumla na ya timu): hayachujwi na LIVE layer (lebo tu).
GOALS_TOTAL_RE = re.compile(r'^(home_|away_)?goals_(over|under)_\d{2,3}$')

# Kigezo cha 'Pro Confidence' ya model - kinatumika kuvunja usawa wa
# probability zilizokaribiana (point 1-2), badala ya namba pekee.
TIER_RANK = {'PRO_STRONG': 2, 'PRO_MEDIUM': 1}

# LIVE layer
LIVE_DAYS = 90          # dirisha la data kwa AUC
LIVE_TTL = 1800         # sekunde 30 kabla ya kusoma database tena
LIVE_MIN_N = 100        # picks za chini kabla ya kuhukumu soko
LIVE_MIN_CLASS = 15     # washindi na walioshindwa wa chini kila upande
LIVE_AUC_MIN = 0.54     # chini ya hapa = haitofautishi
HONEST_DAYS = 60        # dirisha la hit-rate halisi
HONEST_BAND = 5.0       # +/- pointi za probability
HONEST_MIN_N = 15       # picks za chini kuonyesha hit-rate

# v5: madaraja yanayojirekebisha
TIER_ORDER = ['X', 'C', 'B', 'A']
PROMO_MIN_N = 80        # picks za chini kupandisha C -> B
PROMO_AUC = 0.55        # AUC ya chini kupandisha
PROMO_GAP = 3.0         # win rate halisi >= probability ya wastani - pointi hizi
DEMOTE_GAP = 6.0        # probability ya wastani - win rate halisi > hii = overconfident, shusha

_LIVE = {'ts': 0.0, 'data': None}


def _dc12_best_allowed():
    """dc_12 inaruhusiwa kuwa Best Pick isipokuwa env BEST_ALLOW_DC12=0."""
    return os.environ.get('BEST_ALLOW_DC12', '1').strip().lower() not in ('0', 'off', 'false', 'no')


def _auto_on():
    """Madaraja yanajirekebisha isipokuwa env QUALITY_AUTO=0."""
    return os.environ.get('QUALITY_AUTO', '1').strip().lower() not in ('0', 'off', 'false', 'no')


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
        return not _dc12_best_allowed()   # inaruhusiwa (BEST_ALLOW_DC12=0 inairudisha marufuku)
    if m in ('dc_1x', 'dc_x2') and not allow_dc_best:
        return True                       # Admin anaweza kuruhusu
    if OVER_05_RE.match(m):
        return True                       # over 0.5: Other Picks tu
    if TEAM_GOALS_UNDER_RE.match(m):
        return True                       # home/away goals under: Other Picks tu
    return False


# ---------------------------------------------------------------------------
# QUALITY GATE (Step 8B) - jedwali la msingi + marekebisho ya moja kwa moja (v5)
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


def _shift_tier(tier, steps):
    if tier not in TIER_ORDER:
        return tier
    i = max(0, min(len(TIER_ORDER) - 1, TIER_ORDER.index(tier) + steps))
    return TIER_ORDER[i]


def _tier_from_stats(st, static_tier):
    """Daraja la moja kwa moja kutoka takwimu za siku 90. Rudisha (daraja, maelezo).
    Kupanda: C -> B tu. Kushuka: A -> B au B -> C tu (haifichi soko)."""
    try:
        if not st or static_tier not in TIER_ORDER:
            return static_tier, None
        n = st['n']
        if static_tier in ('A', 'B') and n >= LIVE_MIN_N and (st['mean_p'] - st['rate']) > DEMOTE_GAP:
            return _shift_tier(static_tier, -1), 'imeshushwa (overconfident)'
        if (static_tier == 'C' and n >= PROMO_MIN_N and st.get('auc') is not None
                and st['auc'] >= PROMO_AUC and st['rate'] >= st['mean_p'] - PROMO_GAP
                and st.get('stable', True)):
            return 'B', 'imepandishwa (data inaunga mkono)'
    except Exception:
        pass
    return static_tier, None


def live_tier(market, static_tier):
    """(daraja, maelezo) kwa soko, kwa kuzingatia data halisi ya siku 90."""
    if not _auto_on():
        return static_tier, None
    stats = (_live().get('stats') or {})
    return _tier_from_stats(stats.get(str(market)), static_tier)


def quality_info(market, probability):
    """Taarifa ya ubora wa soko kwa probability husika, au None kama soko halimo
    kwenye jedwali (masoko yasiyojulikana hayachujwi na jedwali; LIVE layer inayakagua)."""
    try:
        key, is_under = _quality_key(market)
        q = QUALITY.get(key)
        if q is None:
            return None
        static_tier, skill, base = q
        tier, note = live_tier(market, static_tier)
        base_eff = (100.0 - base) if is_under else base
        edge = float(probability or 0) - base_eff
        min_edge = MIN_EDGE.get(tier, 0.0)
        return {
            'tier': tier,
            'static_tier': static_tier,
            'tier_note': note,
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
# LIVE LAYER (v3): data halisi kutoka prediction_record
# ---------------------------------------------------------------------------
def _live_on():
    return os.environ.get('QUALITY_LIVE', '1').strip().lower() not in ('0', 'off', 'false', 'no')


def _family(m):
    m = str(m)
    if '_and_' in m:
        return 'combo'
    if m.startswith('corners_'):
        return 'corners'
    if m.startswith('sot_'):
        return 'sot'
    if m.startswith(('yellows_', 'cards_', 'booking_', 'both_teams_carded',
                     'both_teams_2plus', 'home_more_cards', 'any_red', 'no_cards')):
        return 'cards'
    if 'goals' in m or m == 'btts':
        return 'goals'
    return 'result'


def _auc(pos, neg):
    """AUC (Mann-Whitney): uwezekano kwamba pick iliyoshinda ilikuwa na probability kubwa kuliko iliyopoteza."""
    n1, n0 = len(pos), len(neg)
    vals = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg], key=lambda t: t[0])
    n = len(vals)
    i = 0
    rank_sum = 0.0
    while i < n:
        j = i
        while j + 1 < n and vals[j + 1][0] == vals[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            if vals[k][1] == 1:
                rank_sum += avg
        i = j + 1
    return (rank_sum - n1 * (n1 + 1) / 2.0) / (n1 * n0)


def _market_stats(items):
    """Takwimu za soko moja: n, win rate %, probability ya wastani %, AUC, utulivu wa nusu mbili."""
    n = len(items)
    wins = sum(y for _p, y, _d in items)
    mean_p = sum(p for p, _y, _d in items) / n
    rate = 100.0 * wins / n
    pos = [p for p, y, _d in items if y]
    neg = [p for p, y, _d in items if not y]
    auc = _auc(pos, neg) if (len(pos) >= LIVE_MIN_CLASS and len(neg) >= LIVE_MIN_CLASS) else None
    stable = True
    try:
        ordered = sorted(items, key=lambda t: t[2])
        half = n // 2
        for part in (ordered[:half], ordered[half:]):
            pp = [p for p, y, _d in part if y]
            nn = [p for p, y, _d in part if not y]
            if len(pp) >= 5 and len(nn) >= 5 and _auc(pp, nn) < 0.50:
                stable = False          # nusu moja inaonyesha soko linafanya kinyume
    except Exception:
        pass
    return {'n': n, 'rate': rate, 'mean_p': mean_p, 'auc': auc, 'stable': stable}


def _log_tier_changes(stats):
    """Andika kwenye logi ya Render kila soko ambalo daraja lake limebadilika (uwazi)."""
    if not _auto_on() or not QUALITY:
        return
    try:
        lines = []
        for m, st in stats.items():
            key, _u = _quality_key(m)
            q = QUALITY.get(key)
            if q is None:
                continue
            tier, note = _tier_from_stats(st, q[0])
            if note:
                lines.append(f"{m}: {q[0]} -> {tier} {note} "
                             f"(n={st['n']}, win={st['rate']:.1f}%, wastani={st['mean_p']:.1f}%, "
                             f"AUC={'-' if st['auc'] is None else round(st['auc'], 3)})")
        for ln in lines:
            print(f'[market_selection] tier {ln}')
        if not lines:
            print('[market_selection] tier: hakuna mabadiliko ya daraja (jedwali la msingi linatumika)')
    except Exception as e:
        print(f'[market_selection] tier log imeshindwa: {type(e).__name__}: {e}')


def _load_live():
    from models import db, PredictionRecord as PR
    since = datetime.utcnow() - timedelta(days=LIVE_DAYS)
    try:
        rows = db.session.query(PR.market, PR.probability, PR.result, PR.match_date).filter(
            PR.section == 'prediction', PR.result.in_(['WON', 'LOST']),
            PR.probability.isnot(None), PR.match_date >= since).all()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        raise
    by_m, by_f = {}, {}
    for m, p, r, d in rows:
        try:
            day = d.date() if hasattr(d, 'date') else d
            item = (float(p), 1 if r == 'WON' else 0, day)
        except Exception:
            continue
        by_m.setdefault(str(m), []).append(item)
        by_f.setdefault(_family(m), []).append(item)
    disc = {}
    for m, items in by_m.items():
        pos = [p for p, y, _d in items if y]
        neg = [p for p, y, _d in items if not y]
        n = len(items)
        if n < LIVE_MIN_N or len(pos) < LIVE_MIN_CLASS or len(neg) < LIVE_MIN_CLASS:
            disc[m] = ('unknown', None, n)
            continue
        auc = _auc(pos, neg)
        disc[m] = ('none' if auc < LIVE_AUC_MIN else 'ok', auc, n)

    stats = {}
    for m, items in by_m.items():
        if len(items) >= 30:
            try:
                stats[m] = _market_stats(items)
            except Exception:
                pass
    _log_tier_changes(stats)
    return {'m': by_m, 'f': by_f, 'disc': disc, 'stats': stats}


def _live():
    if not _live_on():
        return {}
    now = time.time()
    if _LIVE['data'] is not None and now - _LIVE['ts'] < LIVE_TTL:
        return _LIVE['data']
    try:
        _LIVE['data'] = _load_live()
        _LIVE['ts'] = now
    except Exception as e:
        print(f'[market_selection] live data haipatikani: {type(e).__name__}: {e}')
        if _LIVE['data'] is None:
            _LIVE['data'] = {}
        _LIVE['ts'] = now - LIVE_TTL + 300      # jaribu tena baada ya dakika 5
    return _LIVE['data']


def discrimination(market):
    """('ok' | 'none' | 'unknown', auc, n). 'none' = soko halitofautishi washindi na walioshindwa."""
    d = _live().get('disc') or {}
    return d.get(str(market), ('unknown', None, 0))


def is_discriminating(market):
    """False TU kama data inathibitisha soko halitofautishi (na si soko la goals).
    Kwa matumizi ya combos na Best Pick."""
    try:
        if GOALS_TOTAL_RE.match(str(market)):
            return True
        return discrimination(market)[0] != 'none'
    except Exception:
        return True


def live_quality_label(market):
    """Kwa UI: (msimbo, maandishi) au None."""
    try:
        v = discrimination(market)[0]
        if v == 'none':
            if GOALS_TOTAL_RE.match(str(market)):
                return ('goals_flat', 'Ishara hafifu')
            return ('nodisc', 'Haitofautishi')
    except Exception:
        pass
    return None


def honest_rate(market, probability, days=HONEST_DAYS, band=HONEST_BAND):
    """Hit-rate halisi: picks zilizokamilika za soko hili (siku 'days') zenye probability
    ndani ya +/- band pointi. Picks zikiwa chache, inatumia familia ya soko. None kama data haitoshi."""
    try:
        if probability is None:
            return None
        p0 = float(probability)
        live = _live()
        if not live:
            return None
        cutoff = date.today() - timedelta(days=days)

        def near(items):
            return [y for (p, y, d) in items
                    if d is not None and d >= cutoff and abs(p - p0) <= band]

        ys = near(live['m'].get(str(market), []))
        scope = 'soko'
        if len(ys) < HONEST_MIN_N:
            ys = near(live['f'].get(_family(market), []))
            scope = 'familia'
        if len(ys) < HONEST_MIN_N:
            return None
        return {'rate': round(100.0 * sum(ys) / len(ys), 1), 'n': len(ys),
                'scope': scope, 'days': days}
    except Exception:
        return None


def honest_text(market, probability):
    h = honest_rate(market, probability)
    if not h:
        return None
    where = 'soko hili' if h['scope'] == 'soko' else 'masoko ya aina hii'
    return f"Ilishinda kweli {h['rate']:.0f}% ({where}, picks {h['n']}, siku {h['days']})"


def live_report():
    """Kwa Admin baadaye: masoko yote na hukumu yao ('none' kwanza)."""
    d = _live().get('disc') or {}
    out = [{'market': m, 'verdict': v, 'auc': None if a is None else round(a, 3), 'n': n}
           for m, (v, a, n) in d.items()]
    return sorted(out, key=lambda r: (r['verdict'] != 'none', r['market']))


def tier_report():
    """Kwa Admin baadaye: masoko yenye daraja lililobadilika kwa data halisi."""
    out = []
    try:
        for m, st in ((_live().get('stats')) or {}).items():
            key, _u = _quality_key(m)
            q = QUALITY.get(key)
            if q is None:
                continue
            tier, note = _tier_from_stats(st, q[0])
            out.append({'market': m, 'static': q[0], 'live': tier, 'note': note, 'n': st['n'],
                        'win': round(st['rate'], 1), 'mean_p': round(st['mean_p'], 1),
                        'auc': None if st['auc'] is None else round(st['auc'], 3)})
    except Exception:
        pass
    return sorted(out, key=lambda r: (r['note'] is None, r['market']))


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
    probability izidi base rate kwa margin ya daraja). Daraja linarekebishwa na data
    halisi ya siku 90 (v5).
    LIVE layer (v3): soko ambalo data halisi inaonyesha halitofautishi washindi na
    walioshindwa haliwezi kuwa Best Pick (linabaki Other Picks). Goals hazigusiwi.

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
        if not is_discriminating(e[0]):
            return False                      # data halisi: haitofautishi
        qi = quality_info(e[0], e[1])
        if qi is None:
            return True                       # soko lisilojulikana kwenye jedwali: LIVE layer imeshaliangalia
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