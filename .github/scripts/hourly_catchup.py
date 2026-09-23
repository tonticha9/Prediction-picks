"""
.github/scripts/hourly_catchup.py

JOB A — Inaendesha KILA SAA. Inavuta mechi zilizomalizika hivi karibuni
kutoka AllSportsAPI, inaziongeza kwenye 'historical-complete-no-gap'
dataset (Kaggle), na inapakia toleo JIPYA la dataset hiyo - ili Job B
(usiku) ipate historia iliyosasika kila wakati, bila pengo kuunda tena.

MPYA (Sept 22 2026): baada ya kupata matokeo mapya, inatathmini moja kwa
moja PredictionRecord zote za PENDING za mechi hizo (kwa market_evaluator.py)
na kuandika WON/LOST/VOID kwenye database - History inasasika kiotomatiki
bila hatua yoyote ya mkono.

Logic ya kuvuta/kuchakata mechi ni NAKALA HALISI ya backfill_gap.py
(iliyokwisha jaribiwa na kufanya kazi).
"""
import os
import sys
import time
import json
import requests
import pandas as pd
import numpy as np
import subprocess
from datetime import datetime, timedelta, date

# --- ongeza root ya repo kwenye sys.path ili tuweze ku-import app/models/evaluator ---
sys.path.insert(0, os.getcwd())

API_KEY = os.environ.get("ALLSPORTSAPI_KEY")
KAGGLE_DATASET = os.environ.get("KAGGLE_DATASET_SLUG")  # mfano: tonticha/historical-complete-no-gap

missing = []
if not API_KEY:
    missing.append("ALLSPORTSAPI_KEY")
if not KAGGLE_DATASET:
    missing.append("KAGGLE_DATASET_SLUG")
if missing:
    print(f"❌ HAIPO: {', '.join(missing)} (angalia GitHub Secrets / workflow env)")
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


def fetch_recent_results(from_date, to_date):
    """NAKALA HALISI ya backfill_gap.py's fetch_gap(), lakini kwa wigo
    mfupi wa muda (masaa machache, si wiki)."""
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
            print(f"   ⚠️ {code}: ombi limeshindwa ({e}) - naruka ligi hii mzunguko huu")
            continue

        fixtures = data.get('result', []) or []
        finished = [f for f in fixtures if f.get('event_status') == 'Finished']
        if finished:
            print(f"   {code}: {len(finished)} mechi mpya zilizokwisha")

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
                'FTR': 'H' if fthg > ftag else ('A' if ftag > fthg else 'D'),
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


def run_kaggle_cmd(args):
    result = subprocess.run(['kaggle'] + args, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"❌ Amri ya kaggle imeshindwa: {result.stderr}")
        sys.exit(1)
    return result.stdout


def check_stuck_fixtures(from_date, to_date):
    """
    UKAGUZI WA UKWELI (si makisio ya wigo/statistiki): kwa kila mechi
    iliyopangwa (kwenye 'from_date' hadi 'to_date'), angalia moja kwa
    moja: je imepangwa kuanza SAA 4+ ZILIZOPITA, lakini bado haina
    matokeo ("Finished")? Hii ni ushahidi HALISI wa tatizo (mechi
    "imekwama" - API haijasasisha, au tatizo la mtandao la chanzo cha
    data) - si "idadi isiyo ya kawaida".

    Returns: list ya (league, HomeTeam, AwayTeam, kickoff_time, status)
    kwa mechi zilizokwama.
    """
    stuck = []
    now = datetime.utcnow()
    for code, league_key in LEAGUE_MAP.items():
        try:
            resp = requests.get(BASE_URL, params={
                "met": "Fixtures", "APIkey": API_KEY, "leagueId": league_key,
                "from": from_date, "to": to_date,
            }, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"   ⚠️ {code}: ukaguzi wa 'stuck fixtures' umeshindwa ({e})")
            continue

        for f in (data.get('result', []) or []):
            status = f.get('event_status', '')
            if status == 'Finished':
                continue
            try:
                kickoff = datetime.strptime(
                    f"{f.get('event_date')} {f.get('event_time')}", '%Y-%m-%d %H:%M')
            except Exception:
                continue
            hours_since_kickoff = (now - kickoff).total_seconds() / 3600
            if hours_since_kickoff > 4:  # muda wa kutosha kwa mechi yoyote kumalizika
                stuck.append((code, f.get('event_home_team'), f.get('event_away_team'),
                              f.get('event_date'), f.get('event_time'), status or '(tupu)'))
        time.sleep(0.3)
    return stuck


def run_stuck_check(from_date, to_date):
    print("\n🔍 Kukagua mechi 'zilizokwama' (zimepita masaa 4+ bila matokeo)...")
    stuck = check_stuck_fixtures(from_date, to_date)
    if stuck:
        print(f"\n⚠️  MECHI {len(stuck)} ZIMEKWAMA (zimepita muda, bado hazina matokeo):")
        for code, home, away, edate, etime, status in stuck:
            msg = f"{code}: {home} vs {away} ({edate} {etime}) - status: '{status}'"
            print(f"   • {msg}")
            print(f"::warning::Mechi imekwama: {msg}")
    else:
        print("✅ Hakuna mechi zilizokwama - kila kitu kiko sawa.")


def evaluate_pending_predictions(df_finished):
    """MPYA: kwa kila mechi mpya iliyoisha (df_finished), tafuta PredictionRecord
    zote za PENDING zinazolingana (Date+HomeTeam+AwayTeam) na uzitathmini kwa
    market_evaluator.py, kisha uandike WON/LOST/VOID. Inahitaji DATABASE_URL."""
    if len(df_finished) == 0:
        return

    if not os.environ.get('DATABASE_URL'):
        print("\nℹ️ DATABASE_URL haipo - naruka utathmini wa PredictionRecord mzunguko huu.")
        return

    try:
        from market_evaluator import evaluate_market, result_label
        from app import app
        from models import db, PredictionRecord
    except Exception as e:
        print(f"\n⚠️ Imeshindwa kuunganisha na app/models/evaluator ({e}) - naruka utathmini.")
        return

    print(f"\n🧮 Kutathmini PredictionRecord za PENDING kwa mechi {len(df_finished)} zilizoisha...")
    evaluated = 0
    with app.app_context():
        for _, r in df_finished.iterrows():
            match_day = r['Date'].date()
            records = PredictionRecord.query.filter_by(
                home_team=r['HomeTeam'], away_team=r['AwayTeam'], result='PENDING'
            ).filter(db.func.date(PredictionRecord.match_date) == match_day).all()

            if not records:
                continue

            result_row = r.to_dict()
            for rec in records:
                evaluated_val = evaluate_market(rec.market, result_row)
                rec.result = result_label(evaluated_val)
                rec.settled_at = datetime.utcnow()
                evaluated += 1

        if evaluated:
            db.session.commit()

    print(f"   ✅ PredictionRecord {evaluated} zimesasishwa na matokeo (WON/LOST/VOID).")


def evaluate_pending_combo_legs(df_finished):
    """MPYA: tathmini ComboLeg za PENDING zinazolingana na mechi mpya
    zilizoisha, kisha kwa kila ComboRecord iliyoguswa, angalia kama legs
    ZOTE tayari zina matokeo (si PENDING) - ikiwa ndiyo, amua result ya
    combo nzima: LOST (leg yoyote LOST) > VOID (hakuna LOST, kuna VOID) > WON."""
    if len(df_finished) == 0:
        return
    if not os.environ.get('DATABASE_URL'):
        return
    try:
        from market_evaluator import evaluate_market, result_label
        from app import app
        from models import db, ComboLeg, ComboRecord
    except Exception as e:
        print(f"\n⚠️ Imeshindwa kuunganisha na app/models/evaluator kwa combos ({e}) - naruka.")
        return

    print(f"\n🧮 Kutathmini ComboLeg za PENDING kwa mechi {len(df_finished)} zilizoisha...")
    legs_evaluated, combos_settled = 0, 0
    with app.app_context():
        touched_combo_ids = set()
        for _, r in df_finished.iterrows():
            match_day = r['Date'].date()
            legs = ComboLeg.query.filter_by(
                home_team=r['HomeTeam'], away_team=r['AwayTeam'], result='PENDING'
            ).filter(db.func.date(ComboLeg.match_date) == match_day).all()

            if not legs:
                continue
            result_row = r.to_dict()
            for leg in legs:
                evaluated_val = evaluate_market(leg.market, result_row)
                leg.result = result_label(evaluated_val)
                legs_evaluated += 1
                touched_combo_ids.add(leg.combo_id)

        for combo_id in touched_combo_ids:
            combo = ComboRecord.query.get(combo_id)
            if not combo or combo.result != 'PENDING':
                continue
            leg_results = [leg.result for leg in combo.legs]
            if any(r == 'PENDING' for r in leg_results):
                continue  # bado kuna legs hazijamalizika - subiri
            if any(r == 'LOST' for r in leg_results):
                combo.result = 'LOST'
            elif any(r == 'VOID' for r in leg_results):
                combo.result = 'VOID'
            else:
                combo.result = 'WON'
            combo.settled_at = datetime.utcnow()
            combos_settled += 1

        if legs_evaluated:
            db.session.commit()

    print(f"   ✅ ComboLeg {legs_evaluated} zimesasishwa, Combos {combos_settled} zimekamilika (WON/LOST/VOID).")


def main():
    # HATUA 1: Pakua dataset ya sasa (historia iliyopo)
    print("📥 Kupakua historical-complete-no-gap dataset ya sasa...")
    os.makedirs('kaggle_dataset', exist_ok=True)
    run_kaggle_cmd(['datasets', 'download', '-d', KAGGLE_DATASET,
                     '-p', 'kaggle_dataset', '--unzip', '-o'])

    csv_files = [f for f in os.listdir('kaggle_dataset') if f.endswith('.csv')]
    if not csv_files:
        print("❌ Hakuna CSV iliyopatikana kwenye dataset")
        sys.exit(1)
    csv_path = os.path.join('kaggle_dataset', csv_files[0])
    df_existing = pd.read_csv(csv_path)
    df_existing['Date'] = pd.to_datetime(df_existing['Date'])
    print(f"   Historia ya sasa: {len(df_existing):,} mechi")

    # HATUA 2: Vuta matokeo mapya (masaa 6 ya nyuma - "overlap" ya usalama)
    to_date = date.today().isoformat()
    from_date = (date.today() - timedelta(days=2)).isoformat()  # wigo mpana kidogo, dedup itashughulikia
    print(f"🔍 Kutafuta mechi zilizokwisha: {from_date} hadi {to_date}")
    df_new = fetch_recent_results(from_date, to_date)
    print(f"   Jumla ya mechi mpya zilizopatikana: {len(df_new)}")

    if len(df_new) == 0:
        print("ℹ️ Hakuna mechi mpya zilizokwisha mzunguko huu. Hakuna kilichobadilika.")
        run_stuck_check(from_date, to_date)
        return

    # HATUA 3: Unganisha (dedupe kwa Date+HomeTeam+AwayTeam, epuka kurudia)
    key_cols = ['Date', 'HomeTeam', 'AwayTeam']
    df_existing_keys = set(df_existing[key_cols].apply(tuple, axis=1))
    df_new_unique = df_new[~df_new[key_cols].apply(tuple, axis=1).isin(df_existing_keys)]
    print(f"   Mechi HALISI mpya (baada ya dedupe): {len(df_new_unique)}")

    if len(df_new_unique) == 0:
        print("ℹ️ Mechi zote zilizopatikana tayari zipo kwenye historia. Hakuna kilichobadilika.")
        run_stuck_check(from_date, to_date)
        return

    df_combined = pd.concat([df_existing, df_new_unique], ignore_index=True)
    df_combined = df_combined.sort_values('Date').reset_index(drop=True)
    df_combined.to_csv(csv_path, index=False)
    print(f"✅ Historia mpya: {len(df_combined):,} mechi (+{len(df_new_unique)})")

    # HATUA 4: Pakia toleo JIPYA la dataset kwenye Kaggle
    metadata = {"title": "historical-complete-no-gap", "id": KAGGLE_DATASET,
                "licenses": [{"name": "CC0-1.0"}]}
    with open(os.path.join('kaggle_dataset', 'dataset-metadata.json'), 'w') as f:
        json.dump(metadata, f)

    print("📤 Kupakia toleo jipya la dataset...")
    run_kaggle_cmd(['datasets', 'version', '-p', 'kaggle_dataset',
                     '-m', f"Auto-update {datetime.utcnow().isoformat()} (+{len(df_new_unique)} mechi)"])
    print("🎉 Dataset imesasishwa kikamilifu!")

    # HATUA 5: MPYA - tathmini PredictionRecord za PENDING kwa mechi hizi mpya
    evaluate_pending_predictions(df_new_unique)

    # HATUA 5b: MPYA - tathmini ComboLeg/ComboRecord za PENDING pia
    evaluate_pending_combo_legs(df_new_unique)

    # HATUA 6: UKAGUZI WA UKWELI — mechi zilizokwama (zimepita muda,
    # bado hazina matokeo). Hii HAIACHISHI job (data tayari imesasishwa
    # salama) - ni ONYO tu, kwa mechi ZA KWELI zilizotambuliwa, si makisio.
    run_stuck_check(from_date, to_date)


if __name__ == '__main__':
    main()
