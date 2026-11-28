"""
.github/scripts/merge_leagues.py  -  v5 (Nov 27 2026)

v5 (HATUA F2): SHERIA YA USHAHIDI kwa masoko yenye base rate ya juu (over 0.5, base >= 90%): soko linaonyeshwa tu kama picks zake zinashinda
    angalau pointi 1 (THIN_LIFT) juu ya base rate ya ligi hiyo kwenye gates. Faida ya chini ya hapo ni 'kelele' (mf. E2 over 0.5: 93.1% dhidi ya 92.5%).
    Ligi 12: soko linaondolewa kwenye 'allowed' (kama gates nyingine). Ligi 5: inahesabiwa kwenye attach_rates5 (ufunguo 'thin' ndani ya 'rates5')
    na picks za (ligi, soko) hilo haziongezwi. Inajisasisha yenyewe kila gates zinaposasishwa (kila Jumapili); hakuna kinachoendeshwa Kaggle.
    Ligi isibaki bila soko lolote (ulinzi). Kwa namba za sasa ni E2 over 0.5 pekee inayoondoka; ligi 12 zote zinabaki.

v4 (HATUA F1): inahifadhi pia base rate ya kila ligi x soko ya ligi 5 mpya (new_leagues_gates.json kutoka output ya lab) ndani ya
    data/league_gates12.json chini ya ufunguo 'rates5' (faili hilo tayari linawekwa kwenye GitHub na nightly, hakuna faili jipya).
    App (league_base_rates.py) inasoma 'rate' ya ligi 12 na 'rates5' ya ligi 5. Ikikosekana kwenye output ya leo, ya jana inabaki.


v3: KOSA LA KUHESABU LIMEREKEBISHWA: log ya nightly ilionyesha "safu -21340 zimeondolewa" (idadi hasi) kwa sababu pandas 2.x ilibadilisha 'keep' kuwa object.
    Matokeo: ulinzi wa MAX_REMOVE_FRAC haukuweza kufanya kazi na idadi za log zilikuwa si sahihi. Sasa 'keep' ni numpy bool; idadi ni sahihi.

v2: kupakua gates sasa kunaandika KILA kitu (matokeo ya Kaggle, orodha ya faili, hali ya notebook) ili kama gates12.json
    haipatikani tujue sababu; pia inatafuta gates kwenye faili yoyote ya .json ya output (si jina moja tu).

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
import shutil
import subprocess
import sys
import zipfile

import numpy as np
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
THIN_BASE = 0.90          # sheria ya ushahidi inatumika kwa masoko yenye base rate >= hii (over 0.5)
THIN_LIFT = 0.010         # faida ya chini inayohitajika dhidi ya base rate (pointi 1)
THIN5 = set()             # {(ligi, soko)} ya ligi 5 yenye faida nyembamba (inajazwa na load_gates)
GATE_NAMES = ['home_win', 'away_win', 'dc_1x', 'dc_x2', 'dc_12', 'btts', 'btts_no', 'goals_over_05', 'goals_over_15',
              'goals_over_25', 'goals_over_35', 'goals_under_25', 'goals_under_35']


def log(msg):
    print(msg, flush=True)


def warn(msg):
    print(f"::warning::{msg}", flush=True)


def norm(s):
    return re.sub(r'\s+', ' ', str(s)).strip().lower()


# ---------------------------------------------------------------------------------- vizingiti (gates)
def kaggle_run(args, timeout=900):
    """Endesha amri ya kaggle. Rudisha (ok, maandishi ya stdout+stderr)."""
    try:
        r = subprocess.run(['kaggle'] + args, capture_output=True, text=True, timeout=timeout)
        out = ((r.stdout or '') + (r.stderr or '')).strip()
        return r.returncode == 0, out
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def kaggle(args):
    ok, out = kaggle_run(args, timeout=600)
    if not ok:
        log(f"   kaggle {' '.join(args[:3])} imeshindwa: {out[:200]}")
    return ok


def _is_gates_obj(o):
    return isinstance(o, dict) and isinstance(o.get('prod_names'), dict) and isinstance(o.get('show'), dict)


def find_fresh_gates(folder):
    """Tafuta gates kwenye folda ya output ya Kaggle: gates12.json kwanza, kisha .json yoyote yenye 'prod_names' na 'show'."""
    files = sorted(glob.glob(os.path.join(folder, '**', '*'), recursive=True))
    files = [f for f in files if os.path.isfile(f)]
    log(f"   faili za output ya {LAB_SLUG}: {len(files)}" + (" (HAKUNA)" if not files else ""))
    for f in files[:40]:
        log(f"      - {os.path.relpath(f, folder)} ({os.path.getsize(f):,} bytes)")
    named = [f for f in files if os.path.basename(f) == 'gates12.json']
    others = [f for f in files if f.endswith('.json') and f not in named and os.path.getsize(f) < 5_000_000]
    for f in named + others:
        try:
            with open(f, encoding='utf-8') as fh:
                o = json.load(fh)
        except Exception as e:
            if f in named:
                warn(f"{os.path.basename(f)} haisomeki ({type(e).__name__}: {e}).")
            continue
        if _is_gates_obj(o):
            if f not in named:
                log(f"   gates zimepatikana kwenye faili lenye jina tofauti: {os.path.basename(f)}")
            return o
    return None


def _thin(rec):
    """Kweli ikiwa soko lina base rate ya juu na picks zake hazizidi base rate kwa THIN_LIFT (faida nyembamba)."""
    try:
        pn = rec.get('pick_n') or 0
        pw, br = rec.get('pick_win'), rec.get('base_rate')
        return pn >= 1 and pw is not None and br is not None and br >= THIN_BASE and (pw - br) < THIN_LIFT
    except Exception:
        return False


def _thin5_list(o):
    """Kutoka new_leagues_gates.json: ['LIGI:soko'] ambayo hali zake zote za 'P' (zenye odds / bila odds) zina faida nyembamba."""
    out = []
    prod = o.get('prod_names') or {}
    gn = o.get('gates_n') or {}
    for lg, sides in (o.get('gates') or {}).items():
        for sd, rw in (sides or {}).items():
            rn = (gn.get(lg) or {}).get(sd) or {}
            regs = [r for r in (rw, rn) if isinstance(r, dict) and r.get('verdict') == 'P']
            mk = prod.get(sd)
            if mk and regs and all(_thin(r) for r in regs):
                out.append(f"{lg}:{mk}")
    return sorted(out)


def attach_rates5(gates, folder):
    """Weka base rate ya ligi 5 mpya kwenye gates['rates5'] (kutoka new_leagues_gates.json). Isipopatikana, ya awali inabaki."""
    found = None
    try:
        for f in sorted(glob.glob(os.path.join(folder, '**', 'new_leagues_gates.json'), recursive=True)):
            with open(f, encoding='utf-8') as fh:
                o = json.load(fh)
            if isinstance(o, dict) and isinstance(o.get('rate'), dict) and o['rate'] and isinstance(o.get('prod_names'), dict):
                found = {'rate': o['rate'], 'prod_names': o['prod_names'], 'last_played': o.get('last_played'),
                         'thin': _thin5_list(o)}
                break
    except Exception as e:
        warn(f"new_leagues_gates.json haisomeki ({type(e).__name__}: {e}) - rates za ligi 5 hazikusasishwa.")
    if found is not None:
        gates['rates5'] = found
        log(f"   rates za ligi 5 mpya zimehifadhiwa: ligi {sorted(found['rate'])} (last_played {found.get('last_played')}) | faida nyembamba: {found['thin'] or 'hakuna'}")
        return True
    try:
        if os.path.exists(GATES_CACHE):
            with open(GATES_CACHE, encoding='utf-8') as fh:
                old = json.load(fh)
            if isinstance(old.get('rates5'), dict):
                gates['rates5'] = old['rates5']
                log("   new_leagues_gates.json haikupatikana leo - rates za ligi 5 za awali zimebaki.")
                return False
    except Exception as e:
        log(f"   siwezi kusoma rates za awali za ligi 5: {e}")
    log("   rates za ligi 5 hazipo (app itatumia base rate ya jumla kwa ligi 5).")
    return False


def load_gates():
    """Rudisha (allowed: {liga: set(masoko)}, info). None ikiwa hakuna gates zinazoaminika."""
    global THIN5
    fresh = None
    if os.environ.get('SKIP_KAGGLE_FETCH') != '1':
        shutil.rmtree('lab_output', ignore_errors=True)
        ok, out = kaggle_run(['kernels', 'output', LAB_SLUG, '-p', 'lab_output'])
        log(f"   kaggle kernels output {LAB_SLUG}: {'sawa' if ok else 'IMESHINDWA'}"
            + (f" | {out[-300:]}" if out else ""))
        if ok:
            fresh = find_fresh_gates('lab_output')
            if fresh is not None:
                log(f"   gates12.json mpya imepakuliwa (last_played {fresh.get('last_played')})")
            else:
                st_ok, st = kaggle_run(['kernels', 'status', LAB_SLUG], timeout=120)
                warn(f"{LAB_SLUG}: output ya toleo la mwisho HAINA gates12.json. Hali ya notebook: {st[-200:] or 'haijulikani'}. "
                     f"Maana yake: toleo la mwisho la notebook halijaendeshwa kamili (Quick Save inaacha output tupu) "
                     f"au G12 haikuendeshwa kwenye toleo hilo. Endesha workflow 'Refresh League Gates' kwa mkono.")
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
        attach_rates5(gates, 'lab_output')
        try:
            with open(GATES_CACHE, 'w', encoding='utf-8') as f:
                json.dump(gates, f)
        except Exception as e:
            log(f"   siwezi kuhifadhi nakala ya gates: {e}")
    # sheria ya ushahidi (v5): ligi 12 - ondoa masoko yenye faida nyembamba (ligi isibaki tupu)
    try:
        thin12 = []
        for lg, sides in (gates.get('gates') or {}).items():
            if lg not in allowed:
                continue
            for sd, rec in (sides or {}).items():
                mk = prod.get(sd)
                if mk in allowed[lg] and isinstance(rec, dict) and rec.get('show') and _thin(rec) and len(allowed[lg]) > 1:
                    allowed[lg].discard(mk)
                    thin12.append(f"{lg}:{mk}")
        log(f"   sheria ya ushahidi (ligi 12): masoko yaliyoondolewa kwa faida nyembamba: {sorted(thin12) or 'hakuna'}")
    except Exception as e:
        log(f"   sheria ya ushahidi (ligi 12) imerukwa: {type(e).__name__}: {e}")
    try:
        THIN5 = {tuple(x.split(':', 1)) for x in ((gates.get('rates5') or {}).get('thin') or [])}
    except Exception:
        THIN5 = set()
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
    # v2 (Nov 26 2026): 'keep' ni safu ya numpy ya bool. Awali ilikuwa pd.Series na pandas 2.x ilibadilisha kuwa 'object' (onyo la FutureWarning),
    # ndipo (~keep).sum() ikatoa idadi HASI (-21340) na ulinzi wa MAX_REMOVE_FRAC haukuweza kufanya kazi kamwe.
    keep = np.ones(len(df), dtype=bool)
    for lg, ok in allowed.items():
        m = (covered & (df['league'] == lg)).to_numpy()
        if m.any():
            keep[m] = df['market'].isin(ok).to_numpy()[m]
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
        if THIN5:
            thin_m = [(lg_, m_) in THIN5 for lg_, m_ in zip(x['league'], x['market'])]
            if any(thin_m):
                log(f"   sheria ya ushahidi (ligi 5): picks {sum(thin_m)} zimeondolewa kwa faida nyembamba ({sorted(THIN5)})")
                x = x[[not t for t in thin_m]]
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
        leg_re = re.c