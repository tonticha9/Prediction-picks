"""
.github/scripts/sync_fixture_times.py

Inavuta ratiba ya mechi (siku 2 nyuma hadi siku 10 mbele) kutoka AllSportsAPI kwa
timezone ya Africa/Dar_es_Salaam (saa zinakuja moja kwa moja kwa EAT) na kuziandika
kwenye jedwali fixture_time. Inaendeshwa ndani ya hourly_catchup.yml, lakini
inaruka kama ilifanya kazi chini ya saa 3 zilizopita (kuokoa API calls).
FORCE_TIMES=1 inalazimisha (run za mkono).
Mwishoni inaripoti mechi ngapi za data/predictions.csv zimepata saa.
"""
import os
import sys
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

sys.path.insert(0, os.getcwd())

BASE_URL = "https://apiv2.allsportsapi.com/football/"
LEAGUE_MAP = {'E0': 152, 'E1': 153, 'D1': 175, 'SP1': 302, 'F1': 168, 'I1': 207,
              'N1': 244, 'B1': 63, 'P1': 266, 'T1': 322, 'G1': 178, 'SC0': 279}
TZ = 'Africa/Dar_es_Salaam'
DAYS_BACK = 2
DAYS_AHEAD = 10
MIN_HOURS_BETWEEN = 3
PRUNE_DAYS = 120


def get_api_key():
    app_url = os.environ.get('APP_URL')
    secret = os.environ.get('REFRESH_SECRET')
    if app_url and secret:
        try:
            r = requests.get(f"{app_url}/api/config/allsportsapi-key",
                             headers={"X-Refresh-Secret": secret}, timeout=15)
            r.raise_for_status()
            k = r.json().get('api_key')
            if k:
                return k
        except Exception as e:
            print(f"⚠️ API key kutoka Admin imeshindwa ({e}) - natumia GitHub Secret.")
    return os.environ.get('ALLSPORTSAPI_KEY')


def fetch_fixtures(api_key, d_from, d_to):
    out, ok = [], 0
    for code, league_key in LEAGUE_MAP.items():
        try:
            resp = requests.get(BASE_URL, params={
                "met": "Fixtures", "APIkey": api_key, "leagueId": league_key,
                "from": d_from, "to": d_to, "timezone": TZ,
            }, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            ok += 1
        except Exception as e:
            print(f"   ⚠️ {code}: ombi limeshindwa ({e})")
            continue
        for f in (data.get('result') or []):
            out.append(f)
        time.sleep(0.3)
    return out, ok


def main():
    if not os.environ.get('DATABASE_URL'):
        print("::error::DATABASE_URL haipo.")
        sys.exit(1)
    api_key = get_api_key()
    if not api_key:
        print("::error::ALLSPORTSAPI_KEY haipo (Admin wala GitHub Secret).")
        sys.exit(1)

    from app import app, get_flag, set_flag
    from models import db
    from fixture_times import FixtureTime, team_key

    force = os.environ.get('FORCE_TIMES') == '1'
    with app.app_context():
        db.create_all()

        last = get_flag('fixture_times_last_sync', '')
        if last and not force:
            try:
                age_h = (datetime.utcnow() - datetime.fromisoformat(last)).total_seconds() / 3600
                if age_h < MIN_HOURS_BETWEEN:
                    print(f"ℹ️ Sync ya mwisho ilikuwa saa {age_h:.1f} zilizopita - naruka (kuokoa API).")
                    return
            except Exception:
                pass

        now_eat = datetime.utcnow() + timedelta(hours=3)
        d_from = (now_eat - timedelta(days=DAYS_BACK)).date()
        d_to = (now_eat + timedelta(days=DAYS_AHEAD)).date()
        print(f"🔍 Ratiba {d_from} hadi {d_to} (timezone {TZ})")

        fixtures, ok_leagues = fetch_fixtures(api_key, d_from.isoformat(), d_to.isoformat())
        print(f"   Ligi {ok_leagues}/{len(LEAGUE_MAP)} zimejibu; mechi {len(fixtures)}")
        if ok_leagues == 0:
            print("::error::Hakuna ligi iliyojibu.")
            sys.exit(1)

        existing = {(r.match_date, r.home_key, r.away_key): r
                    for r in FixtureTime.query.filter(FixtureTime.match_date >= d_from).all()}
        added = changed = skipped = 0
        samples = []
        for f in fixtures:
            try:
                if str(f.get('event_status', '')).lower() in ('cancelled', 'canceled'):
                    skipped += 1
                    continue
                d = datetime.strptime(str(f.get('event_date')), '%Y-%m-%d').date()
                t = str(f.get('event_time') or '').strip()
                datetime.strptime(t, '%H:%M')
                home, away = f.get('event_home_team'), f.get('event_away_team')
                if not home or not away:
                    skipped += 1
                    continue
            except Exception:
                skipped += 1
                continue
            hk, ak = team_key(home), team_key(away)
            cur = existing.get((d, hk, ak))
            if cur is None:
                obj = FixtureTime(match_date=d, home_team=home, away_team=away,
                                  home_key=hk, away_key=ak, kickoff=t,
                                  updated_at=datetime.utcnow())
                db.session.add(obj)
                existing[(d, hk, ak)] = obj
                added += 1
            elif cur.kickoff != t:
                cur.kickoff = t
                cur.updated_at = datetime.utcnow()
                changed += 1
            if len(samples) < 4:
                samples.append(f"{home} vs {away} {d} {t}")

        prune_before = (now_eat - timedelta(days=PRUNE_DAYS)).date()
        FixtureTime.query.filter(FixtureTime.match_date < prune_before).delete()
        db.session.commit()
        set_flag('fixture_times_last_sync', datetime.utcnow().isoformat(timespec='seconds'))
        print(f"✅ Saa za mechi: mpya {added}, zilizobadilika {changed}, zilizorukwa {skipped}.")
        print("   Mifano (linganisha na saa halisi ya mechi ili kuthibitisha EAT):")
        for s in samples:
            print("    •", s)

        # Ripoti ya mechi za predictions.csv zilizopata saa
        try:
            p = pd.read_csv(os.path.join('data', 'predictions.csv'),
                            usecols=['HomeTeam', 'AwayTeam', 'match_date'])
            p['match_date'] = pd.to_datetime(p['match_date'], format='mixed', errors='coerce')
            p = p.dropna().drop_duplicates()
            p = p[p['match_date'].dt.date >= now_eat.date()]
            miss = []
            for _, r in p.iterrows():
                d = r['match_date'].date()
                hk, ak = team_key(r['HomeTeam']), team_key(r['AwayTeam'])
                if not any((d + timedelta(days=o), hk, ak) in existing for o in (0, -1, 1)):
                    miss.append(f"{r['HomeTeam']} vs {r['AwayTeam']} {d}")
            print(f"📊 Mechi za predictions.csv: {len(p)}; zenye saa {len(p) - len(miss)}; bila saa {len(miss)}")
            for m in miss[:15]:
                print("    ✗", m)
            if miss:
                print("::warning::Mechi zisizo na saa: huenda jina la timu linatofautiana na API "
                      "(ongeza alias kwenye team_names.py).")
        except Exception as e:
            print(f"ℹ️ Ripoti ya coverage imeshindwa: {e}")


if __name__ == '__main__':
    main()