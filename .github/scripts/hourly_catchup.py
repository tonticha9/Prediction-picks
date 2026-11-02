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

v4 (STATS KUTOKA football-data.co.uk):
  - Dataset ikikosa safu HF (mara ya kwanza): inajaza historia YOTE (2014 hadi sasa) kutoka
    football-data: shots, SOT, corners, kadi, fouls (HF/AF) na Referee. Inajaza SEHEMU TUPU
    tu; thamani zilizopo hazibadilishwi.
  - Baada ya hapo: msimu huu na uliopita unasawazishwa mara 4 kwa siku (na kila run ya mkono).
    FD_FULL=1 inalazimisha usawazishaji kamili tena; FD_SYNC=1 inalazimisha wa kawaida.
  - Kadi 'sifuri za uongo' (API haikutoa takwimu wala kadi) zinakuwa tupu (NaN), si 0.
  - HF/AF/Referee vinahifadhiwa kutoka API pia (mechi mpya).
  - Ulinzi: CSV lazima iitwe historical_complete_no_gap.csv; dataset ikiwa na mechi chini ya
    MIN_ROWS, job inasimama (haiandiki juu ya historia).
  - Mechi za football-data zisizolingana na dataset zinaleta ::warning:: (majina/tarehe).

v5 (ODDS ZA BET365 ZA HISTORIA):
  - Safu 10 MPYA zenye kiambishi 'fdo_' (football-data odds), ili pipeline ya v6 isizichukue
    kwa bahati mbaya:
      fdo_B365H / D / A        = odds za 1X2 za Bet365 (kabla ya mechi)
      fdo_B365O25 / U25        = Bet365 over/under 2.5
      fdo_AvgH / D / A         = wastani wa soko 1X2 (BbAv* kwa misimu ya zamani)
      fdo_AvgO25 / U25         = wastani wa soko over/under 2.5
  - Dataset ikikosa safu fdo_B365H (mara ya kwanza): inajaza historia YOTE (2014 hadi sasa).
    Baada ya hapo: msimu huu na uliopita (kama stats). Inajaza SEHEMU TUPU tu.
  - Odds zisizo na maana (chini ya 1.01 au zaidi ya 100) zinakuwa tupu.
  - Ripoti ya uthabiti: coverage kwa msimu + overround ya 1X2 (inaonekana kwenye logi).

v6 (UPAKUAJI WA AKIBA): Kaggle ikishindwa kupakua dataset NZIMA (404 kwa toleo fulani,
  wakati kila faili inapakuliwa vizuri), script inapakua faili moja moja
  (historical_complete_no_gap.csv na team_aliases.json).

v7 (UIMARA WA API + USIRI WA KEY):
  - Kila ombi la AllSportsAPI linajaribiwa mara 3 (502/5xx/429/timeout), na kusubiri kati.
  - API key inafichwa kwenye logi ya GitHub (::add-mask::) na kwenye ujumbe wa makosa.
  - Ligi 4 mfululizo zikishindwa, API inahesabiwa iko chini: naruka zilizobaki (kuokoa muda).
  - Ligi 6+ zikishindwa kwenye utafutaji wa matokeo, run inakuwa NYEKUNDU (si kijani cha uongo).
  - Ukaguzi wa 'stuck fixtures' hausemi 'sawa' ukishindwa.
  - Hali ya API inahifadhiwa kwenye flags (job_a_api_failed_leagues, job_a_api_last_check).

v8 (USAHIHI WA MATOKEO YA MASOKO):
  - Kadi: API ikitoa takwimu bila orodha ya kadi, njano zinachukuliwa kutoka takwimu na nyekundu
    zinabaki TUPU (si 0 ya uongo). football-data ikiwa na kadi NYINGI kuliko dataset (msimu huu na
    uliopita), kadi za football-data zinatumika (API ilitoa pungufu).
  - HTHG/HTAG (nusu-muda) zinajazwa kutoka football-data (masoko ya nusu hayawi VOID bila sababu).
  - Usawazishaji KAMILI wa football-data unafanyika mara moja zaidi (alama: fd_markers.json ndani
    ya dataset), ili HT na kadi ziwekwe kwa historia yote.
  - Mechi za siku VOID_GRACE_DAYS zilizopita hazipewi VOID mara moja: zinabaki PENDING mpaka
    data ijazwe. Rekodi za siku RECHECK_DAYS zilizopita zinakaguliwa upya kila mzunguko.
  - Mara moja tu (flag ya DB 'resettle_version'): HISTORIA YOTE ya predictions/legs/combos
    inatathminiwa upya kwa evaluator mpya. Logi inaonyesha marekebisho yote kwa soko.
  - market_evaluator.selftest() lazima ipite, vinginevyo historia haiguswi na run inakuwa nyekundu.
  - Masoko yasiyo na evaluator yanaorodheshwa kwenye logi.

API_KEY inaombwa kutoka Admin (Flask /api/config/allsportsapi-key,
ndicho chanzo cha ukweli), na GITHUB SECRET ni FALLBACK TU.
"""
import io
import os
import re
import sys
import time
import json
import zipfile
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

DATASET_CSV = 'historical_complete_no_gap.csv'
MIN_ROWS = 45000         # dataset ikiwa chini ya hii, kuna tatizo - usiandike juu yake

# v7: uimara wa API
API_RETRIES = 3          # majaribio kwa kila ombi
API_BACKOFF = (2, 6)     # sekunde za kusubiri kati ya majaribio
CIRCUIT_BREAKER = 4      # ligi 4 mfululizo zikishindwa = API iko chini
API_FAIL_THRESHOLD = 6   # ligi 6+ zikishindwa kwenye utafutaji wa matokeo = run nyekundu

# v8: usahihi wa matokeo
VOID_GRACE_DAYS = 4      # mechi changa hazipewi VOID mara moja (data inaweza kujazwa baadaye)
RECHECK_DAYS = 10        # rekodi zilizosettlewa ndani ya siku hizi zinakaguliwa upya kila mzunguko
RESETTLE_VERSION = 'v8-2026-11-02'   # flag ya DB: historia yote inasettlewa upya mara moja
MARKER_FILE = 'fd_markers.json'      # alama ndani ya dataset: usawazishaji kamili wa v8 umefanyika
MARKER_VERSION = 'v8-ht-cards'

FD_BASE = "https://www.football-data.co.uk/mmz4281"
FD_FIRST_SEASON = 2014
FD_NUM = ['HS', 'AS', 'HST', 'AST', 'HC', 'AC', 'HY', 'AY', 'HR', 'AR', 'HF', 'AF', 'HTHG', 'HTAG']
CARD_COLS = ['HY', 'AY', 'HR', 'AR']
NOSTAT_COLS = ['HC', 'AC', 'HS', 'AS', 'HST', 'AST']
DIAG_COLS = ['HY', 'AY', 'HR', 'AR', 'HC', 'AC']

# v5: odds za football-data -> safu zenye kiambishi 'fdo_'.
# Kila safu ina orodha ya majina ya chanzo (la kwanza lenye thamani linatumika).
FDO_MAP = {
    'fdo_B365H':   ['B365H'],
    'fdo_B365D':   ['B365D'],
    'fdo_B365A':   ['B365A'],
    'fdo_B365O25': ['B365>2.5'],
    'fdo_B365U25': ['B365<2.5'],
    'fdo_AvgH':    ['AvgH', 'BbAvH'],
    'fdo_AvgD':    ['AvgD', 'BbAvD'],
    'fdo_AvgA':    ['AvgA', 'BbAvA'],
    'fdo_AvgO25':  ['Avg>2.5', 'BbAv>2.5'],
    'fdo_AvgU25':  ['Avg<2.5', 'BbAv<2.5'],
}
FDO_COLS = list(FDO_MAP.keys())
ODDS_MIN = 1.01
ODDS_MAX = 100.0


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

# v7: ficha key kwenye logi zote za GitHub kuanzia hapa (key ya Admin haifichwi na GitHub yenyewe)
if API_KEY:
    print(f"::add-mask::{API_KEY}")

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


# ============================================================
# API: usiri wa key + majaribio + mzunguko wa ligi (v7)
# ============================================================

def redact(text):
    """Ficha API key kwenye maandishi yoyote (ujumbe wa makosa una URL kamili)."""
    s = str(text)
    if API_KEY:
        s = s.replace(API_KEY, '***')
    return re.sub(r'(APIkey=)[^&\s)]+', r'\1***', s)


def api_get_fixtures(league_key, d_from, d_to):
    """Omba mechi za ligi moja. Hujaribu mara API_RETRIES kwa makosa ya muda
    (5xx, 429, timeout, muunganisho). Kosa la 4xx (mfano key mbaya) halijaribiwi tena.
    Inarudisha dict ya JSON, au inatupa RuntimeError yenye sababu (key imefichwa)."""
    params = {"met": "Fixtures", "APIkey": API_KEY, "leagueId": league_key,
              "from": d_from, "to": d_to}
    last = 'haijulikani'
    for attempt in range(1, API_RETRIES + 1):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
        except requests.RequestException as e:
            last = redact(e)
        else:
            if resp.status_code < 400:
                try:
                    return resp.json()
                except ValueError:
                    last = 'jibu si JSON'
            else:
                last = f"HTTP {resp.status_code} {resp.reason}"
                if resp.status_code < 500 and resp.status_code != 429:
                    break   # kosa la mteja (mfano key mbaya): kujaribu tena hakusaidii
        if attempt < API_RETRIES:
            time.sleep(API_BACKOFF[min(attempt - 1, len(API_BACKOFF) - 1)])
    raise RuntimeError(last)


def fetch_all_leagues(d_from, d_to, label):
    """Pitia ligi zote 12. Inarudisha (matokeo_kwa_ligi, orodha_ya_ligi_zilizoshindwa).
    Ligi CIRCUIT_BREAKER mfululizo zikishindwa, zilizobaki zinarukwa na kuhesabiwa kama zimeshindwa."""
    results, failed = {}, []
    consecutive = 0
    items = list(LEAGUE_MAP.items())
    for i, (code, league_key) in enumerate(items):
        if consecutive >= CIRCUIT_BREAKER:
            rest = [c for c, _ in items[i:]]
            failed.extend(rest)
            print(f"   ⛔ API inaonekana iko chini (ligi {consecutive} mfululizo zimeshindwa) "
                  f"- naruka ligi {len(rest)} zilizobaki.")
            break
        try:
            data = api_get_fixtures(league_key, d_from, d_to)
            results[code] = data.get('result', []) or []
            consecutive = 0
        except Exception as e:
            failed.append(code)
            consecutive += 1
            print(f"   ⚠️ {code}: {label} limeshindwa ({redact(e)})")
        time.sleep(0.3)
    return results, failed


def record_api_status(failed_fetch):
    """Hifadhi hali ya API kwenye flags za app (Admin Health inaweza kuisoma baadaye).
    Haiwezi kuvunja job: makosa yoyote yanaishia kuwa onyo."""
    if not os.environ.get('DATABASE_URL'):
        return
    try:
        from app import app, set_flag
        with app.app_context():
            set_flag('job_a_api_failed_leagues', str(len(failed_fetch)))
            set_flag('job_a_api_failed_list', ','.join(failed_fetch) if failed_fetch else '-')
            set_flag('job_a_api_last_check', datetime.utcnow().isoformat(timespec='seconds'))
    except Exception as e:
        print(f"::warning::Imeshindwa kuhifadhi hali ya API kwenye flags (haiathiri job): {redact(e)}")


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
    """Rudisha (DataFrame ya mechi zilizokwisha, orodha ya ligi zilizoshindwa)."""
    results, failed = fetch_all_leagues(from_date, to_date, "ombi")
    all_matches = []
    for code, fixtures in results.items():
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

            stats_list = f.get('statistics', []) or []
            cards_list = f.get('cards', []) or []
            stats = parse_statistics(stats_list)
            if cards_list:
                hy, ay, hr, ar = parse_cards(cards_list)
            else:
                # v8: hakuna orodha ya kadi - SI sifuri (hatujui). Njano kutoka takwimu ikiwa
                # zipo; nyekundu hazijulikani. football-data inajaza baadaye.
                yh = stats['home'].get('yellow_cards')
                ya = stats['away'].get('yellow_cards')
                hy = yh if (yh is not None and not pd.isna(yh)) else np.nan
                ay = ya if (ya is not None and not pd.isna(ya)) else np.nan
                hr = ar = np.nan

            all_matches.append({
                'Date': f.get('event_date'), 'HomeTeam': f.get('event_home_team'),
                'AwayTeam': f.get('event_away_team'), 'FTHG': fthg, 'FTAG': ftag,
                'FTR': 'H' if fthg > ftag else ('A' if ftag > fthg else 'D'),
                'HTHG': hthg, 'HTAG': htag, 'league': code,
                'HC': stats['home'].get('corners'), 'AC': stats['away'].get('corners'),
                'HS': stats['home'].get('shots_total'), 'AS': stats['away'].get('shots_total'),
                'HST': stats['home'].get('shots_on_target'), 'AST': stats['away'].get('shots_on_target'),
                'HY': hy, 'AY': ay, 'HR': hr, 'AR': ar,
                'HF': stats['home'].get('fouls'), 'AF': stats['away'].get('fouls'),
                'Referee': (f.get('event_referee') or np.nan),
            })

    df = pd.DataFrame(all_matches)
    if len(df) > 0:
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
        df = df.dropna(subset=['Date', 'HomeTeam', 'AwayTeam']).copy()
        # Majina rasmi (chanzo kimoja: team_names.py)
        df['HomeTeam'] = df['HomeTeam'].map(team_names.canon)
        df['AwayTeam'] = df['AwayTeam'].map(team_names.canon)
    return df, failed


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


def download_dataset():
    """Pakua dataset ya sasa kwenye folda 'kaggle_dataset'.
    Kwanza njia ya kawaida (dataset nzima). Ikishindwa (Kaggle inaweza kujibu 404 kwa
    upakuaji wa dataset nzima wakati kila faili inapakuliwa vizuri), tumia upakuaji wa
    faili moja moja."""
    os.makedirs('kaggle_dataset', exist_ok=True)
    out = run_kaggle_cmd(['datasets', 'download', '-d', KAGGLE_DATASET,
                          '-p', 'kaggle_dataset', '--unzip', '-o'], fatal=False)
    if out is not None and os.path.exists(os.path.join('kaggle_dataset', DATASET_CSV)):
        return

    print("::warning::Upakuaji wa dataset nzima umeshindwa - natumia upakuaji wa faili moja moja.")
    for fname in (DATASET_CSV, 'team_aliases.json', MARKER_FILE):
        res = run_kaggle_cmd(['datasets', 'download', '-d', KAGGLE_DATASET, '-f', fname,
                              '-p', 'kaggle_dataset', '-o'], fatal=(fname == DATASET_CSV))
        if res is None:
            print(f"::warning::Faili {fname} haikupakuliwa.")
        else:
            print(f"   ✅ {fname} imepakuliwa")

    # Ikiwa faili imekuja kama zip, ifungue
    for z in [f for f in os.listdir('kaggle_dataset') if f.endswith('.zip')]:
        zp = os.path.join('kaggle_dataset', z)
        try:
            with zipfile.ZipFile(zp) as zf:
                zf.extractall('kaggle_dataset')
            os.remove(zp)
        except Exception as e:
            print(f"::warning::Imeshindwa kufungua {z}: {e}")


def check_stuck_fixtures(from_date, to_date):
    """Rudisha (orodha ya mechi zilizokwama, orodha ya ligi zilizoshindwa kukaguliwa)."""
    results, failed = fetch_all_leagues(from_date, to_date, "ukaguzi wa 'stuck fixtures'")
    stuck = []
    now = datetime.utcnow()
    for code, fixtures in results.items():
        for f in fixtures:
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
    return stuck, failed


def run_stuck_check(from_date, to_date):
    print("\n🔍 Kukagua mechi 'zilizokwama' (zimepita masaa 4+ bila matokeo)...")
    stuck, failed = check_stuck_fixtures(from_date, to_date)
    if stuck:
        print(f"\n⚠️  MECHI {len(stuck)} ZIMEKWAMA (zimepita muda, bado hazina matokeo):")
        for code, home, away, edate, etime, status in stuck:
            msg = f"{code}: {home} vs {away} ({edate} {etime}) - status: '{status}'"
            print(f"   • {msg}")
            print(f"::warning::Mechi imekwama: {msg}")
    if failed:
        print(f"::warning::Ukaguzi wa mechi zilizokwama HAUKUKAMILIKA: ligi {len(failed)}/{len(LEAGUE_MAP)} "
              f"zimeshindwa ({', '.join(failed)}).")
    elif not stuck:
        print("✅ Hakuna mechi zilizokwama - kila kitu kiko sawa.")
    return failed


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
# ALAMA YA USAWAZISHAJI KAMILI (v8): faili ndani ya dataset
# ============================================================

def read_marker():
    try:
        with open(os.path.join('kaggle_dataset', MARKER_FILE), encoding='utf-8') as f:
            return json.load(f).get('version')
    except Exception:
        return None


def write_marker():
    with open(os.path.join('kaggle_dataset', MARKER_FILE), 'w', encoding='utf-8') as f:
        json.dump({'version': MARKER_VERSION,
                   'time': datetime.utcnow().isoformat(timespec='seconds')}, f)


# ============================================================
# STATS + ODDS KUTOKA football-data.co.uk (v4 + v5 + v8)
# ============================================================

def _k(n):
    return re.sub(r'[^a-z0-9]', '', str(n).lower())


_ALIAS_K = {_k(k): v for k, v in team_names.ALIASES.items()}


def tkey(name):
    """Ufunguo wa jina la timu: jina rasmi, herufi ndogo, bila alama (ule ule
    uliothibitishwa kwenye notebook: mechi 100% zililingana)."""
    return _k(_ALIAS_K.get(_k(name), name))


def fetch_football_data(seasons):
    """Pakua faili za ligi zote kwa misimu iliyotolewa (2014 = msimu 14/15).
    v5: pia odds za Bet365 na wastani wa soko (safu fdo_*).
    Inarudisha (DataFrame, faili_zilizofanikiwa, faili_zote)."""
    frames = []
    headers = {'User-Agent': 'Mozilla/5.0'}
    ok_total = 0
    tot_total = 0
    for s in seasons:
        code = f"{str(s)[2:]}{str(s + 1)[2:]}"
        ok = 0
        for lg in LEAGUE_MAP:
            tot_total += 1
            url = f"{FD_BASE}/{code}/{lg}.csv"
            try:
                resp = requests.get(url, headers=headers, timeout=30)
                resp.raise_for_status()
                x = pd.read_csv(io.StringIO(resp.content.decode('latin-1')), on_bad_lines='skip')
            except Exception as e:
                print(f"   ⚠️ football-data {lg} {code}: {e}")
                continue
            if 'HomeTeam' not in x.columns or 'AwayTeam' not in x.columns:
                continue
            x = x.dropna(subset=['HomeTeam', 'AwayTeam'])
            x['league'] = lg
            for c in FD_NUM:
                x[c] = pd.to_numeric(x[c], errors='coerce') if c in x.columns else np.nan
            if 'Referee' not in x.columns:
                x['Referee'] = np.nan
            # v5: odds -> fdo_* (chanzo la kwanza lenye thamani; thamani zisizo na maana = tupu)
            for out_col, srcs in FDO_MAP.items():
                s_odds = pd.Series(np.nan, index=x.index, dtype='float64')
                for src in srcs:
                    if src in x.columns:
                        s_odds = s_odds.fillna(pd.to_numeric(x[src], errors='coerce'))
                x[out_col] = s_odds.where((s_odds >= ODDS_MIN) & (s_odds <= ODDS_MAX))
            frames.append(x[['league', 'Date', 'HomeTeam', 'AwayTeam']
                            + FD_NUM + ['Referee'] + FDO_COLS])
            ok += 1
            ok_total += 1
            time.sleep(0.2)
        print(f"   football-data msimu {s}/{s + 1}: ligi {ok}/{len(LEAGUE_MAP)}")
    if not frames:
        return pd.DataFrame(), ok_total, tot_total
    fd = pd.concat(frames, ignore_index=True)
    fd['dt'] = pd.to_datetime(fd['Date'], dayfirst=True, format='mixed', errors='coerce').dt.normalize()
    fd = fd.dropna(subset=['dt'])
    fd['hk'] = fd['HomeTeam'].map(tkey)
    fd['ak'] = fd['AwayTeam'].map(tkey)
    return fd, ok_total, tot_total


def football_data_mode(df):
    """Rudisha (full, do_sync). full=True: dataset haina HF au fdo_B365H bado, alama ya v8
    haipo (HT + kadi bado hazijarekebishwa kwa historia yote), au FD_FULL=1."""
    full = (('HF' not in df.columns) or ('fdo_B365H' not in df.columns)
            or os.environ.get('FD_FULL') == '1' or read_marker() != MARKER_VERSION)
    manual = os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
    forced = os.environ.get('FD_SYNC') == '1'
    scheduled = datetime.utcnow().hour % 6 == 0
    return full, (full or manual or forced or scheduled)


def print_odds_coverage(df):
    """Ripoti fupi kwenye logi: coverage ya odds kwa msimu + ukaguzi wa overround."""
    d = pd.to_datetime(df['Date'], errors='coerce')
    season = d.dt.year.where(d.dt.month >= 7, d.dt.year - 1)
    played = d <= pd.Timestamp(date.today())
    print("   Bet365 odds coverage (mechi zilizochezwa):")
    for s in sorted(season[played].dropna().unique()):
        m = played & (season == s)
        n = int(m.sum())
        if n == 0:
            continue
        p1 = df.loc[m, 'fdo_B365H'].notna().mean() * 100
        p2 = df.loc[m, 'fdo_B365O25'].notna().mean() * 100
        print(f"     {int(s)}/{str(int(s) + 1)[2:]}: 1X2 {p1:.0f}% | O/U 2.5 {p2:.0f}% (n={n})")
    h, dr, a = df['fdo_B365H'], df['fdo_B365D'], df['fdo_B365A']
    ok = h.notna() & dr.notna() & a.notna()
    if ok.any():
        ov = 1 / h[ok] + 1 / dr[ok] + 1 / a[ok]
        bad = ((ov < 1.0) | (ov > 1.25)).mean() * 100
        print(f"   Overround ya B365 1X2: wastani {ov.mean():.3f}; nje ya 1.00-1.25: {bad:.2f}%")
        if bad > 1.0:
            print(f"::warning::Odds za B365: {bad:.1f}% ziko nje ya overround ya kawaida (1.00-1.25) - angalia data.")


def print_ht_coverage(df):
    """Ripoti fupi: nusu-muda (HTHG/HTAG) zilizopo kwa msimu."""
    d = pd.to_datetime(df['Date'], errors='coerce')
    season = d.dt.year.where(d.dt.month >= 7, d.dt.year - 1)
    played = d <= pd.Timestamp(date.today())
    print("   Nusu-muda (HTHG/HTAG) zilizopo kwa msimu:")
    for s in sorted(season[played].dropna().unique())[-4:]:
        m = played & (season == s)
        n = int(m.sum())
        if n == 0:
            continue
        p = (df.loc[m, 'HTHG'].notna() & df.loc[m, 'HTAG'].notna()).mean() * 100
        print(f"     {int(s)}/{str(int(s) + 1)[2:]}: {p:.1f}% (n={n})")


def sync_stats_from_football_data(df_in, full):
    """Jaza SEHEMU TUPU za stats (na HF/AF/HT/Referee) na odds (fdo_*) kutoka football-data.
    Haibadilishi thamani zilizopo, ISIPOKUWA kadi za msimu huu na uliopita pale football-data
    ina kadi NYINGI kuliko dataset (API ilitoa pungufu). Pia inabadilisha kadi 'sifuri za uongo'
    kuwa tupu. Inarudisha (df, idadi_ya_mabadiliko, maelezo, fd_ok)."""
    df = df_in.copy()
    n_before = len(df)
    for c in FD_NUM:
        if c not in df.columns:
            df[c] = np.nan
        df[c] = pd.to_numeric(df[c], errors='coerce')
    if 'Referee' not in df.columns:
        df['Referee'] = np.nan
    for c in FDO_COLS:
        if c not in df.columns:
            df[c] = np.nan
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['Referee'] = df['Referee'].astype('object')

    d = pd.to_datetime(df['Date'], errors='coerce').dt.normalize()
    season = d.dt.year.where(d.dt.month >= 7, d.dt.year - 1)
    cur = season.max()
    if pd.isna(cur):
        return df_in, 0, 'hakuna tarehe', False
    cur = int(cur)
    seasons = list(range(FD_FIRST_SEASON, cur + 1)) if full else [cur - 1, cur]
    mask = season.isin(seasons)
    recent = season.isin([cur - 1, cur])
    label = f"kamili {seasons[0]}-{cur}" if full else f"msimu {cur - 1}-{cur}"
    print(f"\n📊 Usawazishaji wa stats + odds kutoka football-data ({label})...")

    changes = 0

    # 1) Kadi 'sifuri za uongo': hakuna takwimu yoyote na kadi zote nne ni 0
    nostat = df[NOSTAT_COLS].isna().all(axis=1)
    zero_cards = (df[CARD_COLS].fillna(-1) == 0).all(axis=1)
    fake = mask & nostat & zero_cards
    if fake.any():
        df.loc[fake, CARD_COLS] = np.nan
        changes += int(fake.sum())
        print(f"   Kadi 'sifuri za uongo' zilizofanywa tupu: {int(fake.sum())}")

    # 2) Pakua football-data
    fd, ok_files, tot_files = fetch_football_data(seasons)
    fd_ok = tot_files > 0 and ok_files >= 0.9 * tot_files
    if len(fd) == 0:
        print("::warning::football-data: hakuna data iliyopatikana - naruka kujaza.")
        return (df if changes else df_in), changes, label, False
    if not fd_ok:
        print(f"::warning::football-data: faili {ok_files}/{tot_files} tu zimepatikana - "
              f"usawazishaji hauhesabiwi kamili.")

    look = {(r.league, r.dt, r.hk, r.ak): r for r in fd.itertuples(index=False)}
    hk = df['HomeTeam'].map(tkey)
    ak = df['AwayTeam'].map(tkey)

    fill_cols = FD_NUM + FDO_COLS
    used = set()
    filled = 0
    mism = Counter()
    card_fix = 0
    card_examples = []
    for i in df.index[mask]:
        r_dt = d.at[i]
        if pd.isna(r_dt):
            continue
        f = None
        for off in (0, -1, 1):
            k = (df.at[i, 'league'], r_dt + pd.Timedelta(days=off), hk.at[i], ak.at[i])
            f = look.get(k)
            if f is not None:
                used.add(k)
                break
        if f is None:
            continue

        # Ukaguzi: tofauti kati ya dataset na football-data (kabla ya marekebisho)
        for c in DIAG_COLS:
            cv, fv = df.at[i, c], getattr(f, c)
            if not pd.isna(cv) and not pd.isna(fv) and cv != fv:
                mism[c] += 1

        # Kadi: football-data ikiwa na kadi NYINGI kuliko dataset (msimu huu na uliopita), itumike
        if recent.at[i]:
            cur_c = [df.at[i, c] for c in CARD_COLS]
            fd_c = [getattr(f, c) for c in CARD_COLS]
            if not any(pd.isna(x) for x in cur_c + fd_c) and sum(fd_c) > sum(cur_c):
                card_fix += 1
                if len(card_examples) < 12:
                    card_examples.append(
                        f"{df.at[i, 'league']}: {df.at[i, 'HomeTeam']} vs {df.at[i, 'AwayTeam']} "
                        f"({r_dt:%Y-%m-%d}) kadi (HY,AY,HR,AR) "
                        f"{tuple(int(x) for x in cur_c)} -> {tuple(int(x) for x in fd_c)}")
                for c, v in zip(CARD_COLS, fd_c):
                    df.at[i, c] = v

        for c in fill_cols:
            v = getattr(f, c)
            if pd.isna(df.at[i, c]) and not pd.isna(v):
                df.at[i, c] = v
                filled += 1
        ref = getattr(f, 'Referee')
        if pd.isna(df.at[i, 'Referee']) and isinstance(ref, str) and ref.strip():
            df.at[i, 'Referee'] = ref.strip()
            filled += 1
    changes += filled + card_fix
    print(f"   Seli zilizojazwa (stats + odds + nusu-muda): {filled:,}")
    if card_fix:
        print(f"   Kadi zilizorekebishwa kutoka football-data (dataset ilikuwa na pungufu): {card_fix:,}")
        for ex in card_examples:
            print(f"     • {ex}")
    if mism:
        print("   Ukaguzi - seli zenye thamani tofauti na football-data (kabla ya marekebisho): "
              + ", ".join(f"{c}={n:,}" for c, n in mism.most_common()))

    # Ripoti ya odds + nusu-muda
    if full or filled:
        try:
            print_odds_coverage(df)
            print_ht_coverage(df)
        except Exception as e:
            print(f"::warning::Ripoti ya odds/HT imeshindwa (haiathiri data): {e}")

    # 3) Mechi za football-data zisizolingana na dataset (zaidi ya siku 3 zilizopita)
    cutoff = pd.Timestamp(date.today() - timedelta(days=3))
    bad = [r for r in fd.itertuples(index=False)
           if r.dt < cutoff and (r.league, r.dt, r.hk, r.ak) not in used]
    if bad:
        print(f"::warning::football-data: mechi {len(bad)} hazikulingana na dataset "
              f"(huenda majina ya timu au playoffs). Mifano:")
        for r in bad[:10]:
            print(f"   • {r.league}: {r.HomeTeam} vs {r.AwayTeam} ({r.dt:%Y-%m-%d})")

    if len(df) != n_before:
        raise RuntimeError("idadi ya mechi imebadilika wakati wa usawazishaji (haipaswi)")
    return df, changes, label, fd_ok


# ============================================================
# UTATHMINI (SETTLEMENT) - v8
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


def resettle_needed():
    """True ikiwa flag ya DB 'resettle_version' si ya toleo la sasa (historia yote isettlewe upya)."""
    if not os.environ.get('DATABASE_URL'):
        return False
    try:
        from app import app, get_flag
        with app.app_context():
            return get_flag('resettle_version', '') != RESETTLE_VERSION
    except Exception as e:
        print(f"::warning::Imeshindwa kusoma flag ya resettle (naendelea kawaida): {redact(e)}")
        return False


def mark_resettled():
    try:
        from app import app, set_flag
        with app.app_context():
            set_flag('resettle_version', RESETTLE_VERSION)
    except Exception as e:
        print(f"::warning::Imeshindwa kuhifadhi flag ya resettle: {redact(e)}")


def settle_pending(df_results, recheck_days=RECHECK_DAYS):
    """Tathmini PredictionRecord, ComboLeg na ComboRecord dhidi ya dataset nzima.
    recheck_days=N: PENDING zote + zilizosettlewa ndani ya siku N (zinarekebishwa ikiwa data
    imebadilika). recheck_days=None: HISTORIA YOTE. Inarudisha True ikiwa kila kitu kiko sawa,
    False ikiwa utathmini umeshindwa kuanza au kuhifadhi."""
    if not os.environ.get('DATABASE_URL'):
        print("::error::DATABASE_URL haipo kwenye workflow - utathmini hauwezi kufanya kazi.")
        return False
    try:
        import market_evaluator
        from market_evaluator import evaluate_market, result_label
        from sqlalchemy import or_
        from app import app
        from models import db, PredictionRecord, ComboLeg, ComboRecord
    except Exception as e:
        print(f"::error::Imeshindwa kuunganisha na app/models/market_evaluator: {e}")
        return False

    selftest = getattr(market_evaluator, 'selftest', None)
    if selftest is None:
        print("::error::market_evaluator.py ni ya ZAMANI (haina selftest). Weka toleo jipya (v2). "
              "Utathmini umesimamishwa, historia haijaguswa.")
        return False
    problems = selftest()
    if problems:
        for p in problems:
            print(f"::error::market_evaluator selftest: {p}")
        print("::error::market_evaluator.py ina kasoro - utathmini umesimamishwa, historia haijaguswa.")
        return False
    market_evaluator.UNKNOWN_MARKETS.clear()

    index = build_results_index(df_results)
    today = date.today()
    if recheck_days is None:
        scope = "HISTORIA YOTE"
        cutoff_dt = None
    else:
        scope = f"PENDING + zilizosettlewa siku {recheck_days} zilizopita"
        cutoff_dt = datetime.combine(today - timedelta(days=recheck_days), datetime.min.time())
    print(f"\n🧮 Kutathmini ({scope}) dhidi ya dataset ({len(df_results):,} mechi)...")

    stats = Counter()
    unmatched = []            # PENDING zilizopita muda lakini hazina matokeo kwenye dataset
    void_markets = Counter()  # markets zilizosettlewa VOID (takwimu hazipo au soko halijulikani)
    corrections = Counter()   # (soko, kabla, baada) -> idadi
    samples = []

    def new_label(market, home, away, match_date):
        """Rudisha (label, sababu). label None = haiwezi kutathminiwa sasa."""
        md = to_date(match_date)
        row = find_result(index, home, away, md)
        if row is None:
            return None, 'no_match'
        try:
            label = result_label(evaluate_market(market, row))
        except Exception as e:
            print(f"   ⚠️ evaluate_market imeshindwa kwa market '{market}' ({home} v {away}): {e}")
            return None, 'error'
        if label == 'VOID' and md is not None and (today - md).days <= VOID_GRACE_DAYS:
            return 'PENDING', 'deferred'   # data inaweza kujazwa (football-data): subiri
        return label, 'ok'

    def process(rec, kind):
        stats[kind + '_total'] += 1
        cur = rec.result
        label, why = new_label(rec.market, rec.home_team, rec.away_team, rec.match_date)
        if why == 'no_match':
            if cur == 'PENDING':
                md = to_date(rec.match_date)
                if md is not None and md < today:
                    unmatched.append((rec.home_team, rec.away_team, str(md), rec.market))
            return
        if label is None:
            return
        if why == 'deferred':
            stats['deferred'] += 1
        if label == cur:
            return
        rec.result = label
        _stamp(rec)
        if cur == 'PENDING':
            stats[kind + '_settled'] += 1
            if label == 'VOID':
                void_markets[str(rec.market)] += 1
        else:
            stats[kind + '_corrected'] += 1
            corrections[(str(rec.market), cur, label)] += 1
            if len(samples) < 20:
                samples.append(f"{rec.home_team} v {rec.away_team} ({to_date(rec.match_date)}) "
                               f"[{rec.market}] {cur} -> {label}")

    try:
        with app.app_context():
            q = PredictionRecord.query
            if cutoff_dt is not None:
                q = q.filter(or_(PredictionRecord.result == 'PENDING',
                                 PredictionRecord.match_date >= cutoff_dt))
            for rec in q.all():
                process(rec, 'pred')

            q = ComboLeg.query
            if cutoff_dt is not None:
                q = q.filter(or_(ComboLeg.result == 'PENDING', ComboLeg.match_date >= cutoff_dt))
            for leg in q.all():
                process(leg, 'leg')

            # --- ComboRecord: matokeo kutoka kwa legs (zote, ili marekebisho ya legs yaonekane) ---
            for combo in ComboRecord.query.all():
                leg_results = [lg.result for lg in combo.legs]
                if not leg_results:
                    continue
                if any(x == 'PENDING' for x in leg_results):
                    new = 'PENDING'
                elif any(x == 'LOST' for x in leg_results):
                    new = 'LOST'
                elif any(x == 'VOID' for x in leg_results):
                    new = 'VOID'
                else:
                    new = 'WON'
                if new != combo.result:
                    old = combo.result
                    combo.result = new
                    _stamp(combo)
                    stats['combo_changed'] += 1
                    if old != 'PENDING':
                        stats['combo_corrected'] += 1

            dirty = any(stats[k] for k in ('pred_settled', 'pred_corrected', 'leg_settled',
                                           'leg_corrected', 'combo_changed'))
            if dirty:
                db.session.commit()
    except Exception as e:
        print(f"::error::Utathmini umeshindwa wakati wa kuhifadhi: {e}")
        return False

    print(f"   Predictions: {stats['pred_settled']} mpya zimesettlewa, "
          f"{stats['pred_corrected']} zimerekebishwa (zilizokaguliwa: {stats['pred_total']})")
    print(f"   Combo legs : {stats['leg_settled']} mpya zimesettlewa, "
          f"{stats['leg_corrected']} zimerekebishwa (zilizokaguliwa: {stats['leg_total']})")
    print(f"   Combos     : {stats['combo_changed']} zimebadilika ({stats['combo_corrected']} marekebisho)")
    if stats['deferred']:
        print(f"   Zinazosubiri data (mechi changa, si VOID bado): {stats['deferred']}")

    if corrections:
        total_corr = sum(corrections.values())
        print(f"\n🔧 MAREKEBISHO YA HISTORIA ({total_corr}): soko: kabla -> baada = idadi")
        for (market, old, new), n in corrections.most_common(40):
            print(f"   • {market}: {old} -> {new} = {n}")
        for s in samples:
            print(f"     mfano: {s}")
        print(f"::warning::Historia imerekebishwa: {total_corr} rekodi (angalia orodha ya marekebisho).")

    if void_markets:
        top = ', '.join(f"{m} x{n}" for m, n in void_markets.most_common(10))
        print(f"\nℹ️ Zilizosettlewa kama VOID ({sum(void_markets.values())}) - takwimu hazipo "
              f"au soko halijulikani: {top}")

    unknown = market_evaluator.UNKNOWN_MARKETS
    if unknown:
        top = ', '.join(f"{m} x{n}" for m, n in unknown.most_common(25))
        print(f"\n❓ MASOKO YASIYO NA EVALUATOR (yanaishia VOID): {top}")
        print(f"::warning::Masoko {len(unknown)} hayana evaluator: {', '.join(list(unknown)[:10])}")

    if unmatched:
        print(f"\n⚠️ {len(unmatched)} PENDING zimepita muda lakini HAZIKUPATIKANA kwenye dataset "
              f"(angalia majina ya timu/tarehe):")
        for home, away, md, market in unmatched[:30]:
            print(f"   • {home} vs {away} ({md}) [{market}]")
        print(f"::warning::{len(unmatched)} PENDING hazikupatikana kwenye dataset - majina ya timu yanaweza kutofautiana")
    return True


def main():
    print("📥 Kupakua historical-complete-no-gap dataset ya sasa...")
    download_dataset()

    csv_files = [f for f in os.listdir('kaggle_dataset') if f.endswith('.csv')]
    if len(csv_files) > 1:
        print(f"::warning::Dataset ina CSV zaidi ya moja ({csv_files}) - natumia {DATASET_CSV} tu.")
    if DATASET_CSV not in csv_files:
        print(f"❌ {DATASET_CSV} haipo kwenye dataset. CSV zilizopo: {csv_files}")
        sys.exit(1)
    csv_path = os.path.join('kaggle_dataset', DATASET_CSV)
    df_existing = pd.read_csv(csv_path)
    df_existing['Date'] = pd.to_datetime(df_existing['Date'], format='mixed')
    print(f"   Historia ya sasa: {len(df_existing):,} mechi")
    if len(df_existing) < MIN_ROWS:
        print(f"::error::Dataset ina mechi {len(df_existing):,} tu (chini ya {MIN_ROWS:,}). "
              f"Nasimama ili nisiandike juu ya historia.")
        sys.exit(1)

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
    df_new, failed_fetch = fetch_recent_results(from_date_str, to_date_str)
    print(f"   Jumla ya mechi zilizopatikana: {len(df_new)}")
    if failed_fetch:
        print(f"::warning::AllSportsAPI: ligi {len(failed_fetch)}/{len(LEAGUE_MAP)} "
              f"zimeshindwa ({', '.join(failed_fetch)}) - matokeo ya ligi hizo hayakuchukuliwa mzunguko huu.")

    df_results = df_existing   # dataset itakayotumika kutathmini
    upload_ok = True
    need_upload = aliases_changed
    csv_dirty = False
    reasons = ["team_aliases.json"] if aliases_changed else []

    if len(df_new) == 0:
        if failed_fetch:
            print("ℹ️ Hakuna mechi mpya zilizopatikana - lakini API ilishindwa kwa baadhi ya ligi, "
                  "kwa hiyo si lazima hakuna mechi. Mzunguko unaofuata utajaribu tena.")
        else:
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
            print(f"✅ Historia mpya: {len(df_combined):,} mechi (+{len(df_new_unique)})")
            df_results = df_combined
            csv_dirty = True
            need_upload = True
            reasons.append(f"+{len(df_new_unique)} mechi")

    # Stats + odds + nusu-muda + kadi kutoka football-data (kamili mara ya kwanza; kisha msimu huu na uliopita)
    full, do_sync = football_data_mode(df_results)
    fd_ok = False
    if do_sync:
        try:
            df_sync, changes, label, fd_ok = sync_stats_from_football_data(df_results, full)
            if changes > 0:
                df_results = df_sync
                csv_dirty = True
                need_upload = True
                reasons.append(f"football-data {label}: {changes} mabadiliko")
            else:
                print("ℹ️ football-data: hakuna cha kujaza mzunguko huu.")
        except Exception as e:
            fd_ok = False
            print(f"::warning::Usawazishaji wa football-data umeshindwa (naendelea bila huo): {e}")

    # Alama ya v8: usawazishaji kamili umefanikiwa -> iandikwe ndani ya dataset (ipakiwe na toleo jipya)
    if full and fd_ok:
        write_marker()
        need_upload = True
        reasons.append(f"alama {MARKER_VERSION}")
    marker_ok = (read_marker() == MARKER_VERSION)

    if csv_dirty:
        df_results.to_csv(csv_path, index=False)
        print(f"💾 CSV imehifadhiwa: {len(df_results):,} mechi")

    if need_upload:
        upload_msg = f"Auto-update {datetime.utcnow().isoformat()} ({'; '.join(reasons)})"
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

    # Utathmini UNAFANYIKA KILA MZUNGUKO. Mara moja tu (baada ya usawazishaji kamili wa football-data),
    # historia YOTE inasettlewa upya kwa evaluator mpya.
    do_resettle = marker_ok and resettle_needed()
    if do_resettle:
        print("\n🔁 Usawazishaji wa HISTORIA YOTE (mara moja, toleo "
              f"{RESETTLE_VERSION}): rekodi zote zinatathminiwa upya.")
    settle_ok = settle_pending(df_results, recheck_days=None if do_resettle else RECHECK_DAYS)
    if do_resettle and settle_ok:
        mark_resettled()
        print("✅ Historia yote imesettlewa upya (haitarudiwa).")
    run_stuck_check(stuck_from_str, to_date_str)

    # Hali ya API kwenye flags + uamuzi wa rangi ya run
    record_api_status(failed_fetch)
    api_down = len(failed_fetch) >= API_FAIL_THRESHOLD
    if api_down:
        print(f"::error::AllSportsAPI haikujibu kwa ligi {len(failed_fetch)}/{len(LEAGUE_MAP)} - "
              f"matokeo mapya hayakuchukuliwa. Mzunguko unaofuata utajaribu tena (dirisha ni siku {LOOKBACK_DAYS}).")

    if not upload_ok or not settle_ok or api_down:
        sys.exit(1)


if __name__ == '__main__':
    main()