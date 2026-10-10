"""
.github/scripts/sync_match_results.py

Inasoma dataset ya Kaggle (kaggle_dataset/*.csv, iliyopakuliwa na
hourly_catchup.py) na kuandika matokeo ya mechi za siku 45 zilizopita kwenye
jedwali la MatchResult (kwa History). Pia inaandika "heartbeat" ya Job A
kwa ajili ya Admin health check.
"""
import os
import sys
from datetime import datetime, date, timedelta

import pandas as pd

sys.path.insert(0, os.getcwd())

DAYS_BACK = 45

COLS = {'FTHG': 'fthg', 'FTAG': 'ftag', 'HTHG': 'hthg', 'HTAG': 'htag',
        'HC': 'hc', 'AC': 'ac', 'HY': 'hy', 'AY': 'ay', 'HR': 'hr', 'AR': 'ar',
        'HST': 'hst', 'AST': 'ast'}


def _int(v):
    try:
        if v is None or pd.isna(v):
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


def main():
    if not os.environ.get('DATABASE_URL'):
        print("::error::DATABASE_URL haipo - siwezi kuandika matokeo.")
        sys.exit(1)

    if not os.path.isdir('kaggle_dataset'):
        print("::error::Folda ya kaggle_dataset/ haipo - hourly_catchup haikupakua dataset.")
        sys.exit(1)
    csv_files = [f for f in os.listdir('kaggle_dataset') if f.endswith('.csv')]
    if not csv_files:
        print("::error::Hakuna CSV kwenye kaggle_dataset/.")
        sys.exit(1)

    df = pd.read_csv(os.path.join('kaggle_dataset', csv_files[0]))
    df['Date'] = pd.to_datetime(df['Date'], format='mixed', errors='coerce')
    cutoff = date.today() - timedelta(days=DAYS_BACK)
    df = df[df['Date'] >= pd.Timestamp(cutoff)].dropna(subset=['Date', 'HomeTeam', 'AwayTeam'])
    print(f"📥 Mechi {len(df)} za siku {DAYS_BACK} zilizopita kutoka dataset.")

    try:
        from app import app, set_flag
        from models import db
        from match_result_model import MatchResult

        with app.app_context():
            db.create_all()   # hakikisha jedwali la match_result lipo

            existing = {}
            for r in MatchResult.query.filter(MatchResult.match_date >= cutoff).all():
                existing[(r.match_date, r.home_team, r.away_team)] = r

            new_objs, updated = [], 0
            for row in df.to_dict('records'):
                d = row['Date'].date()
                home = str(row['HomeTeam']).strip()
                away = str(row['AwayTeam']).strip()
                vals = {attr: _int(row.get(col)) for col, attr in COLS.items()}

                cur = existing.get((d, home, away))
                if cur is None:
                    obj = MatchResult(match_date=d, home_team=home, away_team=away,
                                      updated_at=datetime.utcnow(), **vals)
                    new_objs.append(obj)
                    existing[(d, home, away)] = obj
                elif any(getattr(cur, k) != v for k, v in vals.items()):
                    for k, v in vals.items():
                        setattr(cur, k, v)
                    cur.updated_at = datetime.utcnow()
                    updated += 1

            if new_objs:
                db.session.add_all(new_objs)
            if new_objs or updated:
                db.session.commit()

            stamp = datetime.utcnow().isoformat(timespec='seconds')
            set_flag('job_a_last_run', f"{stamp}|mpya={len(new_objs)}|zilizosasishwa={updated}")
            print(f"✅ MatchResult: mpya {len(new_objs)}, zilizosasishwa {updated}.")
    except Exception as e:
        print(f"::error::sync_match_results imeshindwa: {type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
