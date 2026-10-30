"""
.github/scripts/restore_history_from_git.py

MOJA KWA MOJA: inarudisha History ya siku chache (default 7) kwenye database
mpya kwa kusoma data/predictions.csv na data/value_bets.csv kutoka git history.

- HAIINGIZI app.py (hakuna db.create_all, hakuna Flask).
- HAITUMII AllSportsAPI. Matokeo (WON/LOST) yanajazwa na Job A baadaye.
- Salama kuendeshwa mara nyingi: haiongezi rekodi zilizokwisha kuwepo.

v2: URL ya database inalazimishwa kutumia dereva wa psycopg2 (SQLAlchemy mpya
    ilikuwa inajaribu 'psycopg' v3 ambayo haipo).

Env vars: DATABASE_URL (lazima), DAYS (default 7), DRY_RUN (yes/no),
          MAX_DAYS_AHEAD (default 3)
"""
import io
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

EAT = timedelta(hours=3)
DAYS = int(os.environ.get('DAYS') or 7)
AHEAD = int(os.environ.get('MAX_DAYS_AHEAD') or 3)
DRY = (os.environ.get('DRY_RUN') or 'no').strip().lower() in ('yes', 'true', '1')
DB_URL = (os.environ.get('DATABASE_URL') or '').strip()
CHUNK = 2000

if not DB_URL:
    print("❌ DATABASE_URL haipo (angalia GitHub Secrets).")
    sys.exit(1)

# Lazimisha dereva wa psycopg2 (si psycopg v3)
if DB_URL.startswith('postgres://'):
    DB_URL = 'postgresql+psycopg2://' + DB_URL[len('postgres://'):]
elif DB_URL.startswith('postgresql://'):
    DB_URL = 'postgresql+psycopg2://' + DB_URL[len('postgresql://'):]


# ------------------------------------------------------------------ git
def run_git(args):
    r = subprocess.run(['git'] + args, capture_output=True)
    return r.returncode, r.stdout


def list_commits(path, since_iso):
    code, out = run_git(['log', f'--since={since_iso}', '--format=%H|%cI', '--', path])
    if code != 0:
        return []
    rows = []
    for line in out.decode('utf-8', 'ignore').splitlines():
        if '|' not in line:
            continue
        sha, ts = line.split('|', 1)
        try:
            t = datetime.fromisoformat(ts.strip()).astimezone(timezone.utc)
        except Exception:
            continue
        rows.append((sha.strip(), t))
    rows.sort(key=lambda x: x[1])
    return rows


def read_csv_at(sha, path):
    code, out = run_git(['show', f'{sha}:{path}'])
    if code != 0 or not out.strip():
        return None
    try:
        return pd.read_csv(io.BytesIO(out))
    except Exception as e:
        print(f"   ⚠️ {path}@{sha[:7]} haisomeki ({e})")
        return None


# ------------------------------------------------------------------ data
def pick(df, *names):
    for n in names:
        if n in df.columns:
            return df[n]
    return pd.Series([None] * len(df), index=df.index)


def is_banned(market, section):
    m = str(market)
    if '_and_' in m and 'under' in m.split('_and_', 1)[1]:
        return True
    if section == 'value_bet' and m == 'dc_12':
        return True
    return False


def normalize(df, section, snap_date):
    need = ['match_date', 'HomeTeam', 'AwayTeam', 'market']
    miss = [c for c in need if c not in df.columns]
    if miss:
        print(f"   ⚠️ {section}: safu zinazokosekana {miss} - naruka")
        return None
    md = pd.to_datetime(df['match_date'], errors='coerce', format='mixed')
    if getattr(md.dt, 'tz', None) is not None:
        md = md.dt.tz_localize(None)
    out = pd.DataFrame({
        'snap_date': snap_date,
        'section': section,
        'match_date': md,
        'home_team': df['HomeTeam'].astype(str).str.strip(),
        'away_team': df['AwayTeam'].astype(str).str.strip(),
        'market': df['market'].astype(str).str.strip(),
        'probability': pd.to_numeric(pick(df, 'probability_%', 'probability'), errors='coerce'),
        'real_odds': pd.to_numeric(pick(df, 'real_odds'), errors='coerce'),
        'pro_tier': pick(df, 'pro_tier'),
        'ev_percent': pd.to_numeric(pick(df, 'ev_percent', 'ev_%', 'EV_%', 'ev'), errors='coerce'),
    })
    out = out[out['match_date'].notna() & (out['market'] != '')]
    d0 = pd.Timestamp(snap_date)
    d1 = d0 + pd.Timedelta(days=AHEAD + 1)
    out = out[(out['match_date'] >= d0) & (out['match_date'] < d1)]
    out = out[~out['market'].apply(lambda m: is_banned(m, section))]
    out = out.drop_duplicates(['section', 'match_date', 'home_team', 'away_team', 'market'])
    return out


def clean_records(df, cols):
    d = df[cols].astype(object)
    d = d.where(df[cols].notna(), None)
    return d.to_dict('records')


# ------------------------------------------------------------------ main
def main():
    now_utc = datetime.now(timezone.utc)
    now_eat = (now_utc + EAT).replace(tzinfo=None)
    today = now_eat.date()
    first_day = today - timedelta(days=DAYS - 1)
    since = (now_utc - timedelta(days=DAYS + 3)).strftime('%Y-%m-%dT%H:%M:%SZ')

    print(f"📅 Leo (EAT): {today} | nairudisha {DAYS} siku: {first_day} hadi {today} | DRY_RUN={DRY}")

    commits = list_commits('data/predictions.csv', since)
    if not commits:
        print("❌ Hakuna commit za data/predictions.csv kwenye git history. "
              "Hakikisha checkout ina fetch-depth: 0.")
        sys.exit(1)
    print(f"   Commits za predictions.csv: {len(commits)}")

    # commit ya MWISHO ya kila siku (siku = EAT, kama app inavyohesabu snap_date)
    per_day = {}
    for sha, t in commits:
        d = (t + EAT).date()
        if first_day <= d <= today:
            per_day[d] = sha

    if not per_day:
        print("❌ Hakuna commit ndani ya siku zilizoombwa.")
        sys.exit(1)

    frames = []
    for d in sorted(per_day):
        sha = per_day[d]
        p = read_csv_at(sha, 'data/predictions.csv')
        v = read_csv_at(sha, 'data/value_bets.csv')
        np_ = nv_ = 0
        if p is not None:
            x = normalize(p, 'prediction', d)
            if x is not None:
                frames.append(x)
                np_ = len(x)
        if v is not None:
            x = normalize(v, 'value_bet', d)
            if x is not None:
                frames.append(x)
                nv_ = len(x)
        print(f"   {d}  commit {sha[:7]}  predictions={np_:,}  value_bets={nv_:,}")

    if not frames:
        print("❌ Hakuna mistari iliyosomeka.")
        sys.exit(1)
    allrows = pd.concat(frames, ignore_index=True)
    print(f"\n📦 Jumla ya mistari ya prediction_daily: {len(allrows):,}")

    # ---------------- rekodi za prediction_record (mechi zilizokwisha anza)
    played = allrows[allrows['match_date'] < now_eat].sort_values('snap_date')
    key = ['section', 'match_date', 'home_team', 'away_team', 'market']
    g = played.groupby(key, sort=False)
    first = g.first().reset_index()
    last = g.last().reset_index()
    rec = last[key + ['probability', 'real_odds', 'pro_tier', 'ev_percent']].copy()
    rec['first_probability'] = first['probability'].values
    rec['first_real_odds'] = first['real_odds'].values
    rec['first_pro_tier'] = first['pro_tier'].values
    rec['shown_date'] = first['snap_date'].values
    rec['result'] = 'PENDING'
    rec['settled_at'] = None
    rec['created_at'] = now_utc.replace(tzinfo=None)
    print(f"📦 Rekodi za prediction_record (mechi zilizokwisha anza): {len(rec):,}")

    if DRY:
        print("\nℹ️ DRY_RUN=yes - hakuna kilichoandikwa kwenye database.")
        return

    engine = create_engine(DB_URL, pool_pre_ping=True)
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    missing = [t for t in ('prediction_daily', 'prediction_record') if t not in tables]
    if missing:
        print(f"❌ Jedwali hazipo kwenye database mpya: {missing}")
        print("   Fungua app (Render) mara moja ili db.create_all() izitengeneze, kisha endesha tena.")
        sys.exit(1)

    md = MetaData()
    t_daily = Table('prediction_daily', md, autoload_with=engine)
    t_rec = Table('prediction_record', md, autoload_with=engine)

    with engine.begin() as conn:
        before_d = conn.execute(text('SELECT COUNT(*) FROM prediction_daily')).scalar()
        before_r = conn.execute(text('SELECT COUNT(*) FROM prediction_record')).scalar()

        # --- prediction_daily (unique constraint inazuia nakala)
        dcols = ['snap_date', 'section', 'match_date', 'home_team', 'away_team',
                 'market', 'probability', 'real_odds', 'pro_tier']
        dcols = [c for c in dcols if c in t_daily.c]
        recs = clean_records(allrows, dcols)
        for i in range(0, len(recs), CHUNK):
            conn.execute(pg_insert(t_daily).on_conflict_do_nothing(), recs[i:i + CHUNK])

        # --- prediction_record (hakuna unique constraint: tunaangalia wenyewe)
        existing = set()
        if len(rec) > 0:
            lo = rec['match_date'].min()
            rows = conn.execute(text(
                "SELECT match_date, home_team, away_team, market, section "
                "FROM prediction_record WHERE match_date >= :lo"), {'lo': lo.to_pydatetime()})
            for r in rows:
                existing.add((pd.Timestamp(r[0]), r[1], r[2], r[3], r[4]))
        mask = [
            (pd.Timestamp(r.match_date), r.home_team, r.away_team, r.market, r.section) not in existing
            for r in rec.itertuples()
        ]
        new_rec = rec[mask]
        rcols = ['match_date', 'home_team', 'away_team', 'market', 'section',
                 'probability', 'first_probability', 'first_real_odds', 'first_pro_tier',
                 'real_odds', 'ev_percent', 'pro_tier', 'result', 'settled_at',
                 'shown_date', 'created_at']
        rcols = [c for c in rcols if c in t_rec.c]
        recs2 = clean_records(new_rec, rcols)
        for i in range(0, len(recs2), CHUNK):
            conn.execute(t_rec.insert(), recs2[i:i + CHUNK])

        after_d = conn.execute(text('SELECT COUNT(*) FROM prediction_daily')).scalar()
        after_r = conn.execute(text('SELECT COUNT(*) FROM prediction_record')).scalar()

    print(f"\n✅ prediction_daily: {before_d:,} -> {after_d:,}  (+{after_d - before_d:,})")
    print(f"✅ prediction_record: {before_r:,} -> {after_r:,}  (+{after_r - before_r:,}, zote PENDING)")
    print("ℹ️ Job A (hourly_catchup) itazisuluhisha WON/LOST/VOID kutoka dataset kwenye run zake zijazo.")


if __name__ == '__main__':
    main()