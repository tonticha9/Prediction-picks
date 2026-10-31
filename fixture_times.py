"""
fixture_times.py — Saa ya kuanza kwa kila mechi (EAT).

Plugin ya admin_tools.py (PLUGINS). Haihitaji kuhariri app.py wala templates:
  - jedwali jipya 'fixture_time' (linatengenezwa lenyewe)
  - Jinja global kickoff(home, away, tarehe) -> 'HH:MM' au ''
  - inafunika render_template ili Dashboard, Zijazo, History na Value Bets
    zipate saa ya mechi na zipangwe kwa saa (mechi zisizo na saa zinakuwa mwisho).
Jedwali linajazwa na .github/scripts/sync_fixture_times.py (ndani ya hourly_catchup).
"""
import re
import time
from datetime import date, datetime, timedelta

from models import db

try:
    import team_names
except Exception:
    team_names = None

_KEY_RE = re.compile(r'[^a-z0-9]+')


def team_key(name):
    """Jina la timu -> ufunguo thabiti (majina rasmi ya team_names.py, herufi ndogo bila alama)."""
    s = str(name or '').strip()
    if team_names is not None:
        try:
            c = team_names.canon(s)
            if isinstance(c, str):
                s = c
        except Exception:
            pass
    return _KEY_RE.sub('', s.lower())


class FixtureTime(db.Model):
    __tablename__ = 'fixture_time'
    __table_args__ = (
        db.UniqueConstraint('match_date', 'home_key', 'away_key', name='uq_fixture_time'),
    )

    id = db.Column(db.Integer, primary_key=True)
    match_date = db.Column(db.Date, nullable=False, index=True)
    home_team = db.Column(db.String(100))
    away_team = db.Column(db.String(100))
    home_key = db.Column(db.String(100), nullable=False)
    away_key = db.Column(db.String(100), nullable=False)
    kickoff = db.Column(db.String(5), nullable=False)      # 'HH:MM' kwa EAT
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)


_CACHE = {'t': 0.0, 'pair': {}, 'home': {}, 'away': {}}
_TTL = 120   # sekunde


def _load():
    now = time.time()
    if _CACHE['t'] and now - _CACHE['t'] < _TTL:
        return
    try:
        since = date.today() - timedelta(days=100)
        rows = FixtureTime.query.filter(FixtureTime.match_date >= since).all()
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        print(f'[fixture_times] kusoma jedwali kumeshindwa: {e}')
        _CACHE['t'] = now
        return
    pair, home, away = {}, {}, {}
    for r in rows:
        pair[(r.match_date, r.home_key, r.away_key)] = r.kickoff
        home.setdefault((r.match_date, r.home_key), set()).add(r.kickoff)
        away.setdefault((r.match_date, r.away_key), set()).add(r.kickoff)
    _CACHE.update(t=now, pair=pair, home=home, away=away)


def _to_date(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt, n in (('%Y-%m-%d', 10), ('%d %b %Y', 11)):
        try:
            return datetime.strptime(s[:n].strip(), fmt).date()
        except ValueError:
            continue
    try:
        import pandas as pd
        return pd.Timestamp(v).date()
    except Exception:
        return None


def kickoff(home, away, when):
    """Saa ya mechi (EAT) kama 'HH:MM', au '' kama haijulikani."""
    d = _to_date(when)
    if d is None:
        return ''
    _load()
    hk, ak = team_key(home), team_key(away)
    offsets = (0, -1, 1)          # tarehe ya CSV na API inaweza kutofautiana kwa siku 1 usiku wa manane
    for off in offsets:
        t = _CACHE['pair'].get((d + timedelta(days=off), hk, ak))
        if t:
            return t
    for off in offsets:           # jina la upande mmoja likitofautiana: tumia upande mmoja ukiwa wa kipekee
        dd = d + timedelta(days=off)
        for table, k in ((_CACHE['home'], hk), (_CACHE['away'], ak)):
            ts = table.get((dd, k))
            if ts and len(ts) == 1:
                return next(iter(ts))
    return ''


def _sort_by_time(items, getter):
    items.sort(key=lambda x: getter(x) or '99:99')


def _decorate(template, ctx):
    if template in ('dashboard.html', 'upcoming.html'):
        ms = ctx.get('matches') or []
        for m in ms:
            if not m.get('time_eat'):
                t = kickoff(m.get('home'), m.get('away'), m.get('date'))
                if t:
                    m['time_eat'] = t          # template inaongeza ' EAT' yenyewe
        _sort_by_time(ms, lambda m: m.get('time_eat'))

    elif template == 'history.html':
        ms = ctx.get('matches') or []
        for m in ms:
            if not m.get('time_eat'):
                t = kickoff(m.get('home'), m.get('away'), m.get('date'))
                if t:
                    m['time_eat'] = t + ' EAT'
        _sort_by_time(ms, lambda m: m.get('time_eat'))
        for r in ctx.get('value_bets_list') or []:
            t = kickoff(r.get('home'), r.get('away'), r.get('date'))
            if t:
                r['date'] = f"{r['date']} · 🕒 {t} EAT"

    elif template == 'value_bets.html':
        for b in ctx.get('bets') or []:
            t = kickoff(b.get('home'), b.get('away'), b.get('date'))
            if t:
                b['date'] = f"{b['date']} · 🕒 {t} EAT"


def register(app, admin_only, app_ns):
    try:
        with app.app_context():
            FixtureTime.__table__.create(db.engine, checkfirst=True)
    except Exception as e:
        print(f'[fixture_times] kutengeneza jedwali kumeshindwa: {e}')

    app.jinja_env.globals['kickoff'] = kickoff

    orig = app_ns.get('render_template')
    if orig is not None and not getattr(orig, '_ft_patched', False):
        def render_template_ft(template_name, **ctx):
            try:
                _decorate(template_name, ctx)
            except Exception as e:
                print(f'[fixture_times] kuongeza saa kumeshindwa: {type(e).__name__}: {e}')
            return orig(template_name, **ctx)
        render_template_ft._ft_patched = True
        app_ns['render_template'] = render_template_ft