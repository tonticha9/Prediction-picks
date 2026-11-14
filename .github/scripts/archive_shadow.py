"""
.github/scripts/archive_shadow.py  (v2 - Nov 14 2026)

Inahifadhi utabiri wa "kivuli" wa v8 (na wa v6 wa siku hiyo kwa ulinganisho) kwenye repo.
HAIVUNJI chochote: faili ikikosekana au kitu kikishindwa, inaonya tu na kumaliza vizuri.

v2: tarehe (match_date) zinasomwa kwa muundo WOWOTE (2026-11-14, 2026-11-14 00:00:00,
    14/11/2026, ...) na kuandikwa upya kama YYYY-MM-DD. Safu zisizo na tarehe inayosomeka
    zinahifadhiwa (si kuachwa kimya). Faili ya app HAIFUTWI kwa data tupu.

Matokeo:
  data/predictions_v8.csv              mechi za leo hadi siku 4 mbele (kwa app)
  shadow/archive/<tarehe>_v8.csv.gz    utabiri wa v8 (leo hadi siku 3)
  shadow/archive/<tarehe>_v6.csv.gz    utabiri wa v6 (predictions.csv) wa siku hiyo hiyo
  shadow/health_log.csv                alama za ukaguzi wa muundo kila siku
Pia inaandika muhtasari wa ulinganisho v6 dhidi ya v8 kwenye GITHUB_STEP_SUMMARY.
"""
import os
import sys
import json
import glob
import datetime

import pandas as pd

KO = 'kaggle_output'
V8 = os.path.join(KO, 'predictions_v8.csv')
V6 = os.path.join(KO, 'predictions.csv')
HEALTH = os.path.join(KO, 'v8_health.json')

LATEST_DAYS = 4        # data/predictions_v8.csv: mechi za leo hadi siku 4 mbele
ARCHIVE_DAYS = 3       # archive: mechi za leo hadi siku 3 mbele
KEEP_DAYS = 150        # futa archive zaidi ya siku hizi
KEYS = ['HomeTeam', 'AwayTeam', 'match_date', 'market']


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
    """Rudisha (maandishi YYYY-MM-DD au '' kama haisomeki). Inakubali miundo mchanganyiko."""
    s = series.astype(str).str.strip()
    s = s.where(~s.str.lower().isin(['', 'nan', 'nat', 'none']), '')
    out = pd.Series('', index=s.index, dtype=object)

    # 1) YYYY-MM-DD mwanzoni (pamoja na saa nyuma yake)
    iso = pd.to_datetime(s.str[:10], format='%Y-%m-%d', errors='coerce')
    ok = iso.notna()
    out[ok] = iso[ok].dt.strftime('%Y-%m-%d')

    # 2) zilizobaki: jaribu miundo mingine (mfano 14/11/2026, 14.11.2026)
    rest = (~ok) & (s != '')
    if rest.any():
        sub = s[rest].str.replace('.', '/', regex=False)
        try:
            p = pd.to_datetime(sub, format='mixed', dayfirst=True, errors='coerce')
        except Exception:
            p = pd.to_datetime(sub, dayfirst=True, errors='coerce')
        good = p.notna()
        idx = p.index[good]
        out.loc[idx] = p[good].dt.strftime('%Y-%m-%d')
    return out


def prep(df, name):
    """Safisha match_date. Rudisha (df, idadi ya safu zisizo na tarehe inayosomeka)."""
    df = df.copy()
    df['match_date'] = parse_dates(df['match_date'])
    bad = int((df['match_date'] == '').sum())
    ok = df.loc[df['match_date'] != '', 'match_date']
    rng = f"{ok.min()} .. {ok.max()}" if len(ok) else "hakuna tarehe inayosomeka"
    print(f"ℹ️ {name}: safu {len(df):,} | tarehe {rng} | zisizosomeka {bad:,}")
    return df, bad


def window(df, today, days):
    """Mechi za leo hadi siku 'days' mbele. Safu zisizo na tarehe zinahifadhiwa
    (faili hizi zina mechi zijazo tu, kwa hiyo si salama kuzitupa kimya)."""
    lo = today.isoformat()
    hi = (today + datetime.timedelta(days=days)).isoformat()
    d = df['match_date']
    keep = ((d >= lo) & (d <= hi)) | (d == '')
    return df[keep]


def md_table(df):
    cols = list(df.columns)
    lines = ['| ' + ' | '.join(cols) + ' |', '|' + '|'.join(['---'] * len(cols)) + '|']
    for _, r in df.iterrows():
        lines.append('| ' + ' | '.join(str(r[c]) for c in cols) + ' |')
    return '\n'.join(lines)


def n_matches(df):
    return df[['HomeTeam', 'AwayTeam', 'match_date']].drop_duplicates().shape[0] if len(df) else 0


def main():
    if not os.path.exists(V8):
        print("ℹ️ predictions_v8.csv haipo - hakuna kivuli cha kuhifadhi (sawa).")
        return

    today = datetime.datetime.utcnow().date()
    d0 = today.isoformat()
    v8 = pd.read_csv(V8)
    if v8.empty or 'match_date' not in v8.columns:
        print("⚠️ predictions_v8.csv ni tupu au haina match_date - naruka.")
        return
    v8, bad8 = prep(v8, 'v8')
    v6 = pd.read_csv(V6) if os.path.exists(V6) else pd.DataFrame()
    bad6 = 0
    if len(v6) and 'match_date' in v6.columns:
        v6, bad6 = prep(v6, 'v6')
    else:
        v6 = pd.DataFrame()

    os.makedirs('data', exist_ok=True)
    os.makedirs(os.path.join('shadow', 'archive'), exist_ok=True)

    # 1) Faili ya hivi karibuni kwa app (HAIFUTWI kwa data tupu)
    latest = window(v8, today, LATEST_DAYS)
    if len(latest):
        latest.to_csv(os.path.join('data', 'predictions_v8.csv'), index=False)
        print(f"✅ data/predictions_v8.csv: safu {len(latest):,}, mechi {n_matches(latest)}")
    else:
        print(f"::warning::Hakuna safu za v8 kati ya {d0} na siku {LATEST_DAYS} mbele - "
              f"data/predictions_v8.csv ya zamani imebaki (haikufutwa).")
    if bad8:
        print(f"::warning::Safu {bad8:,} za v8 hazina tarehe inayosomeka (zimehifadhiwa bila tarehe).")

    # 2) Archive ya kudumu (compressed) - hakuna faili tupu
    a8 = window(v8, today, ARCHIVE_DAYS)
    if len(a8):
        a8.to_csv(os.path.join('shadow', 'archive', f'{d0}_v8.csv.gz'), index=False, compression='gzip')
    a6 = window(v6, today, ARCHIVE_DAYS) if len(v6) else pd.DataFrame()
    if len(a6):
        a6.to_csv(os.path.join('shadow', 'archive', f'{d0}_v6.csv.gz'), index=False, compression='gzip')
    print(f"✅ archive ya {d0}: v8 safu {len(a8):,}, v6 safu {len(a6):,}")
    if len(a8) == 0 or (len(v6) and len(a6) == 0):
        print("::warning::Archive ya leo ina safu 0 kwa v6 au v8 - angalia muundo wa match_date hapo juu.")

    # 3) Health log
    if os.path.exists(HEALTH):
        try:
            h = json.load(open(HEALTH))
            path = os.path.join('shadow', 'health_log.csv')
            new = not os.path.exists(path)
            with open(path, 'a') as f:
                if new:
                    f.write('date,version,passed,total,recalibrated,fixtures\n')
                f.write(f"{d0},{h.get('version')},{h.get('passed')},{h.get('total')},"
                        f"{h.get('recalibrated')},{h.get('fixtures')}\n")
        except Exception as e:
            print(f"⚠️ health log imeshindwa: {e}")

    # 4) Ulinganisho v6 dhidi ya v8 (mechi zilezile, masoko yale yale)
    if len(v6) and len(latest):
        a = window(v6, today, LATEST_DAYS)[KEYS + ['probability_%']].rename(columns={'probability_%': 'p6'})
        b = latest[KEYS + ['probability_%']].rename(columns={'probability_%': 'p8'})
        m = a.merge(b, on=KEYS, how='inner')
        n6, nm = len(a), len(m)
        print(f"Ulinganisho: safu za v6 {n6:,}, zilizopatikana pia kwenye v8 {nm:,}")
        if nm:
            m['diff'] = m['p8'] - m['p6']
            overall = m['diff'].abs().mean()
            print(f"Tofauti ya wastani (v8 - v6) kwa pointi: mutlaki {overall:.2f}, yenye ishara {m['diff'].mean():+.2f}")
            g = m.groupby('market')['diff'].agg(['count', 'mean', lambda s: s.abs().mean()])
            g.columns = ['safu', 'v8-v6 (wastani)', '|tofauti|']
            g = g[g['safu'] >= 20].sort_values('|tofauti|', ascending=False).head(15).round(2).reset_index()
            print(g.to_string(index=False))
            summary(f"### Kivuli v8 dhidi ya v6 ({d0})")
            summary(f"Safu zilizolinganishwa: **{nm:,}** kati ya {n6:,} za v6 | tofauti ya wastani ya mutlaki: **{overall:.2f}** pointi, "
                    f"yenye ishara: **{m['diff'].mean():+.2f}** (chanya = v8 inatoa probability kubwa zaidi)")
            summary(md_table(g))
        miss = n6 - nm
        if n6 and miss / n6 > 0.02:
            print(f"::warning::Safu {miss:,} za v6 hazipo kwenye v8 (zaidi ya 2%) - angalia majina ya timu/masoko.")

    # 5) Futa archive za zamani
    cutoff = today - datetime.timedelta(days=KEEP_DAYS)
    removed = 0
    for fpath in glob.glob(os.path.join('shadow', 'archive', '*.csv.gz')):
        try:
            fdate = datetime.date.fromisoformat(os.path.basename(fpath)[:10])
            if fdate < cutoff:
                os.remove(fpath)
                removed += 1
        except Exception:
            pass
    if removed:
        print(f"🧹 Archive za zamani zimefutwa: {removed}")


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"::warning::archive_shadow imeshindwa (haiathiri app): {e!r}")
    sys.exit(0)