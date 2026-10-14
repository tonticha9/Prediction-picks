"""
team_names.py — CHANZO KIMOJA cha majina ya timu (Job A, Flask, na notebooks).

canon(jina) -> jina RASMI la dataset (kwa mfano 'PSG' -> 'Paris Sg').
Notebooks (nightly, retrain) hazisomi faili hili moja kwa moja: Job A inaandika
team_aliases.json ndani ya dataset ya Kaggle kutoka kwenye ALIASES hapa chini.

KUONGEZA JINA JIPYA: ongeza mstari kwenye ALIASES:  'Jina la API': 'Jina rasmi la dataset'
(Herufi kubwa/ndogo hazijalishi. Jina rasmi lisiwe pia upande wa kushoto.)
"""
import difflib
import json
import os
import re

ALIASES = {
    # ---------- Majina ya API / ya mkono -> jina la dataset (kutoka Job A + nightly) ----------
    'AC Milan': 'Milan',
    'AEL Larissa': 'Larisa',
    'AS Roma': 'Roma',
    'AZ Alkmaar': 'Az Alkmaar',
    'Academico Viseu': 'Academica',
    'Alavés': 'Alaves',
    'Arsenal FC': 'Arsenal',
    'Asteras': 'Asteras Tripolis',
    'Atl. Madrid': 'Ath Madrid',
    'Atromitos FC': 'Atromitos',
    'B. Monchengladbach': "M'Gladbach",
    'Bayer Leverkusen': 'Leverkusen',
    'Bayern München': 'Bayern Munich',
    'Beerschot VA': 'Beerschot Va',
    'Beveren': 'Waasland-Beveren',
    'Beşiktaş': 'Besiktas',
    'Bournemouth FC': 'Bournemouth',
    'Braga': 'Sp Braga',
    'Bremen': 'Werder Bremen',
    'Celta Vigo': 'Celta',
    'Cercle Brugge KSV': 'Cercle Brugge',
    'Club Brugge KV': 'Club Brugge',
    'Dep. A Coruna': 'La Coruna',
    'Dundee FC': 'Dundee',
    'Dundee Utd': 'Dundee United',
    'Eintracht Frankfurt': 'Ein Frankfurt',
    'Erzurumspor': 'Erzurum Bb',
    'Espanyol': 'Espanol',
    'FC Koln': 'Fc Koln',
    'FC Porto': 'Porto',
    'FC Volendam': 'Volendam',
    'Famalicão': 'Famalicao',
    'Fenerbahçe': 'Fenerbahce',
    'Fortuna': 'For Sittard',
    'Frankfurt': 'Ein Frankfurt',
    'G.A. Eagles': 'Go Ahead Eagles',
    'Gençlerbirliği': 'Genclerbirligi',
    'Go Ahead': 'Go Ahead Eagles',
    'Goztepe': 'Goztep',
    'Göztepe': 'Goztep',
    'Hamburger SV': 'Hamburg',
    'Iraklis 1908': 'Iraklis',
    'Juventus FC': 'Juventus',
    'KV Mechelen': 'Mechelen',
    'Kasımpaşa': 'Kasimpasa',
    'Kayseri': 'Kayserispor',
    'Larissa': 'Larisa',
    'Leipzig': 'Rb Leipzig',
    'Levadiakos': 'Levadeiakos',
    "M'gladbach": "M'Gladbach",
    'Mainz 05': 'Mainz',
    'Man Utd': 'Man United',
    'Manchester City': 'Man City',
    'Manchester Utd': 'Man United',
    'NAC Breda': 'Nac Breda',
    'Nottm Forest': "Nott'M Forest",
    'OFI Crete': 'Ofi Crete',
    'Olympiacos Piraeus': 'Olympiakos',
    'Panaitolikos': 'Panetolikos',
    'Partick Thistle': 'Partick',
    'RB Leipzig': 'Rb Leipzig',
    'Rayo Vallecano': 'Vallecano',
    'Real Sociedad': 'Sociedad',
    'Rize': 'Rizespor',
    'Schalke': 'Schalke 04',
    'Sheff Utd': 'Sheffield United',
    'Sheff Wed': 'Sheffield Weds',
    'Sheffield Utd': 'Sheffield United',
    'Sheffield Wed': 'Sheffield Weds',
    'Sint-Truiden': 'St Truiden',
    'Sittard': 'For Sittard',
    'St. Mirren': 'St Mirren',
    'St. Truiden': 'St Truiden',
    'Vitoria Guimaraes': 'Guimaraes',
    'Volos': 'Volos Nfc',
    'West Bromwich': 'West Brom',
    'Willem II': 'Willem Ii',

    # ---------- Majina ya enzi za API (Ago 2025+) ambayo yaligawanya historia ----------
    # (kila jina upande wa kushoto lilionekana kwenye dataset pamoja na jina rasmi la zamani)
    'PSG': 'Paris Sg',
    'Paris SG': 'Paris Sg',
    'Paris Saint Germain': 'Paris Sg',
    'Paris Saint-Germain': 'Paris Sg',
    'PSV': 'Psv Eindhoven',
    'PSV Eindhoven': 'Psv Eindhoven',
    'AZ': 'Az Alkmaar',
    'NAC': 'Nac Breda',
    'Sparta': 'Sparta Rotterdam',
    'AEK': 'Aek',
    'AEK Athens': 'Aek',
    'AEK Athens FC': 'Aek',
    'PAOK': 'Paok',
    'Nottingham': "Nott'M Forest",
    'Nottingham Forest': "Nott'M Forest",
    'Köln': 'Fc Koln',
    'QPR': 'Qpr',
    'Oxford': 'Oxford Utd',
    'Leuven': 'Oud-Heverlee Leuven',
    'La Louvière': 'RAAL La Louviere',
    'R. Union SG': 'Royale Union SG',
    'Sporting': 'Sp Lisbon',
    'Sporting CP': 'Sp Lisbon',
    'Athletic': 'Ath Bilbao',
    'Atlético FC': 'Ath Madrid',
    'R. Oviedo': 'Oviedo',
    'Basaksehir': 'Buyuksehyr',
    'Başakşehir': 'Buyuksehyr',
    'Bodrumspor': 'Bodrum FK',
    'St Pauli': 'St. Pauli',
}

_LOWER = {}


def _norm(s):
    return re.sub(r'\s+', ' ', str(s).strip())


def _rebuild():
    _LOWER.clear()
    for k, v in ALIASES.items():
        _LOWER[_norm(k).lower()] = v


_rebuild()


def canon(name):
    """Jina lolote la timu -> jina rasmi la dataset. Jina lisilojulikana linarudishwa
    kama lilivyo (baada ya kusafisha nafasi). Thamani zisizo maandishi (NaN) hazibadilishwi."""
    if not isinstance(name, str):
        return name
    s = _norm(name)
    return ALIASES.get(s) or _LOWER.get(s.lower()) or s


def validate_aliases():
    """Orodha ya matatizo ya ALIASES (mnyororo wa majina, ugongano wa herufi). Tupu = sawa."""
    problems, seen = [], {}
    for k, v in ALIASES.items():
        lk = _norm(k).lower()
        if lk in seen and seen[lk] != v:
            problems.append(f"'{k}' inaelekeza kwenye majina mawili tofauti: '{seen[lk]}' na '{v}'")
        seen[lk] = v
    for k, v in ALIASES.items():
        if _norm(v).lower() in seen and seen[_norm(v).lower()] != v:
            problems.append(f"mnyororo: '{k}' -> '{v}' -> '{seen[_norm(v).lower()]}' (jina rasmi lisiwe pia alias)")
    return problems


def suggest(name, known, n=2):
    """Majina yanayofanana zaidi na 'name' kutoka 'known' (kwa tahadhari ya jina jipya)."""
    pool = {k.lower(): k for k in known}
    hits = difflib.get_close_matches(str(name).lower(), list(pool), n=n, cutoff=0.5)
    return [pool[h] for h in hits]


def aliases_json_text():
    return json.dumps({'version': 1, 'aliases': dict(sorted(ALIASES.items()))},
                      ensure_ascii=False, indent=1) + '\n'


def write_aliases_json(folder):
    """Andika team_aliases.json kwenye 'folder' (folda ya dataset ya Kaggle).
    Inarudisha True kama maudhui yamebadilika (au faili halikuwepo)."""
    path = os.path.join(folder, 'team_aliases.json')
    new_text = aliases_json_text()
    try:
        with open(path, encoding='utf-8') as f:
            old = f.read()
    except FileNotFoundError:
        old = None
    if old == new_text:
        return False
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_text)
    return True