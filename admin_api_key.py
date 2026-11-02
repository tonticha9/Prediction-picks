"""
admin_api_key.py: plugin (v3).
Endpoint /api/internal/allsports-key inamruhusu Kaggle (seli A1) kuchukua AllSportsAPI key
ileile inayotumiwa na Job A: key iliyo kwenye kitufe cha "Sasisha API Key" cha Admin (ApiConfig).
Hakuna sehemu ya pili ya key.
Ulinzi: header X-Kaggle-Token lazima ilingane na Render env KAGGLE_LINK_TOKEN;
majaribio 5 yakikosea kutoka IP moja = kuzuiwa dakika 15.
"""
import hmac
import os
import time

from flask import jsonify, request

from models import db, ApiConfig

TOKEN_ENV = 'KAGGLE_LINK_TOKEN'
MAX_FAILS = 5
BLOCK_SECONDS = 900
_FAILS = {}


def _ip():
    xff = request.headers.get('X-Forwarded-For', '')
    return xff.split(',')[0].strip() or request.remote_addr or '?'


def _blocked(ip):
    now = time.time()
    hits = [t for t in _FAILS.get(ip, []) if now - t < BLOCK_SECONDS]
    _FAILS[ip] = hits
    return len(hits) >= MAX_FAILS


def _fail(ip):
    _FAILS.setdefault(ip, []).append(time.time())


def register(app, admin_only, app_ns):
    @app.route('/api/internal/allsports-key')
    def allsports_key_endpoint():
        ip = _ip()
        if _blocked(ip):
            return jsonify({'error': 'too many attempts'}), 429
        expected = (os.environ.get(TOKEN_ENV) or '').strip()
        got = (request.headers.get('X-Kaggle-Token') or '').strip()
        if not expected or not got or not hmac.compare_digest(got.encode(), expected.encode()):
            _fail(ip)
            return jsonify({'error': 'forbidden'}), 403
        try:
            cfg = ApiConfig.query.first()
            key = (getattr(cfg, 'api_key', '') or '').strip() if cfg else ''
        except Exception as e:
            try:
                db.session.rollback()
            except Exception:
                pass
            print(f'[admin_api_key] imeshindwa kusoma ApiConfig: {type(e).__name__}: {e}')
            return jsonify({'error': 'server error'}), 500
        if not key:
            return jsonify({'error': 'no key saved'}), 404
        resp = jsonify({'key': key})
        resp.headers['Cache-Control'] = 'no-store'
        return resp