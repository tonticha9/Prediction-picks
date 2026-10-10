"""
.github/scripts/hourly_catchup.py

JOB A — Inaendesha KILA SAA. Inavuta mechi zilizomalizika hivi karibuni
kutoka AllSportsAPI, inaziongeza kwenye 'historical-complete-no-gap'
dataset (Kaggle), na inapakia toleo JIPYA la dataset hiyo - ili Job B
(usiku) ipate historia iliyosasika kila wakati, bila pengo kuunda tena.

UTATHMINI (MPYA - v2): PredictionRecord / ComboLeg / ComboRecord zote za
PENDING zinatathminiwa KILA MZUNGUKO dhidi ya dataset NZIMA (si mechi mpya
tu). Kwa hiyo hata kama mzunguko uliopita ulishindwa, mzunguko unaofuata
unazirekebisha. Utathmini ukishindwa kuanza (DATABASE_URL haipo, import
imeshindwa), job inakuwa NYEKUNDU (si kijani kimya kimya).

API_KEY inaombwa kutoka Admin (Flask /api/config/allsportsapi-key,
ndicho chanzo cha ukweli), na GITHUB SECRET ni FALLBACK TU.
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

sys.path.insert(0, os.getcwd())

APP_URL = os.environ.get("APP_URL")
REFRESH_SECRET = os.environ.get("REFRESH_SECRET")
KAGGLE_DATASET = os.environ.get("KAGGLE_DATASET_SLUG")


def fetch_api_key_from_admin():
    """Jaribu kupata API key kutoka Admin (chanzo cha ukweli). None
    ikishindikana (Render iko chini, secrets hazipo, n.k) - GITHUB_SECRET
    fallback itatumika badala yake."""
    if not APP_URL or not REFRESH_SECRET:
        return None
    try:
        resp = requests.get(
            f"{APP_URL}/api/config/allsportsapi-key",
            headers={"X-Refresh-Secret": REFRESH_SECRET}, timeout=15
        )
        resp.raise_for_status()
        key = resp.json().get('api_key')
        if key:
            print("✅ API key imepatikana kutoka Admin.")
        return key
    except Exception as e:
        print(f"⚠️ Imeshindwa kupata API key kutoka Admin ({e}) - natumia GitHub Secret fallback.")
        return None


API_KEY = fetch_api_key_from_admin() or os.environ.get("ALLSPORTSAPI_KEY")

missing = []
if not API_KEY:
    missing.append("ALLSPORTSAPI_KEY (Admin wala GitHub Secret havina thamani)")
if not KAGGLE_DATASET:
    missing.append("KAGGLE_DATASET_SLUG")
if missing:
    print(f"❌ HAIPO: {', '.join(missing)}")
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


def run_kaggle_cmd(args, fatal=True):
    """fatal=True: ikishindwa, job inasimama. fatal=False: inarudisha None
    na job inaendelea (ili utathmini ufanyike hata kama upload imeshindwa)."""
    result = subprocess.run(['kaggle'] + args, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"❌ Amri ya kaggle imeshindwa: {result.stderr}")
        if fatal:
            sys.exit(1)
        return None
    return result.stdout


def check_stuck_fixtures(from_date, to_date):
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
            if hours_since_kickoff > 4:
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


# ============================================================
# UTATHMINI (SETTLEMENT) - v2
# ============================================================

def norm_team(name):
    """Jina la timu lililosafishwa: trim + mapping ya API -> dataset + herufi ndogo.
    Inatumika pande zote mbili (rekodi za DB na dataset) ili zilingane."""
    n = str(name or '').strip()
    n = TEAM_NAME_MAPPING.get(n, n)
    return n.lower()


def to_date(v):
    """Badilisha datetime/date/string kuwa date. None ikishindikana."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return pd.Timestamp(v).date()
    except Exception:
        return None


def build_results_index(df):
    """(home, away) -> orodha ya (tarehe, row_dict) kutoka dataset nzima."""
    index = {}
    if df is None or len(df) == 0:
        return index
    for row in df.to_dict('records'):
        d = to_date(row.get('Date'))
        if d is None:
            continue
        key = (norm_team(row.get('HomeTeam')), norm_team(row.get('AwayTeam')))
        index.setdefault(key, []).append((d, row))
    return index


def find_result(index, home, away, match_date):
    """Tafuta matokeo ya mechi. Inakubali tofauti ya siku +/-1 (tofauti za
    saa/timezone kati ya app na API)."""
    if match_date is None:
        return None
    best, best_diff = None, None
    for d, row in index.get((norm_team(home), norm_team(away)), []):
        diff = abs((d - match_date).days)
        if diff <= 1 and (best_diff is None or diff < best_diff):
            best, best_diff = row, diff
    return best


def settle_pending(df_results):
    """Tathmini PredictionRecord, ComboLeg na ComboRecord zote za PENDING
    dhidi ya dataset nzima. Inarudisha True ikiwa kila kitu kiko sawa
    (hata kama hakuna cha kutathmini), False ikiwa utathmini umeshindwa
    kuanza au kuhifadhi."""
    if not os.environ.get('DATABASE_URL'):
        print("::error::DATABASE_URL haipo kwenye workflow - utathmini hauwezi kufanya kazi.")
        return False
    try:
        from market_evaluator import evaluate_market, result_label
        from app import app
        from models import db, PredictionRecord, ComboLeg, ComboRecord
    except Exception as e:
        print(f"::error::Imeshindwa kuunganisha na app/models/market_evaluator: {e}")
        return False

    index = build_results_index(df_results)
    today = date.today()
    print(f"\n🧮 Kutathmini PENDING zote dhidi ya dataset ({len(df_results):,} mechi)...")

    def settle_one(market, home, away, match_date):
        """Rudisha (label, sababu). label None = bado haijaweza kutathminiwa."""
        md = to_date(match_date)
        row = find_result(index, home, away, md)
        if row is None:
            return None, 'no_match'
        try:
            val = evaluate_market(market, row)
            label = result_label(val)
        except Exception as e:
            print(f"   ⚠️ evaluate_market imeshindwa kwa market '{market}' ({home} v {away}): {e}")
            return None, 'error'
        if label == 'PENDING' or label is None:
            return None, 'unknown_market'
        return label, 'ok'

    stats = {'pred_total': 0, 'pred_settled': 0, 'leg_total': 0, 'leg_settled': 0,
             'combos_settled': 0}
    unmatched = []          # mechi zilizopita muda lakini hazina matokeo kwenye dataset
    unknown_markets = set()  # markets ambazo evaluator haizielewi

    try:
        with app.app_context():
            # --- PredictionRecord ---
            preds = PredictionRecord.query.filter_by(result='PENDING').all()
            stats['pred_total'] = len(preds)
            for rec in preds:
                label, why = settle_one(rec.market, rec.home_team, rec.away_team, rec.match_date)
                if label:
                    rec.result = label
                    rec.settled_at = datetime.utcnow()
                    stats['pred_settled'] += 1
                elif why == 'unknown_market':
                    unknown_markets.add(str(rec.market))
                elif why == 'no_match':
                    md = to_date(rec.match_date)
                    if md is not None and md < today:
                        unmatched.append((rec.home_team, rec.away_team, str(md), rec.market))

            # --- ComboLeg ---
            legs = ComboLeg.query.filter_by(result='PENDING').all()
            stats['leg_total'] = len(legs)
            for leg in legs:
                label, why = settle_one(leg.market, leg.home_team, leg.away_team, leg.match_date)
                if label:
                    leg.result = label
                    stats['leg_settled'] += 1
                elif why == 'unknown_market':
                    unknown_markets.add(str(leg.market))
                elif why == 'no_match':
                    md = to_date(leg.match_date)
                    if md is not None and md < today:
                        unmatched.append((leg.home_team, leg.away_team, str(md), leg.market))

            # --- ComboRecord (zote za PENDING, hata za zamani) ---
            for combo in ComboRecord.query.filter_by(result='PENDING').all():
                leg_results = [lg.result for lg in combo.legs]
                if not leg_results or any(x == 'PENDING' for x in leg_results):
                    continue
                if any(x == 'LOST' for x in leg_results):
                    combo.result = 'LOST'
                elif any(x == 'VOID' for x in leg_results):
                    combo.result = 'VOID'
                else:
                    combo.result = 'WON'
                combo.settled_at = datetime.utcnow()
                stats['combos_settled'] += 1

            if stats['pred_settled'] or stats['leg_settled'] or stats['combos_settled']:
                db.session.commit()
    except Exception as e:
        print(f"::error::Utathmini umeshindwa wakati wa kuhifadhi: {e}")
        return False

    print(f"   Predictions: {stats['pred_settled']}/{stats['pred_total']} zimesettlewa")
    print(f"   Combo legs : {stats['leg_settled']}/{stats['leg_total']} zimesettlewa")
    print(f"   Combos     : {stats['combos_settled']} zimekamilika")

    if unmatched:
        print(f"\n⚠️ {len(unmatched)} PENDING zimepita muda lakini HAZIKUPATIKANA kwenye dataset "
              f"(angalia majina ya timu/tarehe):")
        for home, away, md, market in unmatched[:30]:
            print(f"   • {home} vs {away} ({md}) [{market}]")
        print(f"::warning::{len(unmatched)} PENDING hazikupatikana kwenye dataset - majina ya timu yanaweza kutofautiana")
    if unknown_markets:
        print(f"\n⚠️ Markets ambazo market_evaluator haizielewi: {sorted(unknown_markets)}")
        print(f"::warning::Markets zisizoeleweka na evaluator: {sorted(unknown_markets)}")
    return True


def main():
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
    df_existing['Date'] = pd.to_datetime(df_existing['Date'], format='mixed')
    print(f"   Historia ya sasa: {len(df_existing):,} mechi")

    to_date_str = date.today().isoformat()
    from_date_str = (date.today() - timedelta(days=2)).isoformat()
    print(f"🔍 Kutafuta mechi zilizokwisha: {from_date_str} hadi {to_date_str}")
    df_new = fetch_recent_results(from_date_str, to_date_str)
    print(f"   Jumla ya mechi mpya zilizopatikana: {len(df_new)}")

    df_results = df_existing   # dataset itakayotumika kutathmini
    upload_ok = True

    if len(df_new) == 0:
        print("ℹ️ Hakuna mechi mpya zilizokwisha mzunguko huu.")
    else:
        key_cols = ['Date', 'HomeTeam', 'AwayTeam']
        df_existing_keys = set(df_existing[key_cols].apply(tuple, axis=1))
        df_new_unique = df_new[~df_new[key_cols].apply(tuple, axis=1).isin(df_existing_keys)]
        print(f"   Mechi HALISI mpya (baada ya dedupe): {len(df_new_unique)}")

        if len(df_new_unique) == 0:
            print("ℹ️ Mechi zote zilizopatikana tayari zipo kwenye historia.")
        else:
            df_combined = pd.concat([df_existing, df_new_unique], ignore_index=True)
            df_combined = df_combined.sort_values('Date').reset_index(drop=True)
            df_combined.to_csv(csv_path, index=False)
            print(f"✅ Historia mpya: {len(df_combined):,} mechi (+{len(df_new_unique)})")
            df_results = df_combined

            metadata = {"title": "historical-complete-no-gap", "id": KAGGLE_DATASET,
                        "licenses": [{"name": "CC0-1.0"}]}
            with open(os.path.join('kaggle_dataset', 'dataset-metadata.json'), 'w') as f:
                json.dump(metadata, f)

            print("📤 Kupakia toleo jipya la dataset...")
            out = run_kaggle_cmd(['datasets', 'version', '-p', 'kaggle_dataset',
                                   '-m', f"Auto-update {datetime.utcnow().isoformat()} (+{len(df_new_unique)} mechi)"],
                                  fatal=False)
            if out is None:
                upload_ok = False
                print("::error::Upload ya dataset imeshindwa - utathmini utaendelea na data ya mzunguko huu.")
            else:
                print("🎉 Dataset imesasishwa kikamilifu!")

    # Utathmini UNAFANYIKA KILA MZUNGUKO, bila kujali kama kuna mechi mpya.
    settle_ok = settle_pending(df_results)
    run_stuck_check(from_date_str, to_date_str)

    if not upload_ok or not settle_ok:
        sys.exit(1)


if __name__ == '__main__':
    main()
