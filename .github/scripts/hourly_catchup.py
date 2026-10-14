"""
.github/scripts/hourly_catchup.py

JOB A — Inaendesha KILA SAA. Inavuta mechi zilizomalizika hivi karibuni
kutoka AllSportsAPI, inaziongeza kwenye 'historical-complete-no-gap'
dataset (Kaggle), na inapakia toleo JIPYA la dataset hiyo - ili Job B
(usiku) ipate historia iliyosasika kila wakati, bila pengo kuunda tena.

UTATHMINI (v2): PredictionRecord / ComboLeg / ComboRecord zote za
PENDING zinatathminiwa KILA MZUNGUKO dhidi ya dataset NZIMA (si mechi mpya
tu). Kwa hiyo hata kama mzunguko uliopita ulishindwa, mzunguko unaofuata
unazirekebisha. Utathmini ukishindwa kuanza (DATABASE_URL haipo, import
imeshindwa), job inakuwa NYEKUNDU (si kijani kimya kimya).

v3 (MAJINA YA TIMU): majina yote ya timu yanapita kwenye team_names.canon()
(chanzo kimoja: team_names.py). Faida:
  - mechi mpya zinaingia kwa jina RASMI (si 'PSG' kwa sehemu na 'Paris Sg' kwa sehemu),
  - kuondoa mechi zinazojirudia kunatumia majina rasmi (jina mbadala halileti nakala),
  - utathmini unalinganisha majina rasmi pande zote mbili (rekodi za zamani zenye jina
    mbadala nazo zinatathminiwa),
  - dataset inapata team_aliases.json (notebooks za Kaggle zinaisoma kuunganisha historia),
  - jina jipya lisilojulikana linaleta ONYO la wazi (::warning::) na pendekezo.
  - mechi zinatafutwa siku 7 nyuma (si 2): Job ikishindwa siku chache, hakuna mechi inayopotea.

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
from collections import Counter
from datetime import datetime, timedelta, date

sys.path.insert(0, os.getcwd())
import team_names

APP_URL = os.environ.get("APP_URL")
REFRESH_SECRET = os.environ.get("REFRESH_SECRET")
KAGGLE_DATASET = os.environ.get("KAGGLE_DATASET_SLUG")

LOOKBACK_DAYS = 7        # mechi zilizokwisha zinatafutwa siku 7 nyuma (dedupe inazuia nakala)
STUCK_DAYS = 2           # ukaguzi wa mechi 'zilizokwama' unabaki siku 2


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

# Kwa utangamano na msimbo wa zamani: ramani ya majina sasa iko team_names.py (chanzo kimoja).
TEAM_NAME_MAPPING = team_names.ALIASES


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
            print(f"   {code}: {len(finished)} mechi zilizokwisha")

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
        df = df.dropna(subset=['Date', 'HomeTeam', 'AwayTeam']).copy()
        # Majina rasmi (chanzo kimoja: team_names.py)
        df['HomeTeam'] = df['HomeTeam'].map(team_names.canon)
        df['AwayTeam'] = df['AwayTeam'].map(team_names.canon)
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
# MAJINA YA TIMU: funguo za mechi (kwa majina rasmi) + onyo la jina jipya
# ============================================================

def canon_keys(df):
    """Ufunguo wa kila mechi: tarehe|nyumbani|ugenini, kwa MAJINA RASMI."""
    d = pd.to_datetime(df['Date'], errors='coerce').dt.strftime('%Y-%m-%d')
    h = df['HomeTeam'].map(team_names.canon).astype(str)
    a = df['AwayTeam'].map(team_names.canon).astype(str)
    return d + '|' + h + '|' + a


def warn_new_team_names(df_existing, df_new):
    """Onya kuhusu majina ya timu yasiyojulikana kwa ligi hiyo (kwa majina rasmi).
    Timu iliyopanda daraja kweli itaonekana mara moja tu; jina mbadala la timu
    ya zamani litaonekana na pendekezo."""
    known = {}
    for lg, g in df_existing.groupby('league'):
        names = pd.concat([g['HomeTeam'], g['AwayTeam']]).dropna().unique()
        known[lg] = {team_names.canon(x) for x in names}
    for lg, g in df_new.groupby('league'):
        names = set(g['HomeTeam']) | set(g['AwayTeam'])
        pool = known.get(lg, set())
        for n in sorted(names - pool):
            hint = team_names.suggest(n, pool)
            extra = f" (huenda ni: {', '.join(hint)})" if hint else ''
            print(f"::warning::Jina jipya la timu {lg}: '{n}'{extra} - ikiwa ni timu ileile "
                  f"ya zamani, ongeza mstari kwenye team_names.py")


# ============================================================
# UTATHMINI (SETTLEMENT) - v2
# ============================================================

def norm_team(name):
    """Jina la timu lililosafishwa: jina rasmi (team_names.canon) + herufi ndogo.
    Inatumika pande zote mbili (rekodi za DB na dataset) ili zilingane, hata
    kama rekodi ya zamani ina jina mbadala."""
    n = str(name or '').strip()
    return str(team_names.canon(n)).lower()


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


def _stamp(obj):
    """Weka settled_at kama model ina field hiyo."""
    if hasattr(obj, 'settled_at'):
        obj.settled_at = datetime.utcnow()


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
            label = result_label(evaluate_market(market, row))
        except Exception as e:
            print(f"   ⚠️ evaluate_market imeshindwa kwa market '{market}' ({home} v {away}): {e}")
            return None, 'error'
        return label, 'ok'

    stats = {'pred_total': 0, 'pred_settled': 0, 'leg_total': 0, 'leg_settled': 0,
             'combos_settled': 0}
    unmatched = []            # mechi zilizopita muda lakini hazina matokeo kwenye dataset
    void_markets = Counter()  # markets zilizosettlewa VOID (takwimu hazipo au soko halijulikani)

    try:
        with app.app_context():
            # --- PredictionRecord ---
            preds = PredictionRecord.query.filter_by(result='PENDING').all()
            stats['pred_total'] = len(preds)
            for rec in preds:
                label, why = settle_one(rec.market, rec.home_team, rec.away_team, rec.match_date)
                if label:
                    rec.result = label
                    _stamp(rec)
                    stats['pred_settled'] += 1
                    if label == 'VOID':
                        void_markets[str(rec.market)] += 1
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
                    if label == 'VOID':
                        void_markets[str(leg.market)] += 1
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
                _stamp(combo)
                stats['combos_settled'] += 1

            if stats['pred_settled'] or stats['leg_settled'] or stats['combos_settled']:
                db.session.commit()
    except Exception as e:
        print(f"::error::Utathmini umeshindwa wakati wa kuhifadhi: {e}")
        return False

    print(f"   Predictions: {stats['pred_settled']}/{stats['pred_total']} zimesettlewa")
    print(f"   Combo legs : {stats['leg_settled']}/{stats['leg_total']} zimesettlewa")
    print(f"   Combos     : {stats['combos_settled']} zimekamilika")

    if void_markets:
        top = ', '.join(f"{m} x{n}" for m, n in void_markets.most_common(10))
        print(f"\nℹ️ Zilizosettlewa kama VOID ({sum(void_markets.values())}) - takwimu hazipo "
              f"au soko halijulikani: {top}")
    if unmatched:
        print(f"\n⚠️ {len(unmatched)} PENDING zimepita muda lakini HAZIKUPATIKANA kwenye dataset "
              f"(angalia majina ya timu/tarehe):")
        for home, away, md, market in unmatched[:30]:
            print(f"   • {home} vs {away} ({md}) [{market}]")
        print(f"::warning::{len(unmatched)} PENDING hazikupatikana kwenye dataset - majina ya timu yanaweza kutofautiana")
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

    # Majina rasmi ya timu: kagua orodha, kisha weka team_aliases.json ndani ya dataset
    for p in team_names.validate_aliases():
        print(f"::warning::team_names.py: {p}")
    aliases_changed = team_names.write_aliases_json('kaggle_dataset')
    if aliases_changed:
        print("📝 team_aliases.json imesasishwa kwenye folda ya dataset (itapakiwa na toleo jipya).")

    today = date.today()
    to_date_str = today.isoformat()
    from_date_str = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    stuck_from_str = (today - timedelta(days=STUCK_DAYS)).isoformat()
    print(f"🔍 Kutafuta mechi zilizokwisha: {from_date_str} hadi {to_date_str}")
    df_new = fetch_recent_results(from_date_str, to_date_str)
    print(f"   Jumla ya mechi zilizopatikana: {len(df_new)}")

    df_results = df_existing   # dataset itakayotumika kutathmini
    upload_ok = True
    need_upload = aliases_changed
    upload_msg = f"Update team_aliases.json {datetime.utcnow().isoformat()}"

    if len(df_new) == 0:
        print("ℹ️ Hakuna mechi mpya zilizokwisha mzunguko huu.")
    else:
        # Nakala zinaondolewa kwa MAJINA RASMI: 'PSG' na 'Paris Sg' ni timu moja
        existing_keys = set(canon_keys(df_existing))
        df_new = df_new.copy()
        df_new['_key'] = canon_keys(df_new)
        df_new_unique = (df_new[~df_new['_key'].isin(existing_keys)]
                         .drop_duplicates('_key').drop(columns='_key'))
        print(f"   Mechi HALISI mpya (baada ya dedupe): {len(df_new_unique)}")

        if len(df_new_unique) == 0:
            print("ℹ️ Mechi zote zilizopatikana tayari zipo kwenye historia.")
        else:
            warn_new_team_names(df_existing, df_new_unique)
            df_combined = pd.concat([df_existing, df_new_unique], ignore_index=True)
            df_combined = df_combined.sort_values('Date').reset_index(drop=True)
            df_combined.to_csv(csv_path, index=False)
            print(f"✅ Historia mpya: {len(df_combined):,} mechi (+{len(df_new_unique)})")
            df_results = df_combined
            need_upload = True
            upload_msg = f"Auto-update {datetime.utcnow().isoformat()} (+{len(df_new_unique)} mechi)"

    if need_upload:
        metadata = {"title": "historical-complete-no-gap", "id": KAGGLE_DATASET,
                    "licenses": [{"name": "CC0-1.0"}]}
        with open(os.path.join('kaggle_dataset', 'dataset-metadata.json'), 'w') as f:
            json.dump(metadata, f)

        print("📤 Kupakia toleo jipya la dataset...")
        out = run_kaggle_cmd(['datasets', 'version', '-p', 'kaggle_dataset', '-m', upload_msg],
                              fatal=False)
        if out is None:
            upload_ok = False
            print("::error::Upload ya dataset imeshindwa - utathmini utaendelea na data ya mzunguko huu.")
        else:
            print("🎉 Dataset imesasishwa kikamilifu!")

    # Utathmini UNAFANYIKA KILA MZUNGUKO, bila kujali kama kuna mechi mpya.
    settle_ok = settle_pending(df_results)
    run_stuck_check(stuck_from_str, to_date_str)

    if not upload_ok or not settle_ok:
        sys.exit(1)


if __name__ == '__main__':
    main()