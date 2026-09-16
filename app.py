"""
Braiton Picks — Flask app
Dashboard ya predictions za kila siku + Value Bets + Combos + History + Admin.
"""
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import pandas as pd
from datetime import datetime, date
import os
import shutil

from models import db, Settings, ApiConfig, DataSnapshot

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, 'data')
HISTORY_DIR = os.path.join(DATA_DIR, 'history')

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'badilisha-hii-kwenye-production')
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(BASE_DIR, 'app.db')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'badilisha-hii')

# ================================================================
# DATA LOADING (cache kwenye kumbukumbu, 'refresh' kuisasisha)
# ================================================================
_cache = {}

def load_live_data():
    global _cache
    
    # Predictions - ina 'first_leg_date'
    predictions = pd.read_csv(os.path.join(DATA_DIR, 'predictions.csv'))
    predictions['first_leg_date'] = pd.to_datetime(predictions['first_leg_date'])
    
    # Value Bets - ina 'match_date'
    value_bets = pd.read_csv(os.path.join(DATA_DIR, 'value_bets.csv'))
    value_bets['match_date'] = pd.to_datetime(value_bets['match_date'])
    
    # Combos - ina 'match_date'
    combos = pd.read_csv(os.path.join(DATA_DIR, 'combos.csv'))
    combos['match_date'] = pd.to_datetime(combos['match_date'])
    
    _cache = {'predictions': predictions, 'value_bets': value_bets, 'combos': combos}
    return _cache
    def get_data():
    if not _cache:
        return load_live_data()
    return _cache

TIER_LABELS = {
    'PRO_STRONG': {'label': 'Nguvu Kubwa', 'class': 'tier-strong', 'icon': '🔥'},
    'PRO_MEDIUM': {'label': 'Wastani', 'class': 'tier-medium', 'icon': '⚡'},
}

def tier_info(tier_code):
    return TIER_LABELS.get(tier_code, {'label': tier_code, 'class': 'tier-other', 'icon': '•'})

# ================================================================
# DASHBOARD — Mechi za leo/mbeleni, best-pick + other picks
# ================================================================
@app.route('/')
def dashboard():
    data = get_data()
    df = data['predictions'].copy()
    settings = Settings.get()

    date_filter = request.args.get('date')
    if date_filter:
        df = df[df['match_date'].dt.strftime('%Y-%m-%d') == date_filter]

    df = df[df['probability_%'] >= settings.min_probability]

    matches = []
    for (home, away, mdate), grp in df.groupby(['HomeTeam', 'AwayTeam', 'match_date']):
        grp_sorted = grp.sort_values('probability_%', ascending=False)
        best = grp_sorted.iloc[0]
        others = grp_sorted.iloc[1:1 + settings.max_markets_per_match]

        best_pick = {
            'market': best['market'],
            'probability': best['probability_%'],
            'tier': tier_info(best['pro_tier']),
            'has_odds': pd.notna(best.get('real_odds')),
            'odds': best.get('real_odds'),
            'ev': round((best['probability_%']/100 * best['real_odds'] - 1) * 100, 1) if pd.notna(best.get('real_odds')) else None,
        }
        other_picks = []
        for _, row in others.iterrows():
            other_picks.append({
                'market': row['market'], 'probability': row['probability_%'],
                'tier': tier_info(row['pro_tier']),
                'has_odds': pd.notna(row.get('real_odds')), 'odds': row.get('real_odds'),
            })

        matches.append({
            'home': home, 'away': away,
            'date': mdate.strftime('%d %b %Y'), 'date_iso': mdate.strftime('%Y-%m-%d'),
            'time_eat': mdate.strftime('%H:%M') if mdate.hour or mdate.minute else None,
            'best_pick': best_pick, 'other_picks': other_picks,
        })

    matches.sort(key=lambda m: m['date_iso'])
    available_dates = sorted(data['predictions']['match_date'].dt.strftime('%Y-%m-%d').unique().tolist())

    return render_template('dashboard.html', matches=matches, available_dates=available_dates,
                            selected_date=date_filter, page='dashboard')


# ================================================================
# VALUE BETS
# ================================================================
@app.route('/value-bets')
def value_bets():
    df = get_data()['value_bets'].copy().sort_values('ev_%', ascending=False)
    bets = []
    for _, row in df.iterrows():
        bets.append({
            'home': row['HomeTeam'], 'away': row['AwayTeam'],
            'date': pd.to_datetime(row['match_date']).strftime('%d %b %Y'),
            'market': row['market'], 'probability': row['probability_%'],
            'odds': row['real_odds'], 'ev': row['ev_%'], 'tier': tier_info(row['pro_tier']),
        })
    return render_template('value_bets.html', bets=bets, page='value_bets')


# ================================================================
# COMBOS
# ================================================================
@app.route('/combos')
def combos():
    df = get_data()['combos'].copy()
    combo_list = []
    for _, row in df.iterrows():
        legs = [leg.strip() for leg in row['legs'].split(' + ')]
        combo_list.append({
            'target_odds': row['target_odds'], 'combined_odds': row['combined_odds'],
            'combined_prob': row['combined_prob_%'], 'n_legs': row['n_legs'], 'legs': legs,
        })
    return render_template('combos.html', combos=combo_list, page='combos')


# ================================================================
# HISTORY — chagua tier (predictions/value_bets/combos), ona tarehe za nyuma
# ================================================================
@app.route('/history')
def history():
    tier = request.args.get('tier', 'predictions')
    snapshot_date = request.args.get('date')

    snapshots = DataSnapshot.query.order_by(DataSnapshot.snapshot_date.desc()).all()
    available_dates = [s.snapshot_date.strftime('%Y-%m-%d') for s in snapshots]

    rows = []
    if snapshot_date:
        snap = DataSnapshot.query.filter_by(snapshot_date=datetime.strptime(snapshot_date, '%Y-%m-%d').date()).first()
        if snap:
            path_map = {'predictions': snap.predictions_path, 'value_bets': snap.value_bets_path, 'combos': snap.combos_path}
            path = path_map.get(tier)
            if path and os.path.exists(path):
                rows = pd.read_csv(path).to_dict('records')

    return render_template('history.html', tier=tier, rows=rows, available_dates=available_dates,
                            selected_date=snapshot_date, page='history')


# ================================================================
# ADMIN — settings, API key, refresh data
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
            settings.min_probability = float(request.form.get('min_probability', 0))
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
            resp.set_cookie('is_admin', 'yes', max_age=60*60*24*7)  # wiki 1
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
    """Pakia upya CSV kutoka data/ (baada ya kupakia faili mpya), na hifadhi kama history snapshot."""
    if not admin_required():
        return redirect(url_for('admin_login'))

    load_live_data()

    today = date.today()
    hist_folder = os.path.join(HISTORY_DIR, today.strftime('%Y-%m-%d'))
    os.makedirs(hist_folder, exist_ok=True)
    for fname in ['predictions.csv', 'value_bets.csv', 'combos.csv']:
        shutil.copy(os.path.join(DATA_DIR, fname), os.path.join(hist_folder, fname))

    existing = DataSnapshot.query.filter_by(snapshot_date=today).first()
    if not existing:
        snap = DataSnapshot(snapshot_date=today,
                             predictions_path=os.path.join(hist_folder, 'predictions.csv'),
                             value_bets_path=os.path.join(hist_folder, 'value_bets.csv'),
                             combos_path=os.path.join(hist_folder, 'combos.csv'))
        db.session.add(snap)
        db.session.commit()

    flash('Data imesasishwa na kuhifadhiwa kwenye history.', 'success')
    return redirect(url_for('admin'))


# ================================================================
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
