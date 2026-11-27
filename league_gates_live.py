"""
league_gates_live.py: plugin (v2, Nov 27 2026) - HATUA E + F3: kizingiti cha LIVE kwa kila ligi x soko, na kinga ya LIGI NZIMA kwa ligi 5 mpya.

Tatizo: vizingiti vya Kaggle (gates12.json / new_leagues_gates.json) vinaamua kutokana na majaribio ya zamani. Soko la ligi fulani
linaweza kuanza kufanya vibaya LIVE (matokeo halisi ya app) bila mtu kugundua. Plugin hii inalizima lenyewe na kulirudisha likipona.

Jinsi inavyofanya kazi (kila dakika 30, kutoka prediction_record):
  - Inachukua picks zilizosettle (WON/LOST) za siku LEAGUE_LIVE_DAYS (chaguo-msingi 60), sehemu 'prediction', na kuzipanga kwa (ligi, soko).
    Ligi inapatikana kwa jina la timu kupitia league_tools (ramani ya ligi 12 + 5 mpya).
  - Kwa kila (ligi, soko) lenye picks >= LEAGUE_LIVE_MIN_N (30): winrate halisi dhidi ya wastani wa probability iliyotabiriwa.
  - Soko linazimwa ikiwa winrate iko CHINI ya probability kwa >= LEAGUE_LIVE_MIN_GAP (0.08) NA pengo hilo ni kubwa kuliko
    LEAGUE_LIVE_Z (1.64) x kosa la takwimu (si bahati tu). Likipona (rolling 60 days), linarudi lenyewe.
  - Soko lililozimwa linatolewa kwenye pick_markets (Best Pick na Other Picks) kwa ligi hiyo tu; Best Pick inashuka kwa soko linalofuata.
  - Kinga: ikiwa zaidi ya LEAGUE_LIVE_CAP (35%) ya (ligi, soko) zote zinazopimika zingezimwa, plugin inajizuia (huenda tatizo
    ni la data, si la masoko) na hakuna kinachofichwa; ukurasa wa admin unaonyesha hali hiyo.
  - LEAGUE_LIVE_OFF=0 (Render env) inazima plugin kabisa.

v2 (HATUA F3) - KINGA YA LIGI NZIMA (pooled): ligi 5 mpya (NOR, MEX, BRA, E2, EC) hazina picks 30 kwa kila soko kwa wiki nyingi, kwa hiyo
kizingiti cha soko hakiwezi kuzipima mapema. Hapa picks za MASOKO YOTE ya ligi moja zinajumlishwa:
  - ligi ikiwa na picks >= LEAGUE_POOL_MIN_N (30) zilizosettle (siku LEAGUE_LIVE_DAYS), winrate halisi dhidi ya wastani wa probability iliyotabiriwa;
  - ligi inafichwa YOTE (picks zake zote hazionyeshwi) ikiwa winrate iko chini kwa >= LEAGUE_POOL_MIN_GAP (0.05) NA pengo hilo ni kubwa kuliko
    LEAGUE_POOL_Z (2.0) x kosa la takwimu. Kosa linahesabiwa kwa jumla ya p(1-p) na kuzidishwa na LEAGUE_POOL_DEFF (1.5) kwa sababu picks za mechi
    moja zinahusiana (mf. home_win na dc_1x), kwa hiyo bahati mbaya haizimi ligi;
  - ligi ikipona (rolling LEAGUE_LIVE_DAYS), inarudi yenyewe;
  - kinga: ikiwa ligi 3 au zaidi zinapimika na zaidi ya LEAGUE_POOL_CAP (60%) zingefichwa, hakuna kinachofichwa (huenda tatizo ni la data/settle);
  - ligi zinazolindwa: LEAGUE_POOL_LEAGUES (chaguo-msingi NOR,MEX,BRA,E2,EC; 'ALL' = zote); LEAGUE_POOL_ENABLED=0 inazima sehemu hii tu.
Ukurasa: /admin/league-gates (jedwali la ligi x soko, na lipi limezimwa), /admin/league-gates.json
"""
import math
import os
import time
from datetime import datetime, timedelta
from html import escape

from flask import jsonify

_MEMO = {'t': 0.0, 'data': None}


def _env_f(name, default):
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return float(default)


def _cfg():
    return {
        'on': os.environ.get('LEAGUE_LIVE_OFF', '1').strip() != '0',
        'days': int(_env_f('LEAGUE_LIVE_DAYS', 60)),
        'min_n': int(_env_f('LEAGUE_LIVE_MIN_N', 30)),
        'min_gap': _env_f('LEAGUE_LIVE_MIN_GAP', 0.08),
        'z': _env_f('LEAGUE_LIVE_Z', 1.64),
        'cap': _env_f('LEAGUE_LIVE_CAP', 0.35),
        'ttl': int(_env_f('LEAGUE_LIVE_TTL', 1800)),
        'pool_on': os.environ.get('LEAGUE_POOL_ENABLED', '1').strip() != '0',
        'pool_leagues': os.environ.get('LEAGUE_POOL_LEAGUES', 'NOR,MEX,BRA,E2,EC').strip(),
        'pool_min_n': int(_env_f('LEAGUE_POOL_MIN_N', 30)),
        'pool_min_gap': _env_f('LEAGUE_POOL_MIN_GAP', 0.05),
        'pool_z': _env_f('LEAGUE_POOL_Z', 2.0),
        'pool_deff': max(1.0, _env_f('LEAGUE_POOL_DEFF', 1.5)),
        'pool_cap': _env_f('LEAGUE_POOL_CAP', 0.60),
    }


def _empty():
    return {'off': {}, 'stats': [], 'tripped': False, 'measured': 0, 'at': time.time(), 'error': None,
            'league_off': {}, 'league_stats': [], 'league_tripped': False}


def compute(ns):
    """Hesabu hali ya kila (ligi, soko). Haivunji kitu ikishindwa (inarudisha hali tupu)."""
    cfg = _cfg()
    res = _empty()
    try:
        from league_tools import team_league_dict, norm
        db = ns['db']
        PR = ns['PredictionRecord']
        cutoff = datetime.utcnow() - timedelta(days=cfg['days'])
        rows = PR.query.filter(PR.match_date >= cutoff, PR.result.in_(('WON', 'LOST'))).all()
        tmap = team_league_dict()
        agg = {}
        pool = {}
        for r in rows:
            if (getattr(r, 'section', 'prediction') or 'prediction') != 'prediction':
                continue
            if r.probability is None:
                continue
            lg = tmap.get(norm(r.home_team)) or tmap.get(norm(r.away_team))
            if not lg:
                continue
            a = agg.setdefault((lg, r.market), [0, 0, 0.0])
            a[0] += 1
            a[1] += 1 if r.result == 'WON' else 0
            a[2] += float(r.probability) / 100.0
            pp = min(max(float(r.probability) / 100.0, 0.01), 0.99)
            pl = pool.setdefault(lg, [0, 0, 0.0, 0.0])
            pl[0] += 1
            pl[1] += 1 if r.result == 'WON' else 0
            pl[2] += pp
            pl[3] += pp * (1 - pp)
        stats = []
        for (lg, mk), (n, w, sp) in agg.items():
            p = sp / n
            wr = w / n
            gap = p - wr
            sd = math.sqrt(max(p * (1 - p), 1e-9) / n)
            measurable = n >= cfg['min_n']
            bad = measurable and gap >= cfg['min_gap'] and gap >= cfg['z'] * sd
            stats.append({'league': lg, 'market': mk, 'n': n, 'wins': w, 'winrate': wr, 'avg_p': p,
                          'gap': gap, 'measurable': measurable, 'off': bool(bad)})
        measured = [s for s in stats if s['measurable']]
        off = [s for s in stats if s['off']]
        res['measured'] = len(measured)
        if len(measured) >= 10 and len(off) > cfg['cap'] * len(measured):
            res['tripped'] = True
            for s in stats:
                s['off_blocked'] = s['off']
                s['off'] = False
            off = []
        res['stats'] = sorted(stats, key=lambda s: (-int(s.get('off_blocked', s['off'])), -s['gap']))
        res['off'] = {(s['league'], s['market']): s for s in off}
        # ---- v2 (F3): kinga ya ligi nzima ----
        scope = cfg['pool_leagues']
        lstats = []
        for lg, (n, w, sp, sv) in pool.items():
            guarded = cfg['pool_on'] and (scope.upper() == 'ALL' or lg in {x.strip() for x in scope.split(',') if x.strip()})
            p = sp / n
            wr = w / n
            gap = p - wr
            sd = math.sqrt(max(sv, 1e-9) * cfg['pool_deff']) / n
            measurable = n >= cfg['pool_min_n']
            bad = bool(guarded and measurable and gap >= cfg['pool_min_gap'] and gap >= cfg['pool_z'] * sd)
            lstats.append({'league': lg, 'n': n, 'wins': w, 'winrate': wr, 'avg_p': p, 'gap': gap, 'sd': sd,
                           'measurable': measurable, 'guarded': guarded, 'off': bad})
        scoped = [s for s in lstats if s['guarded'] and s['measurable']]
        loff = [s for s in scoped if s['off']]
        if len(scoped) >= 3 and len(loff) > cfg['pool_cap'] * len(scoped):
            res['league_tripped'] = True
            for s in lstats:
                s['off_blocked'] = s['off']
                s['off'] = False
            loff = []
        res['league_stats'] = sorted(lstats, key=lambda s: (-int(s.get('off_blocked', s['off'])), -s['gap']))
        res['league_off'] = {s['league']: s for s in loff}
    except Exception as e:
        try:
            ns['db'].session.rollback()
        except Exception:
            pass
        res['error'] = f'{type(e).__name__}: {e}'
        print(f'[league_gates_live] imeshindwa kuhesabu: {res["error"]}')
    return res


def get_state(ns, force=False):
    cfg = _cfg()
    if force or _MEMO['data'] is None or time.time() - _MEMO['t'] > cfg['ttl']:
        _MEMO['data'] = compute(ns)
        _MEMO['t'] = time.time()
    return _MEMO['data']


def _row_league(row, tmap_fn):
    try:
        lg = row.get('league') if hasattr(row, 'get') else None
    except Exception:
        lg = None
    if lg is not None and str(lg).strip() not in ('', 'nan', 'None'):
        return str(lg).strip()
    try:
        from league_tools import norm
        tmap = tmap_fn()
        return tmap.get(norm(row.get('HomeTeam'))) or tmap.get(norm(row.get('AwayTeam')))
    except Exception:
        return None


def register(app, admin_only, app_ns):
    orig_pm = app_ns.get('pick_markets')
    _tmap_cache = {'t': 0.0, 'm': {}}

    def tmap_fn():
        if time.time() - _tmap_cache['t'] > 600 or not _tmap_cache['m']:
            try:
                from league_tools import team_league_dict
                _tmap_cache['m'] = team_league_dict()
            except Exception:
                pass
            _tmap_cache['t'] = time.time()
        return _tmap_cache['m']

    if orig_pm is not None and not getattr(orig_pm, '_lgl_patched', False):
        def pick_markets_patched(entries, settings):
            try:
                if not _cfg()['on']:
                    return orig_pm(entries, settings)
                st = get_state(app_ns)
                off = st['off']
                loff = st.get('league_off') or {}
                if not off and not loff:
                    return orig_pm(entries, settings)
                kept = []
                for e in entries:
                    lg = _row_league(e[4], tmap_fn) if len(e) > 4 else None
                    if lg and (lg in loff or (lg, str(e[0])) in off):
                        continue
                    kept.append(e)
                if not kept:
                    return []
                return orig_pm(kept, settings)
            except Exception as ex:
                print(f'[league_gates_live] pick_markets imerudi kwa tabia ya kawaida: {type(ex).__name__}: {ex}')
                return orig_pm(entries, settings)

        pick_markets_patched._lgl_patched = True
        app_ns['pick_markets'] = pick_markets_patched

    names = {}
    try:
        from league_tools import LEAGUE_NAMES
        names = dict(LEAGUE_NAMES)
    except Exception:
        pass

    @app.route('/admin/league-gates.json')
    @admin_only
    def admin_league_gates_json():
        st = get_state(app_ns, force=True)
        return jsonify({'tripped': st['tripped'], 'measured': st['measured'], 'error': st['error'],
                        'off': [f'{k[0]}:{k[1]}' for k in st['off']], 'stats': st['stats'],
                        'league_tripped': st.get('league_tripped', False),
                        'league_off': sorted((st.get('league_off') or {}).keys()),
                        'league_stats': st.get('league_stats', [])})

    @app.route('/admin/league-gates')
    @admin_only
    def admin_league_gates():
        cfg = _cfg()
        st = get_state(app_ns, force=True)
        rows = []
        for s in st['stats']:
            if not s['measurable']:
                continue
            blocked = s.get('off_blocked')
            state = 'ZIMWA' if s['off'] else ('ingezimwa (kinga)' if blocked else 'sawa')
            color = '#c0392b' if s['off'] else ('#d68910' if blocked else '#1e8449')
            rows.append(
                f"<tr><td>{escape(names.get(s['league'], s['league']))} ({escape(s['league'])})</td><td>{escape(s['market'])}</td>"
                f"<td>{s['n']}</td><td>{s['winrate'] * 100:.1f}%</td><td>{s['avg_p'] * 100:.1f}%</td>"
                f"<td>{s['gap'] * 100:+.1f}</td><td style='color:{color};font-weight:600'>{state}</td></tr>")
        unmeasured = len([s for s in st['stats'] if not s['measurable']])
        lrows = []
        for s in st.get('league_stats', []):
            if not s['guarded']:
                continue
            blocked = s.get('off_blocked')
            if not s['measurable']:
                state, color = 'bado picks chache (%d/%d)' % (s['n'], cfg['pool_min_n']), '#7f8c8d'
            elif s['off']:
                state, color = 'IMEFICHWA', '#c0392b'
            elif blocked:
                state, color = 'ingefichwa (kinga)', '#d68910'
            else:
                state, color = 'sawa', '#1e8449'
            lrows.append('<tr><td>' + escape(names.get(s['league'], s['league'])) + ' (' + escape(s['league']) + ')</td>'
                         '<td>%d</td><td>%.1f%%</td><td>%.1f%%</td><td>%+.1f</td>' % (s['n'], s['winrate'] * 100, s['avg_p'] * 100, s['gap'] * 100)
                         + "<td style='color:" + color + ";font-weight:600'>" + state + '</td></tr>')
        league_html = ('<h3>Kinga ya ligi nzima (ligi 5 mpya)</h3><p>Picks za masoko yote ya ligi moja zinajumlishwa. Ligi inafichwa yote ikiwa na picks &ge; '
                       + str(cfg['pool_min_n']) + ' zilizosettle na winrate iko chini ya probability kwa &ge; ' + format(cfg['pool_min_gap'] * 100, '.0f')
                       + ' pointi (na si bahati tu). Ikipona, inarudi yenyewe.</p>'
                       + ('' if cfg['pool_on'] else '<p style="color:#c0392b"><b>Sehemu hii imezimwa</b> (LEAGUE_POOL_ENABLED=0).</p>')
                       + ('<p style="color:#d68910"><b>Kinga imewaka:</b> ligi nyingi sana zingefichwa kwa pamoja, kwa hiyo hakuna kinachofichwa. '
                          'Angalia kama kuna tatizo la data/matokeo.</p>' if st.get('league_tripped') else '')
                       + '<div class="w"><table><tr><th>Ligi</th><th>Picks</th><th>Winrate</th><th>Probability ya wastani</th><th>Pengo (pointi)</th><th>Hali</th></tr>'
                       + (''.join(lrows) or '<tr><td colspan=6>Bado hakuna ligi inayolindwa yenye picks zilizosettle.</td></tr>') + '</table></div>')
        msg = ''
        if not cfg['on']:
            msg = '<p style="color:#c0392b"><b>Plugin imezimwa</b> (LEAGUE_LIVE_OFF=0). Hakuna soko linalofichwa.</p>'
        if st['tripped']:
            msg += ('<p style="color:#d68910"><b>Kinga imewaka:</b> masoko mengi sana yangezimwa kwa pamoja, kwa hiyo hakuna kinachofichwa. '
                    'Angalia kama kuna tatizo la data/matokeo.</p>')
        if st['error']:
            msg += f'<p style="color:#c0392b">Kosa: {escape(st["error"])}</p>'
        html = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vizingiti vya LIVE - ligi x soko</title>
<style>body{{font-family:system-ui,sans-serif;margin:12px;background:#f6f7f9;color:#222}}table{{border-collapse:collapse;width:100%;font-size:13px;background:#fff}}
th,td{{border:1px solid #ddd;padding:5px 7px;text-align:left}}th{{background:#eef0f3}}.w{{overflow-x:auto}}</style></head><body>
<p><a href="/admin">&larr; Admin</a></p>
<h3>Vizingiti vya LIVE: ligi x soko</h3>
<p>Soko linazimwa lenyewe kwa ligi husika likiwa na picks &ge; {cfg['min_n']} za siku {cfg['days']} zilizosettle na winrate iko chini ya probability
kwa &ge; {cfg['min_gap'] * 100:.0f} pointi (na si bahati tu). Likipona, linarudi lenyewe. Linaonyeshwa hapa: {len(rows)} lililopimika, {len(st['off'])} lililozimwa,
{unmeasured} bado hazina picks za kutosha.</p>{msg}
<div class="w"><table><tr><th>Ligi</th><th>Soko</th><th>Picks</th><th>Winrate</th><th>Probability ya wastani</th><th>Pengo (pointi)</th><th>Hali</th></tr>
{''.join(rows) or '<tr><td colspan=7>Bado hakuna soko lenye picks za kutosha.</td></tr>'}</table></div>{league_html}</body></html>"""
        return html