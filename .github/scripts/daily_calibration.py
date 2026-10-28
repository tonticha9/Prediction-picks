"""
.github/scripts/daily_calibration.py

CALIBRATION YA KILA SIKU KWA MATOKEO HALISI YA LIVE (v1, Oct 27 2026)

Inaendeshwa kwenye nightly_predictions.yml BAADA ya kunakili faili za Kaggle kwenye data/
na KABLA ya commit. Kazi yake: probability ya kila soko ibaki ya kweli
("70% = inashinda takriban 70%") kwa kutumia utabiri wa live uliokwisha kutathminiwa
(PredictionRecord WON/LOST), si historia ya mafunzo (ambayo modeli tayari imeiona).

Hatua:
  1. Soma data/predictions.csv (probability ghafi kutoka Kaggle) na value_bets.csv.
  2. Soma rekodi za live zilizokwisha (siku WINDOW_DAYS) kutoka database; ghafi (raw)
     inapatikana kutoka jedwali calib_raw_log (au ni probability iliyochapishwa kama
     calibration haikuwepo siku hiyo).
  3. Kwa kila soko (jozi za over/under na home_win/dc_x2, away_win/dc_1x zinachanganywa
     kuwa soko moja ili ziendelee kuwa sawa: p + (1-p) = 1) hesabu marekebisho laini
     (logit shift + slope ndogo) yenye KINGA:
       - sampuli ya chini (MIN_N), mtihani wa z (Z_MIN), jaribio la data ya hivi karibuni
         (holdout) lazima liboreshe log loss, ukubwa wa marekebisho una kikomo,
         na marekebisho yanapunguzwa nguvu (shrinkage) kulingana na idadi ya picks.
     Soko lisilo na ushahidi wa kutosha HALIBADILISHWI.
  4. Weka marekebisho kwenye data/predictions.csv, hesabu upya EV/edge za value_bets.csv
     (vigezo vile vile vya N1c: EV >= 0.5%, edge >= 2, odds 1.05-15, tier STRONG/MEDIUM).
  5. Andika calib_raw_log (ghafi + iliyochapishwa) na calib_report (ripoti ya kila soko
     kwa siku) kwenye database, kwa ajili ya Market Analysis.

USALAMA: kosa lolote = faili HAZIBADILISHWI na script inamaliza kwa utulivu (nightly
inaendelea). Ikiwa calib_raw_log haikuweza kuandikwa, marekebisho HAYATUMIKI (vinginevyo
fits za kesho zingechanganya ghafi na iliyorekebishwa).

CALIB_MODE=apply (chaguo-msingi) | shadow (inahesabu na kuripoti tu, haibadilishi faili)
"""
import os
import sys
import json
import datetime
import traceback

import numpy as np
import pandas as pd

DATA_DIR = 'data'
P_PATH = os.path.join(DATA_DIR, 'predictions.csv')
V_PATH = os.path.join(DATA_DIR, 'value_bets.csv')

MODE = os.environ.get('CALIB_MODE', 'apply').strip().lower()

# ---------- VIGEZO ----------
WINDOW_DAYS = 150          # rekodi za mechi za siku hizi za mwisho
MIN_N = 120                # sampuli ya chini kwa soko (baada ya kuchanganya jozi)
Z_MIN = 2.0                # |z| ya pengo la jumla lazima ifikie hii
HOLDOUT_FRAC = 0.35        # sehemu mpya zaidi ya data inayotumika kujaribu (haitumiki kufundisha)
MIN_HOLDOUT_GAIN = 0.0002  # log loss ya holdout lazima ishuke angalau hii
TAU_B = 60.0               # nguvu ya shrinkage ya shift (kubwa = polepole)
TAU_S = 40.0               # nguvu ya shrinkage ya slope (slope inajifunza tu ikiwa data ni nyingi)
B_CAP, S_CAP = 0.60, 0.30  # kikomo cha shift (logit) na slope
MIN_EFFECT = 0.03          # wastani wa |marekebisho| (logit) chini ya hii = hakuna haja
MAX_DP = 0.08              # marekebisho ya probability ya safu moja hayazidi pointi 8
TAPER = 0.5                # nje ya masafa yaliyofundishwa, marekebisho yanapungua hadi 0 ndani ya logit 0.5
P_CLIP = (0.01, 0.99)
MATCH_TOL = 0.0015         # kulinganisha probability iliyohifadhiwa na 'applied' ya log

VALUE_TIERS = ('PRO_STRONG', 'PRO_MEDIUM')
MIN_EV, MIN_EDGE = 0.5, 2.0
ODDS_MIN, ODDS_MAX = 1.05, 15.0
ALLOW_NEW_VALUE_BETS = True   # value bets zinazostahili TU baada ya marekebisho zinaongezwa


# ================================================================
# HESABU SAFI (hazihitaji database)
# ================================================================
def logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def sigm(z):
    return 1.0 / (1.0 + np.exp(-z))


def logloss(y, p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def canon(market):
    """(soko_kuu, imepinduliwa). Jozi zinazosaidiana zinakuwa soko moja:
    X_under_N = 1 - X_over_N, dc_x2 = 1 - home_win, dc_1x = 1 - away_win."""
    m = str(market)
    if m == 'dc_x2':
        return 'home_win', True
    if m == 'dc_1x':
        return 'away_win', True
    if '_and_' not in m and 'under' in m:
        return m.replace('under', 'over', 1), True
    return m, False


def fit_shift(x, y):
    """Logistic regression ndogo yenye shrinkage kuelekea 'hakuna marekebisho'.
    logit(p') = x + b + s * (x - c). Inarudisha (b, s, c)."""
    c = float(np.mean(x))
    z = x - c
    b = s = 0.0
    for _ in range(40):
        pp = sigm(x + b + s * z)
        r = pp - y
        w = pp * (1 - pp)
        g = np.array([r.sum() + TAU_B * b, (r * z).sum() + TAU_S * s])
        H = np.array([[w.sum() + TAU_B, (w * z).sum()],
                      [(w * z).sum(), (w * z * z).sum() + TAU_S]])
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            break
        b -= step[0]
        s -= step[1]
        if np.max(np.abs(step)) < 1e-9:
            break
    return float(np.clip(b, -B_CAP, B_CAP)), float(np.clip(s, -S_CAP, S_CAP)), c


def delta(x, par):
    """Marekebisho (logit) ya kila x. Nje ya masafa yaliyofundishwa yanapungua hadi 0."""
    x = np.asarray(x, dtype=float)
    lo, hi = par['lo'], par['hi']
    xc = np.clip(x, lo, hi)
    d = par['b'] + par['s'] * (xc - par['c'])
    dist = np.maximum(np.maximum(lo - x, x - hi), 0.0)
    return d * np.clip(1.0 - dist / TAPER, 0.0, 1.0)


def make_params(x, y):
    b, s, c = fit_shift(x, y)
    return {'b': b, 's': s, 'c': c, 'lo': float(np.percentile(x, 3)), 'hi': float(np.percentile(x, 97))}


def analyse_market(p, y):
    """p, y zimepangwa kwa muda (za zamani kwanza). Rudisha (ripoti, params au None)."""
    n = len(p)
    rep = {'n': int(n), 'mean_p': float(np.mean(p)) if n else None,
           'win_rate': float(np.mean(y)) if n else None, 'gap_pts': None, 'z': None,
           'b': 0.0, 's': 0.0, 'holdout_gain': None, 'status': 'TOO_FEW', 'applied': 0}
    if n == 0:
        return rep, None
    rep['gap_pts'] = float((np.mean(y) - np.mean(p)) * 100)
    var = float(np.sum(p * (1 - p)))
    rep['z'] = float((np.sum(y) - np.sum(p)) / np.sqrt(var)) if var > 0 else 0.0
    if n < MIN_N:
        return rep, None
    if abs(rep['z']) < Z_MIN:
        rep['status'] = 'NO_SIGNAL'
        return rep, None
    x = logit(p)
    k = int(n * (1 - HOLDOUT_FRAC))
    if k >= 40 and n - k >= 40:
        pt = make_params(x[:k], y[:k])
        xh, yh = x[k:], y[k:]
        gain = logloss(yh, sigm(xh)) - logloss(yh, sigm(xh + delta(xh, pt)))
        rep['holdout_gain'] = float(gain)
        if gain < MIN_HOLDOUT_GAIN:
            rep['status'] = 'NO_HOLDOUT_GAIN'
            return rep, None
    par = make_params(x, y)
    rep['b'], rep['s'] = par['b'], par['s']
    eff = float(np.mean(np.abs(delta(x, par))))
    if eff < MIN_EFFECT:
        rep['status'] = 'TINY'
        return rep, None
    rep['status'] = 'APPLY'
    rep['applied'] = 1
    return rep, par


def apply_to_predictions(pred, params):
    """pred: DataFrame yenye market + 'probability_%' (ghafi). Rudisha mfululizo mpya wa probability_%."""
    out = pred['probability_%'].astype(float).values.copy()
    cm = pred['market'].map(canon)
    for i, (m, inv) in enumerate(cm.values):
        par = params.get(m)
        if par is None or np.isnan(out[i]):
            continue
        p = out[i] / 100.0
        pc = 1 - p if inv else p
        pc2 = float(sigm(logit(pc) + delta(logit(pc), par)))
        pc2 = float(np.clip(pc2, pc - MAX_DP, pc + MAX_DP))
        pc2 = float(np.clip(pc2, P_CLIP[0], P_CLIP[1]))
        p2 = 1 - pc2 if inv else pc2
        out[i] = round(p2 * 100, 1)
    return pd.Series(out, index=pred.index)


def build_training_frame(settled, log):
    """settled: market, probability(%), result, match_date(datetime), home_team, away_team.
    log: mkey, home, away, market, raw_prob, applied_prob. Rudisha DataFrame: cm, p, y, t."""
    d = settled.dropna(subset=['probability']).copy()
    d = d[d['result'].isin(['WON', 'LOST'])]
    if len(d) == 0:
        return pd.DataFrame(columns=['cm', 'p', 'y', 't'])
    d['mkey'] = pd.to_datetime(d['match_date']).dt.strftime('%Y-%m-%d %H:%M:%S')
    d['p_pub'] = d['probability'].astype(float) / 100.0
    if log is not None and len(log):
        lg = log.rename(columns={'home': 'home_team', 'away': 'away_team'})
        d = d.merge(lg[['mkey', 'home_team', 'away_team', 'market', 'raw_prob', 'applied_prob']],
                    on=['mkey', 'home_team', 'away_team', 'market'], how='left')
        use_raw = d['raw_prob'].notna() & ((d['p_pub'] - d['applied_prob']).abs() <= MATCH_TOL)
        d['p'] = np.where(use_raw, d['raw_prob'], d['p_pub'])
    else:
        d['p'] = d['p_pub']
    d['y'] = (d['result'] == 'WON').astype(float)
    cm = d['market'].map(canon)
    d['cm'] = [c[0] for c in cm]
    inv = np.array([c[1] for c in cm])
    d['p'] = np.where(inv, 1 - d['p'], d['p'])
    d['y'] = np.where(inv, 1 - d['y'], d['y'])
    d['t'] = pd.to_datetime(d['match_date'])
    return d[['cm', 'p', 'y', 't']]


def fit_all(train):
    reports, params = {}, {}
    for m, g in train.groupby('cm'):
        g = g.sort_values('t')
        rep, par = analyse_market(g['p'].values, g['y'].values)
        reports[m] = rep
        if par is not None:
            params[m] = par
    return reports, params


# ================================================================
# VALUE BETS (vigezo vilevile vya N1c)
# ================================================================
def _mk(df):
    d = pd.to_datetime(df['match_date'], errors='coerce', format='mixed').dt.strftime('%Y-%m-%d %H:%M:%S').fillna('')
    return d + '|' + df['HomeTeam'].astype(str) + '|' + df['AwayTeam'].astype(str) + '|' + df['market'].astype(str)


def rebuild_value_bets(pred_raw, pred_new, vb):
    """Hesabu upya EV/edge za value bets kwa probability zilizorekebishwa."""
    cols = list(vb.columns) if len(vb.columns) else list(pred_new.columns) + ['ev_%']
    if 'ev_%' not in cols:
        cols.append('ev_%')
    newp = dict(zip(_mk(pred_new), pred_new['probability_%']))
    out = vb.copy()
    removed = changed = added = 0
    if len(out):
        out['_k'] = _mk(out)
        out['probability_%'] = [newp.get(k, p) for k, p in zip(out['_k'], out['probability_%'])]
        o = out['real_odds'].astype(float)
        out['ev_%'] = ((out['probability_%'] / 100.0 * o - 1) * 100).round(1)
        edge = (out['probability_%'] - 100.0 / o).round(1)
        ok = ((out['ev_%'] >= MIN_EV) & (edge >= MIN_EDGE) & (o >= ODDS_MIN) & (o <= ODDS_MAX)
              & out['pro_tier'].isin(VALUE_TIERS))
        removed = int((~ok).sum())
        out = out[ok].copy()
        old_p = dict(zip(_mk(vb), vb['probability_%']))
        changed = int(sum(1 for k, p in zip(out['_k'], out['probability_%']) if abs(old_p.get(k, p) - p) > 1e-9))
    else:
        out['_k'] = pd.Series(dtype=str)
    if ALLOW_NEW_VALUE_BETS and 'real_odds' in pred_new.columns:
        cand = pred_new[pred_new['real_odds'].notna() & pred_new['pro_tier'].isin(VALUE_TIERS)].copy()
        if len(cand):
            raw_p = dict(zip(_mk(pred_raw), pred_raw['probability_%']))
            cand['_k'] = _mk(cand)
            o = cand['real_odds'].astype(float)
            in_rng = (o >= ODDS_MIN) & (o <= ODDS_MAX)
            ev_new = (cand['probability_%'] / 100.0 * o - 1) * 100
            ed_new = cand['probability_%'] - 100.0 / o
            pr = cand['_k'].map(raw_p).astype(float)
            ev_old = (pr / 100.0 * o - 1) * 100
            ed_old = pr - 100.0 / o
            q_new = in_rng & (ev_new.round(1) >= MIN_EV) & (ed_new.round(1) >= MIN_EDGE)
            q_old = in_rng & (ev_old.round(1) >= MIN_EV) & (ed_old.round(1) >= MIN_EDGE)
            newrows = cand[q_new & ~q_old & ~cand['_k'].isin(set(out['_k']))].copy()
            if len(newrows):
                newrows['ev_%'] = ev_new[newrows.index].round(1)
                added = len(newrows)
                out = pd.concat([out, newrows], ignore_index=True)
    if len(out):
        out = out.drop_duplicates('_k').sort_values('ev_%', ascending=False)
    for c in cols:
        if c not in out.columns:
            out[c] = np.nan
    return out[cols], {'removed': removed, 'changed': changed, 'added': added}


# ================================================================
# DATABASE
# ================================================================
SQL_LOG = ("CREATE TABLE IF NOT EXISTS calib_raw_log ("
           "mkey VARCHAR(32) NOT NULL, home VARCHAR(120) NOT NULL, away VARCHAR(120) NOT NULL, "
           "market VARCHAR(120) NOT NULL, raw_prob FLOAT, applied_prob FLOAT, run_date VARCHAR(10), "
           "PRIMARY KEY (mkey, home, away, market))")
SQL_REP = ("CREATE TABLE IF NOT EXISTS calib_report ("
           "run_date VARCHAR(10) NOT NULL, market VARCHAR(120) NOT NULL, n INTEGER, mean_p FLOAT, "
           "win_rate FLOAT, gap_pts FLOAT, z FLOAT, b FLOAT, s FLOAT, holdout_gain FLOAT, "
           "status VARCHAR(40), applied INTEGER, PRIMARY KEY (run_date, market))")
SQL_LOG_UP = ("INSERT INTO calib_raw_log (mkey, home, away, market, raw_prob, applied_prob, run_date) "
              "VALUES (:mkey, :home, :away, :market, :raw_prob, :applied_prob, :run_date) "
              "ON CONFLICT (mkey, home, away, market) DO UPDATE SET raw_prob = EXCLUDED.raw_prob, "
              "applied_prob = EXCLUDED.applied_prob, run_date = EXCLUDED.run_date")
SQL_REP_INS = ("INSERT INTO calib_report (run_date, market, n, mean_p, win_rate, gap_pts, z, b, s, "
               "holdout_gain, status, applied) VALUES (:run_date, :market, :n, :mean_p, :win_rate, "
               ":gap_pts, :z, :b, :s, :holdout_gain, :status, :applied)")


class Store:
    """Mlango wa database (app.app_context + models za app)."""

    def __init__(self):
        sys.path.insert(0, os.getcwd())
        from sqlalchemy import text
        from app import app
        from models import db, PredictionRecord
        self.text, self.app, self.db, self.PR = text, app, db, PredictionRecord
        self.ctx = app.app_context()

    def __enter__(self):
        self.ctx.__enter__()
        return self

    def __exit__(self, *a):
        try:
            self.db.session.remove()
        except Exception:
            pass
        return self.ctx.__exit__(*a)

    def exec(self, sql, params=None):
        self.db.session.execute(self.text(sql), params or {})

    def commit(self):
        self.db.session.commit()

    def rollback(self):
        self.db.session.rollback()

    def ensure_tables(self):
        self.exec(SQL_LOG)
        self.exec(SQL_REP)
        self.commit()

    def settled(self, cutoff):
        PR = self.PR
        q = self.db.session.query(PR.market, PR.probability, PR.result, PR.match_date,
                                  PR.home_team, PR.away_team).filter(
            PR.section == 'prediction', PR.result.in_(['WON', 'LOST']), PR.match_date >= cutoff)
        return pd.DataFrame(q.all(), columns=['market', 'probability', 'result', 'match_date',
                                              'home_team', 'away_team'])

    def read_log(self, cutoff_key):
        rows = self.db.session.execute(self.text(
            "SELECT mkey, home, away, market, raw_prob, applied_prob FROM calib_raw_log "
            "WHERE mkey >= :c"), {'c': cutoff_key}).fetchall()
        return pd.DataFrame(rows, columns=['mkey', 'home', 'away', 'market', 'raw_prob', 'applied_prob'])

    def write_log(self, recs):
        if recs:
            self.exec(SQL_LOG_UP, recs)
        self.commit()

    def write_report(self, run_date, recs):
        self.exec("DELETE FROM calib_report WHERE run_date = :d", {'d': run_date})
        if recs:
            self.exec(SQL_REP_INS, recs)
        self.commit()


# ================================================================
# MUUNGANISHO
# ================================================================
def summary(text_):
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if path:
        try:
            with open(path, 'a') as f:
                f.write(text_ + "\n")
        except Exception:
            pass


def log_records(pred_raw, pred_new, today):
    d = pred_raw.copy()
    d['_md'] = pd.to_datetime(d['match_date'], errors='coerce', format='mixed')
    keep = d['_md'].notna() & (d['_md'].dt.date >= today)
    d = d[keep]
    newp = pred_new.loc[d.index, 'probability_%']
    recs = []
    for (md, h, a, m, pr, pn) in zip(d['_md'], d['HomeTeam'], d['AwayTeam'], d['market'],
                                      d['probability_%'], newp):
        if pd.isna(pr):
            continue
        recs.append({'mkey': md.strftime('%Y-%m-%d %H:%M:%S'), 'home': str(h), 'away': str(a),
                     'market': str(m), 'raw_prob': float(pr) / 100.0,
                     'applied_prob': float(pn) / 100.0, 'run_date': today.isoformat()})
    return recs


def run(store=None, today=None):
    today = today or datetime.date.today()
    if not os.path.exists(P_PATH):
        print("ℹ️ predictions.csv haipo - calibration imerukwa.")
        return
    pred_raw = pd.read_csv(P_PATH)
    if len(pred_raw) == 0 or 'probability_%' not in pred_raw.columns:
        print("ℹ️ predictions.csv tupu - calibration imerukwa.")
        return
    vb = pd.read_csv(V_PATH) if os.path.exists(V_PATH) else pd.DataFrame()
    print(f"🎯 Calibration ya kila siku | mode={MODE} | predictions {len(pred_raw):,} | value bets {len(vb)}")

    own = store is None
    if own:
        if not os.environ.get('DATABASE_URL'):
            print("::warning::DATABASE_URL haipo - calibration imerukwa (faili hazijabadilishwa).")
            return
        store = Store().__enter__()
    try:
        store.ensure_tables()
        cutoff = datetime.datetime.combine(today - datetime.timedelta(days=WINDOW_DAYS), datetime.time.min)
        settled = store.settled(cutoff)
        log = store.read_log(cutoff.strftime('%Y-%m-%d %H:%M:%S'))
        train = build_training_frame(settled, log)
        print(f"   Rekodi zilizokamilika (siku {WINDOW_DAYS}): {len(settled):,} | masoko (jozi zimeunganishwa): "
              f"{train['cm'].nunique() if len(train) else 0}")
        reports, params = fit_all(train)

        pred_new = pred_raw.copy()
        pred_new['probability_%'] = apply_to_predictions(pred_raw, params)
        moved = int((pred_new['probability_%'] != pred_raw['probability_%']).sum())

        # Ukaguzi wa akili kabla ya kuandika chochote
        pv = pred_new['probability_%']
        if len(pred_new) != len(pred_raw) or pv.isna().any() or (pv < 0).any() or (pv > 100).any():
            print("::error::Ukaguzi wa akili umeshindwa - faili hazijabadilishwa.")
            return

        vb_new, vstat = (rebuild_value_bets(pred_raw, pred_new, vb) if len(vb.columns) else (vb, {}))

        # Andika ghafi + ripoti KWANZA; ikishindwa, hatubadilishi faili
        recs = log_records(pred_raw, pred_new, today)
        rep_recs = [{'run_date': today.isoformat(), 'market': m, **{k: r[k] for k in
                    ('n', 'mean_p', 'win_rate', 'gap_pts', 'z', 'b', 's', 'holdout_gain', 'status', 'applied')}}
                    for m, r in sorted(reports.items())]
        try:
            store.write_log(recs)
            store.write_report(today.isoformat(), rep_recs)
        except Exception as e:
            store.rollback()
            print(f"::warning::Kuandika calibration_log kumeshindwa ({type(e).__name__}: {e}) - faili hazijabadilishwa.")
            return

        # Ripoti kwenye logi
        n_apply = sum(1 for r in reports.values() if r['status'] == 'APPLY')
        print(f"   Masoko yaliyochambuliwa: {len(reports)} | yenye marekebisho: {n_apply} | safu zilizobadilika leo: {moved:,}")
        shown = sorted(reports.items(), key=lambda kv: -(kv[1]['n'] or 0))[:25]
        print(f"   {'soko':34s} {'n':>5s} {'ilisema':>8s} {'ilishinda':>9s} {'pengo':>7s} {'z':>6s}  hali")
        for m, r in shown:
            z = '' if r['z'] is None else f"{r['z']:+.1f}"
            gp = '' if r['gap_pts'] is None else f"{r['gap_pts']:+.1f}"
            mp = '' if r['mean_p'] is None else f"{r['mean_p']*100:.1f}"
            wr = '' if r['win_rate'] is None else f"{r['win_rate']*100:.1f}"
            print(f"   {m:34s} {r['n']:5d} {mp:>8s} {wr:>9s} {gp:>7s} {z:>6s}  {r['status']}")
        if vstat:
            print(f"   Value bets: zimeondolewa {vstat['removed']} | zilizobadilika {vstat['changed']} | mpya {vstat['added']}")
        summary(f"### Calibration: masoko {len(reports)} yamechambuliwa, {n_apply} yamerekebishwa, "
                f"safu {moved:,} zimebadilika, value bets -{vstat.get('removed', 0)}/+{vstat.get('added', 0)} (mode {MODE})")

        if MODE != 'apply':
            print("🧪 SHADOW: ripoti imeandikwa, lakini faili HAZIKUBADILISHWA.")
            return
        pred_new.to_csv(P_PATH, index=False)
        if len(vb.columns):
            vb_new.to_csv(V_PATH, index=False)
        print("✅ predictions.csv na value_bets.csv zimerekebishwa.")
    finally:
        if own:
            store.__exit__(None, None, None)


def main():
    try:
        run()
    except Exception as e:
        print(f"::warning::Calibration imeshindwa ({type(e).__name__}: {e}) - faili hazijabadilishwa, nightly inaendelea.")
        traceback.print_exc(limit=4)
    sys.exit(0)


if __name__ == '__main__':
    main()