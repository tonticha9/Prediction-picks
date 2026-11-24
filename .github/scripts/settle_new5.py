"""
.github/scripts/settle_new5.py  -  v1 (Nov 24 2026)

Inasettle picks za ligi 5 mpya (NOR, MEX, BRA, E2, EC) ambazo hourly_catchup.py haziwezi (dataset yake ina ligi 12 tu).
Hatua:
  1. Inachukua rekodi za PENDING za miaka siku chache zilizopita ambazo timu zake ni za ligi 5 (kupitia league_tools).
  2. Inaomba matokeo ya mechi zilizokwisha kutoka AllSportsAPI kwa kila ligi (ombi 1 kwa ligi, si kwa rekodi).
  3. Inalinganisha mechi kwa jina (name_match.py: pande ZOTE mbili lazima zilingane) na tarehe (+/- siku 1).
  4. Inaandika WON/LOST/VOID kwa market_evaluator ileile ya hourly_catchup, na matokeo (MatchResult) kwa History.
Haiguswi ligi 12. Ikishindwa (API chini, n.k.) rekodi zinabaki PENDING na run inayofuata inajaribu tena. Haivunji workflow.
"""
import os
import re
import sys
import time
from datetime import date, datetime, timedelta

sys.path.insert(0, os.getcwd())

BASE_URL = "https://apiv2.allsportsapi.com/football/"
NEW5_KEYS = {'NOR': 253, 'MEX': 235, 'BRA': 99, 'E2': 154, 'EC': 151}
LOOKBACK_DAYS = 21          # rekodi za PENDING za zamani zaidi ya hii hazishughulikiwi tena (zinaripotiwa tu)
DATE_TOLERANCE = 1          # siku +/-
API_RETRIES = 3
API_BACKOFF = (2, 6)


def redact(text, key=None):
    s = str(text)
    if key:
        s = s.replace(key, '***')
    return re.sub(r'(APIkey=)[^&\s)]+', r'\1***', s)


def get_api_key():
    import requests
    app_url, secret = os.environ.get('APP_URL'), os.environ.get('REFRESH_SECRET')
    if app_url and secret:
        try:
            r = requests.get(f"{app_url}/api/config/allsportsapi-key", headers={"X-Refresh-Secret": secret}, timeout=15)
            r.raise_for_status()
            k = r.json().get('api_key')
            if k:
                return k
        except Exception as e:
            print(f"⚠️ API key kutoka Admin imeshindwa ({redact(e)}) - natumia GitHub Secret.")
    return os.environ.get('ALLSPORTSAPI_KEY')


def api_fixtures(api_key, league_key, d_from, d_to):
    import requests
    params = {"met": "Fixtures", "APIkey": api_key, "leagueId": league_key, "from": d_from, "to": d_to}
    last = 'haijulikani'
    for attempt in range(1, API_RETRIES + 1):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
        except requests.RequestException as e:
            last = redact(e, api_key)
        else:
            if resp.status_code < 400:
                try:
                    return resp.json().get('result') or []
                except ValueError:
                    last = 'jibu si JSON'
            else:
                last = f"HTTP {resp.status_code} {resp.reason}"
                if resp.status_code < 500 and resp.status_code != 429:
                    break
        if attempt < API_RETRIES:
            time.sleep(API_BACKOFF[min(attempt - 1, len(API_BACKOFF) - 1)])
    raise RuntimeError(last)


def _pair(s):
    if not s or ' - ' not in str(s):
        return None
    try:
        a, b = [int(x.strip()) for x in str(s).split(' - ')]
        return a, b
    except Exception:
        return None


def parse_finished(fx):
    """Mechi moja ya API -> dict (au None ikiwa haijakwisha / haina matokeo sahihi)."""
    if str(fx.get('event_status', '')).strip().lower() != 'finished':
        return None
    ft = _pair(fx.get('event_final_result'))
    if ft is None:
        return None
    try:
        d = datetime.strptime(str(fx.get('event_date')), '%Y-%m-%d').date()
    except Exception:
        return None
    ht = _pair(fx.get('event_halftime_result'))
    return {'date': d, 'home': fx.get('event_home_team') or '', 'away': fx.get('event_away_team') or '',
            'FTHG': ft[0], 'FTAG': ft[1],
            'HTHG': ht[0] if ht else None, 'HTAG': ht[1] if ht else None}


def plan_settlement(records, league_of, fixtures_by_league, evaluate, name_match):
    """Kazi safi (hakuna DB/API): rudisha (updates, results, unmatched).
      records: vitu vyenye .id .home_team .away_team .match_date(date) .market .result
      league_of(home, away) -> msimbo wa ligi au ''
      fixtures_by_league: {liga: [dict kutoka parse_finished]}
      evaluate(market, row) -> 'WON'/'LOST'/'VOID'
    updates = [(rec, label)], results = {(date, home_db, away_db): fixture}, unmatched = [rec]"""
    updates, results, unmatched = [], {}, []
    cache = {}
    for rec in records:
        lg = league_of(rec.home_team, rec.away_team)
        if lg not in NEW5_KEYS:
            continue
        key = (lg, rec.home_team, rec.away_team, rec.match_date)
        if key not in cache:
            cands = [f for f in fixtures_by_league.get(lg, []) if abs((f['date'] - rec.match_date).days) <= DATE_TOLERANCE]
            cache[key] = name_match.find_fixture(rec.home_team, rec.away_team, cands, get=lambda c: (c['home'], c['away']))
        fx = cache[key]
        if fx is None:
            unmatched.append(rec)
            continue
        row = {'FTHG': fx['FTHG'], 'FTAG': fx['FTAG'], 'HTHG': fx['HTHG'], 'HTAG': fx['HTAG']}
        updates.append((rec, evaluate(rec.market, row)))
        results[(rec.match_date, rec.home_team, rec.away_team)] = fx
    return updates, results, unmatched


def main():
    if not os.environ.get('DATABASE_URL'):
        print("::warning::DATABASE_URL haipo - settle_new5 imerukwa.")
        return 0
    api_key = get_api_key()
    if not api_key:
        print("::warning::ALLSPORTSAPI_KEY haipo - settle_new5 imerukwa.")
        return 0
    print(f"::add-mask::{api_key}")

    import pandas as pd
    import name_match
    from app import app
    from models import db, PredictionRecord
    from match_result_model import MatchResult
    import league_tools
    import market_evaluator as me

    def evaluate(market, row):
        return me.result_label(me.evaluate_market(market, row))

    with app.app_context():
        try:
            db.create_all()
            pred = pd.read_csv(os.path.join('data', 'predictions.csv')) if os.path.exists(os.path.join('data', 'predictions.csv')) else None
            n_sync = league_tools.sync_teams(pred)
            if n_sync:
                print(f"   ramani ya timu->ligi: {n_sync} mpya/zilizobadilika")
            tmap = league_tools.team_league_dict()
        except Exception as e:
            db.session.rollback()
            print(f"::warning::ramani ya timu->ligi imeshindwa ({type(e).__name__}: {e}) - settle_new5 imerukwa.")
            return 0

        def league_of(h, a):
            return tmap.get(league_tools.norm(h)) or tmap.get(league_tools.norm(a)) or ''

        today = (datetime.utcnow() + timedelta(hours=3)).date()          # EAT
        since = datetime.combine(today - timedelta(days=LOOKBACK_DAYS), datetime.min.time())
        until = datetime.combine(today + timedelta(days=1), datetime.min.time())
        pend = PredictionRecord.query.filter(PredictionRecord.result == 'PENDING',
                                             PredictionRecord.match_date >= since,
                                             PredictionRecord.match_date < until).all()
        mine = []
        for r in pend:
            if league_of(r.home_team, r.away_team) in NEW5_KEYS:
                r._d = r.match_date.date() if isinstance(r.match_date, datetime) else r.match_date
                mine.append(r)
        print(f"   PENDING ya ligi 5: {len(mine)} (kati ya PENDING {len(pend)} za siku {LOOKBACK_DAYS} zilizopita)")
        if not mine:
            return 0

        # mwisho wa upeo wa tarehe kwa kila ligi
        by_league = {}
        for r in mine:
            by_league.setdefault(league_of(r.home_team, r.away_team), []).append(r._d)
        fixtures_by_league, failed = {}, []
        for lg, dates in by_league.items():
            d_from = (min(dates) - timedelta(days=DATE_TOLERANCE)).isoformat()
            d_to = (max(dates) + timedelta(days=DATE_TOLERANCE)).isoformat()
            try:
                raw = api_fixtures(api_key, NEW5_KEYS[lg], d_from, d_to)
                fixtures_by_league[lg] = [x for x in (parse_finished(f) for f in raw) if x]
                print(f"   {lg}: mechi {len(raw)} (zilizokwisha {len(fixtures_by_league[lg])}) {d_from}..{d_to}")
            except Exception as e:
                failed.append(lg)
                print(f"::warning::{lg}: ombi la API limeshindwa ({redact(e, api_key)}) - rekodi zake zinabaki PENDING.")
            time.sleep(0.3)

        class _R:     # kitu chepesi kwa plan_settlement chenye tarehe kama date
            __slots__ = ('obj', 'id', 'home_team', 'away_team', 'match_date', 'market', 'result')

            def __init__(self, o):
                self.obj, self.id, self.home_team, self.away_team = o, o.id, o.home_team, o.away_team
                self.match_date, self.market, self.result = o._d, o.market, o.result

        wrapped = [_R(o) for o in mine if league_of(o.home_team, o.away_team) not in failed]
        updates, results, unmatched = plan_settlement(wrapped, league_of, fixtures_by_league, evaluate, name_match)

        counts = {'WON': 0, 'LOST': 0, 'VOID': 0}
        now = datetime.utcnow()
        try:
            for w, label in updates:
                w.obj.result = label
                w.obj.settled_at = now
                counts[label] = counts.get(label, 0) + 1
            # matokeo halisi kwa History
            n_new = 0
            for (d, h, a), fx in results.items():
                cur = MatchResult.query.filter_by(match_date=d, home_team=h, away_team=a).first()
                vals = dict(fthg=fx['FTHG'], ftag=fx['FTAG'], hthg=fx['HTHG'], htag=fx['HTAG'])
                if cur is None:
                    db.session.add(MatchResult(match_date=d, home_team=h, away_team=a, updated_at=now, **vals))
                    n_new += 1
                else:
                    for k, v in vals.items():
                        setattr(cur, k, v)
                    cur.updated_at = now
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"::warning::kuandika matokeo kumeshindwa ({type(e).__name__}: {e}) - hakuna kilichobadilishwa.")
            return 0
        print(f"✅ settle_new5: zimesettlewa {len(updates)} (WON {counts['WON']}, LOST {counts['LOST']}, VOID {counts['VOID']}); "
              f"matokeo mapya {n_new}; hazikupata mechi {len(unmatched)}")
        old = [u for u in unmatched if (today - u.match_date).days >= 3]
        if old:
            names = sorted({f"{u.home_team} v {u.away_team} ({u.match_date})" for u in old})[:8]
            print(f"::warning::Rekodi {len(old)} za ligi 5 za siku 3+ zilizopita hazikupata mechi kwenye API (jina au mechi iliahirishwa): "
                  + "; ".join(names))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print(f"::warning::settle_new5 imeshindwa ({type(e).__name__}: {e}) - rekodi zinabaki PENDING.")
        sys.exit(0)