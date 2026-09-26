"""
.github/scripts/run_kaggle_and_fetch.py

Inaendesha KERNELS MBILI kwa mpangilio:
  1) KAGGLE_SLUG        - production kuu (football-prediction-final)
  2) KAGGLE_SLUG_V7      - v7 merge (new-features-combine-f), inayosoma
                           output ya (1) kama Input, na kuunganisha v7
Kisha inapakua predictions.csv/value_bets.csv/combos.csv kutoka (2).
"""
import os
import sys
import time
import subprocess
import json

KAGGLE_SLUG = os.environ.get('KAGGLE_SLUG')
KAGGLE_SLUG_V7 = os.environ.get('KAGGLE_SLUG_V7')
if not KAGGLE_SLUG or not KAGGLE_SLUG_V7:
    print("❌ KAGGLE_SLUG na/au KAGGLE_SLUG_V7 hazijawekwa (env variables)")
    sys.exit(1)

MAX_WAIT_MINUTES = 80
POLL_INTERVAL_SECONDS = 90
MAX_RETRIES_PER_CALL = 4


def run_kaggle_cmd(args, allow_fail=False):
    delays = [15, 60, 180, 300]
    last_err = None
    for attempt in range(MAX_RETRIES_PER_CALL):
        result = subprocess.run(['kaggle'] + args, capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout
        last_err = result.stderr or result.stdout
        print(f"⚠️  Jaribio {attempt+1}/{MAX_RETRIES_PER_CALL} lilishindwa: {last_err[:300]}")
        if attempt < MAX_RETRIES_PER_CALL - 1:
            wait = delays[min(attempt, len(delays)-1)]
            print(f"   Kusubiri sekunde {wait} kabla ya kujaribu tena...")
            time.sleep(wait)
    if allow_fail:
        return None
    print(f"❌ Imeshindwa baada ya majaribio {MAX_RETRIES_PER_CALL}: {last_err}")
    sys.exit(1)


def run_and_wait(slug, workdir):
    """Endesha kernel MOJA (push) na usubiri imalize. Inarudisha True/False/'error'."""
    print(f"\n🚀 Kutrigger notebook: {slug}")
    os.makedirs(workdir, exist_ok=True)
    run_kaggle_cmd(['kernels', 'pull', slug, '-p', workdir, '-m'])

    print("📤 Kuanzisha uendeshaji mpya...")
    run_kaggle_cmd(['kernels', 'push', '-p', workdir])

    print(f"⏳ Kusubiri {slug} imalize (upeo: dakika {MAX_WAIT_MINUTES})...")
    waited = 0
    status = None
    while waited < MAX_WAIT_MINUTES * 60:
        time.sleep(POLL_INTERVAL_SECONDS)
        waited += POLL_INTERVAL_SECONDS
        output = run_kaggle_cmd(['kernels', 'status', slug], allow_fail=True)
        if output is None:
            print("   (Hali haikupatikana mzunguko huu, naendelea kusubiri...)")
            continue
        print(f"   [{waited//60} dakika] Hali: {output.strip()}")
        if 'complete' in output.lower():
            status = 'complete'
            break
        if 'error' in output.lower() or 'failed' in output.lower():
            status = 'error'
            break
    return status


def dump_error_logs(slug):
    print(f"❌ {slug} ilishindwa kuendesha (error).")
    print("🔍 Kupakua logs za Kaggle kuonyesha error HASA...")
    os.makedirs('kaggle_debug_logs', exist_ok=True)
    subprocess.run(['kaggle', 'kernels', 'output', slug,
                     '-p', 'kaggle_debug_logs'], capture_output=True, text=True)
    found_log = False
    for fname in os.listdir('kaggle_debug_logs'):
        fpath = os.path.join('kaggle_debug_logs', fname)
        if fname.endswith('.log'):
            found_log = True
            print(f"\n===== MAUDHUI YA {fname} =====")
            with open(fpath, 'r', errors='replace') as f:
                print(f.read())
            print(f"===== MWISHO WA {fname} =====\n")
        elif fname.endswith('.ipynb'):
            found_log = True
            print(f"\n===== KUTAFUTA ERROR NDANI YA {fname} =====")
            try:
                with open(fpath, 'r', errors='replace') as f:
                    nb = json.load(f)
                for cell in nb.get('cells', []):
                    for out in cell.get('outputs', []):
                        if out.get('output_type') == 'error':
                            print('TRACEBACK:')
                            for line in out.get('traceback', []):
                                print(line)
            except Exception as e:
                print(f"(Imeshindwa kusoma notebook: {e})")
            print("===== MWISHO =====\n")
    if not found_log:
        print("⚠️ Hakuna log/notebook file iliyopatikana kwenye output ya Kaggle.")
        print("   Nenda kaggle.com moja kwa moja kuona 'Version History' ya notebook.")


def main():
    # ---- KERNEL 1: Production kuu (football-prediction-final) ----
    status1 = run_and_wait(KAGGLE_SLUG, 'kaggle_kernel')
    if status1 == 'error':
        dump_error_logs(KAGGLE_SLUG)
        sys.exit(1)
    if status1 != 'complete':
        print(f"❌ {KAGGLE_SLUG}: muda umeisha bila kukamilika.")
        sys.exit(1)
    print(f"✅ {KAGGLE_SLUG} imemaliza kuendesha!")

    # ---- KERNEL 2: v7 merge (new-features-combine-f) - inasoma output ya (1) ----
    status2 = run_and_wait(KAGGLE_SLUG_V7, 'kaggle_kernel_v7')
    if status2 == 'error':
        dump_error_logs(KAGGLE_SLUG_V7)
        sys.exit(1)
    if status2 != 'complete':
        print(f"❌ {KAGGLE_SLUG_V7}: muda umeisha bila kukamilika.")
        sys.exit(1)
    print(f"✅ {KAGGLE_SLUG_V7} imemaliza kuendesha!")

    # ---- Pakua matokeo ya MWISHO kutoka kernel 2 (v7 merge) ----
    os.makedirs('kaggle_output', exist_ok=True)
    run_kaggle_cmd(['kernels', 'output', KAGGLE_SLUG_V7, '-p', 'kaggle_output'])

    import pandas as pd
    predictions_path = os.path.join('kaggle_output', 'predictions.csv')
    for fname in ['predictions.csv', 'value_bets.csv', 'combos.csv']:
        path = os.path.join('kaggle_output', fname)
        if not os.path.exists(path):
            print(f"❌ {fname} haipo baada ya kupakua - kitu kimeshindikana.")
            sys.exit(1)
        print(f"✅ {fname}: {os.path.getsize(path):,} bytes")

    try:
        df_pred = pd.read_csv(predictions_path)
        n_matches = len(df_pred)
    except Exception as e:
        print(f"❌ predictions.csv haiwezi kusomwa ({e}) - faili imeharibika.")
        sys.exit(1)

    if n_matches == 0:
        print("\n⚠️  MECHI 0 ZIMEPATIKANA - HII SI KOSA. Data ya app HAITABADILISHWA.")
        with open(os.environ.get('GITHUB_ENV', '/dev/null'), 'a') as f:
            f.write("SKIP_UPDATE=true\n")
        sys.exit(0)

    print(f"\n🎉 Mechi/rows {n_matches} zimepatikana - data itasasishwa kwenye app.")


if __name__ == '__main__':
    main()
