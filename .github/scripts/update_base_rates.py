"""
.github/scripts/update_base_rates.py

Inahesabu BASE RATE halisi ya kila soko (miezi 12, mechi ZOTE za ligi 12) kutoka dataset
ya Kaggle, kwa market_evaluator ileile ya Job A. Inaandika kwenye flags za app
(quality_br_*), kwa vipande vya herufi 200, kwenye "slot" mbili zinazobadilishana:
ikishindwa katikati, slot ya wiki iliyopita inabaki salama (rollback ya moja kwa moja).

market_selection.py v6 inazisoma na kuzitumia badala ya base rate za kudumu za
market_quality.py (zikiwa na kikomo cha usalama).
"""
import json
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime, date, timedelta

import pandas as pd

sys.path.insert(0, os.getcwd())

KAGGLE_DATASET = os.environ.get('KAGGLE_DATASET_SLUG')
DATASET_CSV = 'historical_complete_no_gap.csv'
WINDOW_DAYS = 365       # miezi 12
MIN_N = 1500            # mechi za chini zenye data kwa soko ili lihesabiwe
MIN_MARKETS = 30        # masoko machache kuliko haya = kuna tatizo, siandiki chochote
CHUNK = 200
REPORT_SHIFT = 5.0      # onyesha kwenye logi masoko yaliyohama zaidi ya pointi hizi


def download_dataset():
    os.makedirs('kaggle_dataset', exist_ok=True)
    r = subprocess.run(['kaggle', 'datasets', 'download', '-d', KAGGLE_DATASET, '-f', DATASET_CSV,
                        '-p', 'kaggle_dataset', '-o'], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"::warning::Kaggle download: {r.stderr.strip()[:300]}")
    for z in [f for f in os.listdir('kaggle_dataset') if f.endswith('.zip')]:
        zp = os.path.join('kaggle_dataset', z)
        try:
            with zipfile.ZipFile(zp) as zf:
                zf.extractall('kaggle_dataset')
            os.remove(zp)
        except Exception as e:
            print(f"::warning::Imeshindwa kufungua {z}: {e}")
    return os.path.join('kaggle_dataset', DATASET_CSV)


def table_name(key):
    """Jina la jedwali la market_quality ('home_win_and_over15') -> jina la soko la DB ('..._over_15')."""
    if '_and_' in key:
        return re.sub(r'over(\d+)', r'over_\1', key)
    return key


def main():
    if not os.environ.get('DATABASE_URL'):
        print("::error::DATABASE_URL haipo.")
        sys.exit(1)
    if not KAGGLE_DATASET:
        print("::error::KAGGLE_DATASET_SLUG haipo.")
        sys.exit(1)

    try:
        import market_evaluator
        from market_evaluator import evaluate_market
        import market_quality
        from app import app, get_flag, set_flag
        from models import db, PredictionRecord as PR
    except Exception as e:
        print(f"::error::Imeshindwa kuunganisha na app/models: {type(e).__name__}: {e}")
        sys.exit(1)

    problems = market_evaluator.selftest()
    if problems:
        for p in problems:
            print(f"::error::market_evaluator selftest: {p}")
        sys.exit(1)

    csv_path = download_dataset()
    if not os.path.exists(csv_path):
        print("::error::Dataset haikupakuliwa - sibadilishi chochote.")
        sys.exit(1)
    df = pd.read_csv(csv_path)
    df['Date'] = pd.to_datetime(df['Date'], format='mixed', errors='coerce')
    today = pd.Timestamp(date.today())
    df = df[(df['Date'] >= today - pd.Timedelta(days=WINDOW_DAYS)) & (df['Date'] <= today)]
    df = df.dropna(subset=['FTHG', 'FTAG'])
    records = df.to_dict('records')
    print(f"📥 Mechi {len(records):,} za siku {WINDOW_DAYS} zilizopita (ligi zote).")
    if len(records) < MIN_N:
        print(f"::error::Mechi ni chache mno ({len(records)}) - sibadilishi chochote.")
        sys.exit(1)

    # Orodha ya masoko: yale yaliyo kwenye DB (siku 150) + yaliyo kwenye jedwali la msingi
    with app.app_context():
        since = datetime.utcnow() - timedelta(days=150)
        db_markets = {str(r[0]) for r in db.session.query(PR.market).filter(
            PR.match_date >= since).distinct().all() if r[0]}
    markets = set(db_markets) | {table_name(k) for k in market_quality.QUALITY}
    print(f"🧮 Masoko ya kuhesabu: {len(markets)}")

    market_evaluator.UNKNOWN_MARKETS.clear()
    result, skipped = {}, []
    for m in sorted(markets):
        t = f = 0
        for row in records:
            try:
                v = evaluate_market(m, row)
            except Exception:
                v = None
            if v is True:
                t += 1
            elif v is False:
                f += 1
        n = t + f
        if n < MIN_N:
            skipped.append((m, n))
            continue
        result[m] = [round(100.0 * t / n, 2), n]
    if skipped:
        print(f"ℹ️ Yaliyorukwa (data haitoshi): " + ', '.join(f"{m}({n})" for m, n in skipped[:15]))
    if len(result) < MIN_MARKETS:
        print(f"::error::Masoko {len(result)} tu yamehesabiwa (chini ya {MIN_MARKETS}) - sibadilishi chochote.")
        sys.exit(1)

    # Ripoti: masoko yaliyohama zaidi ya REPORT_SHIFT dhidi ya jedwali la msingi
    shifts = []
    for key, (_tier, _skill, base) in market_quality.QUALITY.items():
        m = table_name(key)
        if m in result:
            shifts.append((m, base, result[m][0]))
    shifts.sort(key=lambda x: -abs(x[2] - x[1]))
    print("\n📊 Base rate: jedwali la msingi -> halisi ya miezi 12 (masoko yaliyohama zaidi):")
    for m, old, new in shifts[:15]:
        mark = '  <-- kubwa' if abs(new - old) >= REPORT_SHIFT else ''
        print(f"   {m}: {old:.1f}% -> {new:.1f}%{mark}")

    payload = json.dumps({'v': 1, 't': datetime.utcnow().isoformat(timespec='seconds'),
                          'win': WINDOW_DAYS, 'm': result}, separators=(',', ':'))
    parts = [payload[i:i + CHUNK] for i in range(0, len(payload), CHUNK)]

    try:
        with app.app_context():
            meta = get_flag('quality_br_meta', '') or ''
            try:
                cur_slot = int(meta.split('|')[1])
            except Exception:
                cur_slot = 0
            new_slot = 1 - cur_slot
            for i, p in enumerate(parts):
                set_flag(f'quality_br_{new_slot}_{i}', p)
            back = ''.join(get_flag(f'quality_br_{new_slot}_{i}', '') or '' for i in range(len(parts)))
            if back != payload:
                print("::error::Uthibitisho wa kusoma upya umeshindwa - slot ya zamani inabaki (hakuna mabadiliko).")
                sys.exit(1)
            set_flag('quality_br_meta',
                     f"{datetime.utcnow().isoformat(timespec='seconds')}|{new_slot}|{len(parts)}")
    except SystemExit:
        raise
    except Exception as e:
        print(f"::error::Kuandika flags kumeshindwa: {type(e).__name__}: {e}")
        sys.exit(1)

    print(f"\n✅ Base rates za masoko {len(result)} zimehifadhiwa (slot {new_slot}, vipande {len(parts)}).")


if __name__ == '__main__':
    main()