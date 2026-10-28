"""
dual_prob.py — Probability ya "bila odds" (model v8) kando ya probability kuu.

Inasoma data/predictions_v8.csv (inayowekwa na archive_shadow.py kila usiku).
Ikikosekana faili au soko, inarudisha None na app inaendelea kawaida.
"""
import os
import re
import pandas as pd

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'predictions_v8.csv')

_state = {'mtime': None, 'map': {}, 'meta': None}
_UNDER_RE = re.compile(r'^(.*)_under_(\d+)$')


def _norm(s):
    return str(s or '').strip().lower()


def _day(v):
    try:
        return pd.Timestamp(v).strftime('%Y-%m-%d')
    except Exception:
        return ''


def _load():
    """Soma faili MARA MOJA kila linapobadilika (cache kwa mtime)."""
    try:
        mtime = os.path.getmtime(DATA_PATH)
    except OSError:
        _state.update(mtime=None, map={}, meta=None)
        return _state['map']
    if _state['mtime'] == mtime:
        return _state['map']

    mp = {}
    meta = None
    try:
        df = pd.read_csv(DATA_PATH)
        df['_prob'] = pd.to_numeric(df['probability_%'], errors='coerce')
        df = df.dropna(subset=['_prob'])
        days = pd.to_datetime(df['match_date'], format='mixed', errors='coerce').dt.strftime('%Y-%m-%d')
        for h, a, d, m, p in zip(df['HomeTeam'], df['AwayTeam'], days, df['market'], df['_prob']):
            if not isinstance(d, str):
                continue
            key = (_norm(h), _norm(a), d, str(m))
            if key not in mp:
                mp[key] = float(p)
        if mp:
            all_days = sorted({k[2] for k in mp})
            meta = {
                'rows': len(mp),
                'matches': len({k[:3] for k in mp}),
                'min_day': all_days[0],
                'max_day': all_days[-1],
            }
    except Exception as e:
        print(f'[dual_prob] imeshindwa kusoma predictions_v8.csv: {type(e).__name__}: {e}')
        mp, meta = {}, None

    _state.update(mtime=mtime, map=mp, meta=meta)
    return mp


def v8_prob(home, away, mdate, market):
    """Probability ya v8 (bila odds) kwa soko hili, au None kama haipo."""
    try:
        mp = _load()
        if not mp:
            return None
        d = _day(mdate)
        if not d:
            return None
        h, a, m = _norm(home), _norm(away), str(market)
        p = mp.get((h, a, d, m))
        if p is None and '_and_' not in m:
            mt = _UNDER_RE.match(m)
            if mt:
                over = mp.get((h, a, d, f'{mt.group(1)}_over_{mt.group(2)}'))
                if over is not None:
                    p = 100.0 - over
        return None if p is None else round(p, 1)
    except Exception:
        return None


def v8_status():
    """Muhtasari wa faili kwa Admin health check; None kama faili haipo/tupu."""
    try:
        _load()
        return _state['meta']
    except Exception:
        return None