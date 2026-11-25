"""
.github/scripts/ingest_new5.py  -  v1 (Nov 25 2026)

Inaandika picks za ligi 5 mpya (NOR, MEX, BRA, E2, EC) kutoka data/predictions.csv na data/value_bets.csv
moja kwa moja kwenye database (PredictionRecord + PredictionDaily), ili History na settle_new5.py ziziione.

Kwa nini: app inaandika rekodi hizi tu wakati 'refresh' inapofanya kazi kwenye data MPYA. Nightly inaita refresh
sekunde 20 baada ya commit, wakati Render bado inadeploy, kwa hiyo refresh mara nyingi inasoma data ya JANA na picks
mpya za ligi 5 hazifiki kwenye database kabisa (zinaonekana Zijazo tu kwa sababu zinasomwa kutoka faili).

Usalama: inaongeza/inasasisha tu (haifuti kitu), inagusa safu za ligi 5 tu, mechi za leo na zijazo tu,
na haivunji workflow ikishindwa.
"""
import os
import sys

sys.path.insert(0, os.getcwd())

NEW5 = ('NOR', 'MEX', 'BRA', 'E2', 'EC')
FILES = (('predictions.csv', 'prediction'), ('value_bets.csv', 'value_bet'))


def main():
    if not os.environ.get('DATABASE_URL'):
        print("::warning::DATABASE_URL haipo - ingest_new5 imerukwa.")
        return 0

    import pandas as pd
    from app import app, db, PredictionRecord, _to_dt, _clean_num, _clean_str, today_eat
    from snapshot_model import PredictionDaily

    total_new = 0
    with app.app_context():
        today = today_eat()
        try:
            for fname, section in FILES:
                path = os.path.join('data', fname)
                if not os.path.exists(path) or os.path.getsize(path) == 0:
                    print(f"   {fname}: haipo au ni tupu - imerukwa")
                    continue
                df = pd.read_csv(path)
                if 'league' not in df.columns or len(df) == 0:
                    print(f"   {fname}: haina safu 'league' - imerukwa")
                    continue
                df = df[df['league'].astype(str).isin(NEW5)].copy()
                if len(df) == 0:
                    print(f"   {fname}: hakuna safu za ligi 5")
                    continue
                df['match_date'] = pd.to_datetime(df['match_date'], format='mixed')
                df = df[df['match_date'].dt.date >= today]
                if len(df) == 0:
                    print(f"   {fname}: hakuna mechi za ligi 5 za leo/zijazo")
                    continue

                rows = df.to_dict('records')
                min_date = _to_dt(df['match_date'].min())

                rec_map = {}
                for r in PredictionRecord.query.filter(PredictionRecord.section == section,
                                                       PredictionRecord.match_date >= min_date).all():
                    rec_map[(r.match_date, r.home_team, r.away_team, r.market)] = r
                snap_map = {}
                for s in PredictionDaily.query.filter_by(snap_date=today, section=section).all():
                    snap_map[(s.match_date, s.home_team, s.away_team, s.market)] = s

                n_rec_new = n_rec_upd = n_snap_new = n_snap_upd = 0
                for row in rows:
                    mdate = _to_dt(row['match_date'])
                    home, away, market = row['HomeTeam'], row['AwayTeam'], row['market']
                    key = (mdate, home, away, market)
                    prob = _clean_num(row.get('probability_%'))
                    odds = _clean_num(row.get('real_odds'))
                    tier = _clean_str(row.get('pro_tier'))

                    cur = rec_map.get(key)
                    if cur is None:
                        obj = PredictionRecord(
                            match_date=mdate, home_team=home, away_team=away, market=market, section=section,
                            probability=prob, first_probability=prob, real_odds=odds, first_real_odds=odds,
                            pro_tier=tier, first_pro_tier=tier, result='PENDING', shown_date=today)
                        db.session.add(obj)
                        rec_map[key] = obj
                        n_rec_new += 1
                    elif cur.result == 'PENDING':
                        cur.probability, cur.real_odds, cur.pro_tier = prob, odds, tier
                        if cur.first_probability is None:
                            cur.first_probability, cur.first_real_odds, cur.first_pro_tier = prob, odds, tier
                        n_rec_upd += 1

                    sc = snap_map.get(key)
                    if sc is None:
                        sobj = PredictionDaily(snap_date=today, section=section, match_date=mdate,
                                               home_team=home, away_team=away, market=market,
                                               probability=prob, real_odds=odds, pro_tier=tier)
                        db.session.add(sobj)
                        snap_map[key] = sobj
                        n_snap_new += 1
                    else:
                        sc.probability, sc.real_odds, sc.pro_tier = prob, odds, tier
                        n_snap_upd += 1

                db.session.commit()
                total_new += n_rec_new
                n_matches = df[['HomeTeam', 'AwayTeam', 'match_date']].drop_duplicates().shape[0]
                print(f"   {fname} ({section}): mechi {n_matches}, safu {len(rows)} | rekodi mpya {n_rec_new}, "
                      f"zilizosasishwa {n_rec_upd} | picha ya siku mpya {n_snap_new}, zilizosasishwa {n_snap_upd}")
        except Exception as e:
            db.session.rollback()
            print(f"::warning::ingest_new5 imeshindwa ({type(e).__name__}: {e}) - hakuna kilichoandikwa.")
            return 0
    print(f"✅ ingest_new5: rekodi mpya {total_new}")
    return total_new


if __name__ == '__main__':
    main()