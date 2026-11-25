"""
.github/scripts/run_kaggle_lab.py  -  v3 (Nov 25 2026)

Inaendesha notebook ya Kaggle (KAGGLE_SLUG, mfano tonticha/add-league-lab-2) upya kwa ratiba, ili
historia na vizingiti (gates) vya ligi 5 mpya + ligi 12 vijisasishe vyenyewe kila wiki.
Njia ileile ya nightly: kernels pull -> kernels push -> subiri hadi ikamilike.

v2 (baada ya run ya Nov 23 iliyokaa QUEUED dakika 76 bila kuanza):
  - inaonyesha mipangilio ya notebook (GPU/TPU/Internet) na kuzima GPU/TPU (hazihitajiki, na foleni yake inaweza kukaa milele),
    na kuwasha Internet ikiwa imezimwa (notebook inahitaji API);
  - ikikaa QUEUED zaidi ya QUEUE_MAX_MIN (chaguo-msingi dakika 25) inasimama mapema na kueleza nini cha kufanya;
  - ikikamilika, inakagua kuwa output ina gates12.json (vinginevyo run inakuwa nyekundu).

v3 (Nov 25 2026): nightly na retrain zinatumia funguo zilezile za Kaggle na zinakubali, kwa hiyo tofauti iko kwenye
mipangilio ya notebook hii yenyewe. Sasa script:
  - INAONYESHA metadata nzima ya notebook kwenye log (hakuna siri ndani yake), ili tuone tofauti halisi na nightly;
  - inazima GPU/TPU na kuondoa 'machine_shape'/'accelerator' ya GPU/TPU (kama zipo), na kuwasha Internet;
  - inaonyesha 'kernel_sources' (notebook nyingine zinazotumika kama input) kama chanzo cha tahadhari;
  - ikikaa QUEUED zaidi ya QUEUE_MAX_MIN (chaguo-msingi dakika 15) inaonyesha hali ghafi na kusimama mapema.
"""
import glob
import json
import os
import subprocess
import sys
import time

SLUG = os.environ.get('KAGGLE_SLUG', 'tonticha/add-league-lab-2')
MAX_WAIT_MIN = int(os.environ.get('MAX_WAIT_MINUTES', '75'))
QUEUE_MAX_MIN = int(os.environ.get('QUEUE_MAX_MIN', '15'))
POLL_SEC = int(os.environ.get('POLL_SEC', '120'))
ATTEMPTS = 2
META = os.path.join('kaggle_lab_kernel', 'kernel-metadata.json')


def kaggle(args):
    r = subprocess.run(['kaggle'] + args, capture_output=True, text=True)
    return r.returncode, (r.stdout or '') + (r.stderr or '')


def fix_metadata():
    """Soma metadata ya notebook, ionyeshe yote, zima GPU/TPU/accelerator na washa Internet. Haivunji kitu ikishindwa."""
    try:
        with open(META, encoding='utf-8') as f:
            meta = json.load(f)
    except Exception as e:
        print(f"   siwezi kusoma {META}: {type(e).__name__}: {e} (naendelea bila kubadilisha mipangilio)")
        return
    print("   ===== metadata ya notebook (kabla ya mabadiliko) =====")
    print("   " + json.dumps(meta, indent=2, ensure_ascii=False).replace("\n", "\n   "))
    print("   ======================================================")

    def on(v):
        return str(v).strip().lower() in ('true', '1', 'yes')

    print(f"   mipangilio: gpu={meta.get('enable_gpu')} tpu={meta.get('enable_tpu')} internet={meta.get('enable_internet')} "
          f"datasets={len(meta.get('dataset_sources') or [])} kernels={len(meta.get('kernel_sources') or [])}")
    if meta.get('kernel_sources'):
        print(f"   ::notice::notebook hii inatumia notebook nyingine kama input: {meta.get('kernel_sources')} "
              f"(nightly haina hili; ni mgombea wa tofauti).")
    changed = False
    for key in ('enable_gpu', 'enable_tpu'):
        if key in meta and on(meta[key]):
            meta[key] = 'false'
            changed = True
            print(f"   ::warning::{key} ilikuwa IMEWASHWA kwenye notebook; nimeizima kwa run hii.")
    for key in ('machine_shape', 'accelerator'):
        val = str(meta.get(key) or '').strip()
        if val and not any(x in val.lower() for x in ('none', 'cpu')):
            del meta[key]
            changed = True
            print(f"   ::warning::{key}='{val}' nimeiondoa kwa run hii (inaomba GPU/TPU; foleni yake inaweza kukaa milele).")
    if 'enable_internet' in meta and not on(meta['enable_internet']):
        meta['enable_internet'] = 'true'
        changed = True
        print("   ::warning::Internet ilikuwa IMEZIMWA kwenye notebook; nimeiwasha kwa run hii (notebook inahitaji AllSportsAPI).")
    if changed:
        try:
            with open(META, 'w', encoding='utf-8') as f:
                json.dump(meta, f, indent=2)
        except Exception as e:
            print(f"   siwezi kuandika {META}: {e}")


def check_output():
    """Baada ya kukamilika: thibitisha output ina gates12.json. Rudisha True/False; ikiwa upakuaji wenyewe umeshindwa, rudisha None."""
    rc, out = kaggle(['kernels', 'output', SLUG, '-p', 'lab_check'])
    if rc != 0:
        print(f"   ukaguzi wa output umeshindwa kupakua: {out[-200:]}")
        return None
    files = [f for f in glob.glob(os.path.join('lab_check', '**', '*'), recursive=True) if os.path.isfile(f)]
    names = [os.path.basename(f) for f in files]
    print(f"   output ina faili {len(files)}: {', '.join(sorted(names)[:20])}")
    return 'gates12.json' in names


def run_once():
    os.makedirs('kaggle_lab_kernel', exist_ok=True)
    rc, out = kaggle(['kernels', 'pull', SLUG, '-p', 'kaggle_lab_kernel', '-m'])
    if rc != 0:
        print(f"kernels pull imeshindwa: {out[:300]}")
        return 'error'
    fix_metadata()
    rc, out = kaggle(['kernels', 'push', '-p', 'kaggle_lab_kernel'])
    if rc != 0:
        print(f"kernels push imeshindwa: {out[:300]}")
        return 'error'
    print(f"Imeanzishwa: {SLUG}. Kusubiri hadi dakika {MAX_WAIT_MIN} (foleni isizidi dakika {QUEUE_MAX_MIN})...")
    start = time.time()
    queued_since = None
    while time.time() - start < MAX_WAIT_MIN * 60:
        time.sleep(POLL_SEC)
        rc, out = kaggle(['kernels', 'status', SLUG])
        mins = int((time.time() - start) // 60)
        if rc != 0:
            print(f"   [{mins} dk] hali haikupatikana, naendelea kusubiri")
            continue
        print(f"   [{mins} dk] {out.strip()}")
        low = out.lower()
        if 'complete' in low:
            return 'complete'
        if 'error' in low or 'failed' in low or 'cancel' in low:
            return 'error'
        if 'queued' in low:
            queued_since = queued_since or time.time()
            if time.time() - queued_since > QUEUE_MAX_MIN * 60:
                rc2, raw = kaggle(['kernels', 'status', SLUG])
                print(f"   hali ghafi ya Kaggle: rc={rc2} {raw.strip()}")
                return 'queued'
        else:
            queued_since = None
    return 'timeout'


def main():
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    for attempt in range(1, ATTEMPTS + 1):
        status = run_once()
        print(f"Jaribio {attempt}/{ATTEMPTS}: {status}")
        if status == 'complete':
            ok = check_output()
            if ok is False:
                print(f"::error::{SLUG} imekamilika lakini output HAINA gates12.json. Fungua notebook kwenye Kaggle na uhakikie cell G12 ipo na inaandika /kaggle/working/gates12.json.")
                return 1
            if summary:
                with open(summary, 'a') as f:
                    f.write(f"### {SLUG}: imekamilika (jaribio {attempt})\n")
            return 0
        if status == 'queued':
            print(f"::error::{SLUG} imekaa kwenye foleni (QUEUED) zaidi ya dakika {QUEUE_MAX_MIN}. "
                  f"Kaggle: fungua notebook > Version History > ghairi (Cancel) toleo lililokwama, kisha jaribu tena au endesha Save & Run All kwa mkono.")
            return 1
        if status == 'timeout':
            break
        time.sleep(60)
    print(f"::error::{SLUG} haikukamilika. Angalia Version History ya notebook kwenye Kaggle.")
    return 1


if __name__ == '__main__':
    sys.exit(main())