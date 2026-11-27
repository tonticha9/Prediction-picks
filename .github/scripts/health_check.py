"""
.github/scripts/health_check.py  -  v1 (Nov 27 2026)  -  HATUA F5

Ukaguzi wa kila siku wa mambo ambayo yangeweza kufa KIMYA KIMYA:
  1. FUNGUO YA AllSportsAPI: inaitwa kweli (met=Leagues). Ikikataliwa au imeisha muda, run inakuwa NYEKUNDU na GitHub inakutumia barua pepe.
     (Haiwezi kukuonya KABLA haijaisha, kwa sababu API haitoi tarehe ya mwisho; inakamata ndani ya saa 24 baada ya kuisha.)
  2. MECHI ZA LIGI 5 MPYA: fixtures za siku 14 zijazo kwa NOR, MEX, BRA, E2, EC. Ikiwa hakuna hata moja kwa ligi zote, onyo (huenda msimu umeisha).
  3. UMRI WA DATA: data/league_gates12.json (gates za ligi 12, last_played) na 'rates5' (ligi 5) vimesasishwa ndani ya siku HEALTH_MAX_AGE_DAYS (10).
     Tarehe hiyo ni ya mechi ya mwisho iliyoingia (si siku ya run), kwa hiyo lab ikikwama tunajua ndani ya siku HEALTH_MAX_AGE_DAYS.
Inaripoti kwa app (/api/ops/report, chanzo 'health_check'), kwa hiyo /admin/ops inaonyesha hali. Ikishindwa kuripoti, haivunji kitu.
Env: APP_URL, REFRESH_SECRET (hiari), ALLSPORTSAPI_KEY (fallback ya funguo ya Admin), HEALTH_MAX_AGE_DAYS.
Toka: 1 ikiwa kuna tatizo la kweli (funguo, data ya zamani); 0 ikiwa sawa au onyo tu.
"""
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta

import requests

BASE_URL = "https://apiv2.allsportsapi.com/football/"
NEW5 = {'NOR': 253, 'MEX': 235, 'BRA': 99, 'E2': 154, 'EC': 151}
MAX_AGE_DAYS = int(os.environ.get('HEALTH_MAX_AGE_DAYS', '10'))
GATES_FILE = os.path.join('data', 'league_gates12.json')
RETRIES = 3
BACKOFF = (3, 8)


def redact(text, key=None):
    s = str(text)
    if key:
        s = s.replace(key, '***')
    return re.sub(r'(APIkey=)[^&\s)]+', r'\1***', s)


def get_api_key():
    app_url, secret = os.environ.get('APP_URL'), os.environ.get('REFRESH_SECRET')
    if app_url and secret:
        try:
            r = requests.get(f"{app_url}/api/config/allsportsapi-key", headers={"X-Refresh-Secret": secret}, timeout=20)
            r.raise_for_status()
            k = r.json().get('api_key')
            if k:
                return k, 'Admin'
        except Exception as e:
            print(f"⚠️ funguo kutoka Admin imeshindwa ({redact(e)}) - natumia GitHub Secret.")
    k = os.environ.get('ALLSPORTSAPI_KEY')
    return (k, 'GitHub Secret') if k else (None, None)


def api_call(params, key):
    """Rudisha (kind, data, msg). kind: 'ok' | 'denied' (API ilijibu lakini ikakataa) | 'down' (mtandao/5xx/jibu lisilo JSON)."""
    last = 'haijulikani'
    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.get(BASE_URL, params=dict(params, APIkey=key), timeout=40)
        except requests.RequestException as e:
            last = redact(e, key)
        else:
            if resp.status_code in (401, 403):
                return 'denied', None, f"HTTP {resp.status_code} {resp.reason}"
            if resp.status_code >= 500 or resp.status_code == 429:
                last = f"HTTP {resp.status_code} {resp.reason}"
            elif resp.status_code >= 400:
                return 'denied', None, f"HTTP {resp.status_code} {resp.reason}"
            else:
                try:
                    data = resp.json()
                except ValueError:
                    last = 'jibu si JSON'
                else:
                    ok = data.get('success') in (1, '1', True)
                    if ok:
                        return 'ok', data, ''
                    msg = redact(json.dumps(data.get('error') or data.get('result') or data, ensure_ascii=False), key)[:200]
                    return 'denied', data, msg
        if attempt < RETRIES:
            time.sleep(BACKOFF[min(attempt - 1, len(BACKOFF) - 1)])
    return 'down', None, last


def check_key(key, problems):
    kind, data, msg = api_call({'met': 'Leagues'}, key)
    if kind == 'ok':
        n = len(data.get('result') or [])
        if n > 0:
            print(f"✅ funguo ya AllSportsAPI inafanya kazi (ligi {n} zimerudi).")
            return True
        problems.append(('error', "funguo ya AllSportsAPI: API ilijibu lakini orodha ya ligi ni tupu (huenda funguo imeisha au mpango umebadilika)."))
        return False
    if kind == 'denied':
        problems.append(('error', f"funguo ya AllSportsAPI IMEKATALIWA au imeisha muda: {msg}. Tengeneza/kuongeza funguo mpya na uibadilishe Admin + GitHub Secret ALLSPORTSAPI_KEY + dataset ya Kaggle (admin-link)."))
        return False
    problems.append(('warn', f"AllSportsAPI haipatikani sasa ({msg}); funguo haikuweza kuthibitishwa leo (huenda ni hitilafu ya muda)."))
    return None


def check_fixtures(key, problems):
    today = date.today()
    d_from, d_to = today.isoformat(), (today + timedelta(days=14)).isoformat()
    total, per, errs = 0, {}, 0
    for lg, lk in NEW5.items():
        kind, data, msg = api_call({'met': 'Fixtures', 'leagueId': lk, 'from': d_from, 'to': d_to}, key)
        if kind == 'ok':
            n = len(data.get('result') or [])
        elif kind == 'denied' and isinstance(data, dict) and data.get('success') in (0, '0') and not (data.get('error')):
            n = 0      # API huweza kujibu success=0 bila makosa wakati hakuna mechi
        else:
            errs += 1
            continue
        per[lg] = n
        total += n
    print(f"   fixtures za siku 14 (ligi 5): {per or 'hakuna majibu'}")
    if errs == len(NEW5):
        return      # tatizo la API limesharipotiwa na check_key
    if total == 0:
        problems.append(('warn', "ligi 5 mpya: hakuna fixture hata moja kwa siku 14 zijazo (huenda msimu umeisha, au API haitoi mechi)."))


def _age_days(v):
    try:
        return (date.today() - datetime.strptime(str(v)[:10], '%Y-%m-%d').date()).days
    except Exception:
        return None


def check_data_age(problems):
    if not os.path.exists(GATES_FILE):
        problems.append(('error', f"{GATES_FILE} haipo kwenye repo."))
        return
    try:
        with open(GATES_FILE, encoding='utf-8') as f:
            g = json.load(f)
    except Exception as e:
        problems.append(('error', f"{GATES_FILE} haisomeki ({type(e).__name__}: {e})."))
        return
    a12 = _age_days(g.get('last_played'))
    r5 = g.get('rates5') if isinstance(g.get('rates5'), dict) else None
    a5 = _age_days(r5.get('last_played')) if r5 else None
    print(f"   umri wa gates ligi 12: {a12} siku | ligi 5: {a5 if r5 else 'hakuna'} siku")
    if a12 is None:
        problems.append(('warn', "gates za ligi 12: tarehe ya last_played haijulikani."))
    elif a12 > MAX_AGE_DAYS:
        problems.append(('error', f"gates za ligi 12 ni za siku {a12} (kikomo {MAX_AGE_DAYS}): 'Refresh League Gates' ya Jumapili au nightly imeshindwa. Angalia Actions."))
    if r5 is None:
        problems.append(('warn', "rates za ligi 5 bado hazijahifadhiwa (merge_leagues v4+ itazihifadhi nightly ijayo)."))
    elif a5 is None:
        problems.append(('warn', "rates za ligi 5: tarehe ya last_played haijulikani."))
    elif a5 > MAX_AGE_DAYS:
        problems.append(('error', f"data ya ligi 5 ni ya siku {a5} (kikomo {MAX_AGE_DAYS}): lab ya ligi 5 haijasasishwa. Angalia 'Refresh League Gates'."))


def report(status, detail):
    app_url, secret = os.environ.get('APP_URL'), os.environ.get('REFRESH_SECRET')
    if not app_url or not secret:
        print("ℹ️ APP_URL/REFRESH_SECRET hazipo - ripoti kwa app imerukwa.")
        return
    body = {'source': 'health_check', 'status': status, 'detail': detail[:780],
            'url': f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', '')}/actions" if os.environ.get('GITHUB_REPOSITORY') else ''}
    for i in range(1, 4):
        try:
            r = requests.post(f"{app_url}/api/ops/report", json=body, headers={"X-Refresh-Secret": secret}, timeout=60)
            print(f"   ripoti kwa app: HTTP {r.status_code}")
            if r.status_code == 200:
                return
        except requests.RequestException as e:
            print(f"   ripoti kwa app imeshindwa ({redact(e)})")
        time.sleep(15)
    print("::warning::Imeshindwa kuripoti kwa app (huenda app imezimika).")


def main():
    problems = []
    key, src = get_api_key()
    if not key:
        problems.append(('error', "funguo ya AllSportsAPI haipatikani (Admin wala GitHub Secret)."))
    else:
        print(f"funguo inatoka: {src}")
        if check_key(key, problems) is not False:
            check_fixtures(key, problems)
    check_data_age(problems)
    errors = [m for lvl, m in problems if lvl == 'error']
    warns = [m for lvl, m in problems if lvl == 'warn']
    for m in errors:
        print(f"::error::{m}")
    for m in warns:
        print(f"::warning::{m}")
    if errors:
        status, detail = 'failure', ' | '.join(errors + warns)
    elif warns:
        status, detail = 'warn', ' | '.join(warns)
    else:
        status, detail = 'success', 'Funguo ya AllSportsAPI inafanya kazi, fixtures za ligi 5 zipo, data ya gates ni mpya.'
    print(f"HALI: {status} - {detail}")
    report(status, detail)
    return 1 if errors else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:      # script yenyewe isivunje chochote
        print(f"::warning::health_check imeshindwa ({type(e).__name__}: {e})")
        sys.exit(0)