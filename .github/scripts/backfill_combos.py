"""
.github/scripts/backfill_combos.py

MOJA KWA MOJA (one-time backfill, SI automation ya kila siku - Combos
zijazo tayari ni automatic kupitia snapshot_today()/generate_daily_combos()).

1) Inasoma data/combos.csv ya ZAMANI (muundo wa kale: "Home vs Away: market
   @odds + Home vs Away: market @odds ..." - legs ZOTE zina odds).
2) Kwa kila leg, inatambua match_date kwa kulinganisha majina ya timu na
   data/predictions.csv ya kipindi kile kile (Home+Away zinapatikana huko
   zikiwa na tarehe).
3) Inavuta matokeo halisi ya mechi hizo kutoka AllSportsAPI.
4) Inatathmini kila leg kwa market_evaluator.py, inaamua matokeo ya combo
   nzima (WON=zote WON, LOST=leg yoyote LOST, VOID=hakuna LOST lakini kuna VOID).
5) Inaandika ComboRecord + ComboLeg (zote is_stake_leg=True - muundo wa
   zamani haukuwa na 'confidence legs').
"""
import os
import sys
import re
import time
import numpy as np
import pandas as pd
import requests
from datetime import datetime

sys.path.insert(0, os.getcwd())

from market_evaluator import evaluate_market, result_label  # noqa: E402
from app import app  # noqa: E402
from models import db, ComboRecord, ComboLeg  # noqa: E402

API_KEY = os.environ.get('ALLSPORTSAPI_KEY')
COMBOS_FILE = os.environ.get('COMBOS_FILE', 'data/combos.csv')
PREDICTIONS_FILE = os.environ.get('PREDICTIONS_FILE', 'data/predictions.csv')

if not API_KEY:
    print("❌ HAIPO: ALLSPORTSAPI_KEY")
    sys.exit(1)

BASE_URL = "https://apiv2.allsportsapi.com/football/"
LEAGUE_MAP = {'E0': 152, 'E1': 153, 'D1': 175, 'SP1': 302, 'F1': 168, 'I1': 207,
              'N1': 244, 'B1': 63, 'P1': 266, 'T1': 322, 'G1': 178, 'SC0': 279}
STAT_TYPE_MAP = {'Corners': 'corners', 'Shots Total': 'shots_total',
                  'Shots On Goal': 'shots_on_target', 'Fouls': 'fouls',
                  'Offsides': 'offsides', 'Ball Possession': 'possession',
                  'Yellow Cards': 'yellow_cards', 'Saves': 'saves'}
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

LEG_RE = re.compile(r'^(.*?)\s+vs\s+(.*?):\s*(\S+)\s*@([\d.]+)$')


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
            print(f"   ⚠️ {code}: ombi limeshindwa ({e})")
            continue
        finished = [f for f in (data.get('result', []) or []) if f.get('event_status') == 'Finished']
        for f in finished:
            try:
                score = f.get('event_final_result', '')
                if ' - ' not in score:
                    continue
                fthg, ftag = [int(x.strip()) for x in score.split(' - ')]
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
                'HTHG': hthg, 'HTAG': htag,
                'HC': stats['home'].get('corners'), 'AC': stats['away'].get('corners'),
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


def main():
    print(f"📥 Kusoma combos kutoka {COMBOS_FILE}...")
    combos_df = pd.read_csv(COMBOS_FILE)

    print(f"📥 Kusoma predictions (kwa kutambua tarehe za mechi) kutoka {PREDICTIONS_FILE}...")
    preds = pd.read_csv(PREDICTIONS_FILE)
    preds['match_date'] = pd.to_datetime(preds['match_date'])
    date_lookup = {}
    for _, r in preds.iterrows():
        date_lookup[(r['HomeTeam'], r['AwayTeam'])] = r['match_date']

    # Chambua legs zote za combos zote, tambua tarehe kwa kila leg
    parsed_combos = []
    all_dates = []
    unmatched_teams = set()
    for _, row in combos_df.iterrows():
        legs_raw = [leg.strip() for leg in row['legs'].split(' + ')]
        legs = []
        for leg_str in legs_raw:
            m = LEG_RE.match(leg_str)
            if not m:
                print(f"   ⚠️ Imeshindwa kuchambua leg: '{leg_str}'")
                continue
            home, away, market, odds = m.group(1), m.group(2), m.group(3), float(m.group(4))
            mdate = date_lookup.get((home, away))
            if mdate is None:
                unmatched_teams.add((home, away))
                continue
            legs.append({'home': home, 'away': away, 'market': market,
                         'real_odds': odds, 'match_date': mdate})
            all_dates.append(mdate)
        if legs:
            parsed_combos.append(legs)

    print(f"   Combos {len(parsed_combos)} zilizochambuliwa, kutoka {len(combos_df)} jumla.")
    if unmatched_teams:
        print(f"   ⚠️ Jozi za timu {len(unmatched_teams)} hazikupata tarehe kwenye predictions.csv "
              f"(zitaachwa nje): {sorted(unmatched_teams)[:10]}")

    if not all_dates:
        print("❌ Hakuna legs zenye tarehe zilizopatikana - sitaweza kuendelea.")
        sys.exit(1)

    from_date = min(all_dates).strftime('%Y-%m-%d')
    to_date = max(all_dates).strftime('%Y-%m-%d')
    print(f"\n🔍 Kuvuta matokeo halisi ({from_date} - {to_date}) kutoka AllSportsAPI...")
    results = fetch_range_results(from_date, to_date)
    print(f"   Jumla ya mechi zenye matokeo: {len(results)}")
    if len(results) == 0:
        print("❌ Hakuna matokeo yaliyopatikana.")
        sys.exit(1)

    results['match_day'] = results['Date'].dt.strftime('%Y-%m-%d')
    results_map = {(r['match_day'], r['HomeTeam'], r['AwayTeam']): r.to_dict()
                   for _, r in results.iterrows()}

    written, skipped_existing, won = 0, 0, 0
    lost = void = incomplete = 0

    with app.app_context():
        for legs in parsed_combos:
            leg_results = []
            all_found = True
            for leg in legs:
                key = (leg['match_date'].strftime('%Y-%m-%d'), leg['home'], leg['away'])
                result_row = results_map.get(key)
                if result_row is None:
                    all_found = False
                    leg_results.append((leg, 'VOID'))  # hatuna matokeo - VOID salama
                    continue
                evaluated = evaluate_market(leg['market'], result_row)
                leg_results.append((leg, result_label(evaluated)))

            if not all_found:
                incomplete += 1  # baadhi ya legs hazina matokeo - combo bado inaandikwa na VOID kwa legs hizo

            labels = [lr[1] for lr in leg_results]
            if any(l == 'LOST' for l in labels):
                combo_result = 'LOST'
                lost += 1
            elif any(l == 'VOID' for l in labels):
                combo_result = 'VOID'
                void += 1
            else:
                combo_result = 'WON'
                won += 1

            shown_date = min(leg['match_date'] for leg in legs).date()
            odds_product = 1.0
            for leg in legs:
                odds_product *= leg['real_odds']

            rec = ComboRecord(shown_date=shown_date, combined_odds=round(odds_product, 2),
                              combined_probability=None, result=combo_result,
                              settled_at=datetime.utcnow())
            db.session.add(rec)
            db.session.flush()
            for leg, label in leg_results:
                db.session.add(ComboLeg(
                    combo_id=rec.id, home_team=leg['home'], away_team=leg['away'],
                    match_date=leg['match_date'], market=leg['market'],
                    probability=None, real_odds=leg['real_odds'],
                    is_stake_leg=True, result=label
                ))
            written += 1

        db.session.commit()

    print(f"\n✅ Combos {written} zimeandikwa: WON={won} LOST={lost} VOID={void} "
          f"(kati ya hizo, {incomplete} zilikuwa na leg 1+ bila matokeo - ziliwekwa VOID kwa sehemu hiyo).")


if __name__ == '__main__':
    main()
