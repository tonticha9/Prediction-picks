"""
combo_builder.py — Mfumo MPYA wa combos (badala ya best_pick_formula):

Kwa kila mechi, kama ipo soko lolote linalopita 'min_leg_probability'
(na, kwa formula za 'odds_only', lina real_odds), mechi hiyo ni
'mgombea'. Kila combo ina legs kati ya min_legs na max_legs, kila leg
kutoka MECHI TOFAUTI (hakuna mechi mbili kwenye combo moja). Formula
inaamua SOKO gani litumike kwa mechi husika:
  - random_threshold: soko la nasibu miongoni mwa yanayopita kiwango
  - highest_probability: soko lenye probability ya juu zaidi
  - ev_odds_only: (mechi zenye odds pekee) soko lenye EV ya juu zaidi
  - highest_probability_odds_only: (mechi zenye odds pekee) probability ya juu zaidi

combined_odds HAIHESABIWI KAMWE (imeachwa None kwa makusudi).
combined_probability = zidisho la probability za legs zote (fraction ya
uwezekano legs zote kutokea, ikiwa ni huru/independent).
"""
import random


def _candidates(match, min_leg_probability, formula):
    markets = match['markets']
    if formula in ('ev_odds_only', 'highest_probability_odds_only'):
        markets = [m for m in markets if m.get('real_odds')]
    markets = [m for m in markets if (m.get('probability') or 0) >= min_leg_probability]
    return markets


def _pick_leg(candidates, formula):
    if formula == 'highest_probability' or formula == 'highest_probability_odds_only':
        return max(candidates, key=lambda m: m.get('probability') or 0)
    if formula == 'ev_odds_only':
        def ev(m):
            odds = m.get('real_odds')
            if not odds:
                return -999
            return ((m.get('probability') or 0) / 100 * odds - 1) * 100
        return max(candidates, key=ev)
    # random_threshold (default/fallback)
    return random.choice(candidates)


def combo_result_from_legs(legs):
    """Result ya combo kutoka result za legs zake (kwa History-simulation,
    ambako kila leg inaweza kuwa na 'result' iliyopachikwa). 'PENDING'
    ikiwa leg yoyote haina result bado."""
    results = [leg.get('result') for leg in legs]
    if any(r in (None, 'PENDING') for r in results):
        return 'PENDING'
    if any(r == 'LOST' for r in results):
        return 'LOST'
    if any(r == 'VOID' for r in results):
        return 'VOID'
    return 'WON'


def build_daily_combos(match_pool, min_legs=4, max_legs=4, min_leg_probability=65.0,
                       formula='random_threshold', max_combos=50, rng=None):
    """match_pool: [{'home','away','match_date','markets':[{'market','probability',
    'real_odds','pro_tier', 'result' (hiari, kwa simulation)}, ...]}, ...]
    Inarudisha listi ya combos: [{'combined_odds': None, 'combined_probability': float,
    'formula_used': str, 'legs': [{'home','away','match_date','market','probability',
    'real_odds','is_stake','result'}, ...]}, ...]"""
    rng = rng or random

    eligible = []
    for match in match_pool:
        cands = _candidates(match, min_leg_probability, formula)
        if cands:
            eligible.append((match, cands))

    if len(eligible) < min_legs:
        return []

    combos = []
    for _ in range(max_combos):
        legs_count = rng.randint(min_legs, max_legs) if max_legs > min_legs else min_legs
        if len(eligible) < legs_count:
            break
        chosen = rng.sample(eligible, legs_count)

        legs = []
        for match, cands in chosen:
            leg_market = _pick_leg(cands, formula)
            legs.append({
                'home': match['home'], 'away': match['away'], 'match_date': match['match_date'],
                'market': leg_market['market'], 'probability': leg_market.get('probability'),
                'real_odds': leg_market.get('real_odds'),
                'is_stake': leg_market.get('real_odds') is not None,
                'result': leg_market.get('result'),  # ipo tu kwa History-simulation
            })

        prob_product = 1.0
        for leg in legs:
            prob_product *= (leg['probability'] or 0) / 100.0

        combos.append({
            'combined_odds': None,
            'combined_probability': round(prob_product * 100, 2),
            'formula_used': formula,
            'legs': legs,
        })

    return combos
