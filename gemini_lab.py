"""
gemini_lab.py — Plugin (weka BAADA ya gemini_boost kwenye PLUGINS).

  - research_match: utafiti wa mechi moja KWA WAKATI MMOJA (utafutaji 7 sambamba)
  - get_model_probs: probability za model kabla ya mechi (masoko 10-15 bora, bila matokeo)
  - list_matches_for_day: orodha ya mechi za siku (kwa backtest)
  - save_ai_analysis: kuhifadhi makadirio ya AI (seva inakata marekebisho > +-8 pts,
    na haikubali 'live' mechi ikishaanza)
  - evaluate_ai_analyses: kupima kama marekebisho yalisaidia (Brier: model dhidi ya AI)
Jedwali jipya: ai_adjust_log (linatengenezwa lenyewe).
"""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, time as dtime

import pandas as pd

import gemini_ai as G
from models import db, PredictionRecord

RESEARCH_CAP = int(os.environ.get('AI_RESEARCH_CAP', '12') or 12)   # mechi kwa siku
MAX_ADJ = 8.0
_SEM = threading.Semaphore(4)         # utafutaji wa Gemini sambamba usizidi 4


class AiAdjustLog(db.Model):
    __tablename__ = 'ai_adjust_log'
    __table_args__ = (
        db.UniqueConstraint('mode', 'match_date', 'home_team', 'away_team', 'market',
                            name='uq_ai_adjust'),
    )
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    mode = db.Column(db.String(10), index=True)          # live | backtest
    match_date = db.Column(db.Date, index=True)
    home_team = db.Column(db.String(120))
    away_team = db.Column(db.String(120))
    market = db.Column(db.String(80))
    model_prob = db.Column(db.Float)
    adj_prob = db.Column(db.Float)
    reasons = db.Column(db.String(600))
    sources = db.Column(db.String(300))
    kickoff_eat = db.Column(db.String(5))


# ---------------------------------------------------------------
# Visaidizi
# ---------------------------------------------------------------
def _f(x):
    try:
        v = float(x)
        return None if v != v else v
    except (TypeError, ValueError):
        return None


def _int(v, d):
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


def _kick(h, a, d):
    try:
        from fixture_times import kickoff
        return kickoff(h, a, d)
    except Exception:
        return ''


def _day_range(d):
    lo = datetime(d.year, d.month, d.day)
    return lo, lo + timedelta(days=1)


def _model_rows(home, away, d):
    """(jina_home, jina_away, [ {market, prob, odds, tier, v8} ]) - probability ya model KABLA ya mechi."""
    NS = G.NS
    hq, aq = home.strip(), away.strip()
    rows, hn, an = [], home, away
    if d >= NS['today_eat']():
        df = NS['get_data']()['predictions']
        m = df[(df['match_date'].dt.date == d)
               & df['HomeTeam'].str.lower().str.contains(hq.lower(), regex=False)
               & df['AwayTeam'].str.lower().str.contains(aq.lower(), regex=False)]
        for _, r in m.iterrows():
            p = _f(r.get('probability_%'))
            if p is None:
                continue
            hn, an = r['HomeTeam'], r['AwayTeam']
            v8 = None
            try:
                v8 = G._v8_prob(hn, an, r['match_date'], r['market'])
            except Exception:
                pass
            rows.append({'market': r['market'], 'prob': p, 'odds': _f(r.get('real_odds')),
                         'tier': r.get('pro_tier'), 'v8': v8})
    else:
        lo, hi = _day_range(d)
        q = PredictionRecord.query.filter(
            PredictionRecord.section == 'prediction',
            PredictionRecord.match_date >= lo, PredictionRecord.match_date < hi,
            PredictionRecord.home_team.ilike(f'%{hq}%'),
            PredictionRecord.away_team.ilike(f'%{aq}%'))
        for r in q.all():
            p = r.first_probability if r.first_probability is not None else r.probability
            if p is None:
                continue
            hn, an = r.home_team, r.away_team
            odds = r.first_real_odds if r.first_real_odds is not None else r.real_odds
            rows.append({'market': r.market, 'prob': float(p), 'odds': _f(odds),
                         'tier': r.first_pro_tier or r.pro_tier, 'v8': None})
    seen, out = set(), []
    for r in rows:
        if r['market'] not in seen:
            seen.add(r['market'])
            out.append(r)
    return hn, an, out


def _strong(rows, n):
    try:
        from market_selection import quality_info, is_display_allowed
    except Exception:
        quality_info, is_display_allowed = None, None
    cand = []
    for r in rows:
        m = r['market']
        if is_display_allowed and not is_display_allowed(m):
            continue
        if m.endswith('goals_over_05'):
            continue
        qi = quality_info(m, r['prob']) if quality_info else None
        if qi is not None and not qi['informative']:
            continue
        r = dict(r)
        r['qi'] = qi
        cand.append(r)
    cand.sort(key=lambda r: r['prob'], reverse=True)
    return cand[:n]


# ---------------------------------------------------------------
# Zana
# ---------------------------------------------------------------
def _t_model_probs(a):
    home, away = str(a.get('home') or '').strip(), str(a.get('away') or '').strip()
    d = G._parse_date(a.get('date'))
    if len(home) < 3 or len(away) < 3 or d is None:
        return 'Weka home, away (jina kama kwenye app) na date (YYYY-MM-DD).'
    n = max(5, min(25, _int(a.get('top'), 15)))
    hn, an, rows = _model_rows(home, away, d)
    if not rows:
        return f'Hakuna probability za model za {home} v {away} tarehe {d}.'
    strong = _strong(rows, n)
    t = _kick(hn, an, d)
    past = d < G.NS['today_eat']()
    L = [f'MECHI: {hn} v {an} | {d} | saa ya mechi: {t + " EAT" if t else "haijulikani"}',
         ('MODEL (picha ya KABLA ya mechi; matokeo hayajajumuishwa)' if past else 'MODEL (sasa)')
         + f' - masoko {len(strong)} bora yenye ishara, kati ya {len(rows)}:']
    for i, r in enumerate(strong, 1):
        qi = r['qi']
        s = f"{i}. {r['market']} — {r['prob']:.1f}%"
        if r['odds']:
            s += f" @{r['odds']:.2f}"
        if r['tier']:
            s += f" | {r['tier']}"
        if qi:
            s += f" | ubora {qi['tier']} ({qi['edge']:+.1f} juu ya base)"
        if r['v8'] is not None:
            s += f" | bila odds {r['v8']}%"
        L.append(s)
    return '\n'.join(L)


def _t_list_day(a):
    d = G._parse_date(a.get('date'))
    if d is None:
        return 'Weka date (YYYY-MM-DD).'
    NS = G.NS
    pairs = {}
    if d >= NS['today_eat']():
        df = NS['get_data']()['predictions']
        for (h, aw), grp in df[df['match_date'].dt.date == d].groupby(['HomeTeam', 'AwayTeam']):
            pairs[(h, aw)] = len(grp)
    else:
        lo, hi = _day_range(d)
        for h, aw in (PredictionRecord.query.filter(
                PredictionRecord.section == 'prediction',
                PredictionRecord.match_date >= lo, PredictionRecord.match_date < hi)
                .with_entities(PredictionRecord.home_team, PredictionRecord.away_team).all()):
            pairs[(h, aw)] = pairs.get((h, aw), 0) + 1
    if not pairs:
        return f'Hakuna mechi zilizorekodiwa tarehe {d}.'
    L = [f'Mechi {len(pairs)} za {d} (masoko yaliyorekodiwa; matokeo hayaonyeshwi):']
    for i, ((h, aw), c) in enumerate(sorted(pairs.items(), key=lambda kv: -kv[1])[:30], 1):
        L.append(f'{i}. {h} v {aw} (masoko {c})')
    return '\n'.join(L)


_RESEARCH_Q = [
    ('Majeruhi/adhabu ya {home}',
     '{home} injury news suspensions doubtful players before {home} vs {away} on {ds}'),
    ('Majeruhi/adhabu ya {away}',
     '{away} injury news suspensions doubtful players before {home} vs {away} on {ds}'),
    ('Kikosi', '{home} vs {away} {ds} predicted or confirmed starting lineup'),
    ('Hali ya timu na muktadha',
     '{home} and {away} recent form last five matches motivation fixture congestion manager comments {ds}'),
    ('xG na takwimu',
     '{home} {away} expected goals xG recent matches Understat FBref FotMob team stats this season'),
    ('Refa na kadi', '{home} vs {away} {ds} referee appointed cards per match corners tendencies'),
    ('Mechi za awali (H2H)', '{home} {away} head to head last meetings results goals cards'),
]


def _t_research(a):
    home, away = str(a.get('home') or '').strip(), str(a.get('away') or '').strip()
    d = G._parse_date(a.get('date'))
    if len(home) < 3 or len(away) < 3 or d is None:
        return 'Weka home, away na date (YYYY-MM-DD).'
    NS = G.NS
    key = f'ai_research_{NS["today_eat"]().isoformat()}'
    try:
        n = int(NS['get_flag'](key, '0') or 0)
        if n >= RESEARCH_CAP:
            return f'Kikomo cha utafiti wa leo (mechi {RESEARCH_CAP}) kimefikiwa. Subiri kesho au ongeza AI_RESEARCH_CAP.'
        NS['set_flag'](key, str(n + 1))
    except Exception:
        pass
    blind = bool(a.get('before_kickoff_only'))
    ds = d.isoformat()
    guard = (f' Use ONLY information published before {ds} (before:{ds}). Ignore any match report, '
             f'score or result of this fixture.') if blind else ''
    jobs = [(t.format(home=home, away=away, ds=ds), q.format(home=home, away=away, ds=ds) + guard)
            for t, q in _RESEARCH_Q]

    def run(job):
        with _SEM:
            try:
                ok, res = G._grounded_search(job[1])
            except Exception as e:
                return job[0], False, f'{type(e).__name__}: {e}'
        return job[0], ok, res

    with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        results = list(ex.map(run, jobs))
    L = [f'UTAFITI: {home} v {away} ({ds}) — '
         + ('BACKTEST (taarifa za kabla ya mechi tu; hatari ya hindsight)' if blind else 'LIVE')]
    for title, ok, res in results:
        L.append(f'[{title}]')
        L.append((res if ok else f'(imeshindwa: {str(res)[:80]})')[:1100])
    return '\n'.join(L)


def _t_save(a):
    home, away = str(a.get('home') or '').strip(), str(a.get('away') or '').strip()
    d = G._parse_date(a.get('date'))
    mode = 'backtest' if str(a.get('mode') or '').lower().startswith('back') else 'live'
    items = a.get('items')
    if len(home) < 3 or len(away) < 3 or d is None or not isinstance(items, list) or not items:
        return 'Weka home, away, date, mode na items (market, adj_prob, reason, source).'
    NS = G.NS
    hn, an, rows = _model_rows(home, away, d)
    model = {r['market']: r['prob'] for r in rows}
    if not model:
        return 'Hakuna probability za model za mechi hii; siwezi kuhifadhi.'
    t = _kick(hn, an, d)
    if mode == 'live':
        if d < NS['today_eat']():
            return 'Mechi ya tarehe iliyopita: tumia mode "backtest".'
        if t:
            ko = datetime(d.year, d.month, d.day, int(t[:2]), int(t[3:5]))
            if NS['now_eat']() >= ko:
                return (f'Mechi ilianza saa {t} EAT. Makadirio ya live hayaruhusiwi baada ya kuanza '
                        f'(hindsight); hayakuhifadhiwa.')
    saved = clamped = skipped = 0
    for it in items[:25]:
        if not isinstance(it, dict):
            skipped += 1
            continue
        market = str(it.get('market') or '').strip()
        adj = _f(it.get('adj_prob'))
        mp = model.get(market)
        if not market or adj is None or mp is None or not (0 <= adj <= 100):
            skipped += 1
            continue
        if abs(adj - mp) > MAX_ADJ:
            adj = mp + MAX_ADJ if adj > mp else mp - MAX_ADJ
            clamped += 1
        row = AiAdjustLog.query.filter_by(mode=mode, match_date=d, home_team=hn,
                                          away_team=an, market=market).first()
        if row is None:
            row = AiAdjustLog(mode=mode, match_date=d, home_team=hn, away_team=an, market=market)
            db.session.add(row)
        row.model_prob = round(mp, 1)
        row.adj_prob = round(adj, 1)
        row.reasons = str(it.get('reason') or '')[:600]
        row.sources = str(it.get('source') or '')[:300]
        row.kickoff_eat = t or None
        row.created_at = datetime.utcnow()
        saved += 1
    db.session.commit()
    return (f'Imehifadhiwa ({mode}): masoko {saved}, yaliyokatwa kwa kikomo cha ±{MAX_ADJ:.0f} pts: {clamped}, '
            f'yaliyoruka (soko halipo kwenye model au namba si sahihi): {skipped}.')


def _brier(pairs):
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def _t_eval(a):
    days = max(1, min(120, _int(a.get('days'), 30)))
    mode = str(a.get('mode') or '').lower()
    mode = 'backtest' if mode.startswith('back') else ('live' if mode.startswith('live') else '')
    NS = G.NS
    since = NS['today_eat']() - timedelta(days=days)
    q = AiAdjustLog.query.filter(AiAdjustLog.match_date >= since)
    if mode:
        q = q.filter(AiAdjustLog.mode == mode)
    logs = q.all()
    if not logs:
        return 'Hakuna makadirio ya AI yaliyohifadhiwa kwa kipindi hicho.'
    lo = datetime.combine(min(l.match_date for l in logs), dtime.min)
    hi = datetime.combine(max(l.match_date for l in logs), dtime.min) + timedelta(days=1)
    recs = (PredictionRecord.query.filter(
        PredictionRecord.section == 'prediction',
        PredictionRecord.match_date >= lo, PredictionRecord.match_date < hi)
        .with_entities(PredictionRecord.match_date, PredictionRecord.home_team,
                       PredictionRecord.away_team, PredictionRecord.market,
                       PredictionRecord.result).all())
    rmap = {(r.match_date.date(), r.home_team, r.away_team, r.market): r.result for r in recs}
    L = [f'Tathmini ya makadirio ya AI (siku {days}):']
    for md in (['live', 'backtest'] if not mode else [mode]):
        sub = [l for l in logs if l.mode == md]
        if not sub:
            continue
        done, pending = [], 0
        for l in sub:
            res = rmap.get((l.match_date, l.home_team, l.away_team, l.market))
            if res in ('WON', 'LOST') and l.model_prob is not None and l.adj_prob is not None:
                done.append((l, 1.0 if res == 'WON' else 0.0))
            elif res not in ('WON', 'LOST'):
                pending += 1
        matches = len({(l.match_date, l.home_team, l.away_team) for l, _ in done})
        L.append(f'\n[{md.upper()}] makadirio {len(sub)}, yaliyosettlewa {len(done)} '
                 f'(mechi {matches}), yanayosubiri {pending}.')
        if not done:
            continue
        pm = [(l.model_prob / 100, y) for l, y in done]
        pa = [(l.adj_prob / 100, y) for l, y in done]
        bm, ba = _brier(pm), _brier(pa)
        changed = [(l, y) for l, y in done if abs(l.adj_prob - l.model_prob) >= 0.5]
        better = sum(1 for l, y in changed if abs(l.adj_prob / 100 - y) < abs(l.model_prob / 100 - y))
        L.append(f'Brier (chini ni bora): model {bm:.4f} | AI {ba:.4f} | tofauti {(bm - ba) * 1000:+.1f} (‰, + = AI bora).')
        if changed:
            L.append(f'Marekebisho yaliyobadilisha namba: {len(changed)}; yaliyoikaribia matokeo zaidi: '
                     f'{better} ({better / len(changed) * 100:.0f}%).')
        for name, cond in (('Alipandisha', lambda l: l.adj_prob > l.model_prob + 0.4),
                           ('Alipunguza', lambda l: l.adj_prob < l.model_prob - 0.4)):
            g = [(l, y) for l, y in done if cond(l)]
            if g:
                L.append(f'{name}: {len(g)} | model wastani {sum(l.model_prob for l, _ in g) / len(g):.1f}% | '
                         f'AI wastani {sum(l.adj_prob for l, _ in g) / len(g):.1f}% | '
                         f'ushindi halisi {sum(y for _, y in g) / len(g) * 100:.1f}%')
        if len(done) < 100 or matches < 30:
            L.append('⚠️ Sampuli ndogo (chini ya makadirio 100 au mechi 30 tofauti): usihukumu bado. '
                     'Masoko ya mechi moja yanahusiana, kwa hiyo sampuli halisi ni ndogo kuliko inavyoonekana.')
        elif bm - ba > 0.001 and better / max(1, len(changed)) > 0.52:
            L.append('✅ Marekebisho yanaonekana kusaidia.')
        elif ba - bm > 0.001:
            L.append('❌ Marekebisho yanadhuru: bora tumia model pekee.')
        else:
            L.append('➖ Hakuna tofauti ya maana.')
        if md == 'backtest':
            L.append('ℹ️ Backtest ina hatari ya hindsight; kipimo cha kweli ni LIVE.')
    return '\n'.join(L)


# ---------------------------------------------------------------
# Maelezo ya zana, maelekezo, usajili
# ---------------------------------------------------------------
_HA = {'home': G._s('Timu ya nyumbani (jina kama kwenye app)'),
       'away': G._s('Timu ya ugenini'), 'date': G._s('YYYY-MM-DD')}

DECLS = {
    'get_model_probs': G._d(
        'get_model_probs',
        'Probability za model KABLA ya mechi (tarehe ya zamani pia: picha ya kabla ya mechi, bila matokeo). '
        'Inarudisha masoko 10-15 bora yenye ishara.',
        dict(_HA, top=G._n('Idadi ya masoko (5-25, chaguo-msingi 15)')), ['home', 'away', 'date']),
    'list_matches_for_day': G._d('list_matches_for_day', 'Orodha ya mechi za siku (kwa backtest), bila matokeo.',
                                 {'date': G._s('YYYY-MM-DD')}, ['date']),
    'research_match': G._d(
        'research_match',
        'Utafiti wa mechi moja KWA WAKATI MMOJA: majeruhi/adhabu ya timu zote, kikosi, hali ya timu, xG, '
        'refa/kadi, H2H (utafutaji 7 sambamba). before_kickoff_only=true kwa backtest.',
        dict(_HA, before_kickoff_only={'type': 'boolean',
                                       'description': 'true = taarifa za kabla ya mechi tu (backtest)'}),
        ['home', 'away', 'date']),
    'save_ai_analysis': {
        'name': 'save_ai_analysis',
        'description': 'Hifadhi makadirio ya AI ya masoko ya mechi ili kupimwa baadaye. Seva hukata marekebisho '
                       'zaidi ya ±8 pts na haikubali mode live baada ya mechi kuanza.',
        'parameters': {'type': 'object', 'properties': dict(_HA, **{
            'mode': G._s('live (kabla ya mechi) au backtest'),
            'items': {'type': 'array', 'description': 'Masoko yote uliyochambua', 'items': {
                'type': 'object', 'properties': {
                    'market': G._s('Jina la soko kama lilivyo kwenye model'),
                    'adj_prob': {'type': 'number', 'description': 'Probability baada ya data za nje (0-100)'},
                    'reason': G._s('Sababu fupi'), 'source': G._s('Jina la tovuti/chanzo')},
                'required': ['market', 'adj_prob']}}}),
            'required': ['home', 'away', 'date', 'mode', 'items']}},
    'evaluate_ai_analyses': G._d(
        'evaluate_ai_analyses', 'Pima kama makadirio ya AI yalileta manufaa dhidi ya model (Brier, kwa live/backtest).',
        {'days': G._n('Siku za nyuma (chaguo-msingi 30)'), 'mode': G._s('live, backtest au tupu = zote')}),
}
TOOLS = {'get_model_probs': _t_model_probs, 'list_matches_for_day': _t_list_day,
         'research_match': _t_research, 'save_ai_analysis': _t_save,
         'evaluate_ai_analyses': _t_eval}
LABELS = {'get_model_probs': '📊 Probability za model', 'list_matches_for_day': '📅 Mechi za siku',
          'research_match': '🔎 Utafiti wa mechi (7 kwa pamoja)', 'save_ai_analysis': '💾 Nahifadhi makadirio',
          'evaluate_ai_analyses': '🧪 Napima makadirio ya AI'}

EXTRA_PROMPT = """

UTAFITI NA KUPIMA MAREKEBISHO YA AI (zana: get_model_probs, research_match, save_ai_analysis, evaluate_ai_analyses, list_matches_for_day):

A) "Chambua mechi X" (LIVE, kabla ya mechi):
1. Katika RAUNDI MOJA piga zana zote pamoja: get_model_probs (top 15), research_match, get_weather (mji wa uwanja) na get_kickoff_time. Usizipige moja moja.
2. Chambua masoko YOTE 10-15 yaliyorudishwa, si Best Pick tu. Yapange kwa makundi (Matokeo, Magoli, Kadi, Corners...). Kwa kila soko: **soko** Model X% → AI Y% (±n) kwa sababu fupi; masoko yenye sababu moja yaunganishwe kwenye mstari mmoja. Soko lisilo na data ya nje: marekebisho 0.
3. Mwisho taja masoko 5 yenye Y% ya juu na kwa nini yanasimama (data + model), kisha onyo la kawaida.
4. Kisha piga save_ai_analysis (mode "live") kwa masoko yote uliyochambua ili tupime baadaye. Ikikataa (mechi imeshaanza), sema hivyo.

B) "Jaribu/backtest mechi za jana, assume hujui matokeo":
1. list_matches_for_day(tarehe), kisha chagua mechi hadi 4 kwa ombi (admin akisema "endelea" chukua zinazofuata).
2. Kwa kila mechi piga pamoja: get_model_probs na research_match yenye before_kickoff_only=true.
3. Tumia TU taarifa za kabla ya mechi kuanza. USIITE get_day_report, get_market_stats wala web_search kuhusu matokeo ya mechi hizo. Ukiona matokeo ndani ya utafiti, yapuuze na useme "uvujaji wa matokeo".
4. save_ai_analysis (mode "backtest") KABLA ya kuzungumzia matokeo yoyote, kisha evaluate_ai_analyses kuonyesha kama marekebisho yalisaidia.
5. Eleza wazi: backtest si kipimo safi (muktadha wa mfumo unaweza kuwa na matokeo ya jana, na utafutaji unaweza kuvuja). Kipimo sahihi ni LIVE.

C) "Pima/tathmini marekebisho ya AI": evaluate_ai_analyses; eleza kwa lugha rahisi, usihukumu sampuli ikiwa ndogo.
Sheria ya kudumu: marekebisho yako baki ndani ya ±6 pts kwa soko (seva inakata zaidi ya ±8).
"""


def register(app, admin_only, app_ns):
    AT = getattr(G, 'AT', None)
    if AT is None or 'UCHAMBUZI WA MECHI KWA DATA ZA NJE' not in (getattr(AT, 'ADMIN_PROMPT', '') or ''):
        print('[gemini_lab] gemini_boost haijawashwa - plugin imejizima.')
        return
    try:
        with app.app_context():
            AiAdjustLog.__table__.create(db.engine, checkfirst=True)
    except Exception as e:
        print(f'[gemini_lab] kutengeneza jedwali kumeshindwa: {e}')
    G.DECLS.update(DECLS)
    G.ADMIN_TOOLS.update(TOOLS)

    orig_label = G._tool_label

    def label(name, a):
        return LABELS.get(name) or orig_label(name, a)
    G._tool_label = label
    AT.ADMIN_PROMPT = AT.ADMIN_PROMPT + EXTRA_PROMPT
    print('[gemini_lab] imewashwa: utafiti sambamba, masoko 15, kupima marekebisho ya AI.')