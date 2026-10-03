"""
Braiton Picks — Flask app
Dashboard ya predictions za kila siku + Value Bets + Combos + History + Zijazo
+ Akaunti za watumiaji + Admin + Health checks.
"""
from flask import (Flask, render_template, request, redirect, url_for, flash,
                   jsonify, session, g, abort)
import pandas as pd
import os
import re
import shutil
import random
import secrets
import time
from functools import wraps
from datetime import datetime, date, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from werkzeug.middleware.proxy_fix import ProxyFix

from models import db, Settings, ApiConfig, DataSnapshot, PredictionRecord, ComboRecord, ComboLeg
from user_model import AppUser
from market_selection import is_market_allowed, select_markets
from combo_builder import build_daily_combos, combo_result_from_legs

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, 'data')
HISTORY_DIR = os.path.join(DATA_DIR, 'history')

# ================================================================
# SAA ZA AFRIKA MASHARIKI (EAT = UTC+3, Tanzania haina DST)
# ================================================================
EAT = timezone(timedelta(hours=3))


def now_eat():
    return datetime.now(EAT).replace(tzinfo=None)


def today_eat():
    return now_eat().date()


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# ================================================================
# USALAMA WA SESSION
# ================================================================
_secret = os.environ.get('SECRET_KEY')
SECRET_KEY_MISSING = not _secret
app.config['SECRET_KEY'] = _secret or secrets.token_hex(32)
COOKIE_SECURE = os.environ.get('COOKIE_SECURE', '1') == '1'
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

_database_url = os.environ.get('DATABASE_URL')
if _database_url:
    if _database_url.startswith('postgres://'):
        _database_url = _database_url.replace('postgres://', 'postgresql://', 1)
    _database_url = _database_url.replace('postgresql+psycopg://', 'postgresql+psycopg2://', 1)
    if '+psycopg' not in _database_url:
        _database_url = _database_url.replace('postgresql://', 'postgresql+psycopg2://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = _database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(BASE_DIR, 'app.db')}"

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

ADMIN_EMAIL = (os.environ.get('ADMIN_EMAIL') or '').strip().lower()
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD') or ''
REFRESH_SECRET = os.environ.get('REFRESH_SECRET')
CONTACT_EMAIL = (os.environ.get('CONTACT_EMAIL') or '').strip()
REQUIRE_LOGIN = os.environ.get('REQUIRE_LOGIN', '0') == '1'

WEAK_ADMIN_PASSWORDS = {'badilisha-hii', 'password', 'admin', 'admin123', '12345678', '123456789'}
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

FORMULA_CHOICES = ['random_threshold', 'ev_odds_only', 'highest_probability', 'highest_probability_odds_only']

_cache = {}


# ================================================================
# AKAUNTI: helpers
# ================================================================
_rate_store = {}


def _rate_limited(bucket, key, limit, window):
    now = time.time()
    k = (bucket, key)
    hits = [t for t in _rate_store.get(k, []) if now - t < window]
    _rate_store[k] = hits
    return len(hits) >= limit


def _rate_hit(bucket, key):
    _rate_store.setdefault((bucket, key), []).append(time.time())


def _rate_clear(bucket, key):
    _rate_store.pop((bucket, key), None)


def _client_ip():
    return request.remote_addr or 'unknown'


def _safe_next(target):
    """Ruhusu redirect za ndani ya app pekee."""
    if target and target.startswith('/') and not target.startswith('//') and '\\' not in target:
        return target
    return None


def _touch_last_seen(user):
    now = datetime.utcnow()
    if user.last_seen is None or (now - user.last_seen) > timedelta(minutes=10):
        try:
            user.last_seen = now
            db.session.commit()
        except Exception:
            db.session.rollback()


def get_current_user():
    if 'user' in g:
        return g.user
    g.user = None
    uid = session.get('uid')
    if uid:
        try:
            user = db.session.get(AppUser, uid)
            if user:
                g.user = user
                _touch_last_seen(user)
            else:
                session.pop('uid', None)   # mtumiaji ameondolewa
        except Exception:
            db.session.rollback()
    return g.user


def _login_session(user):
    session.clear()
    session['uid'] = user.id
    session.permanent = True


def admin_only(f):
    """Ukurasa wa admin: asiye admin anaona 404 (hajui kama upo)."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user:
            return redirect(url_for('login', next=request.path))
        if not user.is_admin:
            abort(404)
        return f(*args, **kwargs)
    return wrapper


def ensure_admin_user():
    """Tengeneza/sawazisha akaunti ya admin kutoka ADMIN_EMAIL + ADMIN_PASSWORD."""
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        print('[WARN] ADMIN_EMAIL/ADMIN_PASSWORD hazijawekwa - hakuna admin.')
        return
    user = AppUser.query.filter_by(email=ADMIN_EMAIL).first()
    if not user:
        user = AppUser(email=ADMIN_EMAIL, is_admin=True)
        user.set_password(ADMIN_PASSWORD)
        db.session.add(user)
    else:
        user.is_admin = True
        if not user.check_password(ADMIN_PASSWORD):
            user.set_password(ADMIN_PASSWORD)
    db.session.commit()


PUBLIC_ENDPOINTS = {'login', 'register', 'logout', 'privacy', 'about', 'static',
                    'api_refresh', 'api_config_key', 'admin_login', 'admin_logout'}


@app.before_request
def require_login_gate():
    if not REQUIRE_LOGIN:
        return None
    if request.endpoint is None or request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if not get_current_user():
        return redirect(url_for('login', next=request.path))
    return None


@app.context_processor
def inject_globals():
    user = get_current_user()
    return {'current_user': user, 'is_admin': bool(user and user.is_admin),
            'contact_email': CONTACT_EMAIL}


@app.template_filter('eat')
def eat_filter(dt, fmt='%d %b %Y %H:%M'):
    """Badilisha muda wa UTC (uliohifadhiwa) kuwa saa za Tanzania."""
    if not dt:
        return '—'
    return (dt + timedelta(hours=3)).strftime(fmt)


# ================================================================
# DATA
# ================================================================
def load_live_data():
    global _cache
    predictions = pd.read_csv(os.path.join(DATA_DIR, 'predictions.csv'))
    predictions['match_date'] = pd.to_datetime(predictions['match_date'], format='mixed')

    value_bets = pd.read_csv(os.path.join(DATA_DIR, 'value_bets.csv'))
    value_bets['match_date'] = pd.to_datetime(value_bets['match_date'], format='mixed')

    combos = pd.read_csv(os.path.join(DATA_DIR, 'combos.csv'))

    _cache = {'predictions': predictions, 'value_bets': value_bets, 'combos': combos}
    return _cache


def get_data():
    if not _cache:
        return load_live_data()
    return _cache


def today_predictions_survivors(settings):
    data = get_data()
    df = data['predictions'].copy()
    today_str = today_eat().strftime('%Y-%m-%d')
    df = df[df['match_date'].dt.strftime('%Y-%m-%d') == today_str]

    out = []
    for (home, away, mdate), grp in df.groupby(['HomeTeam', 'AwayTeam', 'match_date']):
        entries = [(row['market'], row['probability_%'],
                    row['real_odds'] if pd.notna(row.get('real_odds')) else None,
                    row.get('pro_tier'), row)
                   for _, row in grp.iterrows()]
        survivors = select_markets(entries, settings.probability_threshold, settings.best_pick_formula)
        if survivors:
            out.append({'home': home, 'away': away, 'match_date': mdate, 'survivors': survivors})
    return out


# ================================================================
# SHERIA YA KUDUMU YA MASOKO - inatumika pia kwa COMBOS
# ================================================================
def _fallback_market_rule(market):
    m = str(market)
    if re.match(r'^goals_.*_(05|45)$', m):
        return False
    if m.startswith(('home_goals_under_', 'away_goals_under_')):
        return False
    if '_and_' in m and 'under' in m:
        return False
    return True


def combo_market_allowed(market):
    try:
        return bool(is_market_allowed(market))
    except TypeError:
        return _fallback_market_rule(market)


def all_matches_market_pool():
    """Mechi ZOTE za LEO + ZIJAZO, kila mechi ikiwa na ORODHA KAMILI ya
    masoko yake yanayoruhusiwa (bila kuchujwa na select_markets())."""
    df = get_data()['predictions'].copy()
    today = today_eat()
    df = df[df['match_date'].dt.date >= today]

    pool = []
    for (home, away, mdate), grp in df.groupby(['HomeTeam', 'AwayTeam', 'match_date']):
        markets = [{
            'market': row['market'], 'probability': row['probability_%'],
            'real_odds': row['real_odds'] if pd.notna(row.get('real_odds')) else None,
            'pro_tier': row.get('pro_tier'),
        } for _, row in grp.iterrows() if combo_market_allowed(row['market'])]
        if markets:
            pool.append({'home': home, 'away': away, 'match_date': mdate, 'markets': markets})
    return pool


# ================================================================
# HELPERS ZA KUSAFISHA THAMANI (NaN -> None, Timestamp -> datetime)
# ================================================================
def _clean_num(v):
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _clean_str(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return str(v)


def _to_dt(v):
    ts = pd.Timestamp(v)
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    return ts.to_pydatetime()


def record_predictions_pending():
    """
    TOLEO LA HARAKA: query MOJA ya rekodi zilizopo (kwa kila section),
    hesabu zote ndani ya memory, na commit MOJA mwishoni.
    (Toleo la zamani lilifanya query moja kwa kila soko -> maelfu ya safari
    za mtandao kwenda Neon -> timeout.)
    """
    section_map = {'predictions': 'prediction', 'value_bets': 'value_bet'}
    today = today_eat()
    new_count = 0
    updated_count = 0

    for cache_key, section in section_map.items():
        df = _cache.get(cache_key)
        if df is None or len(df) == 0:
            continue

        rows = df.to_dict('records')
        min_date = _to_dt(df['match_date'].min())

        existing_map = {}
        existing_q = PredictionRecord.query.filter(
            PredictionRecord.section == section,
            PredictionRecord.match_date >= min_date
        ).all()
        for rec in existing_q:
            existing_map[(rec.match_date, rec.home_team, rec.away_team, rec.market)] = rec

        new_objs = []
        for row in rows:
            mdate = _to_dt(row['match_date'])
            home = row['HomeTeam']
            away = row['AwayTeam']
            market = row['market']
            key = (mdate, home, away, market)

            prob = _clean_num(row.get('probability_%'))
            odds = _clean_num(row.get('real_odds'))
            tier = _clean_str(row.get('pro_tier'))

            existing = existing_map.get(key)
            if existing is not None:
                if existing.result == 'PENDING':
                    existing.probability = prob
                    existing.real_odds = odds
                    existing.pro_tier = tier
                    if existing.first_probability is None:
                        existing.first_probability = prob
                        existing.first_real_odds = odds
                        existing.first_pro_tier = tier
                    updated_count += 1
                continue

            rec = PredictionRecord(
                match_date=mdate, home_team=home, away_team=away,
                market=market, section=section,
                probability=prob, first_probability=prob,
                real_odds=odds, first_real_odds=odds,
                pro_tier=tier, first_pro_tier=tier,
                result='PENDING', shown_date=today,
            )
            new_objs.append(rec)
            existing_map[key] = rec
            new_count += 1

        if new_objs:
            db.session.add_all(new_objs)

    if new_count or updated_count:
        db.session.commit()
    return new_count


# ================================================================
# MSHALE (DELTA): probability ya sasa - probability ya mara ya kwanza.
# Inapakia first_probability MARA MOJA kwa kila ombi (si query kwa kila soko).
# ================================================================
def _first_prob_map():
    cached = getattr(g, '_first_prob_map', None)
    if cached is not None:
        return cached
    today_start = datetime.combine(today_eat(), datetime.min.time())
    rows = db.session.query(
        PredictionRecord.match_date, PredictionRecord.home_team,
        PredictionRecord.away_team, PredictionRecord.market,
        PredictionRecord.first_probability
    ).filter(
        PredictionRecord.section == 'prediction',
        PredictionRecord.match_date >= today_start
    ).all()
    fmap = {(r[0], r[1], r[2], r[3]): r[4] for r in rows}
    g._first_prob_map = fmap
    return fmap


def calc_delta(current_prob, home, away, mdate, market, section='prediction'):
    if current_prob is None:
        return None
    if section == 'prediction':
        try:
            first = _first_prob_map().get((_to_dt(mdate), home, away, market))
        except Exception:
            first = None
    else:
        rec = PredictionRecord.query.filter_by(
            home_team=home, away_team=away, match_date=mdate, market=market, section=section
        ).first()
        first = rec.first_probability if rec else None
    if first is None:
        return None
    return round(float(current_prob) - float(first), 1)


def generate_daily_combos():
    today = today_eat()
    already = ComboRecord.query.filter_by(shown_date=today).first()
    if already:
        return 0

    settings = Settings.get()
    pool = all_matches_market_pool()
    if not pool:
        return 0

    combos = build_daily_combos(
        pool, min_legs=settings.combo_min_legs, max_legs=settings.combo_max_legs,
        min_leg_probability=settings.combo_min_leg_probability,
        min_combined_probability=settings.combo_min_combined_probability,
        formula=settings.combo_leg_formula, max_combos=settings.max_combos_per_day,
    )

    count = 0
    for c in combos:
        rec = ComboRecord(
            shown_date=today, combined_odds=c['combined_odds'],
            combined_probability=c['combined_probability'],
            formula_used=c['formula_used'], result='PENDING'
        )
        db.session.add(rec)
        db.session.flush()
        for leg in c['legs']:
            db.session.add(ComboLeg(
                combo_id=rec.id, home_team=leg['home'], away_team=leg['away'],
                match_date=leg['match_date'], market=leg['market'],
                probability=leg['probability'], real_odds=leg['real_odds'],
                is_stake_leg=leg['is_stake'], result='PENDING'
            ))
        count += 1
    db.session.commit()
    return count


def market_pool_asof(target_date, section='prediction'):
    """Masoko yaliyokuwepo kufikia tarehe hii (thamani za SIKU YA KWANZA,
    fallback: za sasa kwa rekodi za kale), yanayoruhusiwa pekee."""
    day_start = datetime.combine(target_date, datetime.min.time())
    records = PredictionRecord.query.filter(
        PredictionRecord.section == section,
        PredictionRecord.shown_date <= target_date,
        PredictionRecord.match_date >= day_start,
    ).all()

    groups = {}
    for r in records:
        if not combo_market_allowed(r.market):
            continue
        key = (r.home_team, r.away_team, r.match_date)
        groups.setdefault(key, []).append(r)

    pool = []
    for (home, away, mdate), recs in groups.items():
        markets = []
        for r in recs:
            prob = r.first_probability if r.first_probability is not None else r.probability
            odds = r.first_real_odds if r.first_real_odds is not None else r.real_odds
            tier = r.first_pro_tier if r.first_pro_tier is not None else r.pro_tier
            markets.append({'market': r.market, 'probability': prob, 'real_odds': odds,
                            'pro_tier': tier, 'result': r.result})
        pool.append({'home': home, 'away': away, 'match_date': mdate, 'markets': markets})
    return pool


def simulate_combos_for_date(target_date, formula, settings, stats=None):
    pool = market_pool_asof(target_date)
    if not pool:
        if stats is not None:
            stats.update({'eligible_matches': 0, 'generated': 0,
                          'below_combined_threshold': 0, 'duplicates': 0})
        return []
    seed = (f"{target_date.isoformat()}|{formula}|{settings.combo_min_legs}|"
            f"{settings.combo_max_legs}|{settings.combo_min_leg_probability}|"
            f"{settings.combo_min_combined_probability}|{settings.max_combos_per_day}")
    rng = random.Random(seed)
    combos = build_daily_combos(
        pool, min_legs=settings.combo_min_legs, max_legs=settings.combo_max_legs,
        min_leg_probability=settings.combo_min_leg_probability,
        min_combined_probability=settings.combo_min_combined_probability,
        formula=formula, max_combos=settings.max_combos_per_day, rng=rng, stats=stats,
    )
    for c in combos:
        c['result'] = combo_result_from_legs(c['legs'])
    return combos


def explain_combos(stats, settings, formula):
    """Maelezo ya wazi kwa nini combos ni sifuri au chache."""
    n = stats.get('eligible_matches', 0)
    generated = stats.get('generated', 0)
    need = settings.combo_min_legs

    if n < need:
        extra = ''
        if formula in ('ev_odds_only', 'highest_probability_odds_only'):
            extra = (" Formula hii inatumia mechi zenye odds pekee "
                     "(corners/kadi/SOT hazina odds, kwa hiyo hazihesabiwi hapa).")
        return (f"Mechi zinazostahili ni {n} tu (kila leg lazima iwe angalau "
                f"{settings.combo_min_leg_probability}%), lakini kila combo inahitaji legs "
                f"{need} kutoka mechi tofauti.{extra} Punguza 'Minimum probability kwa LEG', "
                f"punguza idadi ya legs, au jaribu formula nyingine.")

    if generated == 0 and stats.get('below_combined_threshold', 0) > 0:
        return (f"Combos {stats['below_combined_threshold']} zilijaribiwa lakini zote zilikataliwa: "
                f"probability ya combo nzima (zidisho la legs) ilikuwa chini ya "
                f"{settings.combo_min_combined_probability}%. Kumbuka: legs 4 za 80% kila moja = "
                f"41% tu. Punguza 'Minimum probability kwa COMBO NZIMA'.")

    if 0 < generated < settings.max_combos_per_day:
        why = []
        if stats.get('below_combined_threshold', 0) > 0:
            why.append(f"{stats['below_combined_threshold']} zilikataliwa na kizingiti cha combo nzima")
        why.append(f"mechi zinazostahili ni {n} tu (michanganyiko ya mechi/masoko imeisha)")
        return (f"Combos {generated} pekee zinawezekana kwa mipangilio hii (kikomo ni "
                f"{settings.max_combos_per_day}): " + "; ".join(why) + ". Hii si hitilafu.")

    return None


TIER_LABELS = {
    'PRO_STRONG': {'label': 'Nguvu Kubwa', 'class': 'tier-strong', 'icon': '🔥'},
    'PRO_MEDIUM': {'label': 'Wastani', 'class': 'tier-medium', 'icon': '⚡'},
}


def tier_info(tier_code):
    return TIER_LABELS.get(tier_code, {'label': tier_code, 'class': 'tier-other', 'icon': '•'})


def calc_edge(probability, real_odds):
    if real_odds is None or not real_odds:
        return None
    implied = 100.0 / real_odds
    return round((probability or 0) - implied, 1)


def calc_ev(probability, real_odds):
    if real_odds is None:
        return None
    return round(((probability or 0) / 100 * real_odds - 1) * 100, 1)


# ================================================================
# HEALTH CHECKS (kwa seemu ya ⚠️ / ✔️ kwenye Admin)
# status: 'ok' = ✔️ | 'warn' = ⚠️ | 'error' = ❌
# ================================================================
def run_health_checks():
    checks = []
    today = today_eat()
    now = now_eat()

    def check(name, fn):
        try:
            status, detail = fn()
        except Exception as e:
            try:
                db.session.rollback()
            except Exception:
                pass
            status, detail = 'error', f'Ukaguzi umeshindwa: {str(e)[:120]}'
        checks.append({'name': name, 'status': status, 'detail': detail})

    def c_db():
        db.session.execute(text('SELECT 1'))
        if not _database_url:
            return 'warn', ('Unatumia SQLite ya muda. History na watumiaji vinaweza kupotea '
                            'kwenye redeploy - weka DATABASE_URL (Neon).')
        return 'ok', 'PostgreSQL imeunganishwa vizuri.'

    def c_api():
        cfg = ApiConfig.get()
        if not cfg.api_key:
            return 'error', 'API key ya AllSportsAPI haijawekwa.'
        days = cfg.days_until_expiry()
        if days is None:
            return 'warn', 'Tarehe ya kuisha kwa API key haijawekwa.'
        if days < 0:
            return 'error', f'API key iliisha muda siku {abs(days)} zilizopita.'
        if days <= 7:
            return 'warn', f'API key inaisha baada ya siku {days}.'
        return 'ok', f'API key ni nzuri (siku {days} zimebaki).'

    def c_snapshot():
        last = DataSnapshot.query.order_by(DataSnapshot.snapshot_date.desc()).first()
        if not last:
            return 'error', 'Hakuna snapshot hata moja - bonyeza Refresh Data.'
        age = (today - last.snapshot_date).days
        if age <= 0:
            return 'ok', f'Data ilisasishwa leo ({last.snapshot_date}).'
        if age == 1 and now.hour < 4:
            return 'ok', 'Data ni ya jana; usasishaji wa usiku (01:00 EAT) unatarajiwa.'
        if age == 1:
            return 'warn', 'Usasishaji wa usiku haujafanyika leo - angalia GitHub Actions (Job B).'
        if age <= 6:
            return 'warn', (f'Data haijasasishwa kwa siku {age}. Ikiwa ni international break ni '
                            f'kawaida; vinginevyo angalia GitHub Actions.')
        return 'error', f'Data haijasasishwa kwa siku {age} - mfumo huenda umekwama.'

    def c_files():
        names = ('predictions.csv', 'value_bets.csv', 'combos.csv')
        paths = {n: os.path.join(DATA_DIR, n) for n in names}
        missing = [n for n, p in paths.items() if not os.path.exists(p)]
        if missing:
            return 'error', 'Faili hazipo: ' + ', '.join(missing)
        empty = [n for n, p in paths.items() if os.path.getsize(p) < 20]
        if empty:
            return 'warn', 'Faili tupu: ' + ', '.join(empty)
        return 'ok', 'Faili zote 3 za data zipo.'

    def c_matches():
        df = get_data()['predictions']
        if len(df) == 0:
            return 'error', 'predictions.csv haina safu yoyote.'
        dates = df['match_date'].dt.date
        latest = dates.max()
        if latest < today:
            return 'error', f'Mechi zote ni za zamani (ya mwisho: {latest}).'
        key = ['HomeTeam', 'AwayTeam', 'match_date']
        today_n = df[dates == today][key].drop_duplicates().shape[0]
        future_n = df[dates > today][key].drop_duplicates().shape[0]
        return 'ok', f'Mechi za leo: {today_n}, mechi zijazo: {future_n}.'

    def c_quality():
        df = get_data()['predictions']
        required = {'HomeTeam', 'AwayTeam', 'match_date', 'market', 'probability_%'}
        miss = required - set(df.columns)
        if miss:
            return 'error', 'Columns hazipo: ' + ', '.join(sorted(miss))
        problems = []
        prob = pd.to_numeric(df['probability_%'], errors='coerce')
        bad_prob = int(((prob < 0) | (prob > 100) | prob.isna()).sum())
        if bad_prob:
            problems.append(f'probability zisizo sahihi: {bad_prob}')
        dups = int(df.duplicated(subset=['HomeTeam', 'AwayTeam', 'match_date', 'market']).sum())
        if dups:
            problems.append(f'safu zilizojirudia: {dups}')
        if 'real_odds' in df.columns:
            odds = pd.to_numeric(df['real_odds'], errors='coerce')
            bad_odds = int((odds.notna() & (odds <= 1.0)).sum())
            if bad_odds:
                problems.append(f'odds zisizo sahihi (<=1.0): {bad_odds}')
        if problems:
            return 'warn', '; '.join(problems)
        return 'ok', f'Safu {len(df)} zote za predictions ni sahihi.'

    def c_combos():
        n = ComboRecord.query.filter_by(shown_date=today).count()
        if n:
            return 'ok', f'Combos {n} za leo zimetengenezwa.'
        return 'warn', ('Combos za leo hazijatengenezwa (refresh haijaendeshwa, au mechi '
                        'hazitoshi kwa mipangilio ya sasa).')

    def c_pending():
        cutoff = now - timedelta(days=3)
        stuck = PredictionRecord.query.filter(
            PredictionRecord.result == 'PENDING',
            PredictionRecord.match_date < cutoff).count()
        stuck_combos = ComboRecord.query.filter(
            ComboRecord.result == 'PENDING',
            ComboRecord.shown_date < today - timedelta(days=3)).count()
        if not stuck and not stuck_combos:
            return 'ok', 'Hakuna rekodi za zamani zilizokwama kwenye PENDING.'
        status = 'error' if (stuck > 100 or stuck_combos > 20) else 'warn'
        return status, (f'Predictions {stuck} na combos {stuck_combos} za zaidi ya siku 3 bado '
                        f'PENDING - angalia Job A (settlement).')

    def c_settings():
        s = Settings.get()
        if s.combo_min_legs > s.combo_max_legs:
            return 'error', 'Min legs ya combo ni kubwa kuliko Max legs - combos hazitatengenezwa.'
        problems = []
        if not (0 <= s.probability_threshold <= 100):
            problems.append('probability threshold iko nje ya 0-100')
        if s.max_combos_per_day < 1:
            problems.append('max combos kwa siku ni chini ya 1')
        if problems:
            return 'warn', '; '.join(problems)
        return 'ok', 'Mipangilio ya picks na combos ni sahihi.'

    def c_admin():
        if not ADMIN_EMAIL or not ADMIN_PASSWORD:
            return 'error', 'ADMIN_EMAIL / ADMIN_PASSWORD hazijawekwa kwenye environment.'
        if len(ADMIN_PASSWORD) < 10 or ADMIN_PASSWORD.lower() in WEAK_ADMIN_PASSWORDS:
            return 'warn', 'Password ya admin ni dhaifu - tumia angalau herufi 10 zisizo rahisi kukisia.'
        if AppUser.query.filter_by(is_admin=True).count() == 0:
            return 'error', 'Hakuna akaunti ya admin kwenye database.'
        return 'ok', 'Akaunti ya admin ipo na password ni imara.'

    def c_secret():
        if SECRET_KEY_MISSING:
            return 'error', ('SECRET_KEY haijawekwa - kila app ikiwaka upya watumiaji wote '
                             'wanatolewa. Iweke kwenye Render.')
        if len(os.environ.get('SECRET_KEY', '')) < 24:
            return 'warn', 'SECRET_KEY ni fupi - tumia angalau herufi 32.'
        return 'ok', 'SECRET_KEY imewekwa vizuri.'

    def c_refresh_secret():
        if not REFRESH_SECRET:
            return 'warn', 'REFRESH_SECRET haijawekwa - /api/refresh (usasishaji wa kiotomatiki) imezimwa.'
        if len(REFRESH_SECRET) < 16:
            return 'warn', 'REFRESH_SECRET ni fupi - tumia angalau herufi 16.'
        return 'ok', 'REFRESH_SECRET imewekwa.'

    def c_cookie():
        if not COOKIE_SECURE:
            return 'warn', 'Cookie za session hazijawekwa "Secure" (COOKIE_SECURE=0).'
        return 'ok', 'Cookie za session ni salama (HTTPS tu).'

    check('Database', c_db)
    check('AllSportsAPI key', c_api)
    check('Usasishaji wa data', c_snapshot)
    check('Faili za data', c_files)
    check('Mechi za leo na zijazo', c_matches)
    check('Ubora wa data', c_quality)
    check('Combos za leo', c_combos)
    check('Matokeo yaliyokwama (PENDING)', c_pending)
    check('Mipangilio', c_settings)
    check('Akaunti ya admin', c_admin)
    check('SECRET_KEY', c_secret)
    check('REFRESH_SECRET', c_refresh_secret)
    check('Cookie za session', c_cookie)

    errors = sum(1 for c in checks if c['status'] == 'error')
    warns = sum(1 for c in checks if c['status'] == 'warn')
    if errors:
        level = 'error'
        msg = f'Matatizo {errors} yanahitaji hatua' + (f', maonyo {warns}' if warns else '')
    elif warns:
        level = 'warn'
        msg = f'Maonyo {warns} ya kuangalia'
    else:
        level = 'ok'
        msg = 'Kila kitu kiko sawa'
    summary = {'level': level, 'text': msg, 'errors': errors, 'warnings': warns,
               'ok': len(checks) - errors - warns}
    return checks, summary


# ================================================================
# AKAUNTI: login / register / logout
# ================================================================
@app.route('/register', methods=['GET', 'POST'])
def register():
    next_url = _safe_next(request.values.get('next'))
    if get_current_user():
        return redirect(next_url or url_for('dashboard'))

    email = ''
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''
        confirm = request.form.get('confirm') or ''
        agree = request.form.get('agree')
        ip = _client_ip()

        error = None
        if _rate_limited('register', ip, 10, 3600):
            error = 'Majaribio mengi sana. Jaribu tena baada ya saa moja.'
        else:
            _rate_hit('register', ip)
            if not EMAIL_RE.match(email) or len(email) > 254:
                error = 'Email si sahihi.'
            elif len(password) < 8:
                error = 'Password lazima iwe na angalau herufi 8.'
            elif len(password) > 128:
                error = 'Password ni ndefu mno.'
            elif password != confirm:
                error = 'Password mbili hazifanani.'
            elif not agree:
                error = 'Lazima ukubali Sera ya Faragha na uthibitishe umri wa miaka 18+.'
            elif email == ADMIN_EMAIL or AppUser.query.filter_by(email=email).first():
                error = 'Email hii tayari imesajiliwa.'

        if not error:
            user = AppUser(email=email, is_admin=False, last_seen=datetime.utcnow())
            user.set_password(password)
            db.session.add(user)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                error = 'Email hii tayari imesajiliwa.'
            else:
                _login_session(user)
                flash('Karibu! Akaunti yako imeundwa.', 'success')
                return redirect(next_url or url_for('dashboard'))

        flash(error, 'error')

    return render_template('auth.html', mode='register', email=email, next=next_url, page='register')


@app.route('/login', methods=['GET', 'POST'])
def login():
    next_url = _safe_next(request.values.get('next'))
    if get_current_user():
        return redirect(next_url or url_for('dashboard'))

    email = ''
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''
        key = f'{_client_ip()}|{email}'

        if _rate_limited('login', key, 5, 600):
            flash('Majaribio mengi sana yaliyoshindwa. Subiri dakika 10 ujaribu tena.', 'error')
        else:
            user = AppUser.query.filter_by(email=email).first() if email else None
            if user and user.check_password(password):
                _rate_clear('login', key)
                _login_session(user)
                _touch_last_seen(user)
                return redirect(next_url or url_for('dashboard'))
            _rate_hit('login', key)
            flash('Email au password si sahihi.', 'error')

    return render_template('auth.html', mode='login', email=email, next=next_url, page='login')


@app.route('/logout')
def logout():
    session.clear()
    flash('Umetoka kwenye akaunti.', 'success')
    return redirect(url_for('dashboard'))


@app.route('/privacy')
def privacy():
    return render_template('info.html', page_key='privacy', page='privacy')


@app.route('/about')
def about():
    return render_template('info.html', page_key='about', page='about')


# ================================================================
# KURASA ZA PREDICTIONS
# ================================================================
@app.route('/')
def dashboard():
    settings = Settings.get()
    match_survivors = today_predictions_survivors(settings)

    matches = []
    for m in match_survivors:
        survivors = m['survivors']
        best = survivors[0]
        others = survivors[1:1 + settings.max_markets_per_match]

        best_pick = {
            'market': best['market'], 'probability': best['probability_%'],
            'tier': tier_info(best.get('pro_tier')),
            'has_odds': pd.notna(best.get('real_odds')), 'odds': best.get('real_odds'),
            'ev': calc_ev(best['probability_%'], best.get('real_odds')) if pd.notna(best.get('real_odds')) else None,
            'delta': calc_delta(best['probability_%'], m['home'], m['away'], m['match_date'], best['market']),
        }
        other_picks = [{
            'market': r['market'], 'probability': r['probability_%'],
            'tier': tier_info(r.get('pro_tier')),
            'has_odds': pd.notna(r.get('real_odds')), 'odds': r.get('real_odds'),
            'delta': calc_delta(r['probability_%'], m['home'], m['away'], m['match_date'], r['market']),
        } for r in others]

        matches.append({
            'home': m['home'], 'away': m['away'],
            'date': m['match_date'].strftime('%d %b %Y'),
            'time_eat': m['match_date'].strftime('%H:%M') if m['match_date'].hour or m['match_date'].minute else None,
            'best_pick': best_pick, 'other_picks': other_picks,
        })

    matches.sort(key=lambda m: m.get('time_eat') or '')
    empty_message = 'NO PICK TODAY' if not matches else None

    return render_template('dashboard.html', matches=matches,
                           empty_message=empty_message, page='dashboard')


@app.route('/upcoming')
def upcoming():
    settings = Settings.get()
    df = get_data()['predictions'].copy()
    today = today_eat()

    future_dates = sorted(df.loc[df['match_date'].dt.date > today, 'match_date']
                          .dt.strftime('%Y-%m-%d').unique().tolist())
    selected_date = request.args.get('date')
    if not selected_date or selected_date not in future_dates:
        selected_date = future_dates[0] if future_dates else None

    matches = []
    if selected_date:
        day_df = df[df['match_date'].dt.strftime('%Y-%m-%d') == selected_date]
        for (home, away, mdate), grp in day_df.groupby(['HomeTeam', 'AwayTeam', 'match_date']):
            entries = [(row['market'], row['probability_%'],
                        row['real_odds'] if pd.notna(row.get('real_odds')) else None,
                        row.get('pro_tier'), row)
                       for _, row in grp.iterrows()]
            survivors = select_markets(entries, settings.probability_threshold, settings.best_pick_formula)
            if not survivors:
                continue
            best = survivors[0]
            others = survivors[1:1 + settings.max_markets_per_match]

            def with_delta(r):
                return {
                    'market': r['market'], 'probability': r['probability_%'],
                    'tier': tier_info(r.get('pro_tier')),
                    'has_odds': pd.notna(r.get('real_odds')), 'odds': r.get('real_odds'),
                    'delta': calc_delta(r['probability_%'], home, away, mdate, r['market']),
                }

            matches.append({
                'home': home, 'away': away,
                'date': mdate.strftime('%d %b %Y'),
                'time_eat': mdate.strftime('%H:%M') if mdate.hour or mdate.minute else None,
                'best_pick': with_delta(best),
                'other_picks': [with_delta(r) for r in others],
            })

    matches.sort(key=lambda m: m.get('time_eat') or '')
    empty_message = 'HAKUNA PREDICTIONS ZA ZIJAZO KWA SASA' if not matches else None

    return render_template('upcoming.html', matches=matches, future_dates=future_dates,
                           selected_date=selected_date, empty_message=empty_message, page='upcoming')


@app.route('/value-bets')
def value_bets():
    df = get_data()['value_bets'].copy()
    today_str = today_eat().strftime('%Y-%m-%d')
    df = df[df['match_date'].dt.strftime('%Y-%m-%d') == today_str]
    df = df.sort_values('ev_%', ascending=False)

    bets = []
    for _, row in df.iterrows():
        bets.append({
            'home': row['HomeTeam'], 'away': row['AwayTeam'],
            'date': pd.to_datetime(row['match_date']).strftime('%d %b %Y'),
            'market': row['market'], 'probability': row['probability_%'],
            'odds': row['real_odds'], 'ev': row['ev_%'],
            'edge': calc_edge(row['probability_%'], row['real_odds']),
            'tier': tier_info(row.get('pro_tier')),
        })

    empty_message = 'NO PICK TODAY' if not bets else None
    return render_template('value_bets.html', bets=bets, empty_message=empty_message, page='value_bets')


@app.route('/combos')
def combos():
    settings = Settings.get()
    today = today_eat()
    records = ComboRecord.query.filter_by(shown_date=today).all()

    combo_list = []
    for rec in records:
        combo_list.append({
            'combined_probability': rec.combined_probability, 'result': rec.result,
            'legs': [{'home': l.home_team, 'away': l.away_team, 'market': l.market,
                     'probability': l.probability, 'odds': l.real_odds, 'has_odds': l.real_odds is not None}
                    for l in rec.legs],
        })

    combo_note = None
    if not combo_list:
        stats = {}
        dry = build_daily_combos(
            all_matches_market_pool(), min_legs=settings.combo_min_legs,
            max_legs=settings.combo_max_legs, min_leg_probability=settings.combo_min_leg_probability,
            min_combined_probability=settings.combo_min_combined_probability,
            formula=settings.combo_leg_formula, max_combos=settings.max_combos_per_day, stats=stats,
        )
        if dry:
            combo_note = ("Combos za leo bado hazijatengenezwa (hutengenezwa data inaposasishwa). "
                          "Bonyeza 'Refresh Data' kwenye Admin.")
        else:
            combo_note = explain_combos(stats, settings, settings.combo_leg_formula)

    empty_message = 'NO PICK TODAY' if not combo_list else None
    return render_template('combos.html', combos=combo_list, empty_message=empty_message,
                           combo_note=combo_note, page='combos')


TIER_TO_SECTION = {'predictions': 'prediction', 'value_bets': 'value_bet'}


@app.route('/history')
def history():
    tier = request.args.get('tier', 'predictions')
    selected_date = request.args.get('date')
    settings = Settings.get()

    matches = []
    value_bets_list = []
    combos_list = []
    summary = None
    combo_note = None

    if tier == 'combos':
        available_dates = sorted(
            {d[0].strftime('%Y-%m-%d') for d in
             db.session.query(PredictionRecord.shown_date)
             .filter(PredictionRecord.section == 'prediction').distinct().all()},
            reverse=True
        )

        if selected_date:
            day = datetime.strptime(selected_date, '%Y-%m-%d').date()
            formula = settings.combo_leg_formula

            if formula == 'random_threshold':
                records = ComboRecord.query.filter_by(shown_date=day).all()
                for rec in records:
                    combos_list.append({
                        'combined_probability': rec.combined_probability, 'result': rec.result,
                        'legs': [{'home': l.home_team, 'away': l.away_team, 'market': l.market,
                                 'probability': l.probability, 'odds': l.real_odds,
                                 'has_odds': l.real_odds is not None, 'result': l.result}
                                for l in rec.legs],
                    })
                if not combos_list:
                    combo_note = ("Hakuna combos zilizohifadhiwa kwa tarehe hii. 'random_threshold' "
                                  "inaonyesha combos HALISI zilizotengenezwa siku hiyo (haisimulishwi). "
                                  "Kwa siku za kabla ya mfumo huu mpya, chagua formula nyingine "
                                  "kwenye Admin kuona simulation.")
            else:
                stats = {}
                simulated = simulate_combos_for_date(day, formula, settings, stats)
                for c in simulated:
                    combos_list.append({
                        'combined_probability': c['combined_probability'], 'result': c['result'],
                        'legs': [{'home': l['home'], 'away': l['away'], 'market': l['market'],
                                 'probability': l['probability'], 'odds': l['real_odds'],
                                 'has_odds': l['real_odds'] is not None, 'result': l.get('result')}
                                for l in c['legs']],
                    })
                combo_note = explain_combos(stats, settings, formula)

            won = sum(1 for c in combos_list if c['result'] == 'WON')
            lost = sum(1 for c in combos_list if c['result'] == 'LOST')
            void = sum(1 for c in combos_list if c['result'] == 'VOID')
            pending = sum(1 for c in combos_list if c['result'] == 'PENDING')
            summary = {'won': won, 'lost': lost, 'void': void, 'pending': pending,
                       'win_rate': round(won / (won + lost) * 100, 1) if (won + lost) > 0 else None}

        return render_template('history.html', tier=tier, available_dates=available_dates,
                               selected_date=selected_date, matches=matches,
                               value_bets_list=value_bets_list, combos_list=combos_list,
                               summary=summary, combos_message=None, combo_note=combo_note,
                               combo_formula=settings.combo_leg_formula, page='history')

    section = TIER_TO_SECTION.get(tier, 'prediction')
    available_dates = sorted(
        {d[0].strftime('%Y-%m-%d') for d in
         db.session.query(PredictionRecord.shown_date)
         .filter(PredictionRecord.section == section).distinct().all()},
        reverse=True
    )

    if selected_date:
        day = datetime.strptime(selected_date, '%Y-%m-%d').date()
        records = PredictionRecord.query.filter_by(section=section, shown_date=day).all()

        if tier == 'predictions':
            groups = {}
            for rec in records:
                key = (rec.home_team, rec.away_team, rec.match_date)
                groups.setdefault(key, []).append(rec)

            for (home, away, mdate), recs in groups.items():
                entries = [(r.market, r.probability, r.real_odds, r.pro_tier, r) for r in recs]
                survivors = select_markets(entries, settings.probability_threshold, settings.best_pick_formula)
                if not survivors:
                    continue
                best = survivors[0]
                others = survivors[1:1 + settings.history_max_markets]
                matches.append({
                    'home': home, 'away': away,
                    'date': mdate.strftime('%d %b %Y'), 'date_iso': mdate.strftime('%Y-%m-%d'),
                    'best_pick': {'market': best.market, 'probability': best.probability,
                                  'odds': best.real_odds, 'result': best.result},
                    'other_picks': [{'market': r.market, 'probability': r.probability,
                                      'odds': r.real_odds, 'result': r.result} for r in others],
                })
            matches.sort(key=lambda m: m['date_iso'])

            won = sum(1 for m in matches if m['best_pick']['result'] == 'WON')
            lost = sum(1 for m in matches if m['best_pick']['result'] == 'LOST')
            void = sum(1 for m in matches if m['best_pick']['result'] == 'VOID')
            summary = {'won': won, 'lost': lost, 'void': void,
                       'win_rate': round(won / (won + lost) * 100, 1) if (won + lost) > 0 else None}

        elif tier == 'value_bets':
            for r in sorted(records, key=lambda r: -(r.probability or 0)):
                value_bets_list.append({
                    'home': r.home_team, 'away': r.away_team,
                    'date': r.match_date.strftime('%d %b %Y'),
                    'market': r.market, 'probability': r.probability,
                    'odds': r.real_odds, 'ev': calc_ev(r.probability, r.real_odds),
                    'edge': calc_edge(r.probability, r.real_odds), 'result': r.result,
                })
            won = sum(1 for r in value_bets_list if r['result'] == 'WON')
            lost = sum(1 for r in value_bets_list if r['result'] == 'LOST')
            void = sum(1 for r in value_bets_list if r['result'] == 'VOID')
            summary = {'won': won, 'lost': lost, 'void': void,
                       'win_rate': round(won / (won + lost) * 100, 1) if (won + lost) > 0 else None}

    return render_template('history.html', tier=tier, available_dates=available_dates,
                           selected_date=selected_date, matches=matches,
                           value_bets_list=value_bets_list, combos_list=combos_list,
                           summary=summary, combos_message=None, combo_note=combo_note,
                           combo_formula=settings.combo_leg_formula, page='history')


# ================================================================
# ADMIN (inahitaji akaunti ya admin; wengine wanaona 404)
# ================================================================
@app.route('/admin', methods=['GET', 'POST'])
@admin_only
def admin():
    settings = Settings.get()
    api_config = ApiConfig.get()

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'update_settings':
            settings.max_markets_per_match = int(request.form.get('max_markets', 5))
            settings.history_max_markets = int(request.form.get('history_max_markets', 10))
            settings.probability_threshold = float(request.form.get('probability_threshold', 50.0))
            settings.best_pick_formula = request.form.get('best_pick_formula', 'hybrid')
            settings.combo_min_legs = int(request.form.get('combo_min_legs', 4))
            settings.combo_max_legs = int(request.form.get('combo_max_legs', 4))
            settings.combo_min_leg_probability = float(request.form.get('combo_min_leg_probability', 65.0))
            settings.combo_min_combined_probability = float(request.form.get('combo_min_combined_probability', 0.0))
            settings.max_combos_per_day = int(request.form.get('max_combos_per_day', 50))
            formula = request.form.get('combo_leg_formula', 'random_threshold')
            settings.combo_leg_formula = formula if formula in FORMULA_CHOICES else 'random_threshold'
            db.session.commit()
            flash('Mipangilio imesasishwa.', 'success')
        elif action == 'update_api':
            api_config.api_key = request.form.get('api_key') or api_config.api_key
            expiry_str = request.form.get('expiry_date')
            if expiry_str:
                api_config.expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
            db.session.commit()
            flash('AllSportsAPI key imesasishwa.', 'success')
        return redirect(url_for('admin'))

    days_left = api_config.days_until_expiry()

    last_snapshot = DataSnapshot.query.order_by(DataSnapshot.snapshot_date.desc()).first()
    hours_since_snapshot = None
    if last_snapshot:
        delta = now_eat() - datetime.combine(last_snapshot.snapshot_date, datetime.min.time())
        hours_since_snapshot = round(delta.total_seconds() / 3600, 1)

    health, health_summary = run_health_checks()
    users_total = AppUser.query.count()

    return render_template('admin.html', settings=settings, api_config=api_config,
                           days_left=days_left, hours_since_snapshot=hours_since_snapshot,
                           formula_choices=FORMULA_CHOICES, health=health,
                           health_summary=health_summary, users_total=users_total,
                           page='admin')


@app.route('/admin/users')
@admin_only
def admin_users():
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)
    users = AppUser.query.order_by(AppUser.created_at.desc()).limit(1000).all()
    stats = {
        'total': AppUser.query.count(),
        'regular': AppUser.query.filter_by(is_admin=False).count(),
        'new_7d': AppUser.query.filter(AppUser.created_at >= week_ago).count(),
        'active_7d': AppUser.query.filter(AppUser.last_seen >= week_ago).count(),
    }
    return render_template('admin_users.html', users=users, stats=stats, page='admin')


@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@admin_only
def admin_delete_user(user_id):
    target = db.session.get(AppUser, user_id)
    me = get_current_user()
    if not target:
        flash('Mtumiaji hapatikani.', 'error')
    elif target.is_admin or target.id == me.id:
        flash('Huwezi kuondoa akaunti ya admin.', 'error')
    else:
        email = target.email
        db.session.delete(target)
        db.session.commit()
        flash(f'Mtumiaji {email} ameondolewa.', 'success')
    return redirect(url_for('admin_users'))


# Endpoint za zamani zinabaki ili templates zisivunjike
@app.route('/admin/login')
def admin_login():
    return redirect(url_for('login', next=url_for('admin')))


@app.route('/admin/logout')
def admin_logout():
    return redirect(url_for('logout'))


@app.route('/admin/refresh', methods=['POST'])
@admin_only
def admin_refresh():
    try:
        load_live_data()
        snapshot_today()
        flash('Data imesasishwa na kuhifadhiwa kwenye history.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Refresh imeshindwa: {type(e).__name__}: {str(e)[:300]}', 'error')
    return redirect(url_for('admin'))


def snapshot_today():
    today = today_eat()
    hist_folder = os.path.join(HISTORY_DIR, today.strftime('%Y-%m-%d'))
    os.makedirs(hist_folder, exist_ok=True)
    for fname in ['predictions.csv', 'value_bets.csv', 'combos.csv']:
        path = os.path.join(DATA_DIR, fname)
        if os.path.exists(path):
            shutil.copy(path, os.path.join(hist_folder, fname))

    existing = DataSnapshot.query.filter_by(snapshot_date=today).first()
    if not existing:
        snap = DataSnapshot(
            snapshot_date=today,
            predictions_path=os.path.join(hist_folder, 'predictions.csv'),
            value_bets_path=os.path.join(hist_folder, 'value_bets.csv'),
            combos_path=os.path.join(hist_folder, 'combos.csv')
        )
        db.session.add(snap)
        db.session.commit()
    else:
        existing.snapshot_date = today

    record_predictions_pending()
    generate_daily_combos()


@app.route('/api/refresh', methods=['POST'])
def api_refresh():
    if not REFRESH_SECRET or request.headers.get('X-Refresh-Secret') != REFRESH_SECRET:
        return jsonify({'error': 'unauthorized'}), 401
    try:
        load_live_data()
        snapshot_today()
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
    return jsonify({
        'status': 'ok',
        'matches': int(_cache['predictions'][['HomeTeam', 'AwayTeam', 'match_date']].drop_duplicates().shape[0]),
        'refreshed_at': now_eat().isoformat() + '+03:00'
    }), 200


@app.route('/api/config/allsportsapi-key', methods=['GET'])
def api_config_key():
    if not REFRESH_SECRET or request.headers.get('X-Refresh-Secret') != REFRESH_SECRET:
        return jsonify({'error': 'unauthorized'}), 401
    cfg = ApiConfig.get()
    return jsonify({'api_key': cfg.api_key}), 200


with app.app_context():
    db.create_all()
    try:
        ensure_admin_user()
    except Exception as e:
        db.session.rollback()
        print(f'[WARN] ensure_admin_user imeshindwa: {e}')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
