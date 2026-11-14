"""
.github/scripts/v8_compare.py  (V1 - Nov 14 2026)

Inapima V8 dhidi ya v6 kwa kila soko, kwa mechi ZILIZOKWISHA, kwa kutumia utabiri
ulihifadhiwa KABLA ya mechi (shadow/archive/<tarehe>_v6.csv.gz na _v8.csv.gz) na
matokeo halisi kutoka kwenye database ya app (jedwali match_result), kupitia market_evaluator.

Kanuni:
  - Kwa kila (mechi, soko) tunachukua faili ya karibuni zaidi yenye tarehe <= tarehe ya mechi
    (utabiri wa mwisho kabla ya mechi) - kwa v6 na v8 kwa njia ile ile.
  - Tunalinganisha TU mechi/masoko yaliyopo kwenye v6 na v8 zote mbili na yenye matokeo ya uhakika.
  - Kipimo kikuu: log loss (ndogo = bora), tofauti ya v8 - v6 na z-score ya jozi.
Matokeo: data/v8_vs_v6.csv, data/v8_vs_v6_summary.json na muhtasari kwenye GITHUB_STEP_SUMMARY.
"""
import os
import sys
import glob
import json
import math
import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.join(os.getcwd(), '.github', 'scripts'))

ARCHIVE = os.path.join('shadow', 'archive')
OUT_CSV = os.path.join('data', 'v8_vs_v6.csv')
OUT_JSON = os.path.join('data', 'v8_vs_v6_summary.json')
KEYS = ['HomeTeam', 'AwayTeam', 'match_date', 'market']
MIN_N = 100          # chini ya hapa: "data haitoshi"
Z_CUT = 2.0          # |z| >= 2 = tofauti ya kweli
PICK_P = 0.65        # kizingiti cha "pick" kwa winrate
RES_COLS = ['fthg', 'ftag', 'hthg', 'htag', 'hc', 'ac', 'hy', 'ay', 'hr', 'ar', 'hst', 'ast']


def summary(text):
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if not path:
        return
    try:
        with open(path, 'a') as f:
            f.write(text + "\n")
    except Exception:
        pass


def parse_dates(series):
    s = series.astype(str).str.strip()
    s = s.where(~s.str.lower().isin(['', 'nan', 'nat', 'none']), '')
    out = pd.Series('', index=s.index, dtype=object)
    iso = pd.to_datetime(s.str[:10], format='%Y-%m-%d', errors='coerce')
    ok = iso.notna()
    out[ok] = iso[ok].dt.strftime('%Y-%m-%d')
    rest = (~ok) & (s != '')
    if rest.any():
        sub = s[rest].str.replace('.', '/', regex=False)
        try:
            p = pd.to_datetime(sub, format='mixed', dayfirst=True, errors='coerce')
        except Exception:
            p = pd.to_datetime(sub, dayfirst=True, errors='coerce')
        good = p.notna()
        out.loc[p.index[good]] = p[good].dt.strftime('%Y-%m-%d')
    return out


def load_archive(version):
    """Utabiri wa mwisho kabla ya mechi kwa kila (mechi, soko)."""
    frames, files, skipped = [], 0, 0
    for path in sorted(glob.glob(os.path.join(ARCHIVE, f'*_{version}.csv.gz'))):
        snap = os.path.basename(path)[:10]
        try:
            df = pd.read_csv(path)
        except Exception:
            skipped += 1
            continue
        need = set(KEYS) | {'probability_%'}
        if df.empty or not need.issubset(df.columns):
            skipped += 1
            continue
        df = df[KEYS + ['probability_%']].copy()
        df['match_date'] = parse_dates(df['match_date'])
        df['HomeTeam'] = df['HomeTeam'].astype(str).str.strip()
        df['AwayTeam'] = df['AwayTeam'].astype(str).str.strip()
        df = df[(df['match_date'] != '') & (df['match_date'] >= snap)]
        if df.empty:
            skipped += 1
            continue
        df['snap'] = snap
        frames.append(df)
        files += 1
    if not frames:
        return pd.DataFrame(columns=KEYS + ['p', 'snap']), files, skipped
    all_ = pd.concat(frames, ignore_index=True)
    all_['p'] = pd.to_numeric(all_['probability_%'], errors='coerce') / 100.0
    all_ = all_.dropna(subset=['p'])
    all_ = all_.sort_values('snap').drop_duplicates(KEYS, keep='last')
    return all_[KEYS + ['p', 'snap']], files, skipped


def load_results():
    from sqlalchemy import create_engine, text
    url = os.environ.get('DATABASE_URL', '').strip()
    if not url:
        raise RuntimeError("DATABASE_URL haipo")
    if url.startswith('postgres://'):
        url = 'postgresql://' + url[len('postgres://'):]
    eng = create_engine(url)
    since = (datetime.date.today() - datetime.timedelta(days=120)).isoformat()
    q = text("SELECT match_date, home_team, away_team, " + ", ".join(RES_COLS) +
             " FROM match_result WHERE match_date >= :d")
    with eng.connect() as c:
        df = pd.read_sql(q, c, params={'d': since})
    df['match_date'] = parse_dates(df['match_date'])
    df['home_team'] = df['home_team'].astype(str).str.strip()
    df['away_team'] = df['away_team'].astype(str).str.strip()
    return df


def build_lookup(res):
    lk = {}
    for r in res.to_dict('records'):
        row = {}
        for c in RES_COLS:
            v = r.get(c)
            try:
                v = None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)
            except Exception:
                v = None
            row[c.upper()] = v
        lk[(r['home_team'], r['away_team'], r['match_date'])] = row
    return lk


def find_result(lk, h, a, d):
    """Tarehe halisi, kisha siku moja kabla/baada (tofauti ya saa/UTC)."""
    if (h, a, d) in lk:
        return lk[(h, a, d)]
    try:
        dd = datetime.date.fromisoformat(d)
    except Exception:
        return None
    for k in (-1, 1):
        key = (h, a, (dd + datetime.timedelta(days=k)).isoformat())
        if key in lk:
            return lk[key]
    return None


def logloss(y, p):
    p = np.clip(p, 0.001, 0.999)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def md_table(df):
    cols = list(df.columns)
    lines = ['| ' + ' | '.join(cols) + ' |', '|' + '|'.join(['---'] * len(cols)) + '|']
    for _, r in df.iterrows():
        lines.append('| ' + ' | '.join(str(r[c]) for c in cols) + ' |')
    return '\n'.join(lines)


def main():
    try:
        from market_evaluator import evaluate_market
    except Exception as e:
        print(f"::error::market_evaluator.py haipatikani: {e!r}")
        return 1

    v6, f6, s6 = load_archive('v6')
    v8, f8, s8 = load_archive('v8')
    print(f"Archive: v6 faili {f6} (zilizorukwa {s6}), safu {len(v6):,} | v8 faili {f8} (zilizorukwa {s8}), safu {len(v8):,}")
    if v6.empty or v8.empty:
        print("::error::Archive ya v6 au v8 haina safu zinazotumika.")
        return 1

    m = v6.rename(columns={'p': 'p6', 'snap': 'snap6'}).merge(
        v8.rename(columns={'p': 'p8', 'snap': 'snap8'}), on=KEYS, how='inner')
    print(f"Safu zilizo kwenye v6 na v8 zote: {len(m):,}")
    if m.empty:
        print("::error::Hakuna safu zinazofanana kati ya v6 na v8 (angalia majina ya timu/masoko).")
        return 1

    try:
        res = load_results()
    except Exception as e:
        print(f"::error::Database imeshindwa: {e!r}")
        return 1
    lk = build_lookup(res)
    print(f"Matokeo kwenye database (siku 120): {len(res):,}")

    matches = m[['HomeTeam', 'AwayTeam', 'match_date']].drop_duplicates()
    mapping = {}
    for h, a, d in matches.itertuples(index=False):
        r = find_result(lk, h, a, d)
        if r is not None:
            mapping[(h, a, d)] = r
    print(f"Mechi za archive: {len(matches):,} | zenye matokeo: {len(mapping):,}")
    if not mapping:
        unm = matches.head(5).values.tolist()
        print(f"::error::Hakuna mechi iliyopata matokeo. Mfano wa majina ya archive: {unm}")
        return 1

    ys = []
    for h, a, d, mk in m[KEYS].itertuples(index=False):
        row = mapping.get((h, a, d))
        if row is None:
            ys.append(np.nan)
            continue
        try:
            r = evaluate_market(mk, row)
        except Exception:
            r = None
        ys.append(np.nan if r is None else (1.0 if r else 0.0))
    m['y'] = ys
    m = m.dropna(subset=['y']).copy()
    print(f"Safu zenye jibu la uhakika (WON/LOST): {len(m):,}")
    if m.empty:
        print("::error::Hakuna safu yenye matokeo ya uhakika.")
        return 1

    m['ll6'] = logloss(m['y'].values, m['p6'].values)
    m['ll8'] = logloss(m['y'].values, m['p8'].values)
    m['d'] = m['ll8'] - m['ll6']

    rows = []
    for mk, g in m.groupby('market'):
        n = len(g)
        md = g['d'].mean()
        se = g['d'].std(ddof=1) / math.sqrt(n) if n > 1 else float('nan')
        z = md / se if se and se > 0 and not math.isnan(se) else 0.0
        rate = g['y'].mean()
        base_ll = float(logloss(g['y'].values, np.full(n, min(max(rate, 0.001), 0.999))).mean())
        p6s, p8s = g[g['p6'] >= PICK_P], g[g['p8'] >= PICK_P]
        if n < MIN_N:
            verdict = 'data haitoshi'
        elif z <= -Z_CUT:
            verdict = 'V8 BORA'
        elif z >= Z_CUT:
            verdict = 'v6 bora'
        else:
            verdict = 'sawa'
        rows.append({
            'market': mk, 'n': n, 'base_rate': round(rate, 3),
            'll_v6': round(float(g['ll6'].mean()), 4), 'll_v8': round(float(g['ll8'].mean()), 4),
            'diff_v8_minus_v6': round(float(md), 4), 'z': round(float(z), 2),
            'skill_v6_%': round(100 * (1 - g['ll6'].mean() / base_ll), 2),
            'skill_v8_%': round(100 * (1 - g['ll8'].mean() / base_ll), 2),
            'gap_v6_pts': round(100 * float(g['p6'].mean() - rate), 1),
            'gap_v8_pts': round(100 * float(g['p8'].mean() - rate), 1),
            'picks_v6': len(p6s), 'win_v6_%': round(100 * float(p6s['y'].mean()), 1) if len(p6s) else None,
            'picks_v8': len(p8s), 'win_v8_%': round(100 * float(p8s['y'].mean()), 1) if len(p8s) else None,
            'verdict': verdict,
        })
    rep = pd.DataFrame(rows).sort_values('z')
    os.makedirs('data', exist_ok=True)
    rep.to_csv(OUT_CSV, index=False)

    ok = rep[rep['n'] >= MIN_N]
    summ = {
        'generated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        'archive_files_v6': f6, 'archive_files_v8': f8,
        'matches_with_results': len(mapping), 'rows_evaluated': int(len(m)),
        'markets_total': int(len(rep)), 'markets_tested': int(len(ok)),
        'v8_better': int((ok['verdict'] == 'V8 BORA').sum()),
        'v6_better': int((ok['verdict'] == 'v6 bora').sum()),
        'same': int((ok['verdict'] == 'sawa').sum()),
        'mean_diff_ll': round(float(ok['diff_v8_minus_v6'].mean()), 4) if len(ok) else None,
        'first_match': m['match_date'].min(), 'last_match': m['match_date'].max(),
    }
    with open(OUT_JSON, 'w') as f:
        json.dump(summ, f, ensure_ascii=False, indent=1)

    print("\n=== MUHTASARI ===")
    print(json.dumps(summ, ensure_ascii=False, indent=1))
    show = rep[['market', 'n', 'll_v6', 'll_v8', 'diff_v8_minus_v6', 'z', 'skill_v6_%', 'skill_v8_%',
                'win_v6_%', 'win_v8_%', 'verdict']]
    print(show.to_string(index=False))

    summary("## V8 dhidi ya v6 (mechi zilizokwisha)")
    summary(f"Mechi zenye matokeo: **{len(mapping):,}** | mechi {summ['first_match']} hadi {summ['last_match']} | "
            f"masoko yaliyopimwa (n>={MIN_N}): **{summ['markets_tested']}** | "
            f"**V8 bora: {summ['v8_better']}** | v6 bora: {summ['v6_better']} | sawa: {summ['same']} | "
            f"tofauti ya wastani ya log loss (V8 - v6): **{summ['mean_diff_ll']}** (hasi = V8 bora)")
    summary(md_table(show))
    return 0


if __name__ == '__main__':
    try:
        rc = main()
    except Exception as e:
        print(f"::error::v8_compare imeshindwa: {e!r}")
        rc = 1
    sys.exit(rc)