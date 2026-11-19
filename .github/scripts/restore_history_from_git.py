"""
.github/scripts/restore_history_from_git.py

v5:
- Imeondoa 'options=-c statement_timeout' (Neon pooler haikubali)
- Kwa kila siku, inajaribu commits zote za siku hiyo (kutoka ya mwisho kurudi nyuma)
  hadi ipate moja yenye data (commit ya mwisho ikiwa tupu haipotezi siku)
- Kila chunk ina commit yake + retry mara 3, keepalive, log yenye muda
- Salama kurudia: haiongezi nakala

Env: DATABASE_URL (lazima), DAYS (default 7), DRY_RUN (yes/no),
     MAX_DAYS_AHEAD (default 3)
"""
import io
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, OperationalError

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

T0 = time.time()


def log(msg):
    print(f"[{time.time() - T0:7.1f}s] {msg}", flush=True)


EAT = timedelta(hours=3)
DAYS = int(os.environ.get('DAYS') or 7)
AHEAD = int(os.environ.get('MAX_DAYS_AHEAD') or 3)
DRY = (os.environ.get('DRY_RUN') or 'no').strip().lower() in ('yes', 'true', '1')
DB_URL = (os.environ.get('DATABASE_URL') or '').strip()
CHUNK = 1000
GIT_TIMEOUT = 180
RETRIES = 3

if not DB_URL:
    log("❌ DATABASE_URL haipo (angalia GitHub Secrets).")
    sys.exit(1)

if DB_URL.startswith('postgres://'):
    DB_URL = 'postgresql+psycopg2://' + DB_URL[len('postgres://'):]
elif DB_URL.startswith('postgresql://'):
    DB_URL = 'postgresql+psycopg2://' + DB_URL[len('postgresql://'):]


# ------------------------------------------------------------------ git
def run_git(args):
    try:
        r = subprocess.run(['git'] + args, capture_output=True, timeout=GIT_TIMEOUT)
        return r.returncode, r.stdout
    except subprocess.TimeoutExpired:
        log(f"   ⚠️ git {' '.join(args[:2])} imezidi {GIT_TIMEOUT}s - naruka")
        return 1, b''


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
        log(f"   ⚠️ {path}@{sha[:7]} haisomeki ({e})")
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


def normalize(df, section, snap_date, quiet=False):
    need = ['match_date', 'HomeTeam', 'AwayTeam', 'market']
    miss = [c for c in need if c not in df.columns]
    if miss:
        if not quiet:
            log(f"   ⚠️ {section}: safu zinazokosekana {miss} - naruka")
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


# ------------------------------------------------------------------ db
def make_engine():
    # Hakuna 'options' (Neon pooler haikubali startup parameters kama statement_timeout)
    return create_engine(
        DB_URL,
        pool_pre_ping=True,
        connect_args={
            'connect_timeout': 30,
            'keepalives': 1,
            'keepalives_idle': 30,
            'keepalives_interval': 10,
            'keepalives_count': 5,
        },
    )


def write_chunks(engine, table, recs, label, skip_conflict):
    total = len(recs)
    if total == 0:
        log(f"   {label}: hakuna cha kuandika")
        return engine
    for i in range(0, total, CHUNK):
        part = recs[i:i + CHUNK]
        for attempt in range(1, RETRIES + 1):
            try:
                with engine.begin() as conn:
                    if skip_conflict:
                        conn.execute(pg_insert(table).on_conflict_do_nothing(), part)
                    else:
                        conn.execute(table.insert(), part)
                break
            except (OperationalError, DBAPIError) as e:
                log(f"   ⚠️ {label} chunk {i // CHUNK + 1} ilifeli (jaribio {attempt}/{RETRIES}): {str(e)[:200]}")
                try:
                    engine.dispose()
                except Exception:
                    pass
                if attempt == RETRIES:
                    log("❌ Imeshindwa baada ya majaribio yote. Zilizokwisha andikwa zimebaki; endesha tena.")
                    sys.exit(1)
                time.sleep(5 * attempt)
        if (i // CHUNK) % 5 == 0 or i + CHUNK >= total:
            log(f"   {label}: {min(i + CHUNK, total):,}/{total:,}")
    return engine


# ------------------------------------------------------------------ main
def main():
    now_utc = datetime.now(timezone.utc)
    now_eat = (now_utc + EAT).replace(tzinfo=None)
    today = now_eat.date()
    first_day = today - timedelta(days=DAYS - 1)
    since = (now_utc - timedelta(days=DAYS + 3)).strftime('%Y-%m-%dT%H:%M:%SZ')

    log(f"📅 Leo (EAT): {today} | nairudisha {DAYS} siku: {first_day} hadi {today} | DRY_RUN={DRY}")

    commits = list_commits('data/predictions.csv', since)
    log(f"   Commits za predictions.csv: {len(commits)}")
    if not commits:
        log("❌ Hakuna commit za data/predictions.csv kwenye git history.")
        sys.exit(1)

    # commits zote za kila siku (kwa mpangilio wa muda, za zamani kwanza)
    per_day = {}
    for sha, t in commits:
        d = (t + EAT).date()
        if first_day <= d <= today:
            per_day.setdefault(d, []).append(sha)

    if not per_day:
        log("❌ Hakuna commit ndani ya siku zilizoombwa.")
        sys.exit(1)

    frames = []
    days_with_data = 0
    empty_days = []
    for d in sorted(per_day):
        shas = per_day[d]
        t1 = time.time()
        used = None
        np_ = nv_ = 0
        tried = 0
        # jaribu kuanzia commit ya MWISHO ya siku, rudi nyuma hadi upate yenye data
        for sha in reversed(shas):
            tried += 1
            p = read_csv_at(sha, 'data/predictions.csv')
            v = read_csv_at(sha, 'data/value_bets.csv')
            xp = normalize(p, 'prediction', d, quiet=True) if p is not None else None
            xv = normalize(v, 'value_bet', d, quiet=True) if v is not None else None
            n1 = len(xp) if xp is not None else 0
            n2 = len(xv) if xv is not None else 0
            if n1 + n2 > 0:
                if n1:
                    frames.append(xp)
                if n2:
                    frames.append(xv)
                used, np_, nv_ = sha, n1, n2
                break
        if used:
            days_with_data += 1
            extra = f" (nimejaribu commit {tried}/{len(shas)})" if tried > 1 else ""
            log(f"   {d}  commit {used[:7]}  predictions={np_:,}  value_bets={nv_:,}{extra}  ({time.time() - t1:.1f}s)")
        else:
            empty_days.append(d)
            log(f"   {d}  ⚠️ hakuna data kwenye commits {len(shas)} za siku hii")

    if not frames:
        log("❌ Hakuna mistari iliyosomeka.")
        sys.exit(1)
    allrows = pd.concat(frames, ignore_index=True)
    log(f"📦 Jumla ya mistari ya prediction_daily: {len(allrows):,}")
    log(f"📦 Siku zenye data: {days_with_data} kati ya {DAYS}  (siku zisizo na data: {len(empty_days)})")

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
    log(f"📦 Rekodi za prediction_record (mechi zilizokwisha anza): {len(rec):,}")

    if DRY:
        log("ℹ️ DRY_RUN=yes - hakuna kilichoandikwa kwenye database.")
        return

    log("🔌 Naunganisha database...")
    engine = make_engine()
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    log("🔌 Imeunganika.")
    missing = [t for t in ('prediction_daily', 'prediction_record') if t not in tables]
    if missing:
        log(f"❌ Jedwali hazipo kwenye database mpya: {missing}")
        log("   Fungua app (Render) mara moja ili db.create_all() izitengeneze, kisha endesha tena.")
        sys.exit(1)

    md = MetaData()
    t_daily = Table('prediction_daily', md, autoload_with=engine)
    t_rec = Table('prediction_record', md, autoload_with=engine)

    with engine.connect() as conn:
        before_d = conn.execute(text('SELECT COUNT(*) FROM prediction_daily')).scalar()
        before_r = conn.execute(text('SELECT COUNT(*) FROM prediction_record')).scalar()
    log(f"   Kabla: daily={before_d:,} record={before_r:,}")

    # --- prediction_daily
    dcols = ['snap_date', 'section', 'match_date', 'home_team', 'away_team',
             'market', 'probability', 'real_odds', 'pro_tier']
    dcols = [c for c in dcols if c in t_daily.c]
    recs = clean_records(allrows, dcols)
    engine = write_chunks(engine, t_daily, recs, 'prediction_daily', True)

    # --- prediction_record (ukaguzi wa nakala kwa merge)
    new_rec = rec
    if len(rec) > 0:
        lo = rec['match_date'].min().to_pydatetime()
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT match_date, home_team, away_team, market, section "
                "FROM prediction_record WHERE match_date >= :lo"), {'lo': lo}).fetchall()
        log(f"   prediction_record zilizopo kwenye kipindi hiki: {len(rows):,}")
        if rows:
            ex = pd.DataFrame(rows, columns=['match_date', 'home_team', 'away_team', 'market', 'section'])
            ex['match_date'] = pd.to_datetime(ex['match_date'], errors='coerce')
            if getattr(ex['match_date'].dt, 'tz', None) is not None:
                ex['match_date'] = ex['match_date'].dt.tz_localize(None)
            ex = ex.drop_duplicates()
            ex['_exists'] = 1
            merged = rec.merge(ex, on=['match_date', 'home_team', 'away_team', 'market', 'section'], how='left')
            new_rec = merged[merged['_exists'].isna()].drop(columns=['_exists'])
    log(f"   prediction_record mpya za kuingiza: {len(new_rec):,}")

    rcols = ['match_date', 'home_team', 'away_team', 'market', 'section',
             'probability', 'first_probability', 'first_real_odds', 'first_pro_tier',
             'real_odds', 'ev_percent', 'pro_tier', 'result', 'settled_at',
             'shown_date', 'created_at']
    rcols = [c for c in rcols if c in t_rec.c]
    recs2 = clean_records(new_rec, rcols)
    engine = write_chunks(engine, t_rec, recs2, 'prediction_record', False)

    with engine.connect() as conn:
        after_d = conn.execute(text('SELECT COUNT(*) FROM prediction_daily')).scalar()
        after_r = conn.execute(text('SELECT COUNT(*) FROM prediction_record')).scalar()

    log(f"✅ prediction_daily: {before_d:,} -> {after_d:,}  (+{after_d - before_d:,})")
    log(f"✅ prediction_record: {before_r:,} -> {after_r:,}  (+{after_r - before_r:,}, zote PENDING)")
    log("ℹ️ Job A (hourly_catchup) itazisuluhisha WON/LOST/VOID kwenye run zake zijazo.")


if __name__ == '__main__':
    main()