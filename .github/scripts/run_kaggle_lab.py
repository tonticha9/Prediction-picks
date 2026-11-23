"""
.github/scripts/run_kaggle_lab.py  -  v1 (Nov 23 2026)

Inaendesha notebook ya Kaggle (KAGGLE_SLUG, mfano tonticha/add-league-lab-2) upya kwa ratiba, ili
historia na vizingiti (gates) vya ligi 5 mpya + ligi 12 vijisasishe vyenyewe kila wiki.
Njia ileile ya nightly: kernels pull -> kernels push -> subiri hadi ikamilike.
Ikishindwa, inajaribu tena mara moja zaidi. Ikishindwa tena, run inakuwa nyekundu (na bango la /admin/ops).
"""
import os
import subprocess
import sys
import time

SLUG = os.environ.get('KAGGLE_SLUG', 'tonticha/add-league-lab-2')
MAX_WAIT_MIN = int(os.environ.get('MAX_WAIT_MINUTES', '75'))
POLL_SEC = 120
ATTEMPTS = 2


def kaggle(args):
    r = subprocess.run(['kaggle'] + args, capture_output=True, text=True)
    return r.returncode, (r.stdout or '') + (r.stderr or '')


def run_once():
    os.makedirs('kaggle_lab_kernel', exist_ok=True)
    rc, out = kaggle(['kernels', 'pull', SLUG, '-p', 'kaggle_lab_kernel', '-m'])
    if rc != 0:
        print(f"kernels pull imeshindwa: {out[:300]}")
        return 'error'
    rc, out = kaggle(['kernels', 'push', '-p', 'kaggle_lab_kernel'])
    if rc != 0:
        print(f"kernels push imeshindwa: {out[:300]}")
        return 'error'
    print(f"Imeanzishwa: {SLUG}. Kusubiri hadi dakika {MAX_WAIT_MIN}...")
    start = time.time()
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
    return 'timeout'


def main():
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    for attempt in range(1, ATTEMPTS + 1):
        status = run_once()
        print(f"Jaribio {attempt}/{ATTEMPTS}: {status}")
        if status == 'complete':
            if summary:
                with open(summary, 'a') as f:
                    f.write(f"### {SLUG}: imekamilika (jaribio {attempt})\n")
            return 0
        if status == 'timeout':
            break
        time.sleep(60)
    print(f"::error::{SLUG} haikukamilika. Angalia Version History ya notebook kwenye Kaggle.")
    return 1


if __name__ == '__main__':
    sys.exit(main())