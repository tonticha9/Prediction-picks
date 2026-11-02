"""
admin_api_key.py: plugin ya Admin.
- Ukurasa /admin/api-key : admin anaweka au kubadilisha API key ya AllSportsAPI (inahifadhiwa kwenye DB)
- Endpoint /api/internal/allsports-key : Kaggle (seli A1) inachukua key hapa kwa kutumia header
  X-Kaggle-Token. Token inalinganishwa na variable ya Render KAGGLE_LINK_TOKEN.
- Kinga: majaribio 5 yakikosea kutoka IP moja = kuzuiwa dakika 15.
"""
import hmac
import os
import time

from flask import jsonify, render_template_string, request

FLAG_KEY = 'allsports_api_key'
TOKEN_ENV = 'KAGGLE_LINK_TOKEN'
MAX_FAILS = 5
BLOCK_SECONDS = 900
_FAILS = {}

PAGE = """<!doctype html><html lang="sw"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>API Key ya AllSportsAPI</title>
<style>
body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:16px}
.c{max-width:520px;margin:0 auto;background:#1e293b;border-radius:14px;padding:18px}
input{width:100%;box-sizing:border-box;padding:12px;border-radius:10px;border:1px solid #475569;
background:#0f172a;color:#e2e8f0;font-size:16px}
button{padding:12px 16px;border:0;border-radius:10px;font-size:16px;margin-top:12px;cursor:pointer}
.ok{background:#16a34a;color:#fff}.bad{background:#b91c1c;color:#fff}
.m{padding:10px;border-radius:10px;background:#334155;margin:10px 0;font-size:15px}
a{color:#7dd3fc}
</style></head><body><div class="c">
<h2>🔑 API Key ya AllSportsAPI</h2>
{% if msg %}<div class="m">{{ msg }}</div>{% endif %}
<div class="m">Key kwenye Admin: <b>{{ key_status }}</b></div>
<div class="m">Token ya Kaggle (Render: KAGGLE_LINK_TOKEN): <b>{{ token_status }}</b></div>
<form method="post">
  <input type="hidden" name="action" value="save">
  <label>Key mpya</label>
  <input type="password" name="api_key" autocomplete="off" placeholder="Bandika key hapa">
  <button class="ok" type="submit">Hifadhi key</button>
</form>
<form method="post" onsubmit="return confirm('Futa key kutoka Admin?');">
  <input type="hidden" name="action" value="clear">
  <button class="bad" type="submit">Futa key</button>
</form>
<p><a href="/admin">⬅ Admin</a></p>
</div></body></html>"""


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
    get_flag = app_ns['get_flag']
    set_flag = app_ns['set_flag']

    def _status():
        key = (get_flag(FLAG_KEY, '') or '').strip()
        ks = f'imehifadhiwa (…{key[-4:]}, urefu {len(key)})' if key else 'haipo'
        ts = 'imewekwa ✅' if (os.environ.get(TOKEN_ENV) or '').strip() else 'HAIJAWEKWA ❌'
        return ks, ts

    @app.route('/admin/api-key', methods=['GET', 'POST'])
    @admin_only
    def admin_api_key_page():
        msg = ''
        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'save':
                key = (request.form.get('api_key') or '').strip()
                if len(key) < 20 or ' ' in key:
                    msg = '❌ Key si sahihi (fupi mno au ina nafasi). Haijahifadhiwa.'
                else:
                    set_flag(FLAG_KEY, key)
                    msg = '✅ Key imehifadhiwa. Kaggle itaichukua kwenye run ijayo.'
            elif action == 'clear':
                set_flag(FLAG_KEY, '')
                msg = '🗑 Key imefutwa kutoka Admin. Kaggle itatumia njia ya zamani.'
        ks, ts = _status()
        return render_template_string(PAGE, msg=msg, key_status=ks, token_status=ts)

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
        key = (get_flag(FLAG_KEY, '') or '').strip()
        if not key:
            return jsonify({'error': 'no key saved'}), 404
        resp = jsonify({'key': key})
        resp.headers['Cache-Control'] = 'no-store'
        return resp