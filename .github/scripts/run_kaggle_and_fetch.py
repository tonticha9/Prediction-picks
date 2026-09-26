"""
.github/scripts/run_kaggle_and_fetch.py

Inaiambia Kaggle "endesha notebook upya" (kernels push), inasubiri kwa
uangalifu (polling ya dakika 1-2, si sekunde), na ikimaliza, inapakua
predictions.csv/value_bets.csv/combos.csv kwenye kaggle_output/.

Ina "retry with backoff" - haikati tamaa mara moja endapo Kaggle
ikatoa error ya muda (403/429/network).
"""
import os
import sys
import time
import subprocess
import json

KAGGLE_SLUG = os.environ.get('KAGGLE_SLUG')
if not KAGGLE_SLUG:
    print("❌ KAGGLE_SLUG haijawekwa (env variable)")
    sys.exit(1)

MAX_WAIT_MINUTES = 80          # Kikomo cha juu cha kusubiri Kaggle imalize
POLL_INTERVAL_SECONDS = 90     # Angalia hali kila dakika 1.5 (tahadhari, si haraka mno)
MAX_RETRIES_PER_CALL = 4


def run_kaggle_cmd(args, allow_fail=False):
    """Endesha amri ya 'kaggle' na retry+backoff endapo ikashindwa."""
    delays = [15, 60, 180, 300]  # sekunde: 15s, dakika 1, dakika 3, dakika 5
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


def main():
    print(f"🚀 Kutrigger notebook: {KAGGLE_SLUG}")

    # HATUA 1: Pakua notebook (kernel files) kwenye folder ya muda
    os.makedirs('kaggle_kernel', exist_ok=True)
    run_kaggle_cmd(['kernels', 'pull', KAGGLE_SLUG, '-p', 'kaggle_kernel', '-m'])

    # HATUA 2: "Push" - hii inaanzisha uendeshaji mpya wa notebook
    print("📤 Kuanzisha uendeshaji mpya...")
    run_kaggle_cmd(['kernels', 'push', '-p', 'kaggle_kernel'])

    # HATUA 3: Subiri ikamilike (polling ya taratibu)
    print(f"⏳ Kusubiri Kaggle imalize (upeo: dakika {MAX_WAIT_MINUTES})...")
    waited = 0
    status = None
    while waited < MAX_WAIT_MINUTES * 60:
        time.sleep(POLL_INTERVAL_SECONDS)
        waited += POLL_INTERVAL_SECONDS
        output = run_kaggle_cmd(['kernels', 'status', KAGGLE_SLUG], allow_fail=True)
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

    if status == 'error':
        print("❌ Kaggle notebook ilishindwa kuendesha (error).")
        print("🔍 Kupakua logs za Kaggle kuonyesha error HASA...")
        os.makedirs('kaggle_debug_logs', exist_ok=True)
        # Jaribu kupakua kila kitu kilichopo (hata baada ya error - mara nyingi
        # Kaggle bado inahifadhi log/notebook iliyoshindwa)
        subprocess.run(['kaggle', 'kernels', 'output', KAGGLE_SLUG,
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
                print(f"===== MWISHO =====\n")
        if not found_log:
            print("⚠️ Hakuna log/notebook file iliyopatikana kwenye output ya Kaggle.")
            print("   Nenda kaggle.com moja kwa moja kuona 'Version History' ya notebook.")
        sys.exit(1)
    if status != 'complete':
        print(f"❌ Muda umeisha (dakika {MAX_WAIT_MINUTES}) bila kukamilika. Kaggle bado inaendesha - angalia baadaye.")
        sys.exit(1)

    print("✅ Kaggle imemaliza kuendesha!")

    # HATUA 4: Pakua matokeo (FAILI ZOTE mara moja - hakuna '-f' flag kwa amri hii)
    os.makedirs('kaggle_output', exist_ok=True)
    run_kaggle_cmd(['kernels', 'output', KAGGLE_SLUG, '-p', 'kaggle_output'])

    # HATUA 5: Uthibitisho wa msingi (sanity check) kabla ya kukubali matokeo
    # MUHIMU: fixtures 0 (mfano wakati wa "international break") SI kosa -
    # ni hali halali. Tunatofautisha "faili halali yenye mechi 0" (endelea,
    # lakini USIBADILISHE data ya app) na "faili iliyoharibika kabisa" (kosa la
    # kweli - simamisha workflow).
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
        print("\n⚠️  MECHI 0 ZIMEPATIKANA (huenda ni 'international break' au ligi hazina")
        print("    ratiba kwa sasa) - HII SI KOSA. Data ya app HAITABADILISHWA -")
        print("    predictions za mwisho zilizo sahihi zitaendelea kuonekana.")
        # Andika alama maalum GitHub Actions itakayoisoma kuruka hatua za
        # "commit" (badala ya kubadilisha data na faili tupu)
        with open(os.environ.get('GITHUB_ENV', '/dev/null'), 'a') as f:
            f.write("SKIP_UPDATE=true\n")
        sys.exit(0)  # Kumaliza VIZURI (si kosa), lakini bila kubadilisha data

    print(f"\n🎉 Mechi {n_matches} zimepatikana - data itasasishwa kwenye app.")


if __name__ == '__main__':
    main()
