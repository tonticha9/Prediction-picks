"""
.github/scripts/backfill_history.py

MOJA KWA MOJA (one-time backfill, SI sehemu ya automation ya kila siku):
1) Inasoma data/predictions.csv (au faili nyingine kupitia PREDICTIONS_FILE env)
2) Inavuta matokeo HALISI ya mechi hizo (score+corners+cards+sot+HT-score)
   kutoka AllSportsAPI, kwa wigo wa tarehe unaopatikana kwenye faili hiyo
3) Inatathmini kila mstari (match+market) kwa market_evaluator.py
4) Inaandika/inasasisha PredictionRecord kwenye database (DATABASE_URL)

Endesha kupitia GitHub Actions workflow_dispatch (backfill_history.yml),
SI kwenye ratiba ya kila siku.
"""
import os
import sys
import time
import numpy as np
import pandas as pd
import requests
from datetime import datetime

# --- ongeza root ya repo kwenye sys.path ili tuweze ku-import app/models/evaluator ---
sys.path.insert(0, os.getcwd())

from market_evaluator import evaluate_market, result_label  # noqa: E402
from app import app  # noqa: E402
from models import db, PredictionRecord  # noqa: E402

API_KEY = os.environ.get('ALLSPORTSAPI_KEY')
PREDICTIONS_FILE = os.environ.get('PREDICTIONS_FILE', 'data/predictions.csv')

if not API_KEY:
    print("❌ HAIPO: ALLSPORTSAPI_KEY (angalia GitHub Secrets)")
    sys.exit(1)

BASE_URL = "https://apiv2.allsportsapi.com/football/"
LEAGUE_MAP = {'E0': 152, 'E1': 153, 'D1': 175, 'SP1': 302, 'F1': 168, 'I1': 207,
              'N1': 244, 'B1': 63, 'P1': 266, 'T1': 322, 'G1': 178, 'SC0': 279}

STAT_TYPE_MAP = {'Corners': 'corners', 'Shots Total': 'shots_total',
                  'Shots On Goal': 'shots_on_target', 'Fouls': 'fouls',
                  'Offsides': 'offsides', 'Ball Possession': 'possession',
                  'Yellow Cards': 'yellow_cards', 'Saves': 'saves'}

# NAKALA HALISI kutoka hourly_catchup.py - ili majina ya timu yalingane
TEAM_NAME_MAPPING = {
    'AC Milan': 'Milan', 'AEL Larissa': 'Larisa', 'AS Roma': 'Roma',
    'AZ Alkmaar': 'Az Alkmaar', 'Academico Viseu': 'Academica', 'Alavés': 'Alaves',
    'Arsenal FC': 'Arsenal', 'Asteras': 'Asteras Tripolis', 'Atl. Madrid': 'Ath Madrid',
    'Atromitos FC': 'Atromitos', 'B. Monchengladbach': "M'Gladbach",
    'Bayer Leverkusen': 'Leverkusen', 'Bayern München': 'Bayern Munich',
    'Beerschot VA': 'Beerschot Va', 'Beveren': 'Waasland-Beveren', 'Beşiktaş': 'Besiktas',
    'Bournemouth FC': 'Bournemouth', 'Braga': 'Sp Braga', 'Bremen': 'Werder Bremen',
    'Celta Vigo': 'Celta', 'Cercle Brugge KSV': 'Cercle Brugge', 'Club Brugge KV': 'Club Brugge',
    'Dep. A Coruna': 'La Coruna', 'Dundee FC': 'Dundee', 'Dundee Utd': 'Dundee United',
    'Eintracht Frankfurt': 'Ein Frankfurt', 'Erzurumspor': 'Erzurum Bb', 'Espanyol': 'Espanol',
    'FC Koln': 'Fc Koln', 'FC Porto': 'Porto', 'FC Volendam': 'Volendam',
    'Famalicão': 'Famalicao', 'Fenerbahçe': 'Fenerbahce', 'Fortuna': 'For Sittard',
    'Frankfurt': 'Ein Frankfurt', 'G.A. Eagles': 'Go Ahead Eagles',
    'Gençlerbirliği': 'Genclerbirligi', 'Go Ahead': 'Go Ahead Eagles', 'Goztepe': 'Goztep',
    'Göztepe': 'Goztep', 'Hamburger SV': 'Hamburg', 'Iraklis 1908': 'Iraklis',
    'Juventus FC': 'Juventus', 'KV Mechelen': 'Mechelen', 'Kasımpaşa': 'Kasimpasa',
    'Kayseri': 'Kayserispor', 'Larissa': 'Larisa', 'Leipzig': 'Rb Leipzig',
    'Levadiakos': 'Levadeiakos', "M'gladbach": "M'Gladbach", 'Mainz 05': 'Mainz',
    'Man Utd': 'Man United', 'Manchester City': 'Man City', 'Manchester Utd': 'Man United',
    'NAC Breda': 'Nac Breda', 'Nottm Forest': "Nott'M Forest", 'OFI Crete': 'Ofi Crete',
    'Olympiacos Piraeus': 'Olympiakos', 'Panaitolikos': 'Panetolikos',
    'Partick Thistle': 'Partick', 'RB Leipzig': 'Rb Leipzig', 'Rayo Vallecano': 'Vallecano',
    'Real Sociedad': 'Sociedad', 'Rize': 'Rizespor', 'Sheff Utd': 'Sheffield United',
    'Sheff Wed': 'Sheffield Weds', 'Sheffield Utd': 'Sheffield United',
    'Sheffield Wed': 'Sheffield Weds', 'Sint-Truiden': 'St Truiden', 'Sittard': 'For Sittard',
    'St. Mirren': 'St Mirren', 'St. Truiden': 'St Truiden',
    'Vitoria Guimaraes': 'Guimaraes', 'Volos': 'Volos Nfc', 'West Bromwich': 'West Brom',
    'Willem II': 'Willem Ii',
}


def parse_statistics(stats_list):
    parsed = {'home': {}, 'away': {}}
    for s in stats_list:
        stype = s.get('type')
        if stype in STAT_TYPE_MAP:
            key = STAT_TYPE_MAP[stype]
            for side in ['home', 'away']:
                val = str(s.get(side, '')).replace('%', '').strip()
                try:
                    parsed[side][key] = float(val) if val else np.nan
                except ValueError:
                    pass
    return parsed


def parse_cards(cards_list):
    hy = ay = hr = ar = 0
    for c in cards_list:
        ctype = str(c.get('card', '')).lower()
        is_home = bool(c.get('home_fault'))
        is_away = bool(c.get('away_fault'))
        if 'yellow' in ctype and 'red' not in ctype:
            if is_home: hy += 1
            if is_away: ay += 1
        elif 'red' in ctype:
            if is_home: hr += 1
            if is_away: ar += 1
    return hy, ay, hr, ar


def fetch_range_results(from_date, to_date):
    """Sawa na fetch_recent_results() ya hourly_catchup.py, kwa wigo mpana
    wa tarehe (siku kadhaa, si masaa)."""
    all_matches = []
    for code, league_key in LEAGUE_MAP.items():
        try:
            resp = requests.get(BASE_URL, params={
                "met": "Fixtures", "APIkey": API_KEY, "leagueId": league_key,
                "from": from_date, "to": to_date,
            }, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"   ⚠️ {code}: ombi limeshindwa ({e}) - naruka ligi hii")
            continue

        fixtures = data.get('result', []) or []
        finished = [f for f in fixtures if f.get('event_status') == 'Finished']
        print(f"   {code}: mechi {len(finished)} zilizokwisha ({from_date} - {to_date})")

        for f in finished:
            try:
                score = f.get('event_final_result', '')
                if ' - ' not in score:
                    continue
                fthg, ftag = score.split(' - ')
                fthg, ftag = int(fthg.strip()), int(ftag.strip())
            except Exception:
                continue

            ht_score = f.get('event_halftime_result', '')
            hthg, htag = np.nan, np.nan
            if ' - ' in ht_score:
                try:
                    hthg, htag = [int(x.strip()) for x in ht_score.split(' - ')]
                except Exception:
                    pass

            stats = parse_statistics(f.get('statistics', []))
            hy, ay, hr, ar = parse_cards(f.get('cards', []))

            all_matches.append({
                'Date': f.get('event_date'), 'HomeTeam': f.get('event_home_team'),
                'AwayTeam': f.get('event_away_team'), 'FTHG': fthg, 'FTAG': ftag,
                'HTHG': hthg, 'HTAG': htag, 'league': code,
                'HC': stats['home'].get('corners'), 'AC': stats['away'].get('corners'),
                'HS': stats['home'].get('shots_total'), 'AS': stats['away'].get('shots_total'),
                'HST': stats['home'].get('shots_on_target'), 'AST': stats['away'].get('shots_on_target'),
                'HY': hy, 'AY': ay, 'HR': hr, 'AR': ar,
            })
        time.sleep(0.3)

    df = pd.DataFrame(all_matches)
    if len(df) > 0:
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
        df['HomeTeam'] = df['HomeTeam'].replace(TEAM_NAME_MAPPING)
        df['AwayTeam'] = df['AwayTeam'].replace(TEAM_NAME_MAPPING)
    return df


def load_section(path, section_name):
    if not path or not os.path.exists(path):
        print(f"   ℹ️ {path} haipo - naruka section '{section_name}'")
        return None
    df = pd.read_csv(path)
    df['match_date'] = pd.to_datetime(df['match_date'])
    df['match_day'] = df['match_date'].dt.strftime('%Y-%m-%d')
    df['_section'] = section_name
    return df


def main():
    value_bets_file = os.environ.get('VALUE_BETS_FILE', 'data/value_bets.csv')

    print(f"📥 Kusoma predictions kutoka {PREDICTIONS_FILE}...")
    preds = load_section(PREDICTIONS_FILE, 'prediction')
    vbets = load_section(value_bets_file, 'value_bet')

    frames = [df for df in (preds, vbets) if df is not None]
    if not frames:
        print("❌ Hakuna faili lolote lililopatikana (predictions/value_bets).")
        sys.exit(1)
    preds = pd.concat(frames, ignore_index=True)

    from_date = preds['match_day'].min()
    to_date = preds['match_day'].max()
    print(f"   Mistari {len(preds):,} jumla (predictions+value_bets), "
          f"mechi {preds[['HomeTeam','AwayTeam','match_day']].drop_duplicates().shape[0]:,} za kipekee, "
          f"tarehe {from_date} hadi {to_date}")

    print(f"\n🔍 Kuvuta matokeo halisi ({from_date} - {to_date}) kutoka AllSportsAPI...")
    results = fetch_range_results(from_date, to_date)
    print(f"   Jumla ya mechi zenye matokeo zilizopatikana: {len(results)}")

    if len(results) == 0:
        print("❌ Hakuna matokeo yaliyopatikana - sitaandika chochote.")
        sys.exit(1)

    results['match_day'] = results['Date'].dt.strftime('%Y-%m-%d')
    results_map = {}
    for _, r in results.iterrows():
        key = (r['match_day'], r['HomeTeam'], r['AwayTeam'])
        results_map[key] = r.to_dict()

    matched = 0
    unmatched_pairs = set()
    won = lost = void = skipped_existing = 0

    with app.app_context():
        for _, row in preds.iterrows():
            key = (row['match_day'], row['HomeTeam'], row['AwayTeam'])
            result_row = results_map.get(key)

            if result_row is None:
                unmatched_pairs.add((row['HomeTeam'], row['AwayTeam'], row['match_day']))
                continue
            matched += 1

            existing = PredictionRecord.query.filter_by(
                match_date=row['match_date'], home_team=row['HomeTeam'],
                away_team=row['AwayTeam'], market=row['market'], section=row['_section']
            ).first()
            if existing:
                skipped_existing += 1
                continue

            evaluated = evaluate_market(row['market'], result_row)
            label = result_label(evaluated)
            if label == 'WON':
                won += 1
            elif label == 'LOST':
                lost += 1
            else:
                void += 1

            rec = PredictionRecord(
                match_date=row['match_date'],
                home_team=row['HomeTeam'],
                away_team=row['AwayTeam'],
                market=row['market'],
                section=row['_section'],
                probability=row.get('probability_%'),
                real_odds=row.get('real_odds') if pd.notna(row.get('real_odds')) else None,
                pro_tier=row.get('pro_tier'),
                result=label,
                settled_at=datetime.utcnow(),
                shown_date=row['match_date'].date(),
            )
            db.session.add(rec)

        db.session.commit()

    print(f"\n✅ Imekamilika: {matched} rows zililingana na matokeo, {skipped_existing} tayari zilikuwepo (ziliruka).")
    print(f"   WON={won}  LOST={lost}  VOID={void}")
    if unmatched_pairs:
        print(f"\n⚠️ Mechi {len(unmatched_pairs)} hazikupata matokeo (huenda haikuwa kwenye AllSportsAPI "
              f"kwa tarehe hiyo, au jina la timu halilingani - angalia TEAM_NAME_MAPPING):")
        for home, away, day in sorted(unmatched_pairs)[:20]:
            print(f"   • {day}: {home} vs {away}")
        if len(unmatched_pairs) > 20:
            print(f"   ... na {len(unmatched_pairs) - 20} nyingine")


if __name__ == '__main__':
    main()
