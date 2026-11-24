"""
.github/scripts/run_kaggle_and_fetch.py

Inaiambia Kaggle "endesha notebook upya" (kernels push), inasubiri kwa
uangalifu (polling ya dakika 1-2, si sekunde), na ikimaliza, inapakua
predictions.csv/value_bets.csv/combos.csv kwenye kaggle_output/.

Ina "retry with backoff" - haikati tamaa mara moja endapo Kaggle
ikatoa error ya muda (403/429/network).

MABADILIKO: predictions.csv pekee ndiyo LAZIMA. value_bets.csv na
combos.csv zinaweza kukosekana siku isiyo na picks - hiyo ni onyo tu,
si kosa.

v2 (Oct 15 2026): usiku mmoja Kaggle ilichukua dakika 70 (kawaida 19-22), karibu na
kikomo cha zamani cha dakika 80. Sasa:
  - kikomo cha kusubiri = dakika 150 (muda unapimwa kwa saa halisi, si kwa kuhesabu usingizi),
  - onyo (::warning::) Kaggle ikipita dakika 45,
  - muda halisi wa Kaggle unaandikwa kwenye muhtasari wa run (GITHUB_STEP_SUMMARY).

v3 (Oct 18 2026): inaripoti hali ya kivuli cha v8 (predictions_v8.csv + v8_health.json)
kwenye muhtasari wa run. HIARI kabisa - ikikosekana, hakuna kinachovunjika.
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

MAX_WAIT_MINUTES = 150         # Kikomo cha juu cha kusubiri Kaggle imalize
SLOW_WARN_MINUTES = 45         # Onyo likipita muda huu (kawaida ni dakika 19-22)
POLL_INTERVAL_SECONDS = 90     # Angalia hali kila dakika 1.5 (tahadhari, si haraka mno)
MAX_RETRIES_PER_CALL = 4


def write_summary(text):
    """Andika mstari kwenye muhtasari wa run ya GitHub Actions (ukipatikana)."""
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if not path:
        return
    try:
        with open(path, 'a') as f:
            f.write(text + "\n")
    except Exception:
        pass


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


def report_v8_shadow():
    """Ripoti hali ya kivuli cha v8. HIARI - haivunji chochote."""
    v8_path = os.path.join('kaggle_output', 'predictions_v8.csv')
    health_path = os.path.join('kaggle_output', 'v8_health.json')
    if not os.path.exists(v8_path):
        print("ℹ️ predictions_v8.csv haipo (kivuli cha v8 bado hakijaongezwa kwenye notebook, au kimeshindwa) - sawa.")
        return
    print(f"✅ predictions_v8.csv (kivuli cha v8): {os.path.getsize(v8_path):,} bytes")
    if not os.path.exists(health_path):
        return
    try:
        with open(health_path) as f:
            h = json.load(f)
        line = (f"v8 muundo: {h.get('passed')}/{h.get('total')} PASS | mechi {h.get('fixtures')} | "
                f"masoko yaliyo-calibrate leo {h.get('recalibrated')} | {h.get('version')}")
        print("   " + line)
        write_summary("### " + line)
        bad = [c.get('name') for c in h.get('checks', []) if not c.get('ok')]
        if bad:
            msg = "v8 ukaguzi ulioshindwa: " + "; ".join(bad)
            print("::warning::" + msg)
            write_summary("- " + msg)
    except Exception as e:
        print(f"⚠️ v8_health.json haisomeki: {e}")


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
    start = time.time()
    status = None
    slow_warned = False
    while time.time() - start < MAX_WAIT_MINUTES * 60:
        time.sleep(POLL_INTERVAL_SECONDS)
        elapsed_min = int((time.time() - start) // 60)

        if elapsed_min >= SLOW_WARN_MINUTES and not slow_warned:
            slow_warned = True
            print(f"::warning::Kaggle imechukua zaidi ya dakika {SLOW_WARN_MINUTES} "
                  f"(kawaida ni dakika 19-22). Angalia logs za notebook kuona seli inayochelewesha.")

        output = run_kaggle_cmd(['kernels', 'status', KAGGLE_SLUG], allow_fail=True)
        if output is None:
            print("   (Hali haikupatikana mzunguko huu, naendelea kusubiri...)")
            continue
        print(f"   [{elapsed_min} dakika] Hali: {output.strip()}")
        if 'complete' in output.lower():
            status = 'complete'
            break
        if 'error' in output.lower() or 'failed' in output.lower():
            status = 'error'
            break

    total_min = int((time.time() - start) // 60)

    if status == 'error':
        write_summary(f"### Nightly: Kaggle ILISHINDWA baada ya dakika {total_min}")
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
        write_summary(f"### Nightly: muda umeisha (dakika {MAX_WAIT_MINUTES}) - Kaggle bado inaendesha")
        print(f"❌ Muda umeisha (dakika {MAX_WAIT_MINUTES}) bila kukamilika. Kaggle bado inaendesha - angalia baadaye.")
        sys.exit(1)

    print(f"✅ Kaggle imemaliza kuendesha! (dakika {total_min})")
    write_summary(f"### Nightly: Kaggle ilichukua **{total_min} dakika** (kikomo {MAX_WAIT_MINUTES}, kawaida 19-22)")

    # HATUA 4: Pakua matokeo (FAILI ZOTE mara moja - hakuna '-f' flag kwa amri hii)
    os.makedirs('kaggle_output', exist_ok=True)
    run_kaggle_cmd(['kernels', 'output', KAGGLE_SLUG, '-p', 'kaggle_output'])

    # Onyesha faili zote zilizopakuliwa (msaada wa kugundua tatizo)
    print("📂 Faili zilizopakuliwa kutoka Kaggle:")
    for fname in sorted(os.listdir('kaggle_output')):
        fpath = os.path.join('kaggle_output', fname)
        if os.path.isfile(fpath):
            print(f"   - {fname}: {os.path.getsize(fpath):,} bytes")

    # HATUA 5: Uthibitisho wa msingi (sanity check) kabla ya kukubali matokeo
    # MUHIMU:
    #  - predictions.csv ni LAZIMA. Ikikosekana = kosa la kweli, simamisha.
    #  - value_bets.csv na combos.csv ni za HIARI. Siku isiyo na picks,
    #    notebook inaweza kutoziandika. Hiyo si kosa - tunaonya tu, na
    #    yml itashughulikia (inaweka header tu).
    #  - fixtures 0 (international break) SI kosa - tunaruka sasisho la data.
    import pandas as pd
    predictions_path = os.path.join('kaggle_output', 'predictions.csv')

    if not os.path.exists(predictions_path):
        print("❌ predictions.csv haipo baada ya kupakua - kitu kimeshindikana.")
        sys.exit(1)
    print(f"✅ predictions.csv: {os.path.getsize(predictions_path):,} bytes")

    for fname in ['value_bets.csv', 'combos.csv']:
        path = os.path.join('kaggle_output', fname)
        if not os.path.exists(path):
            print(f"⚠️  {fname} haipo kwenye output ya Kaggle (huenda hakuna picks leo,")
            print(f"    au cell ya {fname} imeshindwa kimya kimya). Naendelea - workflow")
            print(f"    itaweka header tu kwenye data/{fname}.")
        else:
            print(f"✅ {fname}: {os.path.getsize(path):,} bytes")

    # Kivuli cha v8 (hiari)
    report_v8_shadow()

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