"""
league_base_rates.py: plugin (v1, Nov 27 2026) - HATUA F1: base rate ya KILA LIGI kwa kila soko.

Tatizo: base rate ya soko (market_selection.quality_info) ilikuwa MOJA kwa soko lote (wastani wa ligi 12 za zamani).
Kwa hiyo pick ya NOR, MEX, BRA, E2, EC (na hata ya ligi 12) ilipimwa dhidi ya wastani wa jumla, si wa ligi yake.
Mfano: over 2.5 ni ~52% kwenye ligi moja na ~61% kwenye nyingine; 'edge' ilikuwa inasema uongo.

Jinsi inavyofanya kazi:
  - Inasoma 'rate' ya ligi x soko kutoka data/league_gates12.json (ligi 12, kila Jumapili) na 'rates5' ndani ya faili hilo hilo
    (ligi 5 mpya, inawekwa na .github/scripts/merge_leagues.py v4). Hakuna faili jipya la kuhifadhi kwenye GitHub.
  - Pick_markets ya mechi moja inapoitwa, ligi ya mechi inatambuliwa (safu 'league', au jina la timu kupitia league_tools).
  - quality_info(soko, probability) inatumia base rate ya ligi hiyo badala ya ya jumla; edge, informative na best_ok vinahesabiwa upya.
  - Kinga: ikiwa rate ya ligi inatofautiana na ya jumla kwa zaidi ya LEAGUE_BR_MAX_SHIFT (12) pointi, au data ni ya zamani kuliko
    LEAGUE_BR_MAX_AGE_DAYS (35), au ligi/soko halipo, tabia ya zamani (ya jumla) inabaki. Kosa lolote = tabia ya zamani.
  - LEAGUE_BR_OFF=1 (Render env) inazima plugin kabisa.
Ukurasa: /admin/league-rates (ligi x soko: rate ya ligi dhidi ya ya jumla), /admin/league-rates.json
"""
import json
import os
import threading
import time
from datetime import datetime, timedelta
from html import escape

from flask import jsonify

_CTX = threading.local()
_MEMO = {'t': 0.0, 'mtime': None, 'path': None, 'rates': {}, 'meta': {}}
_TMAP = {'t': 0.0, 'm': {}}
_HERE = os.path.dirname(os.path.abspath(__file__))

# soko lenye jozi (rate ya 'pili' = 100 - rate ya 'kwanza')
_PAIRS = {'btts': 'btts_no', 'goals_over_25': 'goals_under_25', 'goals_over_35': 'goals_under_35',
          'goals_over_15': 'goals_under_15'}
_SHOW = ['home_win', 'away_win', 'dc_1x', 'dc_x2', 'dc_12', 'btts', 'goals_over_05', 'goals_over_15',
         'goals_over_25', 'goals_over_35']


def _env_f(name, default):
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return float(default)


def _cfg():
    return {
        'on': os.environ.get('LEAGUE_BR_OFF', '0').strip() != '1',
        'max_shift': _env_f('LEAGUE_BR_MAX_SHIFT', 12.0),
        'max_age': int(_env_f('LEAGUE_BR_MAX_AGE_DAYS', 35)),
        'ttl': int(_env_f('LEAGUE_BR_TTL', 600)),
    }


def _gates_path():
    for p in (os.path.join(_HERE, 'data', 'league_gates12.json'), os.path.join('data', 'league_gates12.json')):
        if os.path.exists(p):
            return p
    return None


def _too_old(last_played, max_age):
    try:
        d = datetime.strptime(str(last_played)[:10], '%Y-%m-%d')
    except Exception:
        return False        # tarehe haijulikani: usiikatae data
    return (datetime.utcnow() - d) > timedelta(days=max_age)


def _extract(block, out):
    """block = {'rate': {liga: {side: sehemu 0-1}}, 'prod_names': {side: soko}} -> out[liga][soko] = asilimia. Rudisha idadi."""
    rate = block.get('rate')
    prod = block.get('prod_names')
    if not isinstance(rate, dict) or not isinstance(prod, dict):
        return 0
    n = 0
    for lg, sides in rate.items():
        if not isinstance(sides, dict):
            continue
        d = out.setdefault(str(lg), {})
        for side, v in sides.items():
            mk = prod.get(side)
            try:
                r = float(v)
            except Exception:
                continue
            if not mk or not (0.02 <= r <= 0.995):
                continue
            d[str(mk)] = round(r * 100.0, 2)
            n += 1
        for a, b in _PAIRS.items():
            if a in d and b not in d:
                d[b] = round(100.0 - d[a], 2)
    return n


def _load():
    path = _gates_path()
    rates, meta = {}, {'path': path, 'g12': 0, 'g5': 0, 'old': [], 'error': None}
    if not path:
        meta['error'] = 'data/league_gates12.json haipo'
        return rates, meta
    max_age = _cfg()['max_age']
    with open(path, encoding='utf-8') as f:
        d = json.load(f)
    blocks = (('g12', d), ('g5', d.get('rates5') if isinstance(d.get('rates5'), dict) else None))
    for name, block in blocks:
        if not block:
            continue
        if _too_old(block.get('last_played'), max_age):
            meta['old'].append(name)
            continue
        meta[name] = _extract(block, rates)
    return rates, meta


def get_rates(force=False):
    """{liga: {soko: base_rate_%}}; haivunji kitu ikishindwa (inarudisha {})."""
    cfg = _cfg()
    path = _gates_path()
    try:
        mt = os.path.getmtime(path) if path else None
    except OSError:
        mt = None
    fresh = (_MEMO['mtime'] == mt and _MEMO['path'] == path and time.time() - _MEMO['t'] <= cfg['ttl'])
    if force or not fresh:
        try:
            _MEMO['rates'], _MEMO['meta'] = _load()
        except Exception as e:
            _MEMO['rates'] = {}
            _MEMO['meta'] = {'path': path, 'g12': 0, 'g5': 0, 'old': [], 'error': f'{type(e).__name__}: {e}'}
            print(f'[league_base_rates] imeshindwa kusoma rates: {_MEMO["meta"]["error"]}')
        _MEMO['t'] = time.time()
        _MEMO['mtime'] = mt
        _MEMO['path'] = path
    return _MEMO['rates']


def _tmap():
    if time.time() - _TMAP['t'] > 600 or not _TMAP['m']:
        try:
            from league_tools import team_league_dict
            _TMAP['m'] = team_league_dict()
        except Exception:
            pass
        _TMAP['t'] = time.time()
    return _TMAP['m']


def _clean(lg):
    if lg is None:
        return None
    s = str(lg).strip()
    return None if s in ('', 'nan', 'None', 'NaN') else s


def _row_league(row):
    """Ligi ya mstari wa mechi: safu 'league', au timu (HomeTeam/AwayTeam au home_team/away_team) kupitia ramani ya ligi."""
    lg = None
    try:
        lg = _clean(row.get('league')) if hasattr(row, 'get') else _clean(getattr(row, 'league', None))
    except Exception:
        lg = None
    if lg:
        return lg
    try:
        from league_tools import norm
        if hasattr(row, 'get'):
            h, a = row.get('HomeTeam'), row.get('AwayTeam')
        else:
            h, a = getattr(row, 'home_team', None), getattr(row, 'away_team', None)
        tm = _tmap()
        return tm.get(norm(h)) or tm.get(norm(a))
    except Exception:
        return None


def _entries_league(entries):
    for e in entries:
        if len(e) > 4:
            lg = _row_league(e[4])
            if lg:
                return lg
    return None


def _make_quality_info(orig):
    def quality_info(market, probability, league=None):
        qi = orig(market, probability)
        try:
            cfg = _cfg()
            if qi is None or not cfg['on']:
                return qi
            lg = _clean(league) or getattr(_CTX, 'league', None)
            if not lg:
                return qi
            r = get_rates().get(str(lg), {}).get(str(market))
            if r is None or abs(r - qi['base_rate']) > cfg['max_shift']:
                return qi
            import market_selection as ms
            edge = float(probability or 0) - r
            out = dict(qi)
            out['base_global'] = qi['base_rate']
            out['base_rate'] = round(r, 1)
            out['base_src'] = 'league'
            out['edge'] = round(edge, 1)
            out['informative'] = (qi['tier'] != 'X') and edge >= qi['min_edge']
            out['best_ok'] = (qi['tier'] in ms.BEST_TIERS) and edge >= qi['min_edge']
            return out
        except Exception:
            return qi
    quality_info._lbr_patched = True
    return quality_info


def _global_rate(ms, market):
    try:
        br = ms._base_rates().get(str(market))
        if br is not None:
            return float(br[0])
    except Exception:
        pass
    try:
        key, is_under = ms._quality_key(market)
        q = ms.QUALITY.get(key)
        if q is not None:
            return (100.0 - q[2]) if is_under else float(q[2])
    except Exception:
        pass
    return None


def install(app, admin_only, app_ns):
    # 1. quality_info yenye ligi (mara moja tu)
    try:
        import market_selection as ms
        if not getattr(ms.quality_info, '_lbr_patched', False):
            ms.quality_info = _make_quality_info(ms.quality_info)
    except Exception as ex:
        print(f'[league_base_rates] quality_info haikubadilishwa: {type(ex).__name__}: {ex}')
        return

    # 2. pick_markets: weka ligi ya mechi kabla ya kuchagua soko
    orig_pm = app_ns.get('pick_markets')
    if orig_pm is not None and not getattr(orig_pm, '_lbr_patched', False):
        def pick_markets_lbr(entries, settings):
            lg = None
            try:
                if _cfg()['on']:
                    lg = _entries_league(entries)
            except Exception:
                lg = None
            prev = getattr(_CTX, 'league', None)
            _CTX.league = lg
            try:
                return orig_pm(entries, settings)
            finally:
                _CTX.league = prev

        pick_markets_lbr._lbr_patched = True
        app_ns['pick_markets'] = pick_markets_lbr

    names = {}
    try:
        from league_tools import LEAGUE_NAMES
        names = dict(LEAGUE_NAMES)
    except Exception:
        pass

    @app.route('/admin/league-rates.json')
    @admin_only
    def admin_league_rates_json():
        rates = get_rates(force=True)
        return jsonify({'on': _cfg()['on'], 'meta': _MEMO['meta'], 'rates': rates})

    @app.route('/admin/league-rates')
    @admin_only
    def admin_league_rates():
        cfg = _cfg()
        rates = get_rates(force=True)
        meta = _MEMO['meta']
        glob = {m: _global_rate(ms, m) for m in _SHOW}
        head = ''.join(f'<th>{escape(m)}</th>' for m in _SHOW)
        grow = ''.join(f"<td>{'' if glob[m] is None else format(glob[m], '.1f')}</td>" for m in _SHOW)
        rows = []
        for lg in sorted(rates):
            cells = []
            for m in _SHOW:
                r = rates[lg].get(m)
                if r is None:
                    cells.append('<td>-</td>')
                    continue
                g = glob[m]
                if g is None:
                    cells.append(f'<td>{r:.1f}</td>')
                    continue
                d = r - g
                color = '#c0392b' if abs(d) > cfg['max_shift'] else ('#d68910' if abs(d) >= 5 else '#1e8449')
                cells.append(f"<td style='color:{color}'>{r:.1f} ({d:+.1f})</td>")
            rows.append(f"<tr><td>{escape(names.get(lg, lg))} ({escape(lg)})</td>{''.join(cells)}</tr>")
        msg = ''
        if not cfg['on']:
            msg += '<p style="color:#c0392b"><b>Plugin imezimwa</b> (LEAGUE_BR_OFF=1). Base rate ya jumla inatumika.</p>'
        if meta.get('error'):
            msg += f"<p style=\"color:#c0392b\">Kosa: {escape(str(meta['error']))}</p>"
        if meta.get('old'):
            msg += (f"<p style=\"color:#d68910\">Data ya zamani kuliko siku {cfg['max_age']} imepuuzwa: "
                    f"{escape(', '.join(meta['old']))}. Endesha 'Refresh League Gates'.</p>")
        html = ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>Base rate kwa ligi</title><style>body{font-family:system-ui,sans-serif;margin:12px;background:#f6f7f9;color:#222}'
                'table{border-collapse:collapse;font-size:12px;background:#fff}th,td{border:1px solid #ddd;padding:4px 6px;text-align:left;'
                'white-space:nowrap}th{background:#eef0f3}.w{overflow-x:auto}</style></head><body>'
                '<p><a href="/admin">&larr; Admin</a></p><h3>Base rate ya kila ligi x soko (%)</h3>'
                f"<p>Ligi zenye data: {len(rates)} (ligi 12: soko {meta.get('g12', 0)}, ligi 5 mpya: soko {meta.get('g5', 0)}). "
                'Namba ya kwanza ni rate ya ligi; kwenye mabano ni tofauti dhidi ya wastani wa jumla (mstari wa kwanza). '
                f"Tofauti kubwa kuliko {cfg['max_shift']:.0f} inapuuzwa (inarudi kwa ya jumla).</p>{msg}"
                f'<div class="w"><table><tr><th>Ligi</th>{head}</tr><tr><td><b>Wastani wa jumla</b></td>{grow}</tr>'
                f"{''.join(rows) or '<tr><td colspan=11>Hakuna data.</td></tr>'}</table></div></body></html>")
        return html