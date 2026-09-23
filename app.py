"""
Braiton Picks — Flask app
Dashboard ya predictions za kila siku + Value Bets + Combos + History + Admin.
"""
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import pandas as pd
import os
import shutil
from datetime import datetime, date

from models import db, Settings, ApiConfig, DataSnapshot, PredictionRecord, ComboRecord, ComboLeg
from market_selection import is_market_allowed, select_markets
from combo_builder import build_daily_combos

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
    app.config['SQLALCHEMY_DATABASE_URI'] = _database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(BASE_DIR, 'app.db')}"

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'badilisha-hii')
REFRESH_SECRET = os.environ.get('REFRESH_SECRET')

# ================================================================
# DATA LOADING (cache kwenye kumbukumbu, 'refresh' kuisasisha)
# ================================================================
_cache = {}


def load_live_data():
    global _cache
    predictions = pd.read_csv(os.path.join(DATA_DIR, 'predictions.csv'))
    predictions['match_date'] = pd.to_datetime(predictions['match_date'])

    value_bets = pd.read_csv(os.path.join(DATA_DIR, 'value_bets.csv'))
    value_bets['match_date'] = pd.to_datetime(value_bets['match_date'])

    combos = pd.read_csv(os.path.join(DATA_DIR, 'combos.csv'))

    _cache = {'predictions': predictions, 'value_bets': value_bets, 'combos': combos}
    return _cache


def get_data():
    if not _cache:
        return load_live_data()
    return _cache


def today_predictions_survivors(settings):
    """Kwa kila mechi ya LEO, rudisha {'home','away','match_date','survivors':[...]}
    kwa kutumia select_markets() - hii ndiyo picha ile ile Dashboard inayoonyesha,
    inatumika pia kujenga Combos za leo."""
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


def record_predictions_pending():
    """Andika PredictionRecord mpya (result='PENDING') kwa kila mstari mpya
    kwenye _cache['predictions']/['value_bets'] - ISIPOKUWA tayari zipo."""
    section_map = {'predictions': 'prediction', 'value_bets': 'value_bet'}
    new_count = 0
    for cache_key, section in section_map.items():
        df = _cache.get(cache_key)
        if df is None or len(df) == 0:
            continue
        for _, row in df.iterrows():
            exists = PredictionRecord.query.filter_by(
                match_date=row['match_date'], home_team=row['HomeTeam'],
                away_team=row['AwayTeam'], market=row['market'], section=section
            ).first()
            if exists:
                continue
            rec = PredictionRecord(
                match_date=row['match_date'], home_team=row['HomeTeam'],
                away_team=row['AwayTeam'], market=row['market'], section=section,
                probability=row.get('probability_%'),
                real_odds=row.get('real_odds') if pd.notna(row.get('real_odds')) else None,
                pro_tier=row.get('pro_tier'), result='PENDING',
                shown_date=date.today(),
            )
            db.session.add(rec)
            new_count += 1
    if new_count:
        db.session.commit()
    return new_count


def generate_daily_combos():
    """Jenga na uhifadhi Combos za LEO (mara moja tu kwa siku - ikiwa
    tayari zipo kwa leo, haziundwi tena)."""
    today = date.today()
    already = ComboRecord.query.filter_by(shown_date=today).first()
    if already:
        return 0

    settings = Settings.get()
    match_survivors = today_predictions_survivors(settings)
    if not match_survivors:
        return 0

    combos = build_daily_combos(
        match_survivors,
        min_legs=settings.combo_min_legs,
        max_legs=settings.combo_max_legs,
        min_leg_probability=settings.combo_min_leg_probability,
    )

    count = 0
    for c in combos:
        rec = ComboRecord(
            shown_date=today, combined_odds=c['combined_odds'],
            combined_probability=c['combined_probability'], result='PENDING'
        )
        db.session.add(rec)
        db.session.flush()  # ili rec.id ipatikane kabla ya legs
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


def snapshot_today():
    """Nakili CSV za sasa kwenye data/history/YYYY-MM-DD/, andika DataSnapshot,
    PredictionRecord mpya (PENDING), na Combos za leo."""
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

    record_predictions_pending()
    generate_daily_combos()


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
        }
        other_picks = [{
            'market': r['market'], 'probability': r['probability_%'],
            'tier': tier_info(r.get('pro_tier')),
            'has_odds': pd.notna(r.get('real_odds')), 'odds': r.get('real_odds'),
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
# COMBOS — LEO PEKEE, kutoka ComboRecord (database), si combos.csv tena.
# ================================================================
@app.route('/combos')
def combos():
    today = date.today()
    records = ComboRecord.query.filter_by(shown_date=today).order_by(ComboRecord.combined_odds).all()

    combo_list = []
    for rec in records:
        combo_list.append({
            'combined_odds': rec.combined_odds, 'combined_probability': rec.combined_probability,
            'result': rec.result,
            'stake_legs': [{'home': l.home_team, 'away': l.away_team, 'market': l.market,
                            'probability': l.probability, 'odds': l.real_odds}
                           for l in rec.legs if l.is_stake_leg],
            'confidence_legs': [{'home': l.home_team, 'away': l.away_team, 'market': l.market,
                                 'probability': l.probability}
                                for l in rec.legs if not l.is_stake_leg],
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
        available_dates = sorted(
            {d[0].strftime('%Y-%m-%d') for d in
             db.session.query(ComboRecord.shown_date).distinct().all()}, reverse=True)

        if selected_date:
            day = datetime.strptime(selected_date, '%Y-%m-%d').date()
            records = ComboRecord.query.filter_by(shown_date=day).order_by(ComboRecord.combined_odds).all()
            for rec in records:
                combos_list.append({
                    'combined_odds': rec.combined_odds, 'combined_probability': rec.combined_probability,
                    'result': rec.result,
                    'stake_legs': [{'home': l.home_team, 'away': l.away_team, 'market': l.market,
                                    'probability': l.probability, 'odds': l.real_odds, 'result': l.result}
                                   for l in rec.legs if l.is_stake_leg],
                    'confidence_legs': [{'home': l.home_team, 'away': l.away_team, 'market': l.market,
                                         'probability': l.probability, 'result': l.result}
                                        for l in rec.legs if not l.is_stake_leg],
                })
            won = sum(1 for c in combos_list if c['result'] == 'WON')
            lost = sum(1 for c in combos_list if c['result'] == 'LOST')
            void = sum(1 for c in combos_list if c['result'] == 'VOID')
            summary = {'won': won, 'lost': lost, 'void': void,
                       'win_rate': round(won / (won + lost) * 100, 1) if (won + lost) > 0 else None}

        return render_template('history.html', tier=tier, available_dates=available_dates,
                               selected_date=selected_date, matches=matches,
                               value_bets_list=value_bets_list, combos_list=combos_list,
                               summary=summary, combos_message=None, page='history')

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
                           summary=summary, combos_message=None, page='history')


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
            settings.combo_min_legs = int(request.form.get('combo_min_legs', 3))
            settings.combo_max_legs = int(request.form.get('combo_max_legs', 5))
            settings.combo_min_leg_probability = float(request.form.get('combo_min_leg_probability', 70.0))
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
    return render_template('admin.html', settings=settings, api_config=api_config,
                           days_left=days_left, page='admin')


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


# ================================================================
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
