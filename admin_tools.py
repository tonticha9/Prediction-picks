"""
admin_tools.py — Zana za Admin zilizo nje ya app.py, ili app.py isibadilishwe kila mara.

Sasa: Market Analysis (/admin/markets) na Admin AI (/admin/ai).
Baadaye: Retraining.

Inasajiliwa kutoka app.py kwa:
    register_admin_tools(app, admin_only, globals())
(globals() inaruhusu zana hizi kutumia function zote za app.py bila kuzi-import.)

Admin AI inatumia API yoyote ya mtindo wa OpenAI (Groq, Mistral, OpenRouter...).
Environment: AI_BASE_URL, AI_MODEL, AI_API_KEY (+ hiari AI_FALLBACK_MODEL).
"""
import json
import math
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from flask import jsonify, redirect, render_template, request, url_for

from models import db, PredictionRecord, ComboRecord, Settings

BUCKET_EDGES = [0, 50, 60, 70, 80, 101]
BUCKET_LABELS = ['<50', '50-60', '60-70', '70-80', '80+']
VERDICT_ORDER = {'strong': 0, 'watch': 1, 'weak': 2, 'nodata': 3}
VERDICT_ICON = {'strong': '🟢', 'watch': '🟡', 'weak': '🔴', 'nodata': '⚪'}

MIN_SAMPLE = 20    # chini ya hii: data haitoshi
GOOD_SAMPLE = 30   # kuanzia hapa tunaweza kusema "imara"


# ================================================================
# MODELI ZA MAZUNGUMZO YA ADMIN AI (jedwali zinatengenezwa na db.create_all())
# ================================================================
class AdminChatThread(db.Model):
    __tablename__ = 'admin_chat_thread'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), default='Mazungumzo mapya')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)
    summary = db.Column(db.Text, default='')
    summarized_upto = db.Column(db.Integer, default=0)


class AdminChatMessage(db.Model):
    __tablename__ = 'admin_chat_message'
    id = db.Column(db.Integer, primary_key=True)
    thread_id = db.Column(db.Integer, db.ForeignKey('admin_chat_thread.id'),
                          nullable=False, index=True)
    role = db.Column(db.String(12), nullable=False)   # 'user' | 'assistant'
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ================================================================
# MARKET ANALYSIS
# ================================================================
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


# ================================================================
# ADMIN AI: maarifa ya msingi (sehemu ya mwanzo ya kila ombi, inahifadhiwa kwenye cache)
# ================================================================
SYSTEM_PROMPT = """Wewe ni "Admin AI" wa mfumo wa Braiton Picks, msaidizi wa admin/mmiliki pekee.

MUUNDAJI: Mfumo huu na wewe mmetengenezwa na Braiton Living, anayejulikana pia kama "Msanii" (A.K.A. Msanii). Mtu unayezungumza naye ni admin, kwa kawaida Msanii mwenyewe. Ukiulizwa nani alikutengeneza, jibu hivyo kwa kujiamini.

KAZI YAKO: kuelewa mfumo kwa kina, kueleza kwa nini mambo yanatokea, kuchambua takwimu, kupendekeza maboresho, na pia kupiga story za kawaida na admin (maswali ya jumla, mawazo ya biashara, kujifunza). Tumia uelewa na hoja, si kukariri majibu. Fikiri kutoka kwenye "DATA YA SASA" unayopewa kila ujumbe.

LUGHA NA MTINDO: Kiswahili fasaha kwa chaguo-msingi. Admin ana simu pekee, kwa hiyo andika aya fupi na orodha ndogo pale inapofaa. Mtumiaji akiandika Kiingereza, jibu Kiingereza. Anza na jibu la moja kwa moja, kisha sababu/ushahidi kutoka kwenye data, kisha hatua inayofuata ikiwa inafaa. Usirudie swali. Usitumie emoji nyingi. Jibu ndefu zaidi pale admin anapoomba maelezo kamili.

UKWELI NA UAMINIFU:
- Namba zote zitoke kwenye DATA YA SASA au kwenye mazungumzo. Usibuni namba wala usidai umeona code usiyoiona. Kama data haipo, sema wazi na umwambie admin aulize nini ili ipatikane (mf. "niulize uchambuzi wa markets" au "picks za leo").
- Tofautisha kilichopimwa kwenye data na makisio yako. Sampuli ndogo (n chini ya 30) = onya.
- Hakuna uhakika wa ushindi kwenye utabiri au kamari. Usiahidi faida. Admin akiuliza kuhusu kuweka pesa, eleza hatari kwa ufupi na kwa heshima mara moja, si kila ujumbe.
- Huoni wala hutoi API keys, passwords, wala emails za watumiaji. Ukiombwa, kataa kwa upole.
- Huwezi kubadilisha mipangilio wala kuendesha kazi. Unashauri na kuelekeza hatua, admin ndiye anayezifanya.

JINSI MFUMO UNAVYOFANYA KAZI (maarifa yako ya msingi):
1. Mfumo: tovuti ya Flask kwenye Render na database ya Postgres (Neon). Kurasa: Dashboard (picks za leo), Zijazo, Value Bets, Combos, History, Admin.
2. Job B (usiku, karibu 01:00 EAT, GitHub Actions + Kaggle): model inazalisha predictions.csv, value_bets.csv na combos.csv kwa ligi 12: Uingereza (E0, E1), Ujerumani D1, Hispania SP1, Ufaransa F1, Italia I1, Uholanzi N1, Ubelgiji B1, Ureno P1, Uturuki T1, Ugiriki G1, Scotland SC0. Mfumo unasasishwa kupitia refresh. Kila mechi ina markets takriban 63: matokeo (home_win, away_win, dc_1x, dc_x2, dc_12, nusu), magoli (over/under, btts), corners, kadi, shots on target, na mchanganyiko (X_and_Y). Kila soko lina probability (%), mara nyingine real_odds, na pro_tier (PRO_STRONG au PRO_MEDIUM).
3. Job A (kila saa, GitHub Actions): inavuta mechi zilizokwisha kutoka AllSportsAPI, inasasisha dataset ya Kaggle (historical-complete-no-gap), inasettle predictions na combos za PENDING kuwa WON/LOST/VOID kwa market_evaluator, inaandika matokeo halisi ya mechi (kwa History), na kuandika "heartbeat" ya kuonyesha ilifanya kazi.
4. Uchaguzi wa picks: soko linaonekana tu kama probability >= threshold ya admin. Best Pick inachaguliwa kwa formula (hybrid = EV+probability; probability; ev = masoko yenye odds tu; no_odds = masoko yasiyo na odds tu). dc_1x na dc_x2 haziwi Best Pick isipokuwa admin aruhusu; dc_12 haiwi kamwe. Other Picks zimepangwa kwa makundi (Matokeo, Magoli, Mchanganyiko, Corners, SOT, Kadi). Mechi isiyo na soko lolote linalopita vigezo haionekani. Ndiyo sababu ya "picks chache": mechi chache za siku, threshold ya juu, au formula inayochuja odds.
5. Combos: legs kutoka mechi tofauti, kulingana na mipangilio ya admin (min/max legs, probability ya leg na ya combo nzima, formula).
6. Matokeo: WON/LOST; VOID = takwimu hazikupatikana (mf. corners/kadi hazikutolewa na API) au soko halijulikani; PENDING = mechi haijaisha au haijasettlewa. Evaluator: booking_pts na yellows zinahesabu kadi ZOTE (njano+nyekundu); both_teams_carded = kila timu angalau kadi 1.
7. Market Analysis: win rate, calibration (probability ya model dhidi ya matokeo halisi), Wilson interval (uhakika wa sampuli), Brier score, ROI/EV kwa markets zenye odds, na mwenendo (drift) wa siku 14. Hukumu: imara, angalia, dhaifu, data haitoshi.
8. Health ya Admin: maonyo (warn/error) kwa data, Job A, PENDING zilizokwama, API key, secrets, n.k.
9. Mipango ijayo: retraining ya model (ya mkono na ya kiotomatiki).
"""

MARKET_KW = ('market', 'soko', 'masoko', 'calibrat', 'roi', 'win rate', 'winrate', 'drift',
             'imara', 'dhaifu', 'uchambuzi', 'analysis', 'probability', 'uwezekano', 'corner',
             'kadi', 'cards', 'magoli', 'goals', 'sot', 'btts', 'nguvu', 'kali', 'faida',
             'hasara', 'bora', 'mbaya', 'sampuli', 'brier', 'retrain')
PICKS_KW = ('pick', 'leo', 'today', 'chache', 'kidogo', 'hakuna', 'mechi', 'dashboard',
            'zijazo', 'upcoming', 'best', 'threshold', 'combo', 'kwa nini', 'why')

HISTORY_MESSAGES = 6      # ujumbe wa karibuni kupelekwa kwa AI
HISTORY_CHAR_CAP = 1500   # kikomo cha herufi kwa kila ujumbe wa nyuma
SUMMARIZE_WHEN = 16       # ujumbe ambao haujafupishwa ukizidi hii, fupisha wa zamani
CONTEXT_MAX = 6500        # kikomo cha herufi za DATA YA SASA

_CACHE = {}


def _cached(key, ttl, fn):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _CACHE[key] = (now, val)
    return val


def _core_context(ns, settings, thr, group_fn, group_meta, shared):
    L = []
    now = ns['now_eat']()
    L.append(f'Muda sasa (EAT): {now:%Y-%m-%d %H:%M}')

    # --- Health ---
    try:
        checks, summ = ns['run_health_checks']()
        L.append(f"HEALTH: {summ['text']} (sawa {summ['ok']}/{len(checks)})")
        for c in checks:
            if c['status'] != 'ok':
                mark = '⚠️' if c['status'] == 'warn' else '❌'
                L.append(f"  {mark} {c['name']}: {str(c['detail'])[:150]}")
    except Exception as e:
        L.append(f'HEALTH: imeshindwa kusomwa ({type(e).__name__})')

    # --- Job A ---
    try:
        raw = ns['get_flag']('job_a_last_run', '')
        if raw:
            parts = raw.split('|')
            last = datetime.fromisoformat(parts[0])
            age = (datetime.utcnow() - last).total_seconds() / 3600
            L.append(f"JOB A (hourly catchup): ilimaliza masaa {age:.1f} yaliyopita ({' | '.join(parts[1:])})")
        else:
            L.append('JOB A: bado haijaripoti (heartbeat haipo).')
    except Exception as e:
        L.append(f'JOB A: imeshindwa kusomwa ({type(e).__name__})')

    # --- Mipangilio ---
    try:
        dc = 'ndiyo' if ns['dc_best_allowed']() else 'hapana'
        L.append(
            f"MIPANGILIO: threshold={thr}%, best_pick_formula={settings.best_pick_formula}, "
            f"markets/mechi Dashboard={settings.max_markets_per_match}, "
            f"markets/History={settings.history_max_markets}, dc_best_pick={dc}; "
            f"combos: formula={settings.combo_leg_formula}, legs {settings.combo_min_legs}-{settings.combo_max_legs}, "
            f"leg>={settings.combo_min_leg_probability}%, combo nzima>={settings.combo_min_combined_probability}%, "
            f"max kwa siku={settings.max_combos_per_day}")
    except Exception as e:
        L.append(f'MIPANGILIO: imeshindwa kusomwa ({type(e).__name__})')

    # --- Leo: funnel ya picks ---
    try:
        df = ns['get_data']()['predictions']
        today = ns['today_eat']()
        keys = ['HomeTeam', 'AwayTeam', 'match_date']
        dates = df['match_date'].dt.date
        tdf = df[dates == today]
        n_today = tdf[keys].drop_duplicates().shape[0]
        n_future = df[dates > today][keys].drop_duplicates().shape[0]
        prob = pd.to_numeric(tdf['probability_%'], errors='coerce')
        n_above = tdf[prob >= thr][keys].drop_duplicates().shape[0]
        n_mk_above = int((prob >= thr).sum())
        surv = ns['today_predictions_survivors'](settings)
        shared['surv'] = surv
        n_combos = ComboRecord.query.filter_by(shown_date=today).count()
        L.append(
            f"LEO: mechi {n_today} (zijazo {n_future}). Funnel: mechi zenye angalau soko 1 >= {thr}%: {n_above}; "
            f"mechi zenye picks zilizobaki baada ya formula/odds/dc: {len(surv)}; "
            f"markets zilizovuka threshold {n_mk_above}/{len(tdf)}; combos za leo: {n_combos}")
    except Exception as e:
        L.append(f'LEO: imeshindwa kusomwa ({type(e).__name__}: {str(e)[:80]})')

    # --- Muhtasari wa Market Analysis ---
    try:
        stats = _cached(('mstats', thr), 300, lambda: compute_market_stats(
            section='prediction', days=60, min_prob=thr, group_fn=group_fn, group_meta=group_meta))
        shared['stats'] = stats
        if stats.get('ok'):
            o, c = stats['overall'], stats['counts']
            L.append(
                f"MARKET ANALYSIS (siku 60, probability>={thr}%): zilizosettlewa {o['n']}, "
                f"win rate {o['win_rate']}% dhidi ya model {o['avg_prob']}% (pengo {o['gap']:+} pts); "
                f"markets: imara {c['strong']}, angalia {c['watch']}, dhaifu {c['weak']}, data haitoshi {c['nodata']}")
        else:
            L.append('MARKET ANALYSIS: hakuna predictions zilizosettlewa bado kwa vigezo hivi.')
    except Exception as e:
        L.append(f'MARKET ANALYSIS: imeshindwa ({type(e).__name__})')
    return L


def _markets_detail(shared):
    stats = shared.get('stats') or {}
    if not stats.get('ok'):
        return ['MAELEZO YA MARKETS: hakuna data iliyosettlewa bado.']
    L = ['MAELEZO YA MARKETS (siku 60):']
    bv = stats['by_verdict']

    def line(m):
        roi = f", ROI {m['roi']:+}%" if m['roi'] is not None else ''
        return (f"  {m['market']}: n={m['n']}, win {m['win_rate']}% (uhakika {m['wilson_low']}-{m['wilson_high']}%) "
                f"dhidi ya model {m['avg_prob']}%{roi}")

    if bv['strong']:
        L.append(' Imara (bora kwanza):')
        L += [line(m) for m in bv['strong'][:6]]
    if bv['watch']:
        L.append(' Angalia:')
        L += [line(m) + f" | {m['reasons'][0]}" for m in bv['watch'][:5]]
    if bv['weak']:
        L.append(' Dhaifu:')
        L += [line(m) + f" | {m['reasons'][0]}" for m in bv['weak'][:6]]
    down = [m['market'] for m in stats['markets'] if m['trend'] == 'down']
    if down:
        L.append(' Zinazoshuka siku 14: ' + ', '.join(down[:8]))
    bk = ', '.join(f"{b['label']}: {b['win_rate']}% ({b['n']})" for b in stats['buckets'])
    L.append(f" Calibration kwa probability: {bk}")
    return L


def _picks_detail(shared):
    surv = shared.get('surv')
    if surv is None:
        return ['PICKS ZA LEO: hazikupatikana.']
    if not surv:
        return ['PICKS ZA LEO: hakuna mechi iliyopita vigezo leo.']
    rows = []
    for m in surv:
        best = m['survivors'][0]
        md = m['match_date']
        t = md.strftime('%H:%M') if (md.hour or md.minute) else ''
        odds = best.get('real_odds')
        odds_txt = f" @{float(odds):.2f}" if odds is not None and pd.notna(odds) else ''
        rows.append((md, f"  {t} {m['home']} v {m['away']}: {best['market']} "
                         f"{float(best['probability_%']):.0f}%{odds_txt} (+{len(m['survivors']) - 1} nyingine)"))
    rows.sort(key=lambda x: x[0])
    L = ['PICKS ZA LEO (Best Pick kwa kila mechi):']
    L += [r[1] for r in rows[:15]]
    if len(rows) > 15:
        L.append(f'  ... na mechi {len(rows) - 15} zaidi')
    return L


def build_context(ns, user_text, prev_text, group_fn, group_meta):
    settings = Settings.get()
    thr = float(settings.probability_threshold or 0)
    low = f'{user_text} {prev_text}'.lower()
    shared = {}
    parts = _core_context(ns, settings, thr, group_fn, group_meta, shared)
    try:
        if any(k in low for k in MARKET_KW):
            parts += _markets_detail(shared)
        if any(k in low for k in PICKS_KW):
            parts += _picks_detail(shared)
    except Exception as e:
        parts.append(f'[sehemu ya ziada imeshindwa: {type(e).__name__}]')
    text = '\n'.join(parts)
    if len(text) > CONTEXT_MAX:
        text = text[:CONTEXT_MAX] + '\n[...data imekatwa]'
    return text


# ================================================================
# ADMIN AI: mawasiliano na LLM (API ya mtindo wa OpenAI, bila library ya nje)
# ================================================================
def _llm_request(base, api_key, model, messages, max_tokens, temperature):
    payload = {'model': model, 'messages': messages,
               'temperature': temperature, 'max_tokens': max_tokens}
    if 'gpt-oss' in model:
        payload['reasoning_effort'] = 'low'
    req = urllib.request.Request(
        base + '/chat/completions', data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': 'Bearer ' + api_key,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            # Cloudflare (ulinzi wa Groq) inakataa User-Agent ya chaguo-msingi ya Python-urllib (error 1010)
            'User-Agent': 'BraitonPicks-AdminAI/1.0',
        },
        method='POST')
    try:
        with urllib.request.urlopen(req, timeout=22) as resp:
            body = json.loads(resp.read().decode('utf-8'))
        text = (body['choices'][0]['message'].get('content') or '').strip()
        if not text:
            return {'ok': False, 'status': 200, 'retry_after': None, 'daily': False,
                    'error': 'AI imerudisha jibu tupu (tokens za kufikiri zimeisha). Jaribu tena.'}
        return {'ok': True, 'text': text}
    except urllib.error.HTTPError as e:
        raw = ''
        try:
            raw = e.read().decode('utf-8', 'ignore')
        except Exception:
            pass
        msg = raw[:400]
        try:
            msg = json.loads(raw).get('error', {}).get('message', msg)
        except Exception:
            pass
        ra = None
        try:
            hdr = e.headers.get('retry-after')
            ra = float(hdr) if hdr else None
        except (TypeError, ValueError):
            ra = None
        low = str(msg).lower()
        daily = e.code == 429 and ('per day' in low or 'tpd' in low or 'rpd' in low)
        return {'ok': False, 'status': e.code, 'retry_after': ra, 'daily': daily,
                'error': str(msg)[:300]}
    except Exception as e:
        return {'ok': False, 'status': 0, 'retry_after': None, 'daily': False,
                'error': f'{type(e).__name__}: {e}'}


def _friendly_error(r):
    st = r.get('status')
    err = r.get('error', '')
    if st == 429 and r.get('daily'):
        return ('Kikomo cha SIKU cha mtoa huduma wa AI kimefikiwa. Subiri hadi kiwe upya, '
                'au weka AI_FALLBACK_MODEL kwenye Render. ' + err[:160])
    if st == 429:
        wait = r.get('retry_after')
        w = f' Subiri takriban sekunde {int(wait) + 1} kisha tuma tena.' if wait else ' Subiri sekunde chache kisha tuma tena.'
        return 'Kikomo cha maombi/tokens kwa dakika kimefikiwa.' + w
    if st == 401:
        return 'API key si sahihi (401). Angalia AI_API_KEY kwenye Render.'
    if st == 403 and '1010' in err:
        return ('Mtoa huduma (Cloudflare) amekataa ombi la seva (error 1010). '
                'Nitumie ujumbe huu ili tubadilishe njia ya mawasiliano.')
    if st in (400, 404):
        return f'Ombi limekataliwa ({st}). Angalia AI_MODEL na AI_BASE_URL. {err[:200]}'
    return f'AI imeshindwa ({st}): {err[:200]}'


def _llm_chat(messages, max_tokens=1500, temperature=0.5):
    """Rudisha (ok, maandishi au ujumbe wa kosa)."""
    api_key = (os.environ.get('AI_API_KEY') or '').strip()
    if not api_key:
        return False, 'AI_API_KEY haijawekwa kwenye Render (Environment).'
    base = (os.environ.get('AI_BASE_URL') or 'https://api.groq.com/openai/v1').rstrip('/')
    models = [(os.environ.get('AI_MODEL') or 'openai/gpt-oss-20b').strip()]
    fb = (os.environ.get('AI_FALLBACK_MODEL') or '').strip()
    if fb and fb not in models:
        models.append(fb)

    t0 = time.time()
    last = {'status': 0, 'error': 'Kosa lisilojulikana.'}
    for model in models:
        for attempt in (1, 2):
            r = _llm_request(base, api_key, model, messages, max_tokens, temperature)
            if r['ok']:
                return True, r['text']
            last = r
            wait = r.get('retry_after')
            if (r.get('status') == 429 and not r.get('daily') and attempt == 1
                    and wait is not None and wait <= 6 and (time.time() - t0) < 10):
                time.sleep(wait + 0.5)
                continue
            break
    return False, _friendly_error(last)


def _maybe_summarize(thread, msgs):
    """Fupisha mazungumzo ya zamani ili yasijaze kikomo cha tokens."""
    try:
        unsummarized = [m for m in msgs if m.id > (thread.summarized_upto or 0)]
        if len(unsummarized) <= SUMMARIZE_WHEN:
            return
        if msgs and (datetime.utcnow() - msgs[-1].created_at).total_seconds() < 45:
            return   # epuka kugongana na kikomo cha tokens kwa dakika
        older = unsummarized[:-HISTORY_MESSAGES]
        if not older:
            return
        transcript = '\n'.join(
            f"{'Admin' if m.role == 'user' else 'AI'}: {m.content[:600]}" for m in older)
        prev = f"Muhtasari wa awali:\n{thread.summary}\n\n" if thread.summary else ''
        prompt = [
            {'role': 'system', 'content':
                'Fupisha mazungumzo kwa Kiswahili, si zaidi ya maneno 220. Hifadhi maswali makuu ya admin, '
                'maamuzi, takwimu muhimu zilizotajwa, na mambo yaliyobaki wazi. Usiongeze chochote kipya.'},
            {'role': 'user', 'content': prev + 'Mazungumzo ya kuongeza:\n' + transcript[:5000]},
        ]
        ok, summ = _llm_chat(prompt, max_tokens=700, temperature=0.2)
        if ok:
            thread.summary = summ[:2500]
            thread.summarized_upto = older[-1].id
            db.session.commit()
    except Exception:
        db.session.rollback()


# ================================================================
# USAJILI WA ROUTES
# ================================================================
def register_admin_tools(app, admin_only, app_ns):
    """Inaitwa MARA MOJA kutoka app.py. app_ns = globals() ya app.py."""
    group_fn = app_ns.get('market_group_key') or (lambda m: 'other')
    group_meta = {k: (icon, label) for k, icon, label in app_ns.get('MARKET_GROUPS', [])}

    @app.context_processor
    def _admin_tools_ctx():
        return {'admin_tools_ok': True}

    # ---------------- Market Analysis ----------------
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

    # ---------------- Admin AI ----------------
    @app.route('/admin/ai')
    @admin_only
    def admin_ai():
        threads = AdminChatThread.query.order_by(AdminChatThread.updated_at.desc()).limit(40).all()
        tid = request.args.get('t', type=int)
        thread = None
        if tid:
            thread = db.session.get(AdminChatThread, tid)
        elif request.args.get('new') != '1' and threads:
            thread = threads[0]
        messages = []
        if thread:
            msgs = AdminChatMessage.query.filter_by(thread_id=thread.id).order_by(AdminChatMessage.id).all()
            messages = [{'role': m.role, 'content': m.content} for m in msgs]
        return render_template('admin_ai.html', threads=threads, thread=thread, messages=messages,
                               ai_ready=bool((os.environ.get('AI_API_KEY') or '').strip()),
                               model=os.environ.get('AI_MODEL') or 'openai/gpt-oss-20b',
                               page='admin')

    @app.route('/admin/ai/send', methods=['POST'])
    @admin_only
    def admin_ai_send():
        data = request.get_json(silent=True) or {}
        text = (data.get('message') or '').strip()[:4000]
        if not text:
            return jsonify(ok=False, error='Ujumbe ni tupu.')

        try:
            tid = int(data.get('thread_id')) if data.get('thread_id') else None
        except (TypeError, ValueError):
            tid = None
        thread = db.session.get(AdminChatThread, tid) if tid else None

        history, prev_user = [], ''
        if thread is not None:
            all_msgs = AdminChatMessage.query.filter_by(thread_id=thread.id) \
                .order_by(AdminChatMessage.id).all()
            _maybe_summarize(thread, all_msgs)
            history = [{'role': m.role, 'content': m.content[:HISTORY_CHAR_CAP]}
                       for m in all_msgs[-HISTORY_MESSAGES:]]
            user_msgs = [m.content for m in all_msgs if m.role == 'user']
            prev_user = user_msgs[-1] if user_msgs else ''

        try:
            context = build_context(app_ns, text, prev_user, group_fn, group_meta)
        except Exception as e:
            db.session.rollback()
            context = f'[DATA haikupatikana: {type(e).__name__}]'

        dyn = 'DATA YA SASA (halisi, kutoka mfumo):\n' + context
        if thread is not None and thread.summary:
            dyn += '\n\nMUHTASARI WA MAZUNGUMZO YA AWALI:\n' + thread.summary

        messages = [{'role': 'system', 'content': SYSTEM_PROMPT},
                    {'role': 'system', 'content': dyn}] + history + [{'role': 'user', 'content': text}]

        ok, reply = _llm_chat(messages)
        if not ok:
            return jsonify(ok=False, error=reply)

        try:
            if thread is None:
                thread = AdminChatThread(title=text[:40])
                db.session.add(thread)
                db.session.flush()
            db.session.add(AdminChatMessage(thread_id=thread.id, role='user', content=text))
            db.session.add(AdminChatMessage(thread_id=thread.id, role='assistant', content=reply))
            thread.updated_at = datetime.utcnow()
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return jsonify(ok=True, reply=reply, thread_id=None,
                           warning=f'Jibu halikuhifadhiwa: {type(e).__name__}')
        return jsonify(ok=True, reply=reply, thread_id=thread.id)

    @app.route('/admin/ai/delete', methods=['POST'])
    @admin_only
    def admin_ai_delete():
        tid = request.form.get('thread_id', type=int)
        if tid:
            AdminChatMessage.query.filter_by(thread_id=tid).delete()
            AdminChatThread.query.filter_by(id=tid).delete()
            db.session.commit()
        return redirect(url_for('admin_ai', new=1))
