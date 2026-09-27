"""
Braiton Picks — Flask app
Dashboard ya predictions za kila siku + Value Bets + Combos + History + Zijazo + Admin.
"""
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import pandas as pd
import os
import shutil
import random
from datetime import datetime, date, timedelta

from models import db, Settings, ApiConfig, DataSnapshot, PredictionRecord, ComboRecord, ComboLeg
from market_selection import is_market_allowed, select_markets
from combo_builder import build_daily_combos, combo_result_from_legs

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, 'data')
HISTORY_DIR = os.path.join(DATA_DIR, 'history')

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'badilisha-hii-kwenye-production')

# ================================================================
# DATABASE — Postgres (Neon) ikiwa DATABASE_URL ipo, vinginevyo SQLite
# ================================================================
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

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'badilisha-hii')
REFRESH_SECRET = os.environ.get('REFRESH_SECRET')

FORMULA_CHOICES = ['random_threshold', 'ev_odds_only', 'highest_probability', 'highest_probability_odds_only']

# ================================================================
# DATA LOADING (cache kwenye kumbukumbu, 'refresh' kuisasisha)
# ================================================================
_cache = {}


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
    """Kwa kila mechi ya LEO, rudisha {'home','away','match_date','survivors':[...]}
    kwa kutumia select_markets() - hii ndiyo picha ile ile Dashboard inayoonyesha."""
    data = get_data()
    df = data['predictions'].copy()
    today_str = date.today().strftime('%Y-%m-%d')
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


def all_matches_market_pool():
    """Mechi ZOTE za LEO + ZIJAZO kutoka predictions.csv, kila mechi ikiwa
    na ORODHA KAMILI ya masoko yake (BILA kuchujwa na select_markets() -
    Combos mpya zinahitaji masoko yote, si 'best pick' pekee)."""
    df = get_data()['predictions'].copy()
    today = date.today()
    df = df[df['match_date'].dt.date >= today]

    pool = []
    for (home, away, mdate), grp in df.groupby(['HomeTeam', 'AwayTeam', 'match_date']):
        markets = [{
            'market': row['market'], 'probability': row['probability_%'],
            'real_odds': row['real_odds'] if pd.notna(row.get('real_odds')) else None,
            'pro_tier': row.get('pro_tier'),
        } for _, row in grp.iterrows()]
        pool.append({'home': home, 'away': away, 'match_date': mdate, 'markets': markets})
    return pool


def record_predictions_pending():
    """Andika PredictionRecord mpya (PENDING) kwa kila mstari mpya, NA
    SASISHA probability/pro_tier/real_odds kwa rekodi ZILIZOPO ambazo bado
    ni PENDING (mechi haijachezwa) - ili 'Zijazo' ziendelee kuboreshwa kila
    siku, mpaka mechi ifike. first_probability/first_real_odds/first_pro_tier
    zinawekwa MARA MOJA TU (siku ya kwanza) na haziguswi tena - msingi wa
    delta indicator NA wa History-simulation ya combos. Rekodi zilizokwisha-
    settle (WON/LOST/VOID) HAZIGUSWI KAMWE."""
    section_map = {'predictions': 'prediction', 'value_bets': 'value_bet'}
    new_count = 0
    updated_count = 0
    for cache_key, section in section_map.items():
        df = _cache.get(cache_key)
        if df is None or len(df) == 0:
            continue
        for _, row in df.iterrows():
            prob = row.get('probability_%')
            odds = row.get('real_odds') if pd.notna(row.get('real_odds')) else None
            tier = row.get('pro_tier')

            existing = PredictionRecord.query.filter_by(
                match_date=row['match_date'], home_team=row['HomeTeam'],
                away_team=row['AwayTeam'], market=row['market'], section=section
            ).first()

            if existing:
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
                match_date=row['match_date'], home_team=row['HomeTeam'],
                away_team=row['AwayTeam'], market=row['market'], section=section,
                probability=prob, first_probability=prob,
                real_odds=odds, first_real_odds=odds,
                pro_tier=tier, first_pro_tier=tier,
                result='PENDING', shown_date=date.today(),
            )
            db.session.add(rec)
            new_count += 1

    if new_count or updated_count:
        db.session.commit()
    return new_count


def calc_delta(current_prob, home, away, mdate, market, section='prediction'):
    rec = PredictionRecord.query.filter_by(
        home_team=home, away_team=away, match_date=mdate, market=market, section=section
    ).first()
    if not rec or rec.first_probability is None or current_prob is None:
        return None
    return round(current_prob - rec.first_probability, 1)


# ================================================================
# COMBOS — jenzi la LIVE (siku ya leo, mechi za Leo+Zijazo, formula ya sasa)
# ================================================================
def generate_daily_combos():
    """Jenga na uhifadhi Combos za LEO (mara moja tu kwa siku)."""
    today = date.today()
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


# ================================================================
# COMBOS — History-SIMULATION (formula za deterministic pekee), kwa
# tarehe yoyote ya nyuma, ikitumia first_probability/first_real_odds
# (fallback: probability/real_odds za sasa, kwa rekodi za kale kabla
# ya uwanja huu kuwepo). 'random_threshold' HAIJASIMULISHWA - History
# yake inasoma ComboRecord ZILIZOHIFADHIWA (rejea history_combos_stored()).
# ================================================================
def market_pool_asof(target_date, section='prediction'):
    day_start = datetime.combine(target_date, datetime.min.time())
    records = PredictionRecord.query.filter(
        PredictionRecord.section == section,
        PredictionRecord.shown_date <= target_date,
        PredictionRecord.match_date >= day_start,
    ).all()

    groups = {}
    for r in records:
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


def simulate_combos_for_date(target_date, formula, settings):
    """Combos zinazoWEZA KUJIRUDIA (thabiti) kwa tarehe hii + formula hii -
    RNG imepandikizwa (seeded) na tarehe+formula+mipangilio, kwa hiyo
    matokeo hayabadiliki unapoangalia tena."""
    pool = market_pool_asof(target_date)
    if not pool:
        return []
    seed = f"{target_date.isoformat()}|{formula}|{settings.combo_min_legs}|{settings.combo_max_legs}|{settings.combo_min_leg_probability}|{settings.max_combos_per_day}"
    rng = random.Random(seed)
    combos = build_daily_combos(
        pool, min_legs=settings.combo_min_legs, max_legs=settings.combo_max_legs,
        min_leg_probability=settings.combo_min_leg_probability,
        formula=formula, max_combos=settings.max_combos_per_day, rng=rng,
    )
    for c in combos:
        c['result'] = combo_result_from_legs(c['legs'])
    return combos


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
# DASHBOARD — LEO PEKEE.
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


# ================================================================
# ZIJAZO
# ================================================================
@app.route('/upcoming')
def upcoming():
    settings = Settings.get()
    df = get_data()['predictions'].copy()
    today = date.today()

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


# ================================================================
# VALUE BETS — LEO PEKEE.
# ================================================================
@app.route('/value-bets')
def value_bets():
    df = get_data()['value_bets'].copy()
    today_str = date.today().strftime('%Y-%m-%d')
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


# ================================================================
# COMBOS — LEO PEKEE (live), kutoka ComboRecord.
# ================================================================
@app.route('/combos')
def combos():
    today = date.today()
    records = ComboRecord.query.filter_by(shown_date=today).all()

    combo_list = []
    for rec in records:
        combo_list.append({
            'combined_probability': rec.combined_probability, 'result': rec.result,
            'legs': [{'home': l.home_team, 'away': l.away_team, 'market': l.market,
                     'probability': l.probability, 'odds': l.real_odds, 'has_odds': l.real_odds is not None}
                    for l in rec.legs],
        })

    empty_message = 'NO PICK TODAY' if not combo_list else None
    return render_template('combos.html', combos=combo_list, empty_message=empty_message, page='combos')


# ================================================================
# HISTORY
# ================================================================
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

    if tier == 'combos':
        # Tarehe zinazopatikana = tarehe zote zenye PredictionRecord za mechi
        # (msingi wa simulation) - zinafanya kazi kwa formula ZOTE nne.
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
                # HAKUNA simulation - onyesha rekodi ZILIZOHIFADHIWA za siku hiyo halisi.
                records = ComboRecord.query.filter_by(shown_date=day).all()
                for rec in records:
                    combos_list.append({
                        'combined_probability': rec.combined_probability, 'result': rec.result,
                        'legs': [{'home': l.home_team, 'away': l.away_team, 'market': l.market,
                                 'probability': l.probability, 'odds': l.real_odds,
                                 'has_odds': l.real_odds is not None, 'result': l.result}
                                for l in rec.legs],
                    })
            else:
                # Simulation - thabiti (seeded), kwa formula ya SASA.
                simulated = simulate_combos_for_date(day, formula, settings)
                for c in simulated:
                    combos_list.append({
                        'combined_probability': c['combined_probability'], 'result': c['result'],
                        'legs': [{'home': l['home'], 'away': l['away'], 'market': l['market'],
                                 'probability': l['probability'], 'odds': l['real_odds'],
                                 'has_odds': l['real_odds'] is not None, 'result': l.get('result')}
                                for l in c['legs']],
                    })

            won = sum(1 for c in combos_list if c['result'] == 'WON')
            lost = sum(1 for c in combos_list if c['result'] == 'LOST')
            void = sum(1 for c in combos_list if c['result'] == 'VOID')
            summary = {'won': won, 'lost': lost, 'void': void,
                       'win_rate': round(won / (won + lost) * 100, 1) if (won + lost) > 0 else None}

        return render_template('history.html', tier=tier, available_dates=available_dates,
                               selected_date=selected_date, matches=matches,
                               value_bets_list=value_bets_list, combos_list=combos_list,
                               summary=summary, combos_message=None,
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
                           summary=summary, combos_message=None,
                           combo_formula=settings.combo_leg_formula, page='history')


# ================================================================
# ADMIN
# ================================================================
def admin_required():
    return request.cookies.get('is_admin') == 'yes'


@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if not admin_required():
        return redirect(url_for('admin_login'))

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

    # Onyo la 'data staleness' - snapshot ya mwisho ilikuwa lini?
    last_snapshot = DataSnapshot.query.order_by(DataSnapshot.snapshot_date.desc()).first()
    hours_since_snapshot = None
    if last_snapshot:
        delta = datetime.utcnow() - datetime.combine(last_snapshot.snapshot_date, datetime.min.time())
        hours_since_snapshot = round(delta.total_seconds() / 3600, 1)

    return render_template('admin.html', settings=settings, api_config=api_config,
                           days_left=days_left, hours_since_snapshot=hours_since_snapshot,
                           formula_choices=FORMULA_CHOICES, page='admin')


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            resp = redirect(url_for('admin'))
            resp.set_cookie('is_admin', 'yes', max_age=60 * 60 * 24 * 7)
            return resp
        flash('Password si sahihi.', 'error')
    return render_template('admin_login.html')


@app.route('/admin/logout')
def admin_logout():
    resp = redirect(url_for('dashboard'))
    resp.delete_cookie('is_admin')
    return resp


@app.route('/admin/refresh', methods=['POST'])
def admin_refresh():
    if not admin_required():
        return redirect(url_for('admin_login'))
    load_live_data()
    snapshot_today()
    flash('Data imesasishwa na kuhifadhiwa kwenye history.', 'success')
    return redirect(url_for('admin'))


def snapshot_today():
    """Nakili CSV za sasa kwenye data/history/YYYY-MM-DD/, andika DataSnapshot,
    PredictionRecord mpya/kusasishwa (PENDING), na Combos za leo."""
    today = date.today()
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
        existing.snapshot_date = today  # gusa 'updated' kwa staleness check

    record_predictions_pending()
    generate_daily_combos()


# ================================================================
# API — inatumiwa na GitHub Actions/hourly_catchup.py. Uthibitisho ni
# header ya siri (X-Refresh-Secret), SI cookie ya admin.
# ================================================================
@app.route('/api/refresh', methods=['POST'])
def api_refresh():
    if not REFRESH_SECRET or request.headers.get('X-Refresh-Secret') != REFRESH_SECRET:
        return jsonify({'error': 'unauthorized'}), 401
    try:
        load_live_data()
        snapshot_today()
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({
        'status': 'ok',
        'matches': int(_cache['predictions'][['HomeTeam', 'AwayTeam', 'match_date']].drop_duplicates().shape[0]),
        'refreshed_at': datetime.utcnow().isoformat()
    }), 200


@app.route('/api/config/allsportsapi-key', methods=['GET'])
def api_config_key():
    """Job A (hourly_catchup.py) inaomba key kutoka hapa badala ya
    GitHub Secret moja kwa moja - Admin ndiyo chanzo cha ukweli."""
    if not REFRESH_SECRET or request.headers.get('X-Refresh-Secret') != REFRESH_SECRET:
        return jsonify({'error': 'unauthorized'}), 401
    cfg = ApiConfig.get()
    return jsonify({'api_key': cfg.api_key}), 200


with app.app_context():
    db.create_all()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
