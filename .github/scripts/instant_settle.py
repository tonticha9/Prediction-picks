"""
.github/scripts/instant_settle.py

SETTLER YA PAPO HAPO: mechi ikiisha, matokeo yanaingia History ndani ya dakika 1-3,
bila kusubiri Job A (kila nusu saa).

Jinsi inavyofanya kazi (kila run, workflow inaianza kila dakika 5):
  1. Inatafuta PENDING za siku 3 zilizopita (PredictionRecord + ComboLeg) ambazo
     tarehe yake ni leo au zamani. Hakuna PENDING = inaondoka mara moja (0 requests za API).
  2. Ratiba ya mechi (ligi 12, jana+leo) inapakuliwa mara moja kila masaa 4 na kuhifadhiwa
     kwenye flags (vipande vidogo vya herufi 200, salama kwa aina yoyote ya safu).
  3. Inauliza AllSportsAPI KWA LIGI TU zenye mechi iliyo ndani ya dirisha la kuisha
     (kuanzia dakika 95 baada ya kickoff hadi masaa 4). Mechi ikiisha ('Finished'):
       - inaandika MatchResult (kwa History),
       - inasettle PENDING kwa market_evaluator ileile ya Job A (WON/LOST tu),
       - inakokotoa upya ComboRecord.
  4. Inarudia kila POLL_SECONDS ndani ya run moja (LOOP_SECONDS).

Usalama:
  - VOID haiandikwi papo hapo (takwimu zinaweza kuwa hazijakamilika): inabaki PENDING,
    Job A inaishughulikia kama kawaida. Job A pia inarekebisha chochote kilichokosewa
    (inakagua upya siku 10).
  - Idempotent: kuendesha mara nyingi hakuharibu kitu.
  - market_evaluator.selftest() lazima ipite, vinginevyo inasimama bila kugusa DB.
"""
import os
import re
import sys
import time
from datetime import datetime, timedelta, date

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.getcwd())
import team_names

APP_URL = os.environ.get("APP_URL")
REFRESH_SECRET = os.environ.get("REFRESH_SECRET")

LOOP_SECONDS = int(os.environ.get("LOOP_SECONDS", "270"))      # urefu wa run moja
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "90"))       # muda kati ya maulizo ya API
MIN_MINUTES_AFTER_KICKOFF = int(os.environ.get("MIN_MINUTES", "95"))   # mechi haiishi mapema zaidi ya hapa
MAX_HOURS_AFTER_KICKOFF = float(os.environ.get("MAX_HOURS", "4"))
SCHED_REFRESH_HOURS = 4
PENDING_DAYS_BACK = 3

BASE_URL = "https://apiv2.allsportsapi.com/football/"
LEAGUE_MAP = {'E0': 152, 'E1': 153, 'D1': 175, 'SP1': 302, 'F1': 168, 'I1': 207,
              'N1': 244, 'B1': 63, 'P1': 266, 'T1': 322, 'G1': 178, 'SC0': 279}

STAT_TYPE_MAP = {'Corners': 'corners', 'Shots Total': 'shots_total',
                 'Shots On Goal': 'shots_on_target', 'Fouls': 'fouls',
                 'Offsides': 'offsides', 'Ball Possession': 'possession',
                 'Yellow Cards': 'yellow_cards', 'Saves': 'saves'}


# ------------------------------------------------------------
# API key (Admin kwanza, GitHub Secret ni fallback) - kama Job A
# ------------------------------------------------------------
def fetch_api_key_from_admin():
    if not APP_URL or not REFRESH_SECRET:
        return None
    try:
        resp = requests.get(f"{APP_URL}/api/config/allsportsapi-key",
                            headers={"X-Refresh-Secret": REFRESH_SECRET}, timeout=15)
        resp.raise_for_status()
        return resp.json().get('api_key')
    except Exception as e:
        print(f"⚠️ Key ya Admin haikupatikana ({type(e).__name__}) - natumia GitHub Secret.")
        return None


API_KEY = fetch_api_key_from_admin() or os.environ.get("ALLSPORTSAPI_KEY")
if API_KEY:
    print(f"::add-mask::{API_KEY}")


def redact(text):
    s = str(text)
    if API_KEY:
        s = s.replace(API_KEY, '***')
    return re.sub(r'(APIkey=)[^&\s)]+', r'\1***', s)


def api_fixtures(league_key, d_from, d_to):
    """Mechi za ligi moja. Majaribio 3 kwa makosa ya muda. Inarudisha orodha au inatupa RuntimeError."""
    params = {"met": "Fixtures", "APIkey": API_KEY, "leagueId": league_key,
              "from": d_from, "to": d_to}
    last = 'haijulikani'
    for attempt in range(1, 4):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
        except requests.RequestException as e:
            last = redact(e)
        else:
            if resp.status_code < 400:
                try:
                    return resp.json().get('result', []) or []
                except ValueError:
                    last = 'jibu si JSON'
            else:
                last = f"HTTP {resp.status_code}"
                if resp.status_code < 500 and resp.status_code != 429:
                    break
        if attempt < 3:
            time.sleep(2 * attempt)
    raise RuntimeError(last)


# ------------------------------------------------------------
# Kuchambua mechi (nakala ya mantiki ya Job A v8)
# ------------------------------------------------------------
def parse_statistics(stats_list):
    parsed = {'home': {}, 'away': {}}
    for s in stats_list:
        stype = s.get('type')
        if stype in STAT_TYPE_MAP:
            key = STAT_TYPE_MAP[stype]
            for side in ('home', 'away'):
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
        is_home, is_away = bool(c.get('home_fault')), bool(c.get('away_fault'))
        if 'yellow' in ctype and 'red' not in ctype:
            hy += 1 if is_home else 0
            ay += 1 if is_away else 0
        elif 'red' in ctype:
            hr += 1 if is_home else 0
            ar += 1 if is_away else 0
    return hy, ay, hr, ar


def build_row(f, league):
    """Mechi iliyokwisha -> dict yenye safu za evaluator. None kama haijakwisha/haina alama."""
    if f.get('event_status') != 'Finished':
        return None
    try:
        score = f.get('event_final_result', '')
        if ' - ' not in score:
            return None
        fthg, ftag = [int(x.strip()) for x in score.split(' - ')]
    except Exception:
        return None

    hthg = htag = np.nan
    ht = f.get('event_halftime_result', '')
    if ' - ' in ht:
        try:
            hthg, htag = [int(x.strip()) for x in ht.split(' - ')]
        except Exception:
            pass

    stats = parse_statistics(f.get('statistics', []) or [])
    cards_list = f.get('cards', []) or []
    if cards_list:
        hy, ay, hr, ar = parse_cards(cards_list)
    else:
        yh, ya = stats['home'].get('yellow_cards'), stats['away'].get('yellow_cards')
        hy = yh if (yh is not None and not pd.isna(yh)) else np.nan
        ay = ya if (ya is not None and not pd.isna(ya)) else np.nan
        hr = ar = np.nan

    try:
        d = datetime.strptime(f.get('event_date'), '%Y-%m-%d').date()
    except Exception:
        return None
    return {
        'Date': d, 'league': league,
        'HomeTeam': str(team_names.canon(f.get('event_home_team'))),
        'AwayTeam': str(team_names.canon(f.get('event_away_team'))),
        'FTHG': fthg, 'FTAG': ftag, 'HTHG': hthg, 'HTAG': htag,
        'HC': stats['home'].get('corners', np.nan), 'AC': stats['away'].get('corners', np.nan),
        'HST': stats['home'].get('shots_on_target', np.nan),
        'AST': stats['away'].get('shots_on_target', np.nan),
        'HY': hy, 'AY': ay, 'HR': hr, 'AR': ar,
    }


def norm_team(name):
    return str(team_names.canon(str(name or '').strip())).lower()


def to_date(v):
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


def _int(v):
    try:
        if v is None or pd.isna(v):
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------
# Ratiba (cache kwenye flags, vipande vya herufi 200)
# ------------------------------------------------------------
CHUNK = 200


def sched_save(set_flag, items):
    """items: orodha ya (league, kickoff_datetime, home, away)."""
    try:
        text = ';'.join(f"{lg}|{ko:%Y%m%d%H%M}|{h.replace('|', ' ').replace(';', ' ')}|"
                        f"{a.replace('|', ' ').replace(';', ' ')}" for lg, ko, h, a in items)
        parts = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)] or ['']
        for i, p in enumerate(parts):
            set_flag(f'instant_sched_{i}', p)
        set_flag('instant_sched_meta', f"{datetime.utcnow():%Y%m%d%H%M}|{len(parts)}")
    except Exception as e:
        print(f"::warning::Cache ya ratiba haikuhifadhiwa (haiathiri kazi): {redact(e)}")


def sched_load(get_flag):
    """Rudisha orodha ya fixtures, au None ikiwa cache haipo/imepitwa na wakati."""
    try:
        meta = get_flag('instant_sched_meta', '')
        if not meta or '|' not in meta:
            return None
        stamp, n = meta.split('|')
        saved = datetime.strptime(stamp, '%Y%m%d%H%M')
        if (datetime.utcnow() - saved) > timedelta(hours=SCHED_REFRESH_HOURS):
            return None
        if saved.date() != datetime.utcnow().date():
            return None
        text = ''.join(get_flag(f'instant_sched_{i}', '') or '' for i in range(int(n)))
        items = []
        for rec in filter(None, text.split(';')):
            lg, ko, h, a = rec.split('|')
            items.append((lg, datetime.strptime(ko, '%Y%m%d%H%M'), h, a))
        return items
    except Exception:
        return None


def fetch_schedule():
    """Mechi ZISIZO Finished za jana+leo kutoka ligi zote 12."""
    today = date.today()
    d_from, d_to = (today - timedelta(days=1)).isoformat(), today.isoformat()
    items, failed = [], 0
    for code, key in LEAGUE_MAP.items():
        try:
            fixtures = api_fixtures(key, d_from, d_to)
        except Exception as e:
            failed += 1
            print(f"   ⚠️ ratiba {code}: {redact(e)}")
            continue
        for f in fixtures:
            if f.get('event_status') == 'Finished':
                continue
            try:
                ko = datetime.strptime(f"{f.get('event_date')} {f.get('event_time')}", '%Y-%m-%d %H:%M')
            except Exception:
                continue
            items.append((code, ko,
                          str(team_names.canon(f.get('event_home_team'))),
                          str(team_names.canon(f.get('event_away_team')))))
        time.sleep(0.3)
    print(f"📅 Ratiba mpya: mechi {len(items)} (ligi zilizoshindwa: {failed})")
    return items, failed


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------
def main():
    if not os.environ.get('DATABASE_URL'):
        print("::error::DATABASE_URL haipo.")
        sys.exit(1)
    if not API_KEY:
        print("::error::ALLSPORTSAPI_KEY haipo (Admin wala GitHub Secret).")
        sys.exit(1)

    try:
        import market_evaluator
        from market_evaluator import evaluate_market, result_label
        from app import app, get_flag, set_flag
        from models import db, PredictionRecord, ComboLeg, ComboRecord
        from match_result_model import MatchResult
    except Exception as e:
        print(f"::error::Imeshindwa kuunganisha na app/models: {type(e).__name__}: {e}")
        sys.exit(1)

    problems = market_evaluator.selftest()
    if problems:
        for p in problems:
            print(f"::error::market_evaluator selftest: {p}")
        sys.exit(1)

    deadline = time.time() + LOOP_SECONDS
    sched = None
    sched_failed = 0
    settled_total = 0
    done_matches = set()
    polls = 0

    while True:
        with app.app_context():
            today = date.today()
            cutoff_dt = datetime.combine(today - timedelta(days=PENDING_DAYS_BACK), datetime.min.time())
            preds = [r for r in PredictionRecord.query.filter(
                PredictionRecord.result == 'PENDING',
                PredictionRecord.match_date >= cutoff_dt).all()
                if (to_date(r.match_date) or today + timedelta(days=1)) <= today]
            legs = [r for r in ComboLeg.query.filter(
                ComboLeg.result == 'PENDING',
                ComboLeg.match_date >= cutoff_dt).all()
                if (to_date(r.match_date) or today + timedelta(days=1)) <= today]
            pending_keys = {(norm_team(r.home_team), norm_team(r.away_team)) for r in preds + legs}

            if not pending_keys:
                print("ℹ️ Hakuna PENDING za leo/jana - sina cha kufanya.")
                break

            if sched is None:
                sched = sched_load(get_flag)
                if sched is None:
                    sched, sched_failed = fetch_schedule()
                    if sched and not sched_failed:
                        sched_save(set_flag, sched)
                else:
                    print(f"📅 Ratiba kutoka cache: mechi {len(sched)}")

            now = datetime.utcnow()
            active = {}   # league -> orodha ya (home, away) zilizo kwenye dirisha
            next_start = None
            for lg, ko, h, a in sched:
                key = (h.lower(), a.lower())
                if key not in pending_keys or key in done_matches:
                    continue
                start = ko + timedelta(minutes=MIN_MINUTES_AFTER_KICKOFF)
                end = ko + timedelta(hours=MAX_HOURS_AFTER_KICKOFF)
                if start <= now <= end:
                    active.setdefault(lg, []).append(key)
                elif now < start and (next_start is None or start < next_start):
                    next_start = start

            if not active:
                if next_start and (next_start - now).total_seconds() < (deadline - time.time()):
                    wait = max(5, (next_start - now).total_seconds())
                    print(f"⏳ Hakuna mechi kwenye dirisha; inayofuata baada ya {wait/60:.1f} dk - nasubiri.")
                    time.sleep(wait)
                    continue
                print("ℹ️ Hakuna mechi inayoweza kuisha ndani ya run hii - naondoka.")
                break

            d_from, d_to = (today - timedelta(days=1)).isoformat(), today.isoformat()
            finished = []
            for lg in active:
                try:
                    for f in api_fixtures(LEAGUE_MAP[lg], d_from, d_to):
                        row = build_row(f, lg)
                        if row is not None:
                            finished.append(row)
                except Exception as e:
                    print(f"   ⚠️ {lg}: ombi limeshindwa ({redact(e)})")
                time.sleep(0.3)
            polls += 1

            # Zilizokwisha na ziko kwenye PENDING
            index = {}
            for row in finished:
                key = (norm_team(row['HomeTeam']), norm_team(row['AwayTeam']))
                if key in pending_keys:
                    index.setdefault(key, []).append((row['Date'], row))

            def find_row(home, away, md):
                best, best_diff = None, None
                for d, row in index.get((norm_team(home), norm_team(away)), []):
                    if md is None:
                        continue
                    diff = abs((d - md).days)
                    if diff <= 1 and (best_diff is None or diff < best_diff):
                        best, best_diff = row, diff
                return best

            changed = 0
            matches_done = []
            try:
                # MatchResult (History)
                for key, lst in index.items():
                    for d, row in lst:
                        vals = {'fthg': _int(row['FTHG']), 'ftag': _int(row['FTAG']),
                                'hthg': _int(row['HTHG']), 'htag': _int(row['HTAG']),
                                'hc': _int(row['HC']), 'ac': _int(row['AC']),
                                'hy': _int(row['HY']), 'ay': _int(row['AY']),
                                'hr': _int(row['HR']), 'ar': _int(row['AR']),
                                'hst': _int(row['HST']), 'ast': _int(row['AST'])}
                        cur = MatchResult.query.filter_by(
                            match_date=d, home_team=row['HomeTeam'], away_team=row['AwayTeam']).first()
                        if cur is None:
                            db.session.add(MatchResult(match_date=d, home_team=row['HomeTeam'],
                                                       away_team=row['AwayTeam'],
                                                       updated_at=datetime.utcnow(), **vals))
                        else:
                            for k, v in vals.items():
                                if v is not None and getattr(cur, k) != v:
                                    setattr(cur, k, v)
                            cur.updated_at = datetime.utcnow()

                # Settle PENDING: WON/LOST tu
                def settle(rec):
                    row = find_row(rec.home_team, rec.away_team, to_date(rec.match_date))
                    if row is None:
                        return 0
                    try:
                        label = result_label(evaluate_market(rec.market, row))
                    except Exception as e:
                        print(f"   ⚠️ evaluate_market '{rec.market}' imeshindwa: {e}")
                        return 0
                    if label not in ('WON', 'LOST'):
                        return 0          # VOID/haijulikani: Job A itashughulikia
                    rec.result = label
                    if hasattr(rec, 'settled_at'):
                        rec.settled_at = datetime.utcnow()
                    return 1

                n_pred = sum(settle(r) for r in preds)
                n_leg = sum(settle(r) for r in legs)

                n_combo = 0
                if n_leg:
                    for combo in ComboRecord.query.filter(ComboRecord.result == 'PENDING').all():
                        res = [lg_.result for lg_ in combo.legs]
                        if not res or any(x == 'PENDING' for x in res):
                            continue
                        new = 'LOST' if 'LOST' in res else ('VOID' if 'VOID' in res else 'WON')
                        combo.result = new
                        if hasattr(combo, 'settled_at'):
                            combo.settled_at = datetime.utcnow()
                        n_combo += 1

                db.session.commit()
                changed = n_pred + n_leg + n_combo
                for key, lst in index.items():
                    done_matches.add(key)
                    for d, row in lst:
                        matches_done.append(f"{row['HomeTeam']} {int(row['FTHG'])}-{int(row['FTAG'])} {row['AwayTeam']}")
                if matches_done:
                    print(f"✅ Zimeisha: {'; '.join(matches_done)} -> predictions {n_pred}, "
                          f"legs {n_leg}, combos {n_combo} zimesettlewa.")
                settled_total += changed
                try:
                    set_flag('instant_settle_last',
                             f"{datetime.utcnow().isoformat(timespec='seconds')}|settled={settled_total}")
                except Exception:
                    pass
            except Exception as e:
                db.session.rollback()
                print(f"::error::Kuhifadhi kumeshindwa: {type(e).__name__}: {e}")
                sys.exit(1)

        if time.time() + POLL_SECONDS >= deadline:
            break
        time.sleep(POLL_SECONDS)

    print(f"🏁 Mwisho wa run: maulizo {polls}, rekodi zilizosettlewa {settled_total}.")


if __name__ == '__main__':
    main()