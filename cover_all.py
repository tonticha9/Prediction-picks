"""
cover_all.py — Comb ya Kufunika (Cover-All).

Plugin ya admin_tools.py (PLUGINS). Haibadilishi app.py wala templates zako.
  - Ukurasa /cover-all: chagua idadi ya mechi (2/3/4), stake kwa kila mchanganyiko, hali ya stake
    (sawa kwa kila mchanganyiko, au sawa-malipo) na uone faida ya kila mchanganyiko.
  - Idadi ya comb HAINA kikomo kilichowekwa: inategemea mechi zilizopo siku hiyo.
  - Ukurasa /cover-all/history: faida/hasara, ROI, win rate, chati na jedwali la siku.
  - Admin: kitufe cha kurekebisha kiwango cha chini cha probability ya dc_12 (+ / -).
  - Data: predictions.csv (home_win, away_win, dc_12 + odds za Bet365). Hakuna kitu kipya kinachohitajika Kaggle.
Mechi inaingia tu kama ina odds za home NA away, na dc_12 >= kiwango cha admin.
Comb inaonyeshwa tu kama mchanganyiko wenye faida ndogo kabisa bado uko juu ya sifuri
(mradi hakuna sare). Sare kwenye mechi yoyote = hasara ya stake yote.
"""
import itertools
import json
import math
import time
from datetime import date, datetime, timedelta

import pandas as pd
from flask import redirect, render_template_string, request, url_for, flash

from models import db

try:
    from fixture_times import kickoff as _kickoff
except Exception:
    def _kickoff(home, away, when):
        return ''

FLAG_DC12_MIN = 'cover_dc12_min'
FLAG_TOPK = 'cover_snapshot_topk'
DEFAULT_DC12_MIN = 70.0
DEFAULT_TOPK = 8
DEFAULT_STAKE = 5000.0
MAX_CANDS = 24          # kikomo cha mechi zinazochanganywa (kasi); zinachukuliwa kwa dc_12 ya juu


# ================================================================
# JEDWALI: picha ya comb za kila siku (inaganda mara ya kwanza ya siku)
# ================================================================
class CoverSlip(db.Model):
    __tablename__ = 'cover_slip'
    __table_args__ = (
        db.UniqueConstraint('snap_date', 'n_matches', 'slip_key', name='uq_cover_slip'),
    )
    id = db.Column(db.Integer, primary_key=True)
    snap_date = db.Column(db.Date, nullable=False, index=True)
    n_matches = db.Column(db.Integer, nullable=False)
    slip_key = db.Column(db.String(700), nullable=False)
    legs_json = db.Column(db.Text, nullable=False)
    ev_dutch = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ================================================================
# HESABU (safi, bila database)
# ================================================================
def _num(v):
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _flag_float(ns, key, default):
    try:
        return float(ns['get_flag'](key, str(default)))
    except Exception:
        return float(default)


def candidates(ns, day, dc12_min):
    """Mechi za siku 'day' zenye odds za home NA away (Bet365) na dc_12 >= dc12_min."""
    df = ns['get_data']()['predictions']
    d = df[(df['match_date'].dt.date == day) &
           (df['market'].isin(['home_win', 'away_win', 'dc_12']))]
    out, total = [], 0
    for (home, away, md), grp in d.groupby(['HomeTeam', 'AwayTeam', 'match_date']):
        rows = {r['market']: r for _, r in grp.iterrows()}
        hw, aw = rows.get('home_win'), rows.get('away_win')
        if hw is None or aw is None:
            continue
        oh, oa = _num(hw.get('real_odds')), _num(aw.get('real_odds'))
        if not oh or not oa or oh <= 1.0 or oa <= 1.0:
            continue
        ph, pa = float(hw['probability_%']) / 100.0, float(aw['probability_%']) / 100.0
        dcr = rows.get('dc_12')
        dc12 = float(dcr['probability_%']) if dcr is not None else (ph + pa) * 100.0
        total += 1
        if dc12 < dc12_min:
            continue
        try:
            k = _kickoff(home, away, md) or ''
        except Exception:
            k = ''
        if not k and (md.hour or md.minute):
            k = md.strftime('%H:%M')
        out.append({'home': home, 'away': away, 'date': md.date().isoformat(), 'kick': k,
                    'oh': oh, 'oa': oa, 'ph': ph, 'pa': pa, 'dc12': dc12,
                    'v': (ph * oh + pa * oa) / 2.0})
    return out, total


def find_groups(cands, n, mode='equal', only_ev=False):
    """Makundi ya mechi 'n' ambayo mchanganyiko wenye faida ndogo kabisa uko juu ya sifuri.
    equal: odds ndogo za kila mechi zikizidishwa > 2^n.  dutch: jumla ya (1/odds) ya mchanganyiko wote < 1."""
    cs = sorted(cands, key=lambda c: -c['dc12'])[:MAX_CANDS]
    res = []
    ncomb = 2 ** n
    for combo in itertools.combinations(cs, n):
        minprod, s, evm, pnd = 1.0, 1.0, 1.0, 1.0
        for c in combo:
            minprod *= min(c['oh'], c['oa'])
            s *= (1.0 / c['oh'] + 1.0 / c['oa'])
            evm *= c['v']
            pnd *= (c['ph'] + c['pa'])
        r_equal = minprod / ncomb - 1.0
        r_dutch = 1.0 / s - 1.0
        ratio = r_equal if mode == 'equal' else r_dutch
        if ratio <= 1e-9:
            continue
        ev = (evm - 1.0) if mode == 'equal' else (pnd / s - 1.0)
        if only_ev and ev <= 0:
            continue
        res.append({'legs': list(combo), 'ratio': ratio, 'r_equal': r_equal,
                    'r_dutch': r_dutch, 'ev': ev, 'pnd': pnd})
    res.sort(key=lambda g: (-g['ev'], -g['ratio']))
    return res


def slip_table(legs, stake, mode):
    """Mchanganyiko wote (home/away kwa kila mechi) na stake, malipo na faida."""
    n = len(legs)
    ncomb = 2 ** n
    rows = []
    for picks in itertools.product((0, 1), repeat=n):
        odds, q, names = 1.0, 1.0, []
        for leg, p in zip(legs, picks):
            odds *= leg['oh'] if p == 0 else leg['oa']
            den = leg['ph'] + leg['pa']
            q *= ((leg['ph'] if p == 0 else leg['pa']) / den) if den > 0 else 0.5
            names.append(leg['home'] if p == 0 else leg['away'])
        rows.append({'picks': picks, 'names': names, 'odds': odds, 'q': q})
    total = stake * ncomb
    if mode == 'dutch':
        inv = sum(1.0 / r['odds'] for r in rows)
        payout = total / inv
        for r in rows:
            r['payout'] = payout
            r['stake'] = payout / r['odds']
    else:
        for r in rows:
            r['stake'] = stake
            r['payout'] = stake * r['odds']
    for r in rows:
        r['profit'] = r['payout'] - total
    exp_pay_nd = sum(r['q'] * r['payout'] for r in rows)
    pnd = 1.0
    for leg in legs:
        pnd *= (leg['ph'] + leg['pa'])
    return {
        'rows': rows, 'total': total, 'ncomb': ncomb,
        'min_profit': min(r['profit'] for r in rows), 'max_profit': max(r['profit'] for r in rows),
        'min_roi': min(r['profit'] for r in rows) / total * 100.0,
        'max_roi': max(r['profit'] for r in rows) / total * 100.0,
        'pnd': pnd * 100.0,
        'breakeven': (total / exp_pay_nd * 100.0) if exp_pay_nd > 0 else None,
        'ev': (pnd * exp_pay_nd / total - 1.0) * 100.0,
    }


def slip_key(legs):
    return '||'.join(sorted(f"{l['home']}|{l['away']}" for l in legs))


def settle_profit(legs, status, picks, stake, mode):
    """Faida/hasara ya comb iliyokamilika. None = bado inasubiri."""
    n = len(legs)
    total = stake * (2 ** n)
    if status == 'PENDING':
        return None
    if status == 'LOST':
        return -total
    if mode == 'dutch':
        inv = 1.0
        for l in legs:
            inv *= (1.0 / l['oh'] + 1.0 / l['oa'])
        return total / inv - total
    odds = 1.0
    for l, p in zip(legs, picks):
        odds *= l['oh'] if p == 'H' else l['oa']
    return stake * odds - total


# ================================================================
# PICHA YA KILA SIKU NA TATHMINI YA MATOKEO
# ================================================================
def snapshot_cover(ns, day=None, force=False):
    """Inahifadhi comb bora za siku (K kwa kila idadi ya mechi) mara ya kwanza ya siku.
    Haibadilishwi baadaye, kwa hiyo History haina hindsight."""
    day = day or ns['today_eat']()
    if not force and CoverSlip.query.filter_by(snap_date=day).count():
        return 0
    cands, _ = candidates(ns, day, _flag_float(ns, FLAG_DC12_MIN, DEFAULT_DC12_MIN))
    topk = int(_flag_float(ns, FLAG_TOPK, DEFAULT_TOPK))
    added = 0
    for n in (2, 3, 4):
        if len(cands) < n:
            continue
        for g in find_groups(cands, n, 'dutch')[:max(1, topk)]:
            key = slip_key(g['legs'])
            if CoverSlip.query.filter_by(snap_date=day, n_matches=n, slip_key=key).first():
                continue
            db.session.add(CoverSlip(snap_date=day, n_matches=n, slip_key=key,
                                     legs_json=json.dumps(g['legs']), ev_dutch=g['ev']))
            added += 1
    if added:
        db.session.commit()
    return added


def evaluate_slip(ns, row, cache):
    """Hali ya comb kutoka matokeo halisi: WON / LOST / PENDING (+ mchanganyiko ulioshinda)."""
    legs = json.loads(row.legs_json)
    if row.snap_date not in cache:
        cache[row.snap_date] = ns['load_match_results'](row.snap_date, span=2)
    res = cache[row.snap_date]
    picks, pending, scores = [], False, []
    for leg in legs:
        mr = ns['find_match_result'](res, leg['home'], leg['away'], leg['date'])
        if mr is None or mr.fthg is None or mr.ftag is None:
            pending = True
            picks.append(None)
            scores.append(None)
            continue
        scores.append(f'{mr.fthg}-{mr.ftag}')
        picks.append('H' if mr.fthg > mr.ftag else ('A' if mr.fthg < mr.ftag else 'D'))
    if 'D' in picks:
        status = 'LOST'          # sare yoyote = comb nzima imepotea, hata mechi nyingine zikisubiri
    elif pending:
        status = 'PENDING'
    else:
        status = 'WON'
    return legs, status, picks, scores


def _load_rows(since):
    return (CoverSlip.query.filter(CoverSlip.snap_date >= since)
            .order_by(CoverSlip.snap_date.desc(), CoverSlip.n_matches, CoverSlip.id).all())


def history_stats(ns, days, mode, stake):
    since = ns['today_eat']() - timedelta(days=days)
    rows = _load_rows(since)
    cache, by_day, by_n = {}, {}, {}
    tot = {'won': 0, 'lost': 0, 'pending': 0, 'staked': 0.0, 'profit': 0.0}
    for r in rows:
        legs, status, picks, scores = evaluate_slip(ns, r, cache)
        profit = settle_profit(legs, status, picks, stake, mode)
        total_stake = stake * (2 ** len(legs))
        item = {'n': r.n_matches, 'legs': legs, 'status': status, 'profit': profit,
                'stake': total_stake, 'scores': scores, 'picks': picks}
        d = by_day.setdefault(r.snap_date, {'date': r.snap_date, 'slips': [], 'profit': 0.0,
                                            'staked': 0.0, 'won': 0, 'lost': 0, 'pending': 0})
        d['slips'].append(item)
        bn = by_n.setdefault(r.n_matches, {'n': r.n_matches, 'won': 0, 'lost': 0, 'pending': 0,
                                           'staked': 0.0, 'profit': 0.0})
        key = status.lower()
        d[key] += 1
        bn[key] += 1
        tot[key] += 1
        if profit is not None:
            d['profit'] += profit
            d['staked'] += total_stake
            bn['profit'] += profit
            bn['staked'] += total_stake
            tot['profit'] += profit
            tot['staked'] += total_stake
    settled = tot['won'] + tot['lost']
    tot['settled'] = settled
    tot['win_rate'] = round(tot['won'] / settled * 100.0, 1) if settled else None
    tot['roi'] = round(tot['profit'] / tot['staked'] * 100.0, 1) if tot['staked'] else None
    for bn in by_n.values():
        s = bn['won'] + bn['lost']
        bn['win_rate'] = round(bn['won'] / s * 100.0, 1) if s else None
        bn['roi'] = round(bn['profit'] / bn['staked'] * 100.0, 1) if bn['staked'] else None
    day_list = sorted(by_day.values(), key=lambda x: x['date'], reverse=True)
    for d in day_list:
        d['roi'] = round(d['profit'] / d['staked'] * 100.0, 1) if d['staked'] else None
    # chati: faida ya kujumlisha kwa siku (kuanzia ya zamani zaidi)
    pts, cum = [], 0.0
    for d in sorted(by_day.values(), key=lambda x: x['date']):
        if d['staked']:
            cum += d['profit']
            pts.append((d['date'].strftime('%d %b'), cum))
    chart = None
    if len(pts) >= 2:
        lo, hi = min(0.0, min(p[1] for p in pts)), max(0.0, max(p[1] for p in pts))
        span = (hi - lo) or 1.0
        w, h, pad = 320.0, 120.0, 8.0
        coords = []
        for i, (_, v) in enumerate(pts):
            x = pad + i * (w - 2 * pad) / (len(pts) - 1)
            y = h - pad - (v - lo) / span * (h - 2 * pad)
            coords.append((round(x, 1), round(y, 1)))
        zero_y = round(h - pad - (0 - lo) / span * (h - 2 * pad), 1)
        chart = {'points': ' '.join(f'{x},{y}' for x, y in coords), 'zero_y': zero_y,
                 'last': pts[-1][1], 'first_label': pts[0][0], 'last_label': pts[-1][0],
                 'min': lo, 'max': hi}
    return {'total': tot, 'by_n': sorted(by_n.values(), key=lambda x: x['n']),
            'days': day_list, 'chart': chart}


# ================================================================
# VIOLEZO (templates) — vinatumia base.html yako na classes zako zilizopo
# ================================================================
_STYLE = """
<style>
  .cv-tabs { display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap; }
  .cv-tab { padding:8px 14px; border-radius:999px; border:1px solid var(--border,#333); color:var(--text-dim); text-decoration:none; font-size:14px; font-weight:600; }
  .cv-tab.active { background:var(--indigo); color:#fff; border-color:var(--indigo); }
  .cv-form { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:14px; }
  .cv-form label { font-size:12px; color:var(--text-dim); display:block; margin-bottom:4px; }
  .cv-form select, .cv-form input[type=number] { width:100%; box-sizing:border-box; }
  .cv-form .full { grid-column:1 / -1; }
  .cv-chips { display:flex; gap:6px; flex-wrap:wrap; margin:8px 0 12px; }
  .cv-chip { font-size:12px; padding:4px 10px; border-radius:999px; background:rgba(255,255,255,.08); }
  .cv-chip b { color:var(--emerald); }
  .cv-note { font-size:13px; color:var(--text-dim); padding:10px 12px; border:1px solid var(--border,#333); border-radius:12px; margin-bottom:12px; line-height:1.5; }
  .cv-warn { border-color:rgba(245,158,11,.5); color:#f59e0b; }
  .cv-slip { margin-bottom:14px; }
  .cv-leg { display:flex; justify-content:space-between; gap:8px; font-size:13px; padding:5px 0; border-bottom:1px solid rgba(255,255,255,.06); }
  .cv-leg small { color:var(--text-dim); }
  .cv-sum { display:grid; grid-template-columns:repeat(3,1fr); gap:8px; margin:10px 0; }
  .cv-sum div { text-align:center; padding:9px 4px; border-radius:12px; background:rgba(255,255,255,.05); font-size:11px; color:var(--text-dim); }
  .cv-sum b { display:block; font-size:15px; color:inherit; }
  .cv-row { display:flex; justify-content:space-between; align-items:center; gap:8px; padding:7px 0; border-top:1px solid rgba(255,255,255,.06); font-size:13px; }
  .cv-names { min-width:0; word-break:break-word; }
  .cv-nums { text-align:right; white-space:nowrap; font-size:12px; color:var(--text-dim); }
  .cv-nums b { display:block; font-size:14px; }
  .cv-up { color:var(--emerald); } .cv-down { color:var(--red); }
  .cv-bad { color:#f59e0b; font-size:12px; margin-top:6px; }
  .cv-kpis { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:14px; }
  .cv-kpi { padding:14px 10px; border-radius:16px; text-align:center; background:rgba(255,255,255,.05); border:1px solid rgba(255,255,255,.09); }
  .cv-kpi b { display:block; font-size:22px; } .cv-kpi span { font-size:11px; color:var(--text-dim); }
  .cv-day { padding:10px 0; border-bottom:1px solid rgba(255,255,255,.07); }
  .cv-day summary { cursor:pointer; display:flex; justify-content:space-between; align-items:center; gap:8px; font-size:14px; }
  .cv-pill { font-size:11px; font-weight:700; padding:2px 8px; border-radius:999px; background:rgba(255,255,255,.1); }
  .cv-pill.won { background:rgba(16,185,129,.22); color:var(--emerald); }
  .cv-pill.lost { background:rgba(239,68,68,.22); color:var(--red); }
</style>
"""

PAGE_TEMPLATE = """{% extends 'base.html' %}
{% block content %}""" + _STYLE + """
<h2 style="margin-bottom:10px;">🧮 Comb ya Kufunika (Cover-All)</h2>
<div class="cv-tabs">
  <a class="cv-tab active" href="{{ url_for('cover_all_page') }}">Leo / Zijazo</a>
  <a class="cv-tab" href="{{ url_for('cover_all_history') }}">Dashboard na History</a>
</div>

<form method="GET" class="match-card cv-form">
  <div><label>Siku</label>
    <select name="date">
      {% for d in dates %}<option value="{{ d }}" {{ 'selected' if d == sel else '' }}>{{ d }}{% if d == today %} • Leo{% endif %}</option>{% endfor %}
      {% if not dates %}<option value="{{ sel }}">{{ sel }}</option>{% endif %}
    </select></div>
  <div><label>Idadi ya mechi kwa comb</label>
    <select name="n">
      {% for k in (2, 3, 4) %}<option value="{{ k }}" {{ 'selected' if k == n else '' }}>Mechi {{ k }} ({{ 2 ** k }} mchanganyiko)</option>{% endfor %}
    </select></div>
  <div><label>Stake kwa kila mchanganyiko</label>
    <input type="number" name="stake" value="{{ stake|int }}" min="100" step="100"></div>
  <div><label>Hali ya stake</label>
    <select name="mode">
      <option value="equal" {{ 'selected' if mode == 'equal' else '' }}>Stake sawa kwa kila mchanganyiko</option>
      <option value="dutch" {{ 'selected' if mode == 'dutch' else '' }}>Malipo sawa (stake inagawanywa)</option>
    </select></div>
  <div class="full"><label style="display:flex; gap:8px; align-items:center;">
    <input type="checkbox" name="ev" value="1" {{ 'checked' if only_ev else '' }}> Onyesha tu comb ambazo model inaziunga (EV chanya)</label></div>
  <div class="full"><button class="btn" type="submit">🔎 Hesabu</button></div>
</form>

<div class="cv-chips">
  <span class="cv-chip">Mechi zenye odds (home na away): <b>{{ total }}</b></span>
  <span class="cv-chip">Zinazopita dc_12 ≥ <b>{{ dc12_min|round(1) }}%</b>: <b>{{ n_cands }}</b></span>
  {% for k in (2, 3, 4) %}<span class="cv-chip">Mechi {{ k }}: <b>{{ counts[k] }}</b> comb</span>{% endfor %}
  {% if is_admin %}<a class="cv-chip" href="/admin#cover">⚙️ Rekebisha dc_12</a>{% endif %}
</div>

<div class="cv-note">
  Unabeti <b>mchanganyiko wote</b> wa washindi (home au away kwa kila mechi). Hakuna sare = unashinda mara zote.
  <b>Sare kwenye mechi yoyote = unapoteza stake yote.</b> Comb inaonyeshwa tu ikiwa mchanganyiko wenye faida ndogo kabisa bado uko juu ya sifuri.
  Idadi ya comb inategemea mechi zilizopo siku hiyo.
</div>

{% if not items %}
  <div class="empty-state">
    {% if n_cands < n %}
      Mechi zinazostahili ni {{ n_cands }} tu (zinazohitajika angalau {{ n }}). Mechi lazima ziwe na odds za home NA away (Bet365) na dc_12 ≥ {{ dc12_min|round(1) }}%.
      {% if is_admin %}Unaweza kupunguza kiwango cha dc_12 kwenye Admin.{% endif %}
    {% else %}
      Hakuna kundi la mechi {{ n }} ambalo mchanganyiko wake mdogo kabisa unalipa zaidi ya stake yote{% if only_ev %} na model inaliunga{% endif %}.
      Jaribu idadi tofauti ya mechi, hali ya "Malipo sawa", au ondoa tiki ya EV.
    {% endif %}
  </div>
{% endif %}

{% for it in items %}
{% set g = it.g %}{% set t = it.tbl %}
<div class="match-card cv-slip">
  <div style="font-weight:700; margin-bottom:6px;">Comb #{{ loop.index }} · mechi {{ g.legs|length }}</div>
  {% for l in g.legs %}
  <div class="cv-leg">
    <div>{{ l.home }} vs {{ l.away }}{% if l.kick %} <small>· 🕒 {{ l.kick }}</small>{% endif %}</div>
    <small>{{ l.oh|round(2) }} / {{ l.oa|round(2) }} · dc_12 {{ l.dc12|round(0)|int }}%</small>
  </div>
  {% endfor %}
  <div class="cv-sum">
    <div><b>{{ t.total|cv_money }}</b>jumla ya stake</div>
    <div><b class="cv-up">{{ t.min_profit|cv_signed }}</b>faida ndogo (bila sare)</div>
    <div><b class="cv-up">{{ t.max_profit|cv_signed }}</b>faida kubwa</div>
  </div>
  <div style="font-size:12px; color:var(--text-dim); margin-bottom:6px;">
    ROI bila sare: {{ '%+.1f'|format(t.min_roi) }}% hadi {{ '%+.1f'|format(t.max_roi) }}% ·
    Sare = <b class="cv-down">{{ (-t.total)|cv_signed }}</b>
  </div>
  <div style="font-size:12px; color:var(--text-dim);">
    Uwezekano wa mechi zote kuisha bila sare (model): <b>{{ '%.0f'|format(t.pnd) }}%</b> ·
    kiwango cha kuvunja sawa: <b>{% if t.breakeven is not none %}{{ '%.0f'|format(t.breakeven) }}%{% else %}—{% endif %}</b> ·
    EV ya model: <b class="{{ 'cv-up' if t.ev >= 0 else 'cv-down' }}">{{ '%+.1f'|format(t.ev) }}%</b>
  </div>
  {% if t.ev < 0 %}<div class="cv-bad">⚠️ Model haioni faida ya kweli hapa: uwezekano wa kutokuwa na sare uko chini ya kiwango cha kuvunja sawa.</div>{% endif %}
  <details style="margin-top:8px;">
    <summary style="cursor:pointer; font-weight:700; font-size:13px;">Mchanganyiko wote ({{ t.ncomb }})</summary>
    {% for r in t.rows %}
    <div class="cv-row">
      <div class="cv-names">{{ r.names|join(' + ') }}</div>
      <div class="cv-nums">@{{ r.odds|round(2) }} · stake {{ r.stake|cv_money }}<br>malipo {{ r.payout|cv_money }}
        <b class="{{ 'cv-up' if r.profit >= 0 else 'cv-down' }}">{{ r.profit|cv_signed }}</b></div>
    </div>
    {% endfor %}
  </details>
</div>
{% endfor %}

{% if more %}
  <div style="text-align:center; margin:10px 0;">
    <a class="btn btn-secondary" href="{{ url_for('cover_all_page', date=sel, n=n, mode=mode, stake=stake|int, ev=('1' if only_ev else '0'), show=show + 10) }}">Onyesha zaidi ({{ n_groups - shown_n }} zimebaki)</a>
  </div>
{% endif %}
{% endblock %}
"""

HISTORY_TEMPLATE = """{% extends 'base.html' %}
{% block content %}""" + _STYLE + """
<h2 style="margin-bottom:10px;">🧮 Cover-All: Dashboard na History</h2>
<div class="cv-tabs">
  <a class="cv-tab" href="{{ url_for('cover_all_page') }}">Leo / Zijazo</a>
  <a class="cv-tab active" href="{{ url_for('cover_all_history') }}">Dashboard na History</a>
</div>

<form method="GET" class="match-card cv-form">
  <div><label>Kipindi</label>
    <select name="days">{% for d in (14, 30, 60, 90) %}<option value="{{ d }}" {{ 'selected' if d == days else '' }}>Siku {{ d }}</option>{% endfor %}</select></div>
  <div><label>Stake kwa kila mchanganyiko</label>
    <input type="number" name="stake" value="{{ stake|int }}" min="100" step="100"></div>
  <div class="full"><label>Hali ya stake</label>
    <select name="mode">
      <option value="equal" {{ 'selected' if mode == 'equal' else '' }}>Stake sawa kwa kila mchanganyiko</option>
      <option value="dutch" {{ 'selected' if mode == 'dutch' else '' }}>Malipo sawa (stake inagawanywa)</option>
    </select></div>
  <div class="full"><button class="btn" type="submit">📊 Onyesha</button></div>
</form>

{% set T = stats.total %}
{% if not stats.days %}
  <div class="empty-state">Bado hakuna comb zilizohifadhiwa. Zinahifadhiwa mara ya kwanza ya siku (baada ya Refresh Data) na zinasettlewa mechi zikiisha.</div>
{% else %}
<div class="cv-kpis">
  <div class="cv-kpi"><b class="{{ 'cv-up' if T.profit >= 0 else 'cv-down' }}">{{ T.profit|cv_signed }}</b><span>Faida / hasara jumla</span></div>
  <div class="cv-kpi"><b class="{{ 'cv-up' if (T.roi or 0) >= 0 else 'cv-down' }}">{% if T.roi is not none %}{{ '%+.1f'|format(T.roi) }}%{% else %}—{% endif %}</b><span>ROI (faida ÷ stake)</span></div>
  <div class="cv-kpi"><b>{{ T.won }} / {{ T.lost }}</b><span>Zilizoshinda / zilizopoteza</span></div>
  <div class="cv-kpi"><b>{% if T.win_rate is not none %}{{ T.win_rate }}%{% else %}—{% endif %}</b><span>Mara zisizo na sare{% if T.pending %} · ⏳ {{ T.pending }} zinasubiri{% endif %}</span></div>
</div>

{% if stats.chart %}
<div class="match-card" style="margin-bottom:14px;">
  <div style="font-size:12px; color:var(--text-dim); margin-bottom:6px;">Faida ya kujumlisha ({{ stats.chart.first_label }} hadi {{ stats.chart.last_label }})</div>
  <svg viewBox="0 0 320 120" style="width:100%; height:auto; display:block;">
    <line x1="8" y1="{{ stats.chart.zero_y }}" x2="312" y2="{{ stats.chart.zero_y }}" stroke="rgba(255,255,255,.25)" stroke-dasharray="4 4"/>
    <polyline points="{{ stats.chart.points }}" fill="none" stroke="{{ '#10b981' if stats.chart.last >= 0 else '#ef4444' }}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>
  </svg>
  <div style="font-size:12px; margin-top:4px; text-align:right;">Sasa: <b class="{{ 'cv-up' if stats.chart.last >= 0 else 'cv-down' }}">{{ stats.chart.last|cv_signed }}</b></div>
</div>
{% endif %}

<div class="match-card" style="margin-bottom:14px;">
  <div style="font-weight:700; margin-bottom:6px; font-size:14px;">Kwa idadi ya mechi</div>
  {% for b in stats.by_n %}
  <div class="cv-row">
    <div>Mechi {{ b.n }} <small style="color:var(--text-dim);">· ✅{{ b.won }} ❌{{ b.lost }}{% if b.pending %} ⏳{{ b.pending }}{% endif %}</small></div>
    <div class="cv-nums"><b class="{{ 'cv-up' if b.profit >= 0 else 'cv-down' }}">{{ b.profit|cv_signed }}</b>ROI {% if b.roi is not none %}{{ '%+.1f'|format(b.roi) }}%{% else %}—{% endif %}</div>
  </div>
  {% endfor %}
</div>

<div class="match-card">
  <div style="font-weight:700; margin-bottom:6px; font-size:14px;">Kila siku</div>
  {% for d in stats.days %}
  <details class="cv-day">
    <summary>
      <span>{{ d.date.strftime('%d %b %Y') }} <small style="color:var(--text-dim);">· comb {{ d.slips|length }} · ✅{{ d.won }} ❌{{ d.lost }}{% if d.pending %} ⏳{{ d.pending }}{% endif %}</small></span>
      {% if d.staked %}<b class="{{ 'cv-up' if d.profit >= 0 else 'cv-down' }}">{{ d.profit|cv_signed }}{% if d.roi is not none %} <small>({{ '%+.0f'|format(d.roi) }}%)</small>{% endif %}</b>{% else %}<span class="cv-pill">⏳</span>{% endif %}
    </summary>
    {% for s in d.slips %}
    <div class="cv-row" style="align-items:flex-start;">
      <div class="cv-names">
        <span class="cv-pill {{ s.status|lower }}">{{ s.status }}</span> mechi {{ s.n }}
        {% for l in s.legs %}<div style="font-size:12px; color:var(--text-dim);">{{ l.home }} vs {{ l.away }}{% if s.scores[loop.index0] %} · <b>{{ s.scores[loop.index0] }}</b>{% endif %}</div>{% endfor %}
      </div>
      <div class="cv-nums">
        {% if s.profit is not none %}<b class="{{ 'cv-up' if s.profit >= 0 else 'cv-down' }}">{{ s.profit|cv_signed }}</b>{% else %}<b>⏳</b>{% endif %}
        stake {{ s.stake|cv_money }}
      </div>
    </div>
    {% endfor %}
  </details>
  {% endfor %}
</div>
<div class="cv-note" style="margin-top:12px;">
  Comb zinahifadhiwa mara ya kwanza ya siku (hazibadilishwi baadaye, kwa hiyo hakuna hindsight). Sare kwenye mechi yoyote inahesabiwa kama hasara ya stake yote.
  Hasara zote zinaonyeshwa kama zilivyo.
</div>
{% endif %}
{% endblock %}
"""


# ================================================================
# SEHEMU ZINAZOINGIZWA KWENYE KURASA ZILIZOPO (admin + kitufe cha haraka)
# ================================================================
_FAB = ("<a href=\"/cover-all\" class=\"cv-fab\">🧮 Cover-All</a>"
        "<style>.cv-fab{position:fixed;right:12px;bottom:84px;z-index:50;padding:10px 14px;border-radius:999px;"
        "background:linear-gradient(135deg,#6366f1,#10b981);color:#fff;font-weight:700;font-size:13px;"
        "text-decoration:none;box-shadow:0 4px 18px rgba(0,0,0,.4)}</style>")

FAB_PAGES = {'dashboard.html', 'upcoming.html', 'value_bets.html', 'combos.html', 'history.html'}


def _admin_block(ns):
    dc12_min = _flag_float(ns, FLAG_DC12_MIN, DEFAULT_DC12_MIN)
    topk = int(_flag_float(ns, FLAG_TOPK, DEFAULT_TOPK))
    info = ''
    try:
        today = ns['today_eat']()
        cands, total = candidates(ns, today, dc12_min)
        info = (f'Leo: mechi {total} zina odds za home na away; {len(cands)} zinapita dc_12 ≥ {dc12_min:g}%.')
    except Exception:
        info = 'Data ya leo haikusomwa.'
    return f"""
<details class="ad-sec" id="cover" style="margin-top:16px;">
  <summary>🧮 Cover-All: kiwango cha dc_12 <span class="ad-chip on">≥ {dc12_min:g}%</span></summary>
  <div class="ad-body">
    <form method="POST" action="/admin/cover/save">
      <div class="form-group">
        <label>Probability ya chini ya dc_12 kwa kila mechi (%)</label>
        <div style="display:flex; gap:8px; align-items:center;">
          <button type="button" class="btn btn-secondary" onclick="cvStep(-1)" style="min-width:48px;">−</button>
          <input id="cvDc" type="number" name="dc12_min" value="{dc12_min:g}" min="50" max="95" step="0.5" style="flex:1; text-align:center;">
          <button type="button" class="btn btn-secondary" onclick="cvStep(1)" style="min-width:48px;">+</button>
        </div>
        <div class="ad-hint">Mechi inaingia kwenye Cover-All tu ikiwa dc_12 (hakuna sare) yake ni sawa au juu ya kiwango hiki. Kiwango cha juu = mechi chache lakini uwezekano mkubwa wa kutokuwa na sare. Punguza ili upate mechi zaidi.</div>
      </div>
      <div class="form-group">
        <label>Comb za kuhifadhiwa kila siku kwa kila idadi ya mechi (History)</label>
        <input type="number" name="topk" value="{topk}" min="1" max="30">
      </div>
      <div class="ad-hint" style="margin-bottom:10px;">{info}</div>
      <button class="btn" type="submit">💾 Hifadhi Cover-All</button>
      <a class="btn btn-secondary" href="/cover-all" style="margin-left:6px;">Fungua Cover-All</a>
    </form>
    <script>function cvStep(d){{var i=document.getElementById('cvDc');var v=parseFloat(i.value||'{dc12_min:g}')+d;i.value=Math.max(50,Math.min(95,v));}}</script>
  </div>
</details>
"""


_ADMIN_TILE = ('<a class="ad-tile" href="/cover-all"><span class="ic">🧮</span><b>Cover-All</b>'
               '<small>Comb ya kufunika + History</small></a>')


# ================================================================
# USAJILI
# ================================================================
def register(app, admin_only, app_ns):
    try:
        with app.app_context():
            CoverSlip.__table__.create(db.engine, checkfirst=True)
    except Exception as e:
        print(f'[cover_all] kutengeneza jedwali kumeshindwa: {e}')

    app.jinja_env.filters['cv_money'] = lambda v: '{:,.0f}'.format(v or 0)
    app.jinja_env.filters['cv_signed'] = lambda v: '{:+,.0f}'.format(v or 0)

    def _lazy_snapshot():
        try:
            snapshot_cover(app_ns)
        except Exception as e:
            try:
                db.session.rollback()
            except Exception:
                pass
            print(f'[cover_all] picha ya siku imeshindwa: {type(e).__name__}: {e}')

    def _arg_float(name, default, lo, hi):
        try:
            return max(lo, min(hi, float(request.args.get(name, default))))
        except (TypeError, ValueError):
            return default

    @app.route('/cover-all')
    def cover_all_page():
        ns = app_ns
        today = ns['today_eat']()
        df = ns['get_data']()['predictions']
        dates = sorted({d for d in df['match_date'].dt.date if d >= today})[:7]
        dates_s = [d.isoformat() for d in dates]
        sel = request.args.get('date')
        if sel not in dates_s:
            sel = today.isoformat() if today in dates else (dates_s[0] if dates_s else today.isoformat())
        sel_day = date.fromisoformat(sel)
        n = int(_arg_float('n', 2, 2, 4))
        stake = _arg_float('stake', DEFAULT_STAKE, 100, 100000000)
        mode = request.args.get('mode') if request.args.get('mode') in ('equal', 'dutch') else 'equal'
        only_ev = request.args.get('ev') == '1'
        show = int(_arg_float('show', 10, 5, 200))
        dc12_min = _flag_float(ns, FLAG_DC12_MIN, DEFAULT_DC12_MIN)

        if sel_day == today:
            _lazy_snapshot()
        cands, total = candidates(ns, sel_day, dc12_min)
        counts = {k: (len(find_groups(cands, k, mode, only_ev)) if len(cands) >= k else 0) for k in (2, 3, 4)}
        groups = find_groups(cands, n, mode, only_ev) if len(cands) >= n else []
        shown = groups[:show]
        items = [{'g': g, 'tbl': slip_table(g['legs'], stake, mode)} for g in shown]
        return render_template_string(
            PAGE_TEMPLATE, items=items, dates=dates_s, sel=sel, today=today.isoformat(), n=n,
            stake=stake, mode=mode, only_ev=only_ev, show=show, dc12_min=dc12_min, total=total,
            n_cands=len(cands), counts=counts, more=len(groups) > len(shown),
            n_groups=len(groups), shown_n=len(shown), page='cover')

    @app.route('/cover-all/history')
    def cover_all_history():
        ns = app_ns
        days = int(_arg_float('days', 30, 7, 365))
        stake = _arg_float('stake', DEFAULT_STAKE, 100, 100000000)
        mode = request.args.get('mode') if request.args.get('mode') in ('equal', 'dutch') else 'equal'
        _lazy_snapshot()
        try:
            stats = history_stats(ns, days, mode, stake)
        except Exception as e:
            db.session.rollback()
            print(f'[cover_all] history imeshindwa: {type(e).__name__}: {e}')
            stats = {'total': {'won': 0, 'lost': 0, 'pending': 0, 'staked': 0.0, 'profit': 0.0,
                               'settled': 0, 'win_rate': None, 'roi': None},
                     'by_n': [], 'days': [], 'chart': None}
        return render_template_string(HISTORY_TEMPLATE, stats=stats, days=days, stake=stake,
                                      mode=mode, page='cover')

    @app.route('/admin/cover/save', methods=['POST'])
    @admin_only
    def cover_admin_save():
        try:
            v = max(50.0, min(95.0, float(request.form.get('dc12_min', DEFAULT_DC12_MIN))))
            k = max(1, min(30, int(request.form.get('topk', DEFAULT_TOPK))))
        except (TypeError, ValueError):
            flash('Thamani si sahihi.', 'error')
            return redirect(url_for('admin') + '#cover')
        app_ns['set_flag'](FLAG_DC12_MIN, str(v))
        app_ns['set_flag'](FLAG_TOPK, str(k))
        flash(f'Cover-All: dc_12 ya chini sasa ni {v:g}%.', 'success')
        return redirect(url_for('admin') + '#cover')

    # Picha ya siku inaandikwa pamoja na Refresh Data (bila kuhariri app.py)
    orig_snap = app_ns.get('snapshot_today')
    if orig_snap is not None and not getattr(orig_snap, '_cv_patched', False):
        def snapshot_today_cover(steps=None):
            result = orig_snap(steps)
            t = time.time()
            try:
                n = snapshot_cover(app_ns)
                if steps is not None:
                    steps.append(f'cover({n}) {round(time.time() - t, 1)}s')
            except Exception as e:
                db.session.rollback()
                print(f'[cover_all] snapshot imeshindwa: {type(e).__name__}: {e}')
            return result
        snapshot_today_cover._cv_patched = True
        app_ns['snapshot_today'] = snapshot_today_cover

    # Admin: sehemu ya dc_12 + tile; kurasa nyingine: kitufe cha haraka (hadi menyu irekebishwe)
    orig_render = app_ns.get('render_template')
    if orig_render is not None and not getattr(orig_render, '_cv_patched', False):
        def render_template_cover(template_name, **ctx):
            html = orig_render(template_name, **ctx)
            try:
                if template_name == 'admin.html' and 'id="cover"' not in html:
                    anchor = '<form method="POST" id="settingsForm">'
                    if anchor in html:
                        html = html.replace(anchor, _admin_block(app_ns) + anchor, 1)
                    if '<div class="ad-grid">' in html:
                        html = html.replace('<div class="ad-grid">', '<div class="ad-grid">' + _ADMIN_TILE, 1)
                elif template_name in FAB_PAGES and 'cv-fab' not in html and '</body>' in html:
                    html = html.replace('</body>', _FAB + '</body>', 1)
            except Exception as e:
                print(f'[cover_all] kuingiza sehemu kumeshindwa: {type(e).__name__}: {e}')
            return html
        render_template_cover._cv_patched = True
        app_ns['render_template'] = render_template_cover