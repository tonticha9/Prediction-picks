"""
combo_builder.py — Mfumo wa combos: kila combo ina legs kati ya min_legs na
max_legs, kila leg kutoka MECHI TOFAUTI (hakuna mechi mbili kwenye combo
moja). Formula inaamua SOKO gani litumike kwa mechi husika.

Hakuna combo mbili zenye MCHANGANYIKO ULEULE wa (mechi+soko) kwa siku moja.
Combo yenye combined_probability CHINI ya min_combined_probability HAIINGII
kwenye matokeo (inarukwa, jaribio linaendelea kutafuta nyingine).

'stats' (dict hiari) inajazwa na sababu za matokeo - inatumika kuonyesha
maelezo wazi kwenye ukurasa pale combos zikiwa chache au sifuri.

combined_odds HAIHESABIWI KAMWE (imeachwa None kwa makusudi).
"""
import random


def _candidates(match, min_leg_probability, formula):
    markets = match['markets']
    if formula in ('ev_odds_only', 'highest_probability_odds_only'):
        markets = [m for m in markets if m.get('real_odds')]
    markets = [m for m in markets if (m.get('probability') or 0) >= min_leg_probability]
    return markets


def _pick_leg(candidates, formula, rng):
    if formula in ('highest_probability', 'highest_probability_odds_only'):
        return max(candidates, key=lambda m: m.get('probability') or 0)
    if formula == 'ev_odds_only':
        def ev(m):
            odds = m.get('real_odds')
            if not odds:
                return -999
            return ((m.get('probability') or 0) / 100 * odds - 1) * 100
        return max(candidates, key=ev)
    return rng.choice(candidates)  # random_threshold (default/fallback)


def combo_result_from_legs(legs):
    results = [leg.get('result') for leg in legs]
    if any(r in (None, 'PENDING') for r in results):
        return 'PENDING'
    if any(r == 'LOST' for r in results):
        return 'LOST'
    if any(r == 'VOID' for r in results):
        return 'VOID'
    return 'WON'


def build_daily_combos(match_pool, min_legs=4, max_legs=4, min_leg_probability=65.0,
                       min_combined_probability=0.0, formula='random_threshold',
                       max_combos=50, rng=None, stats=None):
    """match_pool: [{'home','away','match_date','markets':[{'market','probability',
    'real_odds','pro_tier', 'result' (hiari, kwa simulation)}, ...]}, ...]

    Inarudisha listi ya combos:
    [{'combined_odds': None, 'combined_probability': float, 'formula_used': str,
      'legs': [{'home','away','match_date','market','probability','real_odds',
                'is_stake','result'}, ...]}, ...]"""
    rng = rng or random
    if stats is None:
        stats = {}
    stats.update({'eligible_matches': 0, 'attempts': 0, 'duplicates': 0,
                  'below_combined_threshold': 0, 'generated': 0})

    eligible = []
    for match in match_pool:
        cands = _candidates(match, min_leg_probability, formula)
        if cands:
            eligible.append((match, cands))
    stats['eligible_matches'] = len(eligible)

    if len(eligible) < min_legs:
        return []

    combos = []
    seen_signatures = set()
    max_attempts = max_combos * 30

    attempts = 0
    while len(combos) < max_combos and attempts < max_attempts:
        attempts += 1
        legs_count = rng.randint(min_legs, max_legs) if max_legs > min_legs else min_legs
        if len(eligible) < legs_count:
            break
        chosen = rng.sample(eligible, legs_count)

        legs = []
        for match, cands in chosen:
            leg_market = _pick_leg(cands, formula, rng)
            legs.append({
                'home': match['home'], 'away': match['away'], 'match_date': match['match_date'],
                'market': leg_market['market'], 'probability': leg_market.get('probability'),
                'real_odds': leg_market.get('real_odds'),
                'is_stake': leg_market.get('real_odds') is not None,
                'result': leg_market.get('result'),
            })

        signature = frozenset((l['home'], l['away'], l['market']) for l in legs)
        if signature in seen_signatures:
            stats['duplicates'] += 1
            continue
        seen_signatures.add(signature)

        prob_product = 1.0
        for leg in legs:
            prob_product *= (leg['probability'] or 0) / 100.0
        combined_probability = round(prob_product * 100, 2)

        if combined_probability < min_combined_probability:
            stats['below_combined_threshold'] += 1
            continue

        combos.append({
            'combined_odds': None,
            'combined_probability': combined_probability,
            'formula_used': formula,
            'legs': legs,
        })

    stats['attempts'] = attempts
    stats['generated'] = len(combos)
    return combos
