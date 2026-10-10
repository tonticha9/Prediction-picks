"""
admin_tools.py — Zana za Admin zilizo nje ya app.py, ili app.py isibadilishwe kila mara.

Sasa: Market Analysis (/admin/markets).
Baadaye: Admin AI, Retraining.

Inasajiliwa kutoka app.py kwa:
    register_admin_tools(app, admin_only, globals())
(globals() inaruhusu zana hizi kutumia function zote za app.py bila kuzi-import.)
"""
import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from flask import render_template, request

from models import db, PredictionRecord, Settings

BUCKET_EDGES = [0, 50, 60, 70, 80, 101]
BUCKET_LABELS = ['<50', '50-60', '60-70', '70-80', '80+']
VERDICT_ORDER = {'strong': 0, 'watch': 1, 'weak': 2, 'nodata': 3}
VERDICT_ICON = {'strong': '🟢', 'watch': '🟡', 'weak': '🔴', 'nodata': '⚪'}

MIN_SAMPLE = 20    # chini ya hii: data haitoshi
GOOD_SAMPLE = 30   # kuanzia hapa tunaweza kusema "imara"


def _today_eat():
    return (datetime.utcnow() + timedelta(hours=3)).date()


def _wilson(wins, n, z=1.96):
    """Kiwango cha uhakika (95%) cha win rate halisi. Rudisha (chini, juu) kama 0-1."""
    if n == 0:
        return 0.0, 0.0
    phat = wins / n
    denom = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    return (centre - margin) / denom, (centre + margin) / denom


def _verdict(n, gap, wilson_high_pct, avg_prob_pct, roi, n_odds, trend):
    """Hukumu ya market + sababu zake."""
    if n < MIN_SAMPLE:
        return 'nodata', [f'data haitoshi (n={n}, inahitaji angalau {MIN_SAMPLE})']

    weak = []
    if gap <= -10:
        weak.append(f'overconfident: win rate iko chini ya probability kwa pts {abs(gap):.1f}')
    if wilson_high_pct < avg_prob_pct - 3:
        weak.append('hata kwa kiwango cha juu cha uhakika, win rate iko chini ya probability ya model')
    if roi is not None and n_odds >= MIN_SAMPLE and roi <= -10:
        weak.append(f'ROI hasi: {roi:.1f}%')
    if weak:
        return 'weak', weak

    watch = []
    if gap <= -5:
        watch.append(f'model ina uhakika kupita kiasi (pengo {gap:+.1f} pts)')
    if trend == 'down':
        watch.append('inashuka siku 14 za mwisho')
    if roi is not None and n_odds >= MIN_SAMPLE and roi < 0:
        watch.append(f'ROI hasi kidogo: {roi:.1f}%')
    if n < GOOD_SAMPLE:
        watch.append(f'sampuli bado ndogo (n={n}, bora 30+)')
    if watch:
        return 'watch', watch

    return 'strong', ['calibration nzuri, sampuli ya kutosha, hakuna dalili za kushuka']


def compute_market_stats(section='prediction', days=60, min_prob=50.0, recent_days=14,
                         group_fn=None, group_meta=None):
    """
    Uchambuzi wa kila market kutoka PredictionRecord zilizosettlewa (WON/LOST).
    VOID na PENDING hazihesabiwi.
    days=0 => historia yote. min_prob => probability ya chini kabisa kuhesabiwa.
    """
    group_fn = group_fn or (lambda m: 'other')
    group_meta = group_meta or {}
    today = _today_eat()

    q = db.session.query(
        PredictionRecord.market, PredictionRecord.probability, PredictionRecord.real_odds,
        PredictionRecord.result, PredictionRecord.match_date
    ).filter(
        PredictionRecord.section == section,
        PredictionRecord.result.in_(['WON', 'LOST']),
        PredictionRecord.probability.isnot(None),
        PredictionRecord.probability >= min_prob,
    )
    if days:
        q = q.filter(PredictionRecord.match_date >=
                     datetime.combine(today - timedelta(days=days), datetime.min.time()))
    rows = q.all()
    if not rows:
        return {'ok': False, 'total_rows': 0}

    df = pd.DataFrame(rows, columns=['market', 'probability', 'real_odds', 'result', 'match_date'])
    df['match_date'] = pd.to_datetime(df['match_date'])
    df['probability'] = df['probability'].astype(float)
    df['y'] = (df['result'] == 'WON').astype(int)
    df['p'] = df['probability'] / 100.0
    odds = pd.to_numeric(df['real_odds'], errors='coerce')
    df['odds'] = odds.where(odds > 1.0)
    has_odds = df['odds'].notna()
    df['profit'] = np.where(has_odds, np.where(df['y'] == 1, df['odds'] - 1.0, -1.0), np.nan)
    df['ev'] = np.where(has_odds, df['p'] * df['odds'] - 1.0, np.nan)
    df['bucket'] = pd.cut(df['probability'], bins=BUCKET_EDGES, labels=BUCKET_LABELS, right=False)
    df['group'] = df['market'].map(group_fn)

    recent_cut = pd.Timestamp(today - timedelta(days=recent_days))

    # ---- Kila market ----
    markets = []
    for market, g in df.groupby('market'):
        n = len(g)
        wins = int(g['y'].sum())
        wr = wins / n
        avg_p = float(g['p'].mean())
        gap = (wr - avg_p) * 100
        lo, hi = _wilson(wins, n)
        brier = float(((g['p'] - g['y']) ** 2).mean())

        og = g[g['odds'].notna()]
        n_odds = len(og)
        roi = float(og['profit'].mean() * 100) if n_odds else None
        avg_ev = float(og['ev'].mean() * 100) if n_odds else None

        bks = []
        for lab in BUCKET_LABELS:
            bg = g[g['bucket'] == lab]
            if len(bg):
                bks.append({'label': lab, 'n': int(len(bg)),
                            'win_rate': round(float(bg['y'].mean()) * 100, 1)})

        rec = g[g['match_date'] >= recent_cut]
        old = g[g['match_date'] < recent_cut]
        trend, recent_wr, older_wr = None, None, None
        if len(rec) >= 10 and len(old) >= 20:
            recent_wr = float(rec['y'].mean())
            older_wr = float(old['y'].mean())
            diff = recent_wr - older_wr
            trend = 'down' if diff <= -0.12 else ('up' if diff >= 0.12 else 'flat')

        verdict, reasons = _verdict(n, gap, hi * 100, avg_p * 100, roi, n_odds, trend)
        gk = group_fn(market)
        icon, label = group_meta.get(gk, ('📌', 'Mengineyo'))

        markets.append({
            'market': market, 'group_key': gk, 'group_icon': icon, 'group_label': label,
            'n': n, 'wins': wins,
            'win_rate': round(wr * 100, 1), 'avg_prob': round(avg_p * 100, 1), 'gap': round(gap, 1),
            'wilson_low': round(lo * 100, 1), 'wilson_high': round(hi * 100, 1),
            'brier': round(brier, 3),
            'n_odds': n_odds,
            'roi': round(roi, 1) if roi is not None else None,
            'avg_ev': round(avg_ev, 1) if avg_ev is not None else None,
            'buckets': bks,
            'trend': trend,
            'recent_n': int(len(rec)), 'older_n': int(len(old)),
            'recent_wr': round(recent_wr * 100, 1) if recent_wr is not None else None,
            'older_wr': round(older_wr * 100, 1) if older_wr is not None else None,
            'verdict': verdict, 'verdict_icon': VERDICT_ICON[verdict], 'reasons': reasons,
        })

    markets.sort(key=lambda m: (VERDICT_ORDER[m['verdict']], -m['wilson_low'], -m['n']))

    by_verdict = {k: [m for m in markets if m['verdict'] == k] for k in VERDICT_ORDER}
    counts = {k: len(v) for k, v in by_verdict.items()}

    # ---- Jumla ----
    n_all = len(df)
    wins_all = int(df['y'].sum())
    overall = {
        'n': n_all, 'wins': wins_all,
        'win_rate': round(wins_all / n_all * 100, 1),
        'avg_prob': round(float(df['p'].mean()) * 100, 1),
    }
    overall['gap'] = round(overall['win_rate'] - overall['avg_prob'], 1)

    # ---- Calibration ya jumla kwa makundi ya probability ----
    buckets = []
    for lab in BUCKET_LABELS:
        bg = df[df['bucket'] == lab]
        if len(bg):
            wr = float(bg['y'].mean()) * 100
            ap = float(bg['p'].mean()) * 100
            buckets.append({'label': lab, 'n': int(len(bg)), 'avg_prob': round(ap, 1),
                            'win_rate': round(wr, 1), 'gap': round(wr - ap, 1)})

    # ---- Makundi ya markets ----
    groups = []
    for gk, g in df.groupby('group'):
        icon, label = group_meta.get(gk, ('📌', 'Mengineyo'))
        n = len(g)
        wr = float(g['y'].mean()) * 100
        ap = float(g['p'].mean()) * 100
        gm = [m for m in markets if m['group_key'] == gk]
        groups.append({
            'key': gk, 'icon': icon, 'label': label, 'n': int(n),
            'win_rate': round(wr, 1), 'avg_prob': round(ap, 1), 'gap': round(wr - ap, 1),
            'markets': len(gm),
            'strong': sum(1 for m in gm if m['verdict'] == 'strong'),
            'weak': sum(1 for m in gm if m['verdict'] == 'weak'),
        })
    groups.sort(key=lambda x: -x['n'])

    return {'ok': True, 'total_rows': n_all, 'overall': overall, 'buckets': buckets,
            'groups': groups, 'markets': markets, 'by_verdict': by_verdict, 'counts': counts}


def register_admin_tools(app, admin_only, app_ns):
    """Inaitwa MARA MOJA kutoka app.py. app_ns = globals() ya app.py."""
    group_fn = app_ns.get('market_group_key') or (lambda m: 'other')
    group_meta = {k: (icon, label) for k, icon, label in app_ns.get('MARKET_GROUPS', [])}

    @app.context_processor
    def _admin_tools_ctx():
        return {'admin_tools_ok': True}

    @app.route('/admin/markets')
    @admin_only
    def admin_markets():
        settings = Settings.get()
        section = request.args.get('section', 'prediction')
        if section not in ('prediction', 'value_bet'):
            section = 'prediction'

        days = request.args.get('days', type=int)
        if days not in (14, 30, 60, 90, 0):
            days = 60

        base_prob = float(settings.probability_threshold or 0)
        min_prob = request.args.get('min_prob', type=float)
        if min_prob is None or min_prob < 0 or min_prob > 100:
            min_prob = base_prob

        prob_options = sorted({round(base_prob, 1), 60.0, 65.0, 70.0, 75.0})
        data = compute_market_stats(section=section, days=days, min_prob=min_prob,
                                    group_fn=group_fn, group_meta=group_meta)
        return render_template('admin_markets.html', data=data, section=section, days=days,
                               min_prob=min_prob, base_prob=base_prob,
                               prob_options=prob_options, page='admin')
