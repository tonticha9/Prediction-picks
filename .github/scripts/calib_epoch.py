"""
.github/scripts/calib_epoch.py

Inaendesha daily_calibration.py kama kawaida, lakini inapuuza rekodi za live za ZAMANI (kabla ya tarehe ya 'epoch')
kwa masoko ambayo injini yake ilibadilishwa. Sababu: daily_calibration inajifunza kutoka siku 150, na rekodi za
kabla ya marekebisho zilitoka injini ya zamani; zingerekebisha tena probability mpya (marekebisho mara mbili).
Soko likikusanya rekodi za kutosha baada ya epoch (>= 120), calibration yake ya kila siku inarudi kufanya kazi yenyewe.

Kubadilisha: ongeza soko jipya na tarehe kwenye CALIB_EPOCH wakati injini ya soko hilo inabadilishwa.
Usalama: kosa lolote = daily_calibration ya kawaida inaendelea (nightly haivunjiki).
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily_calibration as dc  # noqa: E402

CALIB_EPOCH = {
    'goals_over_25': '2026-11-08',   # Nov 7 2026: shift ya V6B = nusu ya muda mrefu + nusu ya siku 60 + kinga ya wastani
    'goals_over_35': '2026-11-08',
}

_orig_build = dc.build_training_frame


def build_with_epoch(settled, log):
    train = _orig_build(settled, log)
    try:
        if len(train) == 0 or not CALIB_EPOCH:
            return train
        keep = np.ones(len(train), dtype=bool)
        for m, d in CALIB_EPOCH.items():
            keep &= ~((train['cm'] == m) & (train['t'] < pd.Timestamp(d))).values
        dropped = int((~keep).sum())
        if dropped:
            print(f"   ℹ️ Epoch: rekodi {dropped} za zamani zimepuuzwa kwa masoko {sorted(CALIB_EPOCH)}")
        return train[keep]
    except Exception as e:
        print(f"::warning::calib_epoch imeshindwa ({type(e).__name__}: {e}) - natumia rekodi zote.")
        return train


dc.build_training_frame = build_with_epoch

if __name__ == '__main__':
    dc.main()