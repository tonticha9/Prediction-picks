"""
ops_monitor.py: ufuatiliaji wa mfumo mzima + udhibiti wa Best Pick (plugin ya Admin).

- Jedwali ops_event: kila kitu kinachofeli au kufanikiwa (GitHub, Kaggle, Render, API, app) kinarekodiwa.
- POST /api/ops/report  (header X-Refresh-Secret): GitHub inaripoti hali ya workflow.
- GET  /healthz         : mlinzi (watchdog) anaangalia kama app iko hai.
- Makosa 5xx ya app yanarekodiwa yenyewe.
- Health checks za Admin zinaongezewa hali ya mifumo ya nje.
- Admin: /admin/ops (matukio), /admin/bestpick/save (zima soko lisiwe Best Pick).
"""
import hmac
import os
import time
from datetime import datetime, timedelta

from flask import flash, jsonify, redirect, render_template, request
from sqlalchemy import text

from models import db

FLAG_BP = 'bestpick_off'
MAX_FAILS = 8
BLOCK_SECONDS = 900
KEEP_DAYS = 45
LEVEL_MAP = {
    'success': 'ok', 'ok': 'ok',
    'failure': 'error', 'error': 'error', 'timed_out': 'error', 'startup_failure': 'error',
    'cancelled': 'warn', 'action_required': 'warn', 'warn': 'warn',
}
# Mifumo inayotarajiwa kuripoti mara kwa mara (jina lina neno -> masaa)
EXPECT = (('nightly', 30.0), ('retrain', 8 * 24.0), ('watchdog', 1.5))

_NS = {}
_FAILS = {}
_LAST5 = {}
_MEMO = {}


class OpsEvent(db.Model):
    __tablename__ = 'ops_event'
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    source = db.Column(db.String(80), index=True)
    level = db.Column(db.String(10))            # ok / warn / error
    title = db.Column(db.String(200))
    detail = db.Column(db.Text, default='')
    url = db.Column(db.String(300), default='')
    resolved = db.Column(db.Boolean, default=False, index=True)


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------
def _memo(key, ttl, fn):
    now = time.time()
    hit = _MEMO.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _MEMO[key] = (now, val)
    return val


def _safe(fn, default=None):
    try:
        return fn()
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        print(f'[ops_monitor] {type(e).__name__}: {e}')
        return default


def _clean(s, n):
    return str(s or '').strip()[:n]


def _ip():
    xff = request.headers.get('X-Forwarded-For', '')
    return xff.split(',')[0].strip() or request.remote_addr or '?'


def _blocked(ip):
    now = time.time()
    hits = [t for t in _FAILS.get(ip, []) if now - t < BLOCK_SECONDS]
    _FAILS[ip] = hits
    return len(hits) >= MAX_FAILS


def _expect_hours(name):
    low = (name or '').lower()
    for key, hours in EXPECT:
        if key in low:
            return hours
    return None


def _job_a_last():
    try:
        raw = _NS['get_flag']('job_a_last_run', '')
        return datetime.fromisoformat(raw.split('|')[0]) if raw else None
    except Exception:
        return None


def _record(source, level, title, detail='', url=''):
    now = datetime.utcnow()
    if level == 'ok':
        OpsEvent.query.filter(
            OpsEvent.source == source, OpsEvent.resolved.is_(False),
            OpsEvent.level.in_(['error', 'warn'])
        ).update({'resolved': True}, synchronize_session=False)
        OpsEvent.query.filter_by(source=source, level='ok').delete(synchronize_session=False)
    else:
        recent = OpsEvent.query.filter(
            OpsEvent.source == source, OpsEvent.level == level,
            OpsEvent.resolved.is_(False), OpsEvent.title == title,
            OpsEvent.created > now - timedelta(minutes=5)
        ).first()
        if recent is not None:
            recent.detail = detail
            recent.url = url
            recent.created = now
            db.session.commit()
            _MEMO.clear()
            return
    db.session.add(OpsEvent(source=source, level=level, title=title, detail=detail, url=url))
    OpsEvent.query.filter(OpsEvent.created < now - timedelta(days=KEEP_DAYS)).delete(
        synchronize_session=False)
    db.session.commit()
    _MEMO.clear()


def _is_open(e, job_a):
    if e.resolved or e.level not in ('error', 'warn'):
        return False
    # Job A (hourly) ikikimbia vizuri baada ya kosa, kosa linahesabiwa limetatuliwa
    if job_a and 'hourly' in (e.source or '').lower() and job_a > e.created:
        return False
    return True


def _status_rows():
    now = datetime.utcnow()
    job_a = _job_a_last()
    rows = []
    for (s,) in db.session.query(OpsEvent.source).distinct().all():
        evs = OpsEvent.query.filter_by(source=s).order_by(OpsEvent.id.desc()).limit(30).all()
        if not evs:
            continue
        opened = [e for e in evs if _is_open(e, job_a)]
        errs = [e for e in opened if e.level == 'error']
        if errs:
            ev, level, txt = errs[0], 'error', errs[0].title
            if len(errs) > 1:
                txt += f' (mara {len(errs)})'
        elif opened:
            ev, level, txt = opened[0], 'warn', opened[0].title
        else:
            ev, level, txt = evs[0], 'ok', 'Sawa'
            exp = _expect_hours(s)
            if exp:
                age = (now - ev.created).total_seconds() / 3600.0
                if age > exp:
                    level = 'warn'
                    txt = f'Haijaripoti kwa masaa {age:.0f} (inatarajiwa ndani ya masaa {exp:g})'
        rows.append({'name': s, 'level': level, 'text': txt, 'when': ev.created,
                     'url': ev.url or '', 'detail': ev.detail or ''})
    order = {'error': 0, 'warn': 1, 'ok': 2}
    rows.sort(key=lambda r: (order[r['level']], r['name'].lower()))
    return rows


def _open_count():
    return _memo('open', 20, lambda: sum(1 for r in _status_rows() if r['level'] != 'ok'))


def _health_checks():
    out = []
    for r in _status_rows():
        out.append({'name': f"⚙️ {r['name']}", 'status': r['level'], 'detail': r['text']})
    try:
        gf = _NS['get_flag']
        raw = gf('job_a_api_failed_leagues', '')
        if raw != '':
            n = int(raw)
            lst = gf('job_a_api_failed_list', '-')
            if n >= 4:
                out.append({'name': '🌐 AllSportsAPI', 'status': 'error',
                            'detail': f'Ligi {n} zimeshindwa kwenye Job A ({lst}). API inaweza kuwa chini au key imeisha.'})
            elif n > 0:
                out.append({'name': '🌐 AllSportsAPI', 'status': 'warn',
                            'detail': f'Ligi {n} zimeshindwa kwenye Job A ({lst}).'})
            else:
                out.append({'name': '🌐 AllSportsAPI', 'status': 'ok', 'detail': 'Ligi zote zimejibu.'})
            last = gf('job_a_api_last_check', '')
            if last:
                age = (datetime.utcnow() - datetime.fromisoformat(last)).total_seconds() / 3600.0
                if age > 4:
                    out.append({'name': '🌐 AllSportsAPI (ukaguzi)', 'status': 'warn',
                                'detail': f'Ukaguzi wa mwisho wa API ulikuwa masaa {age:.0f} yaliyopita.'})
    except Exception:
        pass
    return out


# ----------------------------------------------------------------
# Best Pick: masoko yaliyozimwa
# ----------------------------------------------------------------
def _bp_off():
    def build():
        try:
            raw = _NS['get_flag'](FLAG_BP, '') or ''
        except Exception:
            return set(), set()
        groups, markets = set(), set()
        for tok in raw.split(','):
            tok = tok.strip()
            if tok.startswith('g:'):
                groups.add(tok[2:])
            elif tok.startswith('m:'):
                markets.add(tok[2:])
        return groups, markets
    return _memo('bp', 10, build)


def _market_of(o):
    try:
        return str(o['market'])
    except Exception:
        return str(getattr(o, 'market', ''))


def _bp_state():
    groups_off, markets_off = _bp_off()
    groups = [{'key': k, 'icon': i, 'label': l, 'allowed': k not in groups_off}
              for k, i, l in (_NS.get('MARKET_GROUPS') or [])]
    options = []
    try:
        df = _NS['get_data']()['predictions']
        options = sorted(str(m) for m in df['market'].dropna().unique() if str(m) not in markets_off)
    except Exception:
        pass
    return {'groups': groups, 'groups_off': sorted(groups_off),
            'markets_off': sorted(markets_off), 'options': options}


# ----------------------------------------------------------------
# Usajili
# ----------------------------------------------------------------
def register(app, admin_only, app_ns):
    _NS.update(app_ns)
    get_flag = app_ns['get_flag']
    set_flag = app_ns['set_flag']
    group_key = app_ns.get('market_group_key') or (lambda m: 'other')

    try:
        with app.app_context():
            db.create_all()
    except Exception as e:
        print(f'[ops_monitor] create_all imeshindwa: {e}')

    pe = app_ns.get('PUBLIC_ENDPOINTS')
    if isinstance(pe, set):
        pe.update({'ops_report', 'ops_health', 'allsports_key_endpoint'})

    # ---- Best Pick: mwisho wa pick_markets ----
    orig_pm = app_ns.get('pick_markets')
    if orig_pm is not None and not getattr(orig_pm, '_ops_patched', False):
        def _off(m):
            groups, markets = _bp_off()
            return m in markets or group_key(m) in groups

        def pick_markets_patched(entries, settings):
            full = orig_pm(entries, settings)
            groups, markets = _bp_off()
            if not full or not (groups or markets):
                return full
            if not _off(_market_of(full[0])):
                return full
            allowed = [e for e in entries if not _off(str(e[0]))]
            alt = orig_pm(allowed, settings) if allowed else []
            if not alt:
                return []          # hakuna soko linaloruhusiwa kuwa Best Pick: mechi inafichwa
            best = alt[0]
            return [best] + [x for x in full if x is not best]

        pick_markets_patched._ops_patched = True
        app_ns['pick_markets'] = pick_markets_patched

    # ---- Health checks: ongeza mifumo ya nje ----
    orig_hc = app_ns.get('run_health_checks')
    if orig_hc is not None and not getattr(orig_hc, '_ops_patched', False):
        def run_health_checks_patched(*a, **k):
            res = orig_hc(*a, **k)
            try:
                checks, summ = res
                extra = _safe(_health_checks, [])
                if not extra:
                    return res
                checks = list(checks) + extra
                summ = dict(summ)
                bad = [c for c in extra if c['status'] != 'ok']
                summ['ok'] = summ.get('ok', 0) + (len(extra) - len(bad))
                if bad:
                    rank = {'ok': 0, 'warn': 1, 'error': 2}
                    worst = 'error' if any(c['status'] == 'error' for c in bad) else 'warn'
                    if rank[worst] > rank.get(summ.get('level', 'ok'), 0):
                        summ['level'] = worst
                        summ['text'] = f'Matatizo {len(bad)} ya mifumo ya nje yanahitaji umakini'
                return checks, summ
            except Exception:
                return res

        run_health_checks_patched._ops_patched = True
        app_ns['run_health_checks'] = run_health_checks_patched

    @app.context_processor
    def _ops_ctx():
        return {
            'ops_open_count': lambda: _safe(_open_count, 0),
            'ops_status': lambda: _safe(_status_rows, []),
            'bp_state': lambda: _safe(_bp_state, None),
        }

    # ---- Kuripoti kutoka GitHub ----
    @app.route('/api/ops/report', methods=['POST'])
    def ops_report():
        ip = _ip()
        if _blocked(ip):
            return jsonify({'error': 'too many attempts'}), 429
        secret = (os.environ.get('REFRESH_SECRET') or '').strip()
        got = (request.headers.get('X-Refresh-Secret') or '').strip()
        if not secret or not got or not hmac.compare_digest(got.encode(), secret.encode()):
            _FAILS.setdefault(ip, []).append(time.time())
            return jsonify({'error': 'forbidden'}), 403
        data = request.get_json(silent=True) or {}
        source = _clean(data.get('source'), 80) or 'haijulikani'
        level = LEVEL_MAP.get(str(data.get('status') or '').lower())
        if level is None:
            return jsonify({'ok': True, 'ignored': True})
        titles = {'ok': 'Imefanikiwa', 'error': 'Imeshindwa', 'warn': 'Imesitishwa au inahitaji umakini'}
        try:
            _record(source, level, titles[level], _clean(data.get('detail'), 800),
                    _clean(data.get('url'), 300))
        except Exception as e:
            db.session.rollback()
            print(f'[ops_monitor] report imeshindwa: {e}')
            return jsonify({'error': 'server error'}), 500
        return jsonify({'ok': True})

    @app.route('/healthz')
    def ops_health():
        try:
            db.session.execute(text('SELECT 1'))
            return jsonify({'ok': True, 'db': True,
                            'time': datetime.utcnow().isoformat(timespec='seconds')})
        except Exception:
            db.session.rollback()
            return jsonify({'ok': False, 'db': False}), 503

    # ---- Makosa ya app yenyewe (5xx) ----
    @app.after_request
    def _ops_capture_5xx(resp):
        try:
            if resp.status_code >= 500 and not request.path.startswith(('/api/ops/', '/healthz')):
                now = time.time()
                if now - _LAST5.get(request.path, 0) > 600:
                    _LAST5[request.path] = now
                    db.session.rollback()
                    _record('Render (app)', 'error', f'Kosa {resp.status_code}: {request.path}',
                            f'{request.method} {request.path}')
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
        return resp

    # ---- Kurasa za Admin ----
    @app.route('/admin/ops')
    @admin_only
    def admin_ops():
        job_a = _job_a_last()
        evs = OpsEvent.query.filter(OpsEvent.level != 'ok').order_by(OpsEvent.id.desc()).limit(120).all()
        open_evs = [e for e in evs if _is_open(e, job_a)]
        old_evs = [e for e in evs if not _is_open(e, job_a)][:30]
        return render_template('admin_ops.html', rows=_safe(_status_rows, []),
                               open_evs=open_evs, old_evs=old_evs, page='admin')

    @app.route('/admin/ops/resolve', methods=['POST'])
    @admin_only
    def admin_ops_resolve():
        eid = request.form.get('id', type=int)
        q = OpsEvent.query.filter(OpsEvent.resolved.is_(False),
                                  OpsEvent.level.in_(['error', 'warn']))
        if eid:
            q = q.filter(OpsEvent.id == eid)
        n = q.update({'resolved': True}, synchronize_session=False)
        db.session.commit()
        _MEMO.clear()
        flash(f'Matukio {n} yamewekwa kama yametatuliwa.', 'success')
        return redirect('/admin/ops')

    @app.route('/admin/ops/test', methods=['POST'])
    @admin_only
    def admin_ops_test():
        _record('Jaribio', 'error', 'Tukio la majaribio', 'Hili ni tukio la majaribio kutoka Admin.')
        flash('Tukio la majaribio limetengenezwa. Liweke kuwa limetatuliwa ukimaliza.', 'success')
        return redirect('/admin/ops')

    @app.route('/admin/bestpick/save', methods=['POST'])
    @admin_only
    def admin_bestpick_save():
        all_groups = [k for k, _i, _l in (app_ns.get('MARKET_GROUPS') or [])]
        allowed = set(request.form.getlist('allow_group'))
        off_groups = [k for k in all_groups if k not in allowed]
        off_markets = {m.strip() for m in request.form.getlist('off_market') if m.strip()}
        add = (request.form.get('add_market') or '').strip()
        if add:
            off_markets.add(add)
        toks = [f'g:{g}' for g in off_groups] + [f'm:{m}' for m in sorted(off_markets)]
        set_flag(FLAG_BP, ','.join(toks))
        _MEMO.clear()
        flash('Mipangilio ya Best Pick imehifadhiwa.' if toks else
              'Masoko yote yanaruhusiwa kuwa Best Pick.', 'success')
        return redirect('/admin#bestpick')