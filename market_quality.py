"""
market_quality.py — Jedwali la ubora wa masoko (Step 8A, lab-v6-audit, Okt 2026).

Kila soko: (daraja, skill, base_rate_%).
  daraja: A = ishara kubwa, B = nzuri, C = dhaifu, X = hakuna ishara
  skill : faida ya log loss dhidi ya kubahatisha (model bila odds, targets safi)
  base_rate_%: kiwango halisi cha soko msimu 2025/26 (kwa soko la OVER / la moja kwa moja)

Masoko ya UNDER yanatumia jedwali la 'over' ya mstari uleule (base = 100 - base_rate).
Jedwali hili litasasishwa na auto-retrain (Hatua ya 9).
"""

# (daraja, skill, base_rate_%)
QUALITY = {
    # ---- Daraja A ----
    'dc_x2': ('A', 0.0769, 57.0),
    'home_win': ('A', 0.0769, 43.0),
    'dc_1x': ('A', 0.0728, 69.4),
    'away_win': ('A', 0.0728, 30.6),
    'home_win_and_over15': ('A', 0.0681, 33.8),
    'away_win_and_over15': ('A', 0.0615, 23.7),
    'home_win_and_over25': ('A', 0.0528, 27.1),
    'dc_1x_and_over15': ('A', 0.0519, 53.3),
    'dc_x2_and_over15': ('A', 0.0519, 43.2),
    'home_goals_over_15': ('A', 0.0509, 44.3),
    'home_goals_over_25': ('A', 0.0464, 20.4),
    'dc_1x_and_over25': ('A', 0.0453, 34.3),
    'away_win_and_over25': ('A', 0.0440, 18.5),
    'away_goals_over_15': ('A', 0.0431, 35.0),
    'dc_x2_and_over25': ('A', 0.0392, 25.7),
    'home_goals_over_05': ('A', 0.0327, 77.4),
    'away_goals_over_25': ('A', 0.0306, 12.4),
    # ---- Daraja B ----
    'away_win_and_cards_over35': ('B', 0.0289, 18.7),
    'home_win_and_corners_over95': ('B', 0.0281, 21.8),
    'away_goals_over_05': ('B', 0.0268, 69.9),
    'away_win_and_corners_over95': ('B', 0.0240, 15.5),
    'yellows_over_55': ('B', 0.0227, 24.4),
    'yellows_over_45': ('B', 0.0221, 39.2),
    'booking_pts_over_55': ('B', 0.0221, 30.0),
    'booking_pts_over_45': ('B', 0.0220, 44.1),
    'yellows_over_35': ('B', 0.0218, 57.6),
    'away_win_and_btts': ('B', 0.0211, 14.8),
    'both_teams_2plus_cards': ('B', 0.0208, 41.3),
    'home_win_and_cards_over35': ('B', 0.0206, 25.2),
    'booking_pts_over_35': ('B', 0.0198, 61.1),
    'home_win_and_btts': ('B', 0.0192, 19.7),
    'dc_x2_and_btts': ('B', 0.0182, 34.4),
    'dc_12_and_over25': ('B', 0.0154, 45.6),
    'goals_over_25': ('B', 0.0149, 52.8),
    'dc_12_and_over15': ('B', 0.0145, 57.4),
    'home_more_cards': ('B', 0.0143, 33.1),
    'cards_range_0_2': ('B', 0.0140, 24.2),
    'yellows_over_25': ('B', 0.0140, 75.8),
    'both_teams_carded': ('B', 0.0128, 78.4),
    'goals_over_35': ('B', 0.0123, 29.1),
    'cards_range_9_20': ('B', 0.0120, 3.9),
    'sot_over_95': ('B', 0.0115, 36.5),
    'cards_range_6_8': ('B', 0.0108, 20.5),
    'sot_over_85': ('B', 0.0106, 49.6),
    'dc_1x_and_btts': ('B', 0.0102, 39.3),
    # ---- Daraja C ----
    'sot_over_75': ('C', 0.0097, 61.9),
    'goals_over_45': ('C', 0.0093, 13.4),
    'goals_over_15': ('C', 0.0087, 77.0),
    'sot_over_65': ('C', 0.0084, 74.0),
    'dc_12': ('C', 0.0076, 73.6),
    'any_red_card': ('C', 0.0058, 17.2),
    'corners_over_105': ('C', 0.0056, 39.2),
    'corners_over_115': ('C', 0.0052, 28.7),
    'corners_over_95': ('C', 0.0045, 50.6),
    'corners_over_85': ('C', 0.0040, 62.0),
    'dc_12_and_btts': ('C', 0.0037, 34.5),
    'corners_over_75': ('C', 0.0036, 72.5),
    # ---- Daraja X (hakuna ishara) ----
    'goals_over_05': ('X', 0.0026, 93.2),
    'no_cards_ft': ('X', 0.0023, 3.1),
    'cards_range_3_5': ('X', 0.0012, 51.4),
    'btts': ('X', 0.0003, 54.1),
}

# Pointi ambazo probability lazima izidi base rate ili pick iitwe "yenye taarifa"
MIN_EDGE = {'A': 0.0, 'B': 2.0, 'C': 5.0, 'X': 99.0}

# Madaraja yanayoruhusiwa kuwa Best Pick
BEST_TIERS = {'A', 'B'}