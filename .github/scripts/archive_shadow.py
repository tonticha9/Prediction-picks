"""
.github/scripts/archive_shadow.py

Inahifadhi utabiri wa "kivuli" wa v8 (na wa v6 wa siku hiyo kwa ulinganisho) kwenye repo.
HAIVUNJI chochote: faili ikikosekana au kitu kikishindwa, inaonya tu na kumaliza vizuri.

Matokeo:
  data/predictions_v8.csv              mechi za leo hadi siku 4 mbele (kwa app, hatua ya 3)
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


def window(df, today, days):
    lo = today.isoformat()
    hi = (today + datetime.timedelta(days=days)).isoformat()
    return df[(df['match_date'] >= lo) & (df['match_date'] <= hi)]


def md_table(df):
    cols = list(df.columns)
    lines = ['| ' + ' | '.join(cols) + ' |', '|' + '|'.join(['---'] * len(cols)) + '|']
    for _, r in df.iterrows():
        lines.append('| ' + ' | '.join(str(r[c]) for c in cols) + ' |')
    return '\n'.join(lines)


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
    v8['match_date'] = v8['match_date'].astype(str)
    v6 = pd.read_csv(V6) if os.path.exists(V6) else pd.DataFrame()
    if len(v6):
        v6['match_date'] = v6['match_date'].astype(str)

    os.makedirs('data', exist_ok=True)
    os.makedirs(os.path.join('shadow', 'archive'), exist_ok=True)

    # 1) Faili ya hivi karibuni kwa app
    latest = window(v8, today, LATEST_DAYS)
    latest.to_csv(os.path.join('data', 'predictions_v8.csv'), index=False)
    print(f"✅ data/predictions_v8.csv: safu {len(latest):,}, mechi {latest[KEYS[:3]].drop_duplicates().shape[0]}")

    # 2) Archive ya kudumu (compressed)
    a8 = window(v8, today, ARCHIVE_DAYS)
    a8.to_csv(os.path.join('shadow', 'archive', f'{d0}_v8.csv.gz'), index=False, compression='gzip')
    if len(v6):
        a6 = window(v6, today, ARCHIVE_DAYS)
        a6.to_csv(os.path.join('shadow', 'archive', f'{d0}_v6.csv.gz'), index=False, compression='gzip')
    print(f"✅ archive ya {d0} imehifadhiwa (v8 safu {len(a8):,}" + (f", v6 safu {len(a6):,})" if len(v6) else ")"))

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
    if len(v6):
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