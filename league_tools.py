"""
league_tools.py — Plugin ya admin_tools (v1, Nov 24 2026)

Inaonyesha LIGI ya kila mechi kwenye app (Dashboard, Zijazo, History, Value Bets) bila kuhariri app.py:
  - jina la ligi kando ya kila mechi, alama nyekundu ya NEW kwa ligi 5 mpya (NOR, MEX, BRA, E2, EC),
  - kichujio cha ligi juu ya orodha (kinakumbukwa kwenye kurasa zote), kikiwemo History,
  - jedwali la 'league_team' (timu -> ligi) linalokua lenyewe kutoka data/predictions.csv, ili History ya zamani
    na settlement ya ligi 5 (settle_new5.py) zijue ligi ya kila timu.
Chanzo cha ligi (kipaumbele cha juu kwenda chini): safu 'league' ya predictions.csv/value_bets.csv > jedwali league_team >
data/team_league_map.csv (ligi 12 kutoka dataset).
Ikipata kosa lolote, kurasa zinaendelea kufanya kazi bila ligi (hakuna kinachovunjika).
"""
import json
import os
import re
from datetime import datetime

from models import db

NEW5 = ('NOR', 'MEX', 'BRA', 'E2', 'EC')
LEAGUE_NAMES = {
    'E0': 'Premier League', 'E1': 'Championship', 'D1': 'Bundesliga', 'SP1': 'La Liga', 'F1': 'Ligue 1',
    'I1': 'Serie A', 'N1': 'Eredivisie', 'B1': 'Pro League', 'P1': 'Primeira Liga', 'T1': 'Süper Lig',
    'G1': 'Super League GR', 'SC0': 'Scottish Prem',
    'NOR': 'Eliteserien', 'MEX': 'Liga MX', 'BRA': 'Brasileirão', 'E2': 'League One', 'EC': 'National League',
}
DATA = 'data'
_WS = re.compile(r'\s+')


def norm(s):
    return _WS.sub(' ', str(s or '')).strip().lower()


class LeagueTeam(db.Model):
    __tablename__ = 'league_team'
    team_key = db.Column(db.String(120), primary_key=True)
    league = db.Column(db.String(12), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)


def _pairs_from_df(df):
    """DataFrame yenye HomeTeam, AwayTeam, league -> {jina_lililosafishwa: ligi} (safu zenye ligi tu)."""
    out = {}
    if df is None or len(df) == 0 or 'league' not in df.columns:
        return out
    for h, a, lg in zip(df['HomeTeam'], df['AwayTeam'], df['league']):
        lg = str(lg).strip() if lg == lg and lg is not None else ''
        if not lg or lg.lower() == 'nan':
            continue
        out[norm(h)] = lg
        out[norm(a)] = lg
    return out


def sync_teams(df):
    """Weka timu za df (safu 'league') kwenye jedwali league_team. Rudisha idadi ya mabadiliko. Inaita commit yenyewe."""
    pairs = _pairs_from_df(df)
    if not pairs:
        return 0
    try:
        existing = {r.team_key: r for r in LeagueTeam.query.filter(LeagueTeam.team_key.in_(list(pairs))).all()}
        n = 0
        for k, lg in pairs.items():
            cur = existing.get(k)
            if cur is None:
                db.session.add(LeagueTeam(team_key=k[:120], league=lg[:12], updated_at=datetime.utcnow()))
                n += 1
            elif cur.league != lg:
                cur.league = lg
                cur.updated_at = datetime.utcnow()
                n += 1
        if n:
            db.session.commit()
        return n
    except Exception:
        db.session.rollback()
        raise


def _file_map():
    path = os.path.join(DATA, 'team_league_map.csv')
    mp = {}
    if os.path.exists(path):
        try:
            import pandas as pd
            c = pd.read_csv(path)
            for t, lg in zip(c['team'], c['league']):
                mp[norm(t)] = str(lg)
        except Exception:
            pass
    return mp


def team_league_dict(df=None):
    """{jina_lililosafishwa: ligi}: ramani ya faili < jedwali la DB < safu 'league' ya df/predictions."""
    mp = _file_map()
    try:
        for r in LeagueTeam.query.all():
            mp[r.team_key] = r.league
    except Exception:
        db.session.rollback()
    mp.update(_pairs_from_df(df))
    return mp


def _read_csv_cols(name):
    path = os.path.join(DATA, name)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    try:
        import pandas as pd
        return pd.read_csv(path, usecols=lambda c: c in ('HomeTeam', 'AwayTeam', 'league'))
    except Exception:
        return None


def _sig():
    out = []
    for n in ('predictions.csv', 'value_bets.csv', 'team_league_map.csv'):
        try:
            out.append(os.path.getmtime(os.path.join(DATA, n)))
        except OSError:
            out.append(0)
    return tuple(out)


def _safe_json(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')


_CACHE = {'sig': None, 'html': ''}


def _build_script():
    p = _read_csv_cols('predictions.csv')
    v = _read_csv_cols('value_bets.csv')
    for df in (p, v):
        try:
            sync_teams(df)
        except Exception as e:
            print(f'[league_tools] sync ya timu imeshindwa: {type(e).__name__}: {e}')
    mp = team_league_dict()
    for df in (p, v):
        mp.update(_pairs_from_df(df))
    return ('<script>window.LEAGUE_MAP=' + _safe_json(mp) + ';window.LEAGUE_NAMES=' + _safe_json(LEAGUE_NAMES) +
            ';window.LEAGUE_NEW=' + _safe_json(list(NEW5)) + ';</script>')


def register(app, admin_only, app_ns):
    @app.context_processor
    def _league_ctx():
        try:
            sig = _sig()
            if _CACHE['sig'] != sig or not _CACHE['html']:
                _CACHE['html'] = _build_script()
                _CACHE['sig'] = sig
            return {'league_script': _CACHE['html']}
        except Exception as e:
            print(f'[league_tools] imeshindwa: {type(e).__name__}: {e}')
            return {'league_script': ''}