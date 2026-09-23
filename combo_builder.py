"""
combo_builder.py — Jenga combos za SIKU (target odds ~2.0, makadirio), kwa
kuchanganya masoko yenye odds (stake-legs) na yasiyo na odds (confidence-
legs) kutoka mechi ZOZOTE za siku hiyo. Mechi moja inaweza kutumika kwenye
combos NYINGI tofauti. Legs zinachanganywa (si probability-kubwa-kwanza
pekee) ili combos zisifanane kila mara.

Vigezo vyote (min_legs, max_legs, min_leg_probability, target range) ni
admin-adjustable (Settings) - vinapitishwa na mwito, si fixed hapa.
"""
import random
from itertools import combinations


def build_daily_combos(match_survivors, target_min=1.8, target_max=2.6,
                       min_legs=3, max_legs=5, min_leg_probability=70.0,
                       max_legs_per_match=2, max_combos=40, seed=None):
    """match_survivors: orodha ya {'home','away','match_date','survivors':[...]}
    ambapo 'survivors' ni matokeo ya select_markets() kwa mechi hiyo (tayari
    yamepangwa best-first, hayapingani moja kwa moja ndani ya mechi moja).

    Inarudisha orodha ya combo dicts: {'combined_odds','combined_probability','legs':[...]}
    kila leg: {'home','away','match_date','market','probability','real_odds','is_stake'}.
    Idadi ya combos inaongezeka kadri idadi ya mechi/michanganyiko inavyoongezeka."""
    rng = random.Random(seed)

    # Chuja kwanza kwa min_leg_probability - leg yoyote chini ya kiwango
    # HAIRUHUSIWI kuingia kwenye combo kabisa (stake wala confidence).
    units = []
    for m in match_survivors:
        eligible = [s for s in m['survivors'] if (_get(s, 'probability_%', 'probability') or 0) >= min_leg_probability]
        top = eligible[:max_legs_per_match]
        if not top:
            continue
        stake = [_normalize(s, m, True) for s in top if _get(s, 'real_odds', 'real_odds')]
        conf = [_normalize(s, m, False) for s in top if not _get(s, 'real_odds', 'real_odds')]
        units.append({'home': m['home'], 'away': m['away'], 'match_date': m['match_date'],
                      'stake': stake, 'conf': conf})

    all_confidence_legs = [leg for u in units for leg in u['conf']]

    stake_units = []
    for u in units:
        if not u['stake']:
            continue
        odds_product = 1.0
        for leg in u['stake']:
            odds_product *= leg['real_odds']
        stake_units.append({'home': u['home'], 'away': u['away'], 'match_date': u['match_date'],
                            'legs': u['stake'], 'odds': odds_product})

    combo_seeds = []
    for u in stake_units:
        if target_min <= u['odds'] <= target_max:
            combo_seeds.append([u])
    for a, b in combinations(stake_units, 2):
        odds = a['odds'] * b['odds']
        if target_min <= odds <= target_max:
            combo_seeds.append([a, b])
    for a, b, c in combinations(stake_units, 3):
        odds = a['odds'] * b['odds'] * c['odds']
        if target_min <= odds <= target_max:
            combo_seeds.append([a, b, c])

    combos = []
    for seed_units in combo_seeds:
        combo = _assemble(seed_units, all_confidence_legs, min_legs, max_legs, rng)
        if combo:
            combos.append(combo)

    # Ondoa combos zinazofanana kabisa (leg set sawa), chagua kwa random
    # kama zimezidi max_combos - siku yenye mechi/michanganyiko mingi
    # itazalisha combos nyingi zaidi moja kwa moja (combo_seeds nyingi zaidi).
    unique = {}
    for c in combos:
        key = frozenset((l['home'], l['away'], l['market']) for l in c['legs'])
        unique[key] = c
    combos = list(unique.values())

    if len(combos) > max_combos:
        combos = rng.sample(combos, max_combos)

    return combos


def _assemble(stake_units, confidence_pool, min_legs, max_legs, rng):
    legs, odds_product, prob_product = [], 1.0, 1.0
    for u in stake_units:
        for leg in u['legs']:
            legs.append(leg)
            odds_product *= leg['real_odds']
            prob_product *= (leg['probability'] or 0) / 100

    existing_keys = {(l['home'], l['away'], l['market']) for l in legs}

    # CHANGANYA (si probability-kubwa-kwanza pekee) - shuffle kwanza,
    # kisha panga kwa uzito unaopendelea probability kubwa bila kuwa rigid.
    candidates = [c for c in confidence_pool if (c['home'], c['away'], c['market']) not in existing_keys]
    rng.shuffle(candidates)
    candidates.sort(key=lambda c: -(c['probability'] or 0) + rng.uniform(-8, 8))

    for c in candidates:
        if len(legs) >= max_legs:
            break
        legs.append(c)
        existing_keys.add((c['home'], c['away'], c['market']))
        prob_product *= (c['probability'] or 0) / 100

    if len(legs) < min_legs:
        return None

    rng.shuffle(legs)  # mpangilio wa kuonyesha - si mpangilio wa uchaguzi
    return {
        'combined_odds': round(odds_product, 2),
        'combined_probability': round(prob_product * 100, 1),
        'legs': legs,
    }


def _get(entry, csv_key, attr_key):
    """entry inaweza kuwa pandas row (dict-like, CSV columns) au ORM object
    (PredictionRecord, attribute names) - jaribu zote mbili."""
    if isinstance(entry, dict) or hasattr(entry, 'get'):
        try:
            return entry.get(csv_key)
        except Exception:
            pass
    return getattr(entry, attr_key, None)


def _normalize(entry, match, is_stake):
    market = _get(entry, 'market', 'market')
    probability = _get(entry, 'probability_%', 'probability')
    real_odds = _get(entry, 'real_odds', 'real_odds')
    return {
        'home': match['home'], 'away': match['away'], 'match_date': match['match_date'],
        'market': market, 'probability': probability,
        'real_odds': real_odds if (real_odds == real_odds and real_odds) else None,  # NaN-safe
        'is_stake': is_stake,
    }
