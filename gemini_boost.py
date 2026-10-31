"""
gemini_boost.py — Plugin ya admin_tools.py (weka BAADA ya gemini_ai kwenye PLUGINS).

Inaboresha Gemini bila kuhariri gemini_ai.py:
  1. Kasi: zana zinaendeshwa kwa pamoja (parallel), utafutaji unahifadhiwa dakika 15.
  2. Kutokatika: kufikiri kunadhibitiwa (thinking budget), tokeni 8192, na jibu likikatika
     (MAX_TOKENS au mtandao) Gemini anaombwa aendelee pale alipoishia (hadi mara 2).
  3. Data za nje: zana mpya get_weather (Open-Meteo) na get_kickoff_time, utafutaji wenye
     vyanzo vya kuaminika, na itifaki ya uchambuzi wa mechi (marekebisho yenye kikomo).
Ikikosa GEMINI_API_KEY (injini ya Groq inatumika), plugin hii inajizima yenyewe.
"""
import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import quote

import gemini_ai as G

CONTINUE_MSG = 'Endelea pale ulipoishia, bila kurudia ulichokwisha andika.'
SEARCH_TTL = 900          # sekunde 15
_SEARCH_CACHE = {}
_LEVEL_BUDGET = {'minimal': 0, 'low': 512, 'medium': 2048, 'high': 4096}


# ---------------------------------------------------------------
# 1) Mipangilio ya jibu: thinking iliyodhibitiwa + tokeni nyingi
# ---------------------------------------------------------------
def _gen_config(model, max_tokens, mode):
    cfg = {'maxOutputTokens': 8192 if mode == 'admin' else 4096}
    lvl = (os.environ.get('GEMINI_THINKING_LEVEL') or 'low').strip().lower()
    if lvl not in _LEVEL_BUDGET:
        lvl = 'low'
    m = model.lower()
    if model in G._NO_THINK:
        return cfg
    if m.startswith('gemini-3'):
        cfg['thinkingConfig'] = {'thinkingLevel': lvl}
    elif m.startswith('gemini-2.5'):
        b = _LEVEL_BUDGET[lvl]
        if 'pro' in m:
            b = max(128, b)
        cfg['thinkingConfig'] = {'thinkingBudget': b}
    return cfg


# ---------------------------------------------------------------
# 2) Utafutaji wa mtandaoni: vyanzo vya kuaminika + cache
# ---------------------------------------------------------------
_SEARCH_SYS = (
    'Wewe ni mtafiti wa mpira wa miguu. Tafuta habari za SASA na jibu kwa Kiswahili (si zaidi ya '
    'maneno 260). Kwa kila taarifa andika: jina la mchezaji/kitu, hali yake, jina la tovuti, na '
    'tarehe ya taarifa. Tumia vyanzo vya kuaminika tu: tovuti rasmi za klabu na ligi, UEFA, BBC Sport, '
    'Sky Sports, ESPN, The Athletic, Reuters, Transfermarkt (majeruhi/kadi), FotMob, Sofascore, '
    'WhoScored (vikosi), Understat/FBref (xG), na vyombo vya habari vya michezo vya nchi husika. '
    'Taarifa inayotoka chanzo kimoja cha mashabiki au uvumi iite "UVUMI". Usibuni majina, namba, '
    'wala tarehe: kama hujapata uhakika, sema "sijapata taarifa ya uhakika".')


def _grounded_search_v2(query):
    k = ' '.join(str(query).lower().split())
    now = time.time()
    hit = _SEARCH_CACHE.get(k)
    if hit and now - hit[0] < SEARCH_TTL:
        return True, hit[1]
    key = G._key()
    body = {
        'systemInstruction': {'parts': [{'text': _SEARCH_SYS}]},
        'contents': [{'role': 'user', 'parts': [{'text': query}]}],
        'tools': [{'googleSearch': {}}],
        'generationConfig': {'maxOutputTokens': 2500},
    }
    last = ''
    for model in G._models():
        url = f'{G.GEMINI_BASE}/models/{model}:generateContent'
        req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={
            'Content-Type': 'application/json', 'x-goog-api-key': key, 'User-Agent': G.UA},
            method='POST')
        try:
            with urllib.request.urlopen(req, timeout=35) as resp:
                obj = json.loads(resp.read().decode('utf-8'))
            cand = (obj.get('candidates') or [{}])[0]
            txt = ''.join(p.get('text', '') for p in (cand.get('content') or {}).get('parts', [])
                          if not p.get('thought')).strip()
            if not txt:
                last = 'jibu tupu'
                continue
            srcs = []
            for ch in (cand.get('groundingMetadata') or {}).get('groundingChunks', []):
                t = (ch.get('web') or {}).get('title')
                if t and t not in srcs:
                    srcs.append(t)
            res = txt[:3200] + (('\nVyanzo: ' + ', '.join(srcs[:6])) if srcs else '')
            if len(_SEARCH_CACHE) > 200:
                _SEARCH_CACHE.clear()
            _SEARCH_CACHE[k] = (now, res)
            return True, res
        except urllib.error.HTTPError as e:
            last = f'HTTP {e.code}'
        except Exception as e:
            last = f'{type(e).__name__}: {e}'
    return False, last


# ---------------------------------------------------------------
# 3) Zana mpya: hali ya hewa (Open-Meteo) na saa ya mechi
# ---------------------------------------------------------------
_WMO = {0: 'anga safi', 1: 'kwa kiasi safi', 2: 'mawingu kiasi', 3: 'mawingu mengi',
        45: 'ukungu', 48: 'ukungu', 51: 'manyunyu', 53: 'manyunyu', 55: 'manyunyu mazito',
        61: 'mvua nyepesi', 63: 'mvua', 65: 'mvua kubwa', 71: 'theluji nyepesi', 73: 'theluji',
        75: 'theluji nyingi', 80: 'mvua za vipindi', 81: 'mvua za vipindi',
        82: 'mvua kali za vipindi', 95: 'radi', 96: 'radi na mvua ya mawe', 99: 'radi na mvua ya mawe'}


def _wmo(c):
    c = int(c or 0)
    return _WMO.get(c) or ('mvua' if 51 <= c <= 67 else 'theluji' if 71 <= c <= 77
                           else 'radi' if c >= 95 else 'mawingu')


def _http_json(url, timeout=8):
    req = urllib.request.Request(url, headers={'User-Agent': G.UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def _t_weather(a):
    place = str(a.get('place') or '').strip()[:80]
    if not place:
        return 'Weka jina la mji wa uwanja (mf. "Manchester").'
    today = (datetime.utcnow() + timedelta(hours=3)).date()
    d = G._parse_date(a.get('date')) or today
    if d < today - timedelta(days=1) or d > today + timedelta(days=15):
        return 'Hali ya hewa inapatikana kwa siku 15 mbele tu (na jana).'
    try:
        geo = _http_json('https://geocoding-api.open-meteo.com/v1/search?name='
                         + quote(place) + '&count=1&language=en&format=json')
        res = geo.get('results') or []
        if not res:
            return f'Mji "{place}" haukupatikana. Jaribu jina la mji pekee.'
        g0 = res[0]
        lat, lon = g0['latitude'], g0['longitude']
        label = ', '.join(x for x in (g0.get('name'), g0.get('admin1'), g0.get('country')) if x)
        fc = _http_json('https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s'
                        '&hourly=temperature_2m,precipitation,precipitation_probability,'
                        'wind_speed_10m,wind_gusts_10m,weather_code&timezone=auto'
                        '&start_date=%s&end_date=%s' % (lat, lon, d, d))
    except Exception as e:
        return f'Hali ya hewa imeshindwa kupatikana ({type(e).__name__}).'
    off = int(fc.get('utc_offset_seconds') or 0)
    h = fc.get('hourly') or {}
    times = h.get('time') or []
    hours = list(range(15, 22))
    kick = str(a.get('kickoff_eat') or '').strip()
    if kick:
        try:
            hh, mm = [int(x) for x in kick.split(':')[:2]]
            local_min = (hh * 60 + mm - 180 + off // 60) % 1440      # EAT = UTC+3
            lh = local_min // 60
            hours = [x for x in (lh, lh + 1, lh + 2) if x < 24]
        except Exception:
            pass
    lines = [f'Hali ya hewa: {label} ({d}), saa za mahali pa uwanja. Chanzo: Open-Meteo.']
    worst_gust, total_rain = 0.0, 0.0
    for i, t in enumerate(times):
        hr = int(t[11:13])
        if hr not in hours:
            continue
        temp, rain = h['temperature_2m'][i], h['precipitation'][i]
        prob, wind = h['precipitation_probability'][i], h['wind_speed_10m'][i]
        gust, code = h['wind_gusts_10m'][i], h['weather_code'][i]
        worst_gust = max(worst_gust, float(gust or 0))
        total_rain += float(rain or 0)
        lines.append(f'{hr:02d}:00 — {_wmo(code)}, {temp}°C, mvua {rain} mm (uwezekano {prob}%), '
                     f'upepo {wind} km/h (mapuliza hadi {gust}).')
    if len(lines) == 1:
        return 'Hakuna data ya saa hizo.'
    lines.append(f'Muhtasari: mvua jumla {total_rain:.1f} mm, mapuliza makali zaidi {worst_gust:.0f} km/h.')
    return '\n'.join(lines)


def _t_kickoff(a):
    try:
        from fixture_times import kickoff
    except Exception:
        return 'Saa za mechi hazijawashwa (fixture_times.py).'
    t = kickoff(a.get('home'), a.get('away'), a.get('date'))
    return f'Saa ya kuanza (EAT): {t}' if t else 'Saa ya mechi hii haijapatikana kwenye mfumo.'


DECL_WEATHER = G._d(
    'get_weather',
    'Hali ya hewa ya mji wa uwanja wakati wa mechi (Open-Meteo, siku 15 mbele). Tumia mvua, upepo na '
    'mapuliza kuamua kama zina athari kwa magoli.',
    {'place': G._s('Mji wa uwanja, mf. "Manchester" (kama huujui, tafuta mji wa uwanja kwanza)'),
     'date': G._s('YYYY-MM-DD'),
     'kickoff_eat': G._s('Saa ya mechi kwa EAT, HH:MM (hiari; pata kwa get_kickoff_time)')},
    ['place', 'date'])
DECL_KICKOFF = G._d(
    'get_kickoff_time', 'Saa ya kuanza (EAT) ya mechi kutoka kwenye mfumo.',
    {'home': G._s('Timu ya nyumbani'), 'away': G._s('Timu ya ugenini'), 'date': G._s('YYYY-MM-DD')},
    ['home', 'away', 'date'])


# ---------------------------------------------------------------
# 4) Injini ya haraka: zana kwa pamoja + kuendelea jibu likikatika
# ---------------------------------------------------------------
def _plain_text(raw):
    return ''.join(p.get('text', '') for p in raw if p.get('text') and not p.get('thought'))


def _run_tools(mode, calls):
    items = [(p['functionCall'].get('name', ''), p['functionCall'].get('args') or {}) for p in calls]
    if len(items) == 1:
        return [G._run_tool(mode, items[0][0], items[0][1])]
    try:
        from flask import copy_current_request_context

        def mk(name, args):
            @copy_current_request_context
            def run():
                return G._run_tool(mode, name, args)
            return run
        runners = [mk(n, a) for n, a in items]
    except Exception:
        return [G._run_tool(mode, n, a) for n, a in items]
    out = [None] * len(items)
    with ThreadPoolExecutor(max_workers=min(4, len(items))) as ex:
        futs = [ex.submit(r) for r in runners]
        for i, f in enumerate(futs):
            try:
                out[i] = f.result(timeout=90)
            except Exception as e:
                out[i] = {'error': f'{type(e).__name__}: {str(e)[:150]}'}
    return out


def _gemini_events_fast(messages, max_tokens=1800, use_tools=True, mode='public'):
    key = G._key()
    if not key:
        yield ('error', 'GEMINI_API_KEY haijawekwa kwenye Render (Environment).')
        return
    sys_text, base_contents = G._convert(messages)
    decls = G._tool_decls(mode) if use_tools else []
    models = G._models()
    last_err = {'code': 0, 'msg': 'Kosa lisilojulikana.'}

    for mi, model in enumerate(models):
        contents = list(base_contents)
        rounds, emitted, sep, cont_left = 0, False, False, 2
        while True:
            cfg = G._gen_config(model, max_tokens, mode)
            body = {'contents': contents, 'generationConfig': cfg}
            if sys_text:
                body['systemInstruction'] = {'parts': [{'text': sys_text}]}
            if decls:
                body['tools'] = [{'functionDeclarations': decls}]
                if rounds >= G.MAX_ROUNDS:
                    body['toolConfig'] = {'functionCallingConfig': {'mode': 'NONE'}}

            failed, cut, st = False, False, None
            for attempt in (1, 2, 3):
                st = {'raw': [], 'calls': [], 'finish': None, 'block': None, 'text': False}
                try:
                    for kind, val in G._stream_round(model, body, key):
                        if kind == 'block':
                            st['block'] = val
                        elif kind == 'finish':
                            st['finish'] = val
                        elif kind == 'part':
                            part = val
                            if part.get('functionCall'):
                                st['calls'].append(part)
                                st['raw'].append(dict(part))
                            elif part.get('text') is not None and not part.get('thought'):
                                t = part['text']
                                if t:
                                    if sep and emitted:
                                        yield ('text', '\n\n')
                                    sep = False
                                    st['text'] = True
                                    emitted = True
                                    yield ('text', t)
                                G._add_raw(st['raw'], part)
                            else:
                                st['raw'].append(dict(part))
                    failed = False
                    break
                except urllib.error.HTTPError as e:
                    err = G._parse_err(e)
                    last_err, failed = err, True
                    if (err['code'] == 400 and 'thinking' in err['msg'].lower()
                            and 'thinkingConfig' in cfg):
                        G._NO_THINK.add(model)
                        cfg.pop('thinkingConfig', None)
                        continue
                    if err['code'] in (429, 500, 502, 503, 504) and attempt < 3 and not st['text']:
                        time.sleep(1.5 * attempt)
                        continue
                    break
                except Exception as e:
                    last_err = {'code': 0, 'msg': f'{type(e).__name__}: {e}'}
                    if st['text']:
                        cut, failed = True, False
                    else:
                        failed = True
                    break

            if cut:                                   # mtandao ulikatika katikati ya jibu
                partial = _plain_text(st['raw'])
                if cont_left > 0 and partial.strip():
                    cont_left -= 1
                    contents.append({'role': 'model', 'parts': [{'text': partial}]})
                    contents.append({'role': 'user', 'parts': [{'text': CONTINUE_MSG}]})
                    sep = False
                    continue
                yield ('error', 'Mtandao ulikatika katikati ya jibu. Jaribu tena.')
                return

            if failed:
                can_switch = (mi + 1 < len(models) and not emitted and
                              (last_err['code'] in (0, 404, 429, 500, 502, 503, 504)
                               or 'not found' in last_err['msg'].lower()))
                if can_switch:
                    break
                yield ('error', G._friendly(last_err))
                return

            if st['calls']:
                if rounds > G.MAX_ROUNDS:
                    yield ('error', 'Zana nyingi mno zimeitwa. Uliza swali kwa ufupi zaidi.')
                    return
                contents.append({'role': 'model', 'parts': st['raw']})
                run_now = st['calls'][:6]
                for part in run_now:
                    fc = part['functionCall']
                    yield ('tool', G._tool_label(fc.get('name', ''), fc.get('args') or {}))
                results = _run_tools(mode, run_now)
                resp_parts = [{'functionResponse': {'name': p['functionCall'].get('name', ''),
                                                    'response': r}}
                              for p, r in zip(run_now, results)]
                for p in st['calls'][6:]:
                    resp_parts.append({'functionResponse': {
                        'name': p['functionCall'].get('name', ''),
                        'response': {'error': 'Zana nyingi mno kwa wakati mmoja.'}}})
                contents.append({'role': 'user', 'parts': resp_parts})
                rounds += 1
                sep = True
                continue

            if st['text']:
                if st.get('finish') == 'MAX_TOKENS' and cont_left > 0:
                    cont_left -= 1
                    contents.append({'role': 'model', 'parts': [{'text': _plain_text(st['raw'])}]})
                    contents.append({'role': 'user', 'parts': [{'text': CONTINUE_MSG}]})
                    sep = False
                    continue
                yield ('done', None)
                return
            yield ('error', G._empty_reason(st))
            return
    yield ('error', G._friendly(last_err))


# ---------------------------------------------------------------
# 5) Itifaki ya uchambuzi wa mechi kwa data za nje
# ---------------------------------------------------------------
EXTRA_PROMPT = """

UCHAMBUZI WA MECHI KWA DATA ZA NJE (admin akiomba "chambua mechi X", "masoko bora ya X kwa data za nje", majeruhi/kikosi/hali ya hewa):
Hatua (piga zana kwa pamoja ukiweza):
1) get_match_markets: probability za model (na bila-odds) za mechi. Hii ndiyo MSINGI. get_kickoff_time: saa ya mechi.
2) web_search (maswali tofauti, kwa Kiingereza): majeruhi na adhabu za kila timu; kikosi rasmi/kinachotarajiwa; hali ya sasa ya timu (mechi 5 za mwisho, motisha, mchezaji muhimu kurudi); xG ya hivi karibuni (Understat/FBref/FotMob) ukiipata.
3) get_weather: mji wa uwanja (tafuta ukiwa huujui) + date + kickoff_eat.
4) Kwa kila soko muhimu toa mstari: **Soko** — Model **X%** → Baada ya data za nje **Y%** (±n pts) — sababu moja kwa moja kila marekebisho (taja chanzo kwa jina la tovuti na umri wa taarifa). Kisha panga masoko 3-5 bora kwa Y% na uthabiti, na odds/edge kama zipo.
Kanuni za marekebisho (ni hukumu, si model; zinabana makosa):
- Marekebisho ya kila soko yasizidi ±6 pts kwa jumla (±8 tu kama mchezaji muhimu amethibitishwa na vyanzo viwili tofauti).
- Taarifa ya chanzo kimoja, ya zamani (zaidi ya siku 3 kwa majeruhi; zaidi ya saa 24 kwa kikosi) au "UVUMI" = marekebisho 0; itaje tu.
- Kikosi rasmi hutoka takriban saa 1 kabla ya mechi; kabla ya hapo ni "kinachotarajiwa" = nusu ya marekebisho.
- Mvua kubwa (≥ 3 mm kwa saa) au mapuliza ≥ 40 km/h: punguza over goals kwa 1-3 pts. Hali ya hewa ya kawaida: 0.
- Mchezaji muhimu wa mashambulizi nje: magoli ya timu hiyo -2 hadi -4; kipa wa kwanza au mlinzi muhimu nje: magoli ya mpinzani +2 hadi +4.
- xG/data nyingine: tumia kuthibitisha au kutilia shaka model, si kubadilisha namba kwa hisia. Usibuni namba; taja chanzo na kipindi.
- Usipopata data ya kuaminika, sema hivyo na weka marekebisho 0. Usijaze pengo kwa makisio.
Mwisho wa kila uchambuzi andika: "⚠️ Hii ni makadirio ya AI kutokana na data za nje, si matokeo ya model; hayajapimwa wala hayahifadhiwi kwenye History."
"""


def register(app, admin_only, app_ns):
    AT = getattr(G, 'AT', None)
    if AT is None or getattr(AT, 'ADMIN_PROMPT', None) is not G.ADMIN_PROMPT_V2:
        print('[gemini_boost] Gemini haijawashwa (GEMINI_API_KEY?) - plugin imejizima.')
        return

    G._gen_config = _gen_config
    G._grounded_search = _grounded_search_v2
    G._gemini_events = _gemini_events_fast

    G.DECLS['get_weather'] = DECL_WEATHER
    G.DECLS['get_kickoff_time'] = DECL_KICKOFF
    G.ADMIN_TOOLS['get_weather'] = _t_weather
    G.ADMIN_TOOLS['get_kickoff_time'] = _t_kickoff

    orig_label = G._tool_label

    def label(name, a):
        if name == 'get_weather':
            return '🌦️ Hali ya hewa ya ' + str(a.get('place') or '')[:40]
        if name == 'get_kickoff_time':
            return '🕒 Saa ya mechi'
        return orig_label(name, a)
    G._tool_label = label

    AT.ADMIN_PROMPT = AT.ADMIN_PROMPT + EXTRA_PROMPT
    print('[gemini_boost] imewashwa: kasi, kutokatika, hali ya hewa, uchambuzi wa data za nje.')