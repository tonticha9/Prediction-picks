"""
.github/scripts/merge_leagues.py  -  v1 (Nov 23 2026)

Inaendeshwa na nightly_predictions.yml BAADA ya predictions.csv, value_bets.csv na combos.csv kuwekwa kwenye data/.
Inafanya mambo manne, na kila moja linajilinda (likishindwa, data ya awali inabaki kama ilivyo):

  1. Inaongeza safu ya 'league' kwenye data/predictions.csv na data/value_bets.csv (ligi 12 za zamani + ligi 5 mpya).
  2. Inaongeza picks za ligi 5 mpya (kaggle_output/predictions_new5.csv) kwenye data/predictions.csv.
  3. Inatumia VIZINGITI (gates) kwa ligi 12 kama ligi 5: soko lisilopita gate ya ligi hiyo linaondolewa
     (gates12.json kutoka notebook add-league-lab-2). Masoko ambayo gates hazizifunika (corners, kadi n.k.) hayaguswi.
  4. Inaondoa combos zenye mguu uliofichwa na gates.

Faili za nje (hupakuliwa kiotomatiki; zikishindwa, nakala ya mwisho iliyohifadhiwa kwenye data/ inatumika):
  - gates12.json                 (kutoka Kaggle notebook tonticha/add-league-lab-2)  -> data/league_gates12.json
  - dataset historical-complete-no-gap (timu -> ligi)                                 -> data/team_league_map.csv

Udhibiti: env GATES12_ENABLED=0 inazima vizingiti vya ligi 12 (ligi bado zinaongezwa).
"""
import ast
import glob
import json
import os
import re
import subprocess
import sys
import zipfile

import pandas as pd

DATA = 'data'
KOUT = 'kaggle_output'
LAB_SLUG = os.environ.get('LAB_SLUG', 'tonticha/add-league-lab-2')
DS_SLUG = os.environ.get('KAGGLE_DATASET_SLUG', 'tonticha/historical-complete-no-gap')
DS_CSV = 'historical_complete_no_gap.csv'
GATES_CACHE = os.path.join(DATA, 'league_gates12.json')
MAP_CACHE = os.path.join(DATA, 'team_league_map.csv')
GATES_ENABLED = os.environ.get('GATES12_ENABLED', '1') != '0'
MAX_REMOVE_FRAC = 0.70      # ulinzi: gates zisiondoe zaidi ya 70% ya safu za masoko yanayofunikwa
PRED_COLS = ['HomeTeam', 'AwayTeam', 'match_date', 'market', 'probability_%', 'pro_tier', 'real_odds']
GATE_NAMES = ['home_win', 'away_win', 'dc_1x', 'dc_x2', 'dc_12', 'btts', 'btts_no', 'goals_over_05', 'goals_over_15',
              'goals_over_25', 'goals_over_35', 'goals_under_25', 'goals_under_35']


def log(msg):
    print(msg, flush=True)


def warn(msg):
    print(f"::warning::{msg}", flush=True)


def norm(s):
    return re.sub(r'\s+', ' ', str(s)).strip().lower()


# ---------------------------------------------------------------------------------- vizingiti (gates)
def kaggle(args):
    try:
        r = subprocess.run(['kaggle'] + args, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            log(f"   kaggle {' '.join(args[:3])} imeshindwa: {r.stderr.strip()[:200]}")
            return False
        return True
    except Exception as e:
        log(f"   kaggle imeshindwa: {type(e).__name__}: {e}")
        return False


def load_gates():
    """Rudisha (allowed: {liga: set(masoko)}, info). None ikiwa hakuna gates zinazoaminika."""
    fresh = None
    if os.environ.get('SKIP_KAGGLE_FETCH') != '1' and kaggle(['kernels', 'output', LAB_SLUG, '-p', 'lab_output']):
        cands = glob.glob('lab_output/**/gates12.json', recursive=True)
        if cands:
            try:
                with open(cands[0], encoding='utf-8') as f:
                    fresh = json.load(f)
                log(f"   gates12.json mpya imepakuliwa (last_played {fresh.get('last_played')})")
            except Exception as e:
                warn(f"gates12.json mpya imeharibika ({e}) - natumia nakala ya zamani.")
    gates = fresh
    if gates is None and os.path.exists(GATES_CACHE):
        try:
            with open(GATES_CACHE, encoding='utf-8') as f:
                gates = json.load(f)
            log(f"   natumia nakala iliyohifadhiwa ya gates (last_played {gates.get('last_played')})")
        except Exception as e:
            warn(f"nakala ya gates imeharibika ({e}).")
    if gates is None:
        return None
    try:
        prod = gates['prod_names']; show = gates['show']
        allowed = {lg: {prod[sd] for sd in sides} for lg, sides in show.items()}
        if not allowed or not all(len(v) > 0 for v in allowed.values()):
            warn("gates12.json haina masoko ya kuonyesha kwa baadhi ya ligi - vizingiti vimerukwa.")
            return None
    except Exception as e:
        warn(f"muundo wa gates12.json si sahihi ({type(e).__name__}: {e}) - vizingiti vimerukwa.")
        return None
    if fresh is not None:
        try:
            with open(GATES_CACHE, 'w', encoding='utf-8') as f:
                json.dump(gates, f)
        except Exception as e:
            log(f"   siwezi kuhifadhi nakala ya gates: {e}")
    return allowed


# ---------------------------------------------------------------------------------- timu -> ligi
def team_league_map(df_list):
    """Rudisha dict {jina la timu (norm): ligi}. Kwanza dataset (ligi 12), kisha ligi 5 kutoka predictions_new5."""
    mp = {}
    path = None
    if os.environ.get('SKIP_KAGGLE_FETCH') != '1':
        os.makedirs('ds_tmp', exist_ok=True)
        if kaggle(['datasets', 'download', '-d', DS_SLUG, '-f', DS_CSV, '-p', 'ds_tmp', '-o']):
            for z in glob.glob('ds_tmp/*.zip'):
                try:
                    with zipfile.ZipFile(z) as zf:
                        zf.extractall('ds_tmp')
                except Exception as e:
                    log(f"   zip ya dataset imeshindwa kufunguliwa: {e}")
            p = os.path.join('ds_tmp', DS_CSV)
            if os.path.exists(p):
                path = p
    if path:
        try:
            d = pd.read_csv(path, usecols=['Date', 'league', 'HomeTeam', 'AwayTeam'], low_memory=False)
            d['Date'] = pd.to_datetime(d['Date'], errors='coerce')
            d = d.dropna(subset=['Date', 'league']).sort_values('Date')
            long = pd.concat([d[['Date', 'league', 'HomeTeam']].rename(columns={'HomeTeam': 'team'}),
                              d[['Date', 'league', 'AwayTeam']].rename(columns={'AwayTeam': 'team'})])
            last = long.sort_values('Date').drop_duplicates('team', keep='last')
            out = pd.DataFrame({'team': last['team'].astype(str), 'league': last['league'].astype(str)})
            out.to_csv(MAP_CACHE, index=False)
            log(f"   ramani ya timu->ligi imejengwa kutoka dataset ({len(out)} timu)")
        except Exception as e:
            warn(f"dataset imepakuliwa lakini haikusomeka ({type(e).__name__}: {e}) - natumia nakala ya zamani.")
    if os.path.exists(MAP_CACHE):
        try:
            c = pd.read_csv(MAP_CACHE)
            for t, lg in zip(c['team'], c['league']):
                mp[norm(t)] = str(lg)
        except Exception as e:
            warn(f"nakala ya ramani ya timu imeharibika: {e}")
    for df in df_list:   # ligi 5 (predictions_new5 ina safu ya 'league'); usiandike juu ya ramani ya ligi 12
        if df is not None and len(df) and 'league' in df.columns:
            for h, a, lg in zip(df['HomeTeam'], df['AwayTeam'], df['league']):
                mp.setdefault(norm(h), str(lg)); mp.setdefault(norm(a), str(lg))
    return mp


def league_of(home, away, mp):
    return mp.get(norm(home)) or mp.get(norm(away)) or ''


# ---------------------------------------------------------------------------------- kazi kuu
def read_csv_safe(path, **kw):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    try:
        return pd.read_csv(path, **kw)
    except Exception as e:
        warn(f"{path} haisomeki ({e}).")
        return None


def apply_gates(df, allowed, what):
    """Ondoa safu za masoko yanayofunikwa na gates ambazo hazikupita kwa ligi hiyo. Rudisha (df_mpya, idadi_iliyoondolewa)."""
    if df is None or not len(df) or allowed is None:
        return df, 0
    covered = df['market'].isin(GATE_NAMES) & df['league'].isin(allowed.keys())
    keep = pd.Series(True, index=df.index)
    for lg, ok in allowed.items():
        m = covered & (df['league'] == lg)
        keep[m] = df.loc[m, 'market'].isin(ok)
    n_cov = int(covered.sum()); n_rm = int((~keep).sum())
    if n_cov and n_rm / n_cov > MAX_REMOVE_FRAC:
        warn(f"{what}: gates zingeondoa {n_rm}/{n_cov} (>{int(MAX_REMOVE_FRAC * 100)}%) - ulinzi umezuia; data haijaguswa.")
        return df, 0
    return df[keep].copy(), n_rm


def main():
    pred = read_csv_safe(os.path.join(DATA, 'predictions.csv'))
    if pred is None or not len(pred):
        warn("data/predictions.csv haipo au ni tupu - merge_leagues imerukwa.")
        return 0
    n5 = read_csv_safe(os.path.join(KOUT, 'predictions_new5.csv'))
    if n5 is None:
        log("   predictions_new5.csv haipo kwenye output ya Kaggle - ligi 5 hazitaongezwa leo.")
    base_cols = [c for c in PRED_COLS if c in pred.columns]
    if len(base_cols) < len(PRED_COLS):
        warn(f"predictions.csv haina safu zote zinazotarajiwa ({set(PRED_COLS) - set(base_cols)}) - merge_leagues imerukwa.")
        return 0
    mp = team_league_map([n5])

    # 1. safu ya 'league' kwa ligi 12
    pred['league'] = [league_of(h, a, mp) for h, a in zip(pred['HomeTeam'], pred['AwayTeam'])]
    unk = pred.loc[pred['league'] == '', ['HomeTeam', 'AwayTeam']].drop_duplicates()
    log(f"   ligi 12: mechi {pred[['HomeTeam', 'AwayTeam', 'match_date']].drop_duplicates().shape[0]}, "
        f"ligi isiyojulikana kwa mechi {len(unk)}")
    if len(unk):
        warn(f"mechi {len(unk)} hazina ligi inayojulikana (vizingiti havitatumika kwao): "
             + "; ".join(f"{h} v {a}" for h, a in unk.head(5).values))

    # 2. vizingiti vya ligi 12
    allowed = None
    if GATES_ENABLED:
        allowed = load_gates()
        if allowed is None:
            warn("vizingiti vya ligi 12 havikupatikana - masoko yote ya ligi 12 yanabaki (tabia ya zamani).")
    else:
        log("   GATES12_ENABLED=0: vizingiti vya ligi 12 vimezimwa.")
    pred, rm = apply_gates(pred, allowed, 'predictions')
    log(f"   vizingiti vya ligi 12: safu {rm} zimeondolewa kwenye predictions.csv")

    # 3. ligi 5 mpya
    n_added = 0
    if n5 is not None and len(n5) and all(c in n5.columns for c in PRED_COLS):
        x = n5.copy()
        cls = x['lite_class'] if 'lite_class' in x.columns else pd.Series('OTHER', index=x.index)
        x['pro_tier'] = ['PRO_STRONG' if str(c) == 'STRONG' else 'PRO_MEDIUM' for c in cls]
        x = x[PRED_COLS + ['league']]
        # usiongeze mechi ambazo tayari zipo (jina moja + tarehe) kutoka ligi 12
        have = set(zip(pred['HomeTeam'].map(norm), pred['AwayTeam'].map(norm), pred['match_date'].astype(str).str[:10]))
        dup = [(norm(h), norm(a), str(d)[:10]) in have for h, a, d in zip(x['HomeTeam'], x['AwayTeam'], x['match_date'])]
        x = x[[not d for d in dup]]
        n_added = len(x)
        pred = pd.concat([pred[PRED_COLS + ['league']], x], ignore_index=True)
        log(f"   ligi 5: safu {n_added} zimeongezwa ({x[['HomeTeam', 'AwayTeam', 'match_date']].drop_duplicates().shape[0]} mechi): "
            f"{x['league'].value_counts().to_dict()}")
    pred = pred[PRED_COLS + ['league']]
    pred.to_csv(os.path.join(DATA, 'predictions.csv'), index=False)

    # 4. value_bets.csv
    vb = read_csv_safe(os.path.join(DATA, 'value_bets.csv'))
    if vb is not None and len(vb) and 'market' in vb.columns:
        vb['league'] = [league_of(h, a, mp) for h, a in zip(vb['HomeTeam'], vb['AwayTeam'])]
        vb, rmv = apply_gates(vb, allowed, 'value_bets')
        vb.to_csv(os.path.join(DATA, 'value_bets.csv'), index=False)
        log(f"   value_bets.csv: safu {rmv} zimeondolewa, safu {len(vb)} zimebaki")
    elif vb is not None and 'league' not in vb.columns:
        vb['league'] = ''
        vb.to_csv(os.path.join(DATA, 'value_bets.csv'), index=False)

    # 5. combos.csv: ondoa combos zenye mguu uliofichwa
    cb = read_csv_safe(os.path.join(DATA, 'combos.csv'))
    if cb is not None and len(cb) and 'legs' in cb.columns and allowed is not None:
        hidden = set()
        visible = set()
        for h, a, m in zip(pred['HomeTeam'], pred['AwayTeam'], pred['market']):
            visible.add((norm(h), norm(a), m))
        keep_rows = []
        for legs in cb['legs']:
            ok = True
            for leg in str(legs).split(' + '):
                mm = re.match(r'^(.*?) vs (.*?): (\S+) @', leg.strip())
                if not mm:
                    continue
                h, a, m = norm(mm.group(1)), norm(mm.group(2)), mm.group(3)
                lg = league_of(h, a, mp)
                if lg in allowed and m in GATE_NAMES and m not in allowed[lg]:
                    ok = False
                    break
            keep_rows.append(ok)
        kept = cb[keep_rows]
        if len(kept) == 0:
            warn("combos: zote zingeondolewa na gates - nimeziacha kama zilivyo (fail-open).")
        else:
            kept.to_csv(os.path.join(DATA, 'combos.csv'), index=False)
            log(f"   combos.csv: combos {len(cb) - len(kept)} zimeondolewa, {len(kept)} zimebaki")
    log("merge_leagues: imekamilika.")
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:   # kamwe isivunje nightly
        warn(f"merge_leagues imeshindwa ({type(e).__name__}: {e}) - data ya awali imebaki.")
        sys.exit(0)