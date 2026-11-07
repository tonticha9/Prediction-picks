"""
.github/scripts/release_monitor.py

UFUATILIAJI WA MATOKEO HALISI + ROLLBACK YA KIOTOMATIKI (Nov 7 2026)

Kila usiku (kwenye nightly_predictions.yml, baada ya daily_calibration) inapima picks za live zenye
probability >= 65% zilizokwisha (WON/LOST) kutoka database:
  1) AFYA: siku 21 za mwisho dhidi ya siku 60 zilizotangulia: pengo (win rate - wastani wa probability) na win rate.
  2) BAADA YA RELEASE: tangu toleo la sasa lilipochapishwa (data/release_state.json) dhidi ya siku 45 kabla yake.
Inaripoti kwenye /admin/ops (chanzo 'release_monitor'), inaandika jedwali release_health, na inaamua:
  action = rollback  -> workflow inarudisha toleo lililopita (retrain_weekly mode=rollback) KIOTOMATIKI
Rollback inatokea TU kama vyote vimetimia: picks >= 250 baada ya release, siku >= 7, win rate imeshuka >= pointi 6
(z <= -2.5), pengo <= -6 pts (z <= -2.5), toleo halina zaidi ya siku 21, na halijarudishwa tayari.
ROLLBACK_ENABLED=no => inaripoti tu (would_rollback), haichukui hatua.
USALAMA: kosa lolote = inamaliza kwa utulivu (exit 0), nightly inaendelea.
"""
import os
import sys
import json
import datetime
import traceback
import urllib.request

import numpy as np
import pandas as pd

STATE_PATH = os.path.join('data', 'release_state.json')
ROLLBACK_ENABLED = os.environ.get('ROLLBACK_ENABLED', 'yes').strip().lower() == 'yes'

CONF_MIN = 65.0            # picks zenye probability (%) >= hii
RECENT_DAYS = 21
BASE_DAYS = 60
PRE_DAYS = 45
MIN_N_RECENT = 150
MIN_N_POST = 250
MIN_DAYS_POST = 7
MAX_RELEASE_AGE_DAYS = 21
LOOKBACK_DAYS = 140

SQL_TABLE = ("CREATE TABLE IF NOT EXISTS release_health ("
             "run_date VARCHAR(10) PRIMARY KEY, release_id VARCHAR(60), n_recent INTEGER, win_recent FLOAT, "
             "mean_p_recent FLOAT, gap_recent FLOAT, z_recent FLOAT, n_post INTEGER, win_post FLOAT, "
             "win_pre FLOAT, status VARCHAR(20), action VARCHAR(20), note VARCHAR(400))")
SQL_UP = ("INSERT INTO release_health (run_date, release_id, n_recent, win_recent, mean_p_recent, gap_recent, "
          "z_recent, n_post, win_post, win_pre, status, action, note) VALUES (:run_date, :release_id, :n_recent, "
          ":win_recent, :mean_p_recent, :gap_recent, :z_recent, :n_post, :win_post, :win_pre, :status, :action, :note) "
          "ON CONFLICT (run_date) DO UPDATE SET release_id = EXCLUDED.release_id, n_recent = EXCLUDED.n_recent, "
          "win_recent = EXCLUDED.win_recent, mean_p_recent = EXCLUDED.mean_p_recent, gap_recent = EXCLUDED.gap_recent, "
          "z_recent = EXCLUDED.z_recent, n_post = EXCLUDED.n_post, win_post = EXCLUDED.win_post, "
          "win_pre = EXCLUDED.win_pre, status = EXCLUDED.status, action = EXCLUDED.action, note = EXCLUDED.note")


def stats(d):
    n = int(len(d))
    if n == 0:
        return {'n': 0, 'wins': 0, 'win': None, 'mean_p': None, 'gap': None, 'z': None}
    wins = float(d['y'].sum())
    sp = float(d['p'].sum())
    var = float((d['p'] * (1 - d['p'])).sum())
    return {'n': n, 'wins': wins, 'win': wins / n, 'mean_p': sp / n, 'gap': (wins - sp) / n,
            'z': (wins - sp) / np.sqrt(var) if var > 0 else 0.0}


def two_prop_z(w1, n1, w0, n0):
    if n1 == 0 or n0 == 0:
        return 0.0
    p1, p0 = w1 / n1, w0 / n0
    pp = (w1 + w0) / (n1 + n0)
    se = np.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n0))
    return float((p1 - p0) / se) if se > 0 else 0.0


def f(x, nd=3):
    return 'n/a' if x is None else f"{x:.{nd}f}"


def report_ops(level, detail, url=''):
    app_url = (os.environ.get('APP_URL') or '').rstrip('/')
    secret = os.environ.get('REFRESH_SECRET') or ''
    if not app_url or not secret:
        return
    try:
        body = json.dumps({'source': 'release_monitor', 'status': level, 'detail': detail[:780], 'url': url}).encode()
        req = urllib.request.Request(app_url + '/api/ops/report', data=body, method='POST',
                                     headers={'Content-Type': 'application/json', 'X-Refresh-Secret': secret})
        urllib.request.urlopen(req, timeout=25).read()
    except Exception as e:
        print(f"::warning::Kuripoti kwa ops kumeshindwa ({type(e).__name__}: {e})")


def set_output(k, v):
    path = os.environ.get('GITHUB_OUTPUT')
    if path:
        try:
            with open(path, 'a') as fh:
                fh.write(f"{k}={v}\n")
        except Exception:
            pass


def summary(text_):
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if path:
        try:
            with open(path, 'a') as fh:
                fh.write(text_ + "\n")
        except Exception:
            pass


class Store:
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

    def settled(self, cutoff):
        PR = self.PR
        q = self.db.session.query(PR.probability, PR.result, PR.match_date).filter(
            PR.section == 'prediction', PR.result.in_(['WON', 'LOST']),
            PR.match_date >= cutoff, PR.probability >= CONF_MIN)
        return pd.DataFrame(q.all(), columns=['probability', 'result', 'match_date'])

    def save(self, rec):
        self.db.session.execute(self.text(SQL_TABLE))
        self.db.session.execute(self.text(SQL_UP), rec)
        self.db.session.commit()

    def rollback(self):
        self.db.session.rollback()


def load_state():
    try:
        if os.path.isfile(STATE_PATH):
            return json.load(open(STATE_PATH))
    except Exception as e:
        print(f"::warning::release_state.json haisomeki ({e})")
    return {}


def run():
    today = datetime.date.today()
    if not os.environ.get('DATABASE_URL'):
        print("::warning::DATABASE_URL haipo - release_monitor imerukwa.")
        set_output('action', 'none')
        return
    state = load_state()
    rid = str(state.get('release_id') or 'haijulikani')
    with Store() as store:
        cutoff = datetime.datetime.combine(today - datetime.timedelta(days=LOOKBACK_DAYS), datetime.time.min)
        df = store.settled(cutoff)
        if len(df) == 0:
            print("ℹ️ Hakuna picks zilizokamilika za kupima bado.")
            set_output('action', 'none')
            return
        df['y'] = (df['result'] == 'WON').astype(float)
        df['p'] = df['probability'].astype(float) / 100.0
        df['d'] = pd.to_datetime(df['match_date']).dt.normalize()
        td = pd.Timestamp(today)

        rec = df[df['d'] >= td - pd.Timedelta(days=RECENT_DAYS)]
        base = df[(df['d'] < td - pd.Timedelta(days=RECENT_DAYS)) & (df['d'] >= td - pd.Timedelta(days=RECENT_DAYS + BASE_DAYS))]
        r, b = stats(rec), stats(base)

        level, notes = 'ok', []
        if r['n'] >= MIN_N_RECENT:
            if r['gap'] <= -0.08 and r['z'] <= -3:
                level = 'error'
                notes.append(f"pengo kubwa hasi la siku {RECENT_DAYS}")
            elif r['gap'] <= -0.05 and r['z'] <= -2:
                level = 'warn'
                notes.append(f"pengo hasi la siku {RECENT_DAYS}")
            if b['n'] >= MIN_N_RECENT:
                zz = two_prop_z(r['wins'], r['n'], b['wins'], b['n'])
                if r['win'] <= b['win'] - 0.05 and zz <= -2:
                    level = 'error' if level == 'error' else 'warn'
                    notes.append("win rate imeshuka dhidi ya siku 60 zilizotangulia")
        else:
            notes.append(f"picks za siku {RECENT_DAYS} ni {r['n']} (chini ya {MIN_N_RECENT})")

        # ---- Tangu release ----
        action, sp, sq, days_post = 'none', stats(df.iloc[0:0]), stats(df.iloc[0:0]), None
        if state and state.get('published_utc') and state.get('run_id') and not state.get('rolled_back'):
            pub = pd.Timestamp(state['published_utc']).normalize()
            days_post = int((td - pub).days)
            if 0 <= days_post <= MAX_RELEASE_AGE_DAYS:
                post = df[df['d'] >= pub + pd.Timedelta(days=1)]
                pre = df[(df['d'] < pub) & (df['d'] >= pub - pd.Timedelta(days=PRE_DAYS))]
                sp, sq = stats(post), stats(pre)
                if sp['n'] >= MIN_N_POST and days_post >= MIN_DAYS_POST and sq['n'] >= MIN_N_RECENT:
                    z2 = two_prop_z(sp['wins'], sp['n'], sq['wins'], sq['n'])
                    if (sp['win'] <= sq['win'] - 0.06 and z2 <= -2.5 and sp['gap'] <= -0.06 and sp['z'] <= -2.5):
                        action = 'rollback' if ROLLBACK_ENABLED else 'would_rollback'
                        level = 'error'
                        notes.append(f"BAADA YA RELEASE {rid}: win rate {f(sp['win'])} dhidi ya {f(sq['win'])} kabla")
                else:
                    notes.append(f"baada ya release: picks {sp['n']}, siku {days_post} (bado haitoshi kuamua)")
        elif state.get('rolled_back'):
            notes.append("toleo la sasa ni la rollback; hakuna rollback nyingine hadi toleo jipya lichapishwe")
        elif not state:
            notes.append("release_state.json bado haipo (itaundwa na publish ijayo)")

        msg = (f"picks>=65%: siku {RECENT_DAYS}: n={r['n']}, ilishinda {f(r['win'])}, ilisema {f(r['mean_p'])}, "
               f"pengo {f(r['gap'])}, z {f(r['z'], 1)} | siku {BASE_DAYS} zilizotangulia: n={b['n']}, ilishinda {f(b['win'])} | "
               f"release {rid}: baada n={sp['n']} win {f(sp['win'])}, kabla n={sq['n']} win {f(sq['win'])} | "
               f"{'; '.join(notes) if notes else 'sawa'} | action={action}")
        print("📈 " + msg)
        summary(f"### Release monitor: **{level.upper()}** | action **{action}**\n{msg}")

        try:
            store.save({'run_date': today.isoformat(), 'release_id': rid[:60], 'n_recent': r['n'],
                        'win_recent': r['win'], 'mean_p_recent': r['mean_p'], 'gap_recent': r['gap'],
                        'z_recent': r['z'], 'n_post': sp['n'], 'win_post': sp['win'], 'win_pre': sq['win'],
                        'status': level, 'action': action, 'note': '; '.join(notes)[:400]})
        except Exception as e:
            store.rollback()
            print(f"::warning::Kuandika release_health kumeshindwa ({type(e).__name__}: {e})")

        report_ops(level, msg)
        set_output('action', 'rollback' if action == 'rollback' else 'none')
        set_output('rollback_run_id', str(state.get('run_id') or ''))
        if action == 'rollback':
            print(f"🛑 ROLLBACK itaombwa: toleo {rid} -> lililopita (artifact ya run {state.get('run_id')})")


def main():
    try:
        run()
    except Exception as e:
        print(f"::warning::release_monitor imeshindwa ({type(e).__name__}: {e}) - nightly inaendelea.")
        traceback.print_exc(limit=4)
        set_output('action', 'none')
    sys.exit(0)


if __name__ == '__main__':
    main()