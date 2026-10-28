"""
retrain_tools.py — Plugin ya admin_tools (v1, Oct 28 2026)

Inaongeza MAELEZO YA MANENO kwenye Market Analysis (/admin/markets):
  1) Baada ya retrain: ushindi umepanda au umeshuka, na calibration imeboreka au imezorota
     (kabla ya retrain dhidi ya baada yake, kwa kila soko).
  2) Calibration ya kila siku: soko limerekebishwa au la, na kwa nini
     (inasoma jedwali calib_report lililoandikwa na daily_calibration.py).

Inapakiwa otomatiki na admin_tools.py (PLUGINS). Ikipata kosa, Market Analysis inaendelea
kufanya kazi bila maelezo haya. Tarehe za retrain zinatoka SEED_EVENTS na data/retrain_log.json
(inaandikwa na workflow ya retrain_weekly kila retrain inapochapishwa).
"""
import json
import math
import os
import time
from datetime import date, datetime, timedelta

import pandas as pd

SEED_EVENTS = [
    {'date': '2026-10-27', 'type': 'retrain',
     'note': 'retrain ya mkono: masoko 64, mawili yalirudishwa kwa utulivu'},
]
LOOKBACK_DAYS = 60         # siku za kabla ya retrain zinazolinganishwa
MIN_AFTER = 20             # picks za chini baada ya retrain kabla ya kuhukumu
Z_STRONG = 1.64            # ushahidi wa mabadiliko ya win rate
GAP_STEP = 1.0             # pointi za pengo zinazohesabiwa kama mabadiliko
_CACHE = {}
_MONTHS = ['Jan', 'Feb', 'Mac', 'Apr', 'Mei', 'Jun', 'Jul', 'Ago', 'Sep', 'Okt', 'Nov', 'Des']


# ================================================================
# HESABU SAFI (hazihitaji Flask wala database)
# ================================================================
def canon(market):
    """Sawa na daily_calibration.py: jozi zinazosaidiana zinakuwa soko moja."""
    m = str(market)
    if m == 'dc_x2':
        return 'home_win', True
    if m == 'dc_1x':
        return 'away_win', True
    if '_and_' not in m and 'under' in m:
        return m.replace('under', 'over', 1), True
    return m, False


def label_date(d):
    return f"{d.day} {_MONTHS[d.month - 1]}"


def retrain_sentence(n_b, y_b, p_b, n_a, y_a, p_a, pre_down, d_label):
    """Rudisha {'text', 'tone'}; tone = good | bad | neutral."""
    if n_a == 0:
        return {'tone': 'neutral',
                'text': f"➖ Retrain ya {d_label}: bado hakuna picks za soko hili zilizokamilika baada yake."}
    wr_a, mp_a = y_a / n_a * 100, p_a * 100
    gap_a = wr_a - mp_a
    if n_a < MIN_AFTER:
        return {'tone': 'neutral',
                'text': (f"➖ Retrain ya {d_label}: picks {n_a} tu zimekamilika baada yake "
                         f"(inahitaji {MIN_AFTER}+ kuhukumu). Hadi sasa win rate {wr_a:.0f}%, "
                         f"model ilisema {mp_a:.0f}%.")}
    if n_b < MIN_AFTER:
        return {'tone': 'neutral',
                'text': (f"➖ Baada ya retrain ya {d_label}: win rate {wr_a:.0f}% ({n_a} picks) dhidi ya "
                         f"model {mp_a:.0f}% (pengo {gap_a:+.1f}). Kabla ya retrain data ilikuwa chache "
                         f"kulinganisha.")}
    wr_b, mp_b = y_b / n_b * 100, p_b * 100
    gap_b = wr_b - mp_b
    fa, fb = y_a / n_a, y_b / n_b
    se = math.sqrt(fa * (1 - fa) / n_a + fb * (1 - fb) / n_b)
    z = (fa - fb) / se if se > 0 else 0.0
    up, down = z >= Z_STRONG, z <= -Z_STRONG
    better = abs(gap_a) <= abs(gap_b) - GAP_STEP
    worse_any = abs(gap_a) >= abs(gap_b) + GAP_STEP
    worse = worse_any and gap_a < 0          # model inajiamini kupita kiasi = mbaya
    cautious = worse_any and gap_a > 0       # model inashinda zaidi ya ilivyosema = si mbaya
    if up:
        win_txt = f"ushindi umepanda ({wr_b:.0f}% → {wr_a:.0f}%)"
    elif down:
        win_txt = (f"ushindi umeendelea kushuka ({wr_b:.0f}% → {wr_a:.0f}%)" if pre_down
                   else f"ushindi umeshuka ({wr_b:.0f}% → {wr_a:.0f}%)")
    else:
        win_txt = f"ushindi haujabadilika kwa ushahidi wa kutosha ({wr_b:.0f}% → {wr_a:.0f}%)"
    if better:
        cal_txt = f"calibration imeboreka (pengo {gap_b:+.1f} → {gap_a:+.1f} pts)"
    elif worse:
        cal_txt = f"calibration imezorota, model inajiamini kupita kiasi (pengo {gap_b:+.1f} → {gap_a:+.1f} pts)"
    elif cautious:
        cal_txt = (f"model sasa inashinda zaidi ya ilivyosema, yaani ina tahadhari "
                   f"(pengo {gap_b:+.1f} → {gap_a:+.1f} pts)")
    else:
        cal_txt = f"calibration karibu sawa (pengo {gap_a:+.1f} pts)"
    good = (up or better) and not (down or worse)
    bad = (down or worse) and not (up or better)
    tone = 'good' if good else ('bad' if bad else 'neutral')
    icon = {'good': '✅', 'bad': '⚠️', 'neutral': '➖'}[tone]
    return {'tone': tone,
            'text': f"{icon} Baada ya retrain ya {d_label}: {win_txt} na {cal_txt}. "
                    f"(picks {n_b} → {n_a})"}


def compute_stories(df, event_date, today=None):
    """df: market, probability(%), result(WON/LOST), match_date. Rudisha {market: sentence_dict}."""
    out = {}
    if df is None or len(df) == 0:
        return out
    d = df.copy()
    d['y'] = (d['result'] == 'WON').astype(int)
    d['p'] = d['probability'].astype(float) / 100.0
    d['day'] = pd.to_datetime(d['match_date']).dt.date
    after_from = event_date + timedelta(days=1)
    label = label_date(event_date)
    for m, g_ in d.groupby('market'):
        b = g_[g_['day'] <= event_date]
        a = g_[g_['day'] >= after_from]
        pre_down = False
        if len(b):
            last = b[b['day'] > event_date - timedelta(days=14)]
            older = b[b['day'] <= event_date - timedelta(days=14)]
            if len(last) >= 10 and len(older) >= 20:
                pre_down = (last['y'].mean() - older['y'].mean()) <= -0.05
        out[m] = retrain_sentence(len(b), int(b['y'].sum()), float(b['p'].sum() / len(b)) if len(b) else 0.0,
                                  len(a), int(a['y'].sum()), float(a['p'].sum() / len(a)) if len(a) else 0.0,
                                  pre_down, label)
    return out


def calib_sentence(market, rows):
    """rows: {soko_kuu: dict ya calib_report}. Rudisha {'text','tone'} au None."""
    cm, inv = canon(market)
    r = rows.get(cm)
    if not r:
        return None
    st, n = r.get('status'), int(r.get('n') or 0)
    gap = r.get('gap_pts')
    z = r.get('z')
    if gap is None:
        return None
    gap_m = -gap if inv else gap
    if st == 'APPLY':
        mp = float(r.get('mean_p') or 0.5)
        eff = float(r.get('b') or 0.0) * mp * (1 - mp) * 100
        eff = -eff if inv else eff
        return {'tone': 'neutral',
                'text': (f"🎯 Calibration ya kila siku: probability ya soko hili imerekebishwa "
                         f"(takriban {eff:+.1f} pts) kwa sababu pengo la {gap_m:+.1f} pts limethibitika "
                         f"(picks {n}, z={float(z or 0):+.1f}).")}
    if st == 'NO_SIGNAL':
        return {'tone': 'neutral',
                'text': (f"🎯 Calibration ya kila siku: hakuna ushahidi wa kosa la probability "
                         f"(pengo {gap_m:+.1f} pts liko ndani ya kelele, picks {n}) — haijarekebishwa.")}
    if st == 'TOO_FEW':
        return {'tone': 'neutral',
                'text': f"🎯 Calibration ya kila siku: picks {n} bado chache (inahitaji 120) — haijarekebishwa."}
    if st == 'NO_HOLDOUT_GAIN':
        return {'tone': 'neutral',
                'text': (f"🎯 Calibration ya kila siku: pengo la {gap_m:+.1f} pts lipo, lakini jaribio la "
                         f"data mpya halikuthibitisha uboreshaji — haijarekebishwa.")}
    if st == 'TINY':
        return {'tone': 'neutral',
                'text': (f"🎯 Calibration ya kila siku: pengo la {gap_m:+.1f} pts, marekebisho yangekuwa "
                         f"madogo mno — haijarekebishwa.")}
    return None


# ================================================================
# DATA (Flask + database)
# ================================================================
def _events(ns):
    ev = [dict(e) for e in SEED_EVENTS]
    path = os.path.join(ns.get('DATA_DIR') or 'data', 'retrain_log.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            extra = json.load(f)
        known = {(e['date'], e['type']) for e in ev}
        for e in extra if isinstance(extra, list) else []:
            if isinstance(e, dict) and e.get('date') and (e['date'], e.get('type')) not in known:
                ev.append(e)
    except Exception:
        pass
    good = []
    for e in ev:
        try:
            e['_d'] = date.fromisoformat(str(e['date'])[:10])
            good.append(e)
        except Exception:
            continue
    good.sort(key=lambda e: e['_d'])
    return good


def _last_event(ns):
    ev = [e for e in _events(ns) if e.get('type') in ('retrain', 'rollback')]
    return ev[-1] if ev else None


def _cached(key, ttl, fn):
    hit = _CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _CACHE[key] = (now, val)
    return val


def _load_stories(ns, min_prob):
    from models import db, PredictionRecord as PR
    ev = _last_event(ns)
    if ev is None:
        return {}
    since = datetime.combine(ev['_d'] - timedelta(days=LOOKBACK_DAYS), datetime.min.time())
    q = db.session.query(PR.market, PR.probability, PR.result, PR.match_date).filter(
        PR.section == 'prediction', PR.result.in_(['WON', 'LOST']),
        PR.probability.isnot(None), PR.probability >= float(min_prob), PR.match_date >= since)
    df = pd.DataFrame(q.all(), columns=['market', 'probability', 'result', 'match_date'])
    return compute_stories(df, ev['_d'])


def _load_calib():
    from models import db
    from sqlalchemy import text
    rows = db.session.execute(text(
        "SELECT market, n, mean_p, win_rate, gap_pts, z, b, s, status FROM calib_report "
        "WHERE run_date = (SELECT MAX(run_date) FROM calib_report)")).fetchall()
    cols = ['market', 'n', 'mean_p', 'win_rate', 'gap_pts', 'z', 'b', 's', 'status']
    return {r[0]: dict(zip(cols, r)) for r in rows}


def register(app, admin_only, app_ns):
    def _safe(fn, default=None):
        try:
            return fn()
        except Exception as e:
            try:
                from models import db
                db.session.rollback()
            except Exception:
                pass
            print(f'[retrain_tools] {type(e).__name__}: {e}')
            return default

    @app.context_processor
    def _retrain_ctx():
        def market_story(market, min_prob, section='prediction'):
            if section != 'prediction':
                return None
            stories = _safe(lambda: _cached(('st', round(float(min_prob), 1)), 120,
                                            lambda: _load_stories(app_ns, min_prob)), {}) or {}
            calib = _safe(lambda: _cached(('cal',), 120, _load_calib), {}) or {}
            lines = []
            s = stories.get(market)
            if s:
                lines.append(s)
            c = calib_sentence(market, calib)
            if c:
                lines.append(c)
            return lines or None

        def retrain_info():
            ev = _safe(lambda: _last_event(app_ns))
            if not ev:
                return None
            days = (date.today() - ev['_d']).days
            return {'label': label_date(ev['_d']), 'days_ago': max(days, 0),
                    'type': ev.get('type'), 'note': ev.get('note', ''),
                    'revision': ev.get('revision'), 'n_markets': ev.get('n_markets')}

        return {'market_story': market_story, 'retrain_info': retrain_info}