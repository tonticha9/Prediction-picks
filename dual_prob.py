"""
dual_prob.py — Probability ya "bila odds" (model v8) kando ya probability kuu.

Inasoma data/predictions_v8.csv (inayowekwa na archive_shadow.py kila usiku).
Ikikosekana faili au soko, inarudisha None na app inaendelea kawaida.

History: faili la 'live' lina mechi za leo hadi siku 4 mbele tu, kwa hiyo mechi
za siku zilizopita hazipo humo. Kwa hizo, v8_prob_on() inasoma archive ya kudumu
shadow/archive/<tarehe>_v8.csv.gz (archive_shadow.py inahifadhi mechi za leo hadi
siku 3 mbele kila usiku). Siku zilizotangulia kuanza kwa archive (kabla ya
2026-10-18) hazina v8, kwa hiyo hakuna kinachoonyeshwa.
"""
import os
import re
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, 'data', 'predictions_v8.csv')
ARCHIVE_DIR = os.path.join(BASE_DIR, 'shadow', 'archive')
ARCHIVE_LOOKBACK = 3          # archive ina mechi za leo hadi siku 3 mbele
ARCHIVE_CACHE_MAX = 30        # idadi ya faili za archive zinazoshikiliwa kwenye cache

_state = {'mtime': None, 'map': {}, 'meta': None}
_archive_cache = {}           # path -> (mtime, map)
_UNDER_RE = re.compile(r'^(.*)_under_(\d+)$')


def _norm(s):
    return str(s or '').strip().lower()


def _day(v):
    try:
        return pd.Timestamp(v).strftime('%Y-%m-%d')
    except Exception:
        return ''


def _read_map(path):
    """Soma faili la v8 (csv au csv.gz) kuwa {(home, away, siku, soko): probability}."""
    mp = {}
    df = pd.read_csv(path)
    df['_prob'] = pd.to_numeric(df['probability_%'], errors='coerce')
    df = df.dropna(subset=['_prob'])
    days = pd.to_datetime(df['match_date'], format='mixed', errors='coerce').dt.strftime('%Y-%m-%d')
    for h, a, d, m, p in zip(df['HomeTeam'], df['AwayTeam'], days, df['market'], df['_prob']):
        if not isinstance(d, str):
            continue
        key = (_norm(h), _norm(a), d, str(m))
        if key not in mp:
            mp[key] = float(p)
    return mp


def _lookup(mp, h, a, d, m):
    """Tafuta probability; 'under' inakokotolewa kama 100 - 'over' ikikosekana."""
    p = mp.get((h, a, d, m))
    if p is None and '_and_' not in m:
        mt = _UNDER_RE.match(m)
        if mt:
            over = mp.get((h, a, d, f'{mt.group(1)}_over_{mt.group(2)}'))
            if over is not None:
                p = 100.0 - over
    return p


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
        mp = _read_map(DATA_PATH)
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


def _load_archive(path):
    """Archive ya siku moja (cache kwa mtime). {} kama faili halipo au limeharibika."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    cached = _archive_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        mp = _read_map(path)
    except Exception as e:
        print(f'[dual_prob] archive imeshindwa ({os.path.basename(path)}): {type(e).__name__}: {e}')
        mp = {}
    if len(_archive_cache) >= ARCHIVE_CACHE_MAX:
        _archive_cache.pop(next(iter(_archive_cache)))
    _archive_cache[path] = (mtime, mp)
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
        p = _lookup(mp, _norm(home), _norm(away), d, str(market))
        return None if p is None else round(p, 1)
    except Exception:
        return None


def v8_prob_on(home, away, mdate, market, snap_day=None, prefer_live=False):
    """Probability ya v8 kwa ajili ya HISTORY ya siku 'snap_day'.

    prefer_live=True (leo na siku zijazo): tumia faili la live kwanza ili iwe SAWA
    na Dashboard. Vinginevyo (siku zilizopita): tafuta archive ya siku hiyo, kisha
    siku 1..3 nyuma (utabiri wa karibuni zaidi kabla ya mechi). Mwisho jaribu live.
    """
    try:
        d = _day(mdate)
        if not d:
            return None
        h, a, m = _norm(home), _norm(away), str(market)

        def from_live():
            mp = _load()
            return _lookup(mp, h, a, d, m) if mp else None

        p = from_live() if prefer_live else None
        if p is None:
            base = pd.Timestamp(snap_day or d).normalize()
            for back in range(ARCHIVE_LOOKBACK + 1):
                fday = (base - pd.Timedelta(days=back)).strftime('%Y-%m-%d')
                mp = _load_archive(os.path.join(ARCHIVE_DIR, f'{fday}_v8.csv.gz'))
                if mp:
                    p = _lookup(mp, h, a, d, m)
                    if p is not None:
                        break
        if p is None and not prefer_live:
            p = from_live()
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
