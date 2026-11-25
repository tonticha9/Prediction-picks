"""
.github/scripts/retrain_and_publish.py

MODE=retrain : anza notebook auto-retrain-release kwenye Kaggle, subiri, pakua output,
               soma release_gate.json, na (PUBLISH=yes tu) chapisha toleo jipya la
               dataset v6b-release ikiwa gate imepita (pass = true).
MODE=rollback: chapisha faili zilizohifadhiwa kwenye rollback_files/ kama toleo jipya.

Usalama (fail-safe): ikiwa kuna shaka yoyote, HAICHAPISHI - production inabaki ilivyo.

v1 (Oct 27 2026)
v2 (Oct 27 2026): gate inasomwa kama JSON ("pass": true/false, "fails": [...]).
                  Muundo usiojulikana = kosa jekundu (exit 1), si kimya.
v3 (Nov 25 2026): (1) gate ikizuia, run sasa inakuwa NYEKUNDU (awali ilikuwa kijani kimya, na kwa siku 9 hakuna aliyejua kuwa
                  toleo halikuchapishwa); (2) kila run inaandika data/retrain_attempt.json (outcome: published, dry_run_ok,
                  stale, blocked, markets_dropped, error, ...); (3) gate ikizuia kwa sababu ya data ya zamani tu ('stale')
                  workflow inajaribu tena siku zinazofuata (Jumanne-Alhamisi) hadi data iwe mpya.
"""
import os
import sys
import time
import json
import shutil
import subprocess
import datetime

RETRAIN_SLUG = os.environ.get('KAGGLE_RETRAIN_SLUG', 'tonticha/auto-retrain-release')
DATASET_SLUG = os.environ.get('KAGGLE_RELEASE_DATASET', 'tonticha/v6b-release')
MODE = os.environ.get('MODE', 'retrain').strip()
PUBLISH = os.environ.get('PUBLISH', 'no').strip() == 'yes'

RELEASE_FILES = ['MANIFEST.json', 'PRODUCTION_MODELS_v6_FINAL.joblib',
                 'goals_pack.joblib', 'feature_union.json']
OUT_DIR = 'kaggle_retrain_output'
BACKUP_DIR = 'backup_prev'
STAGE_DIR = 'publish_stage'

MAX_WAIT_MINUTES = 120
POLL_INTERVAL_SECONDS = 60
MAX_RETRIES_PER_CALL = 4
MIN_MARKETS = 60
MIN_MODEL_MB = 20


def write_summary(text):
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if not path:
        return
    try:
        with open(path, 'a') as f:
            f.write(text + "\n")
    except Exception:
        pass


ATTEMPT_PATH = os.path.join('data', 'retrain_attempt.json')
_ATTEMPT = {'written': False}


def write_attempt(outcome, detail=''):
    """Andika matokeo ya run hii (workflow inayasoma kuamua kujaribu tena). Haivunji kitu ikishindwa."""
    try:
        os.makedirs('data', exist_ok=True)
        with open(ATTEMPT_PATH, 'w') as f:
            json.dump({'utc': datetime.datetime.utcnow().isoformat(timespec='seconds'), 'mode': MODE,
                       'publish': PUBLISH, 'outcome': outcome, 'detail': str(detail)[:300]}, f)
        _ATTEMPT['written'] = True
    except Exception as e:
        print(f"::warning::siwezi kuandika {ATTEMPT_PATH}: {e}")


def finish(outcome, msg, code):
    """Malizia run: andika outcome + muhtasari; code != 0 = run nyekundu."""
    write_attempt(outcome, msg)
    write_summary(f"### Retrain: {msg}")
    if code:
        print(f"::error::Retrain: {msg}")
    sys.exit(code)


def gate_fail_kind(gate_text):
    """'stale' ikiwa sababu ZOTE za gate kushindwa ni data ya zamani (itajirekebisha nightly ikisasisha data); vinginevyo 'blocked'."""
    try:
        g = json.loads(gate_text)
    except Exception:
        return 'blocked'
    fails = g.get('fails') if isinstance(g, dict) else None
    if fails and all('zamani' in str(x).lower() for x in fails):
        return 'stale'
    return 'blocked'


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
            time.sleep(delays[min(attempt, len(delays) - 1)])
    if allow_fail:
        return None
    print(f"❌ Imeshindwa baada ya majaribio {MAX_RETRIES_PER_CALL}: {last_err}")
    sys.exit(1)


def find_file(root, name):
    for dp, _dn, fns in os.walk(root):
        if name in fns:
            return os.path.join(dp, name)
    return None


def read_json(path):
    with open(path, 'r', errors='replace') as f:
        return json.load(f)


def check_release_dir(d):
    """Rudisha (ok, maelezo, manifest)."""
    for f in RELEASE_FILES:
        if not os.path.isfile(os.path.join(d, f)):
            return False, f"faili {f} haipo", None
    try:
        man = read_json(os.path.join(d, 'MANIFEST.json'))
    except Exception as e:
        return False, f"MANIFEST.json haisomeki: {e}", None
    n = man.get('n_markets')
    if not isinstance(n, int) or n < MIN_MARKETS:
        return False, f"n_markets={n} (chini ya {MIN_MARKETS})", man
    mb = os.path.getsize(os.path.join(d, 'PRODUCTION_MODELS_v6_FINAL.joblib')) / 1e6
    if mb < MIN_MODEL_MB:
        return False, f"PRODUCTION_MODELS ni {mb:.1f} MB tu (chini ya {MIN_MODEL_MB})", man
    return True, f"masoko {n}, model {mb:.1f} MB, revision {man.get('bundle_revision')}", man


def gate_is_ready(gate_text):
    """Rudisha (ready, maelezo, muundo_unajulikana)."""
    try:
        g = json.loads(gate_text)
    except Exception:
        g = None
    if isinstance(g, dict) and isinstance(g.get('pass'), bool):
        fails = g.get('fails') or []
        if g['pass'] and not fails:
            return True, f"gate pass=true, fails=0, masoko {g.get('n_markets')}, data_end {g.get('data_end')}", False
        if g['pass'] and fails:
            return False, f"gate pass=true lakini fails si tupu: {fails}", False
        return False, f"gate pass=false, fails={fails}", False
    t = gate_text.upper()
    if 'RELEASE_BLOCKED' in t:
        return False, "gate imesema RELEASE_BLOCKED", False
    if 'RELEASE_READY' in t:
        return True, "gate imesema RELEASE_READY", False
    return False, "muundo wa release_gate.json haujulikani", True


def run_retrain_notebook():
    print(f"🚀 Kutrigger notebook: {RETRAIN_SLUG}")
    os.makedirs('kaggle_retrain_kernel', exist_ok=True)
    run_kaggle_cmd(['kernels', 'pull', RETRAIN_SLUG, '-p', 'kaggle_retrain_kernel', '-m'])
    print("📤 Kuanzisha uendeshaji mpya...")
    run_kaggle_cmd(['kernels', 'push', '-p', 'kaggle_retrain_kernel'])

    start = time.time()
    status = None
    while time.time() - start < MAX_WAIT_MINUTES * 60:
        time.sleep(POLL_INTERVAL_SECONDS)
        mins = int((time.time() - start) // 60)
        out = run_kaggle_cmd(['kernels', 'status', RETRAIN_SLUG], allow_fail=True)
        if out is None:
            print("   (hali haikupatikana, naendelea kusubiri...)")
            continue
        print(f"   [{mins} dakika] Hali: {out.strip()}")
        low = out.lower()
        if 'complete' in low:
            status = 'complete'
            break
        if 'error' in low or 'failed' in low:
            status = 'error'
            break

    total = int((time.time() - start) // 60)
    if status == 'error':
        write_summary(f"### Retrain: Kaggle ILISHINDWA baada ya dakika {total}")
        os.makedirs('kaggle_debug_logs', exist_ok=True)
        subprocess.run(['kaggle', 'kernels', 'output', RETRAIN_SLUG, '-p', 'kaggle_debug_logs'],
                       capture_output=True, text=True)
        for fn in os.listdir('kaggle_debug_logs'):
            fp = os.path.join('kaggle_debug_logs', fn)
            if fn.endswith('.log'):
                print(f"\n===== {fn} (mwisho) =====")
                with open(fp, 'r', errors='replace') as f:
                    print(f.read()[-6000:])
        print("❌ Notebook ya retrain ilishindwa. Production haijaguswa.")
        sys.exit(1)
    if status != 'complete':
        write_summary(f"### Retrain: muda umeisha (dakika {MAX_WAIT_MINUTES})")
        print("❌ Muda umeisha bila kukamilika. Production haijaguswa.")
        sys.exit(1)

    print(f"✅ Kaggle imemaliza (dakika {total})")
    write_summary(f"### Retrain: Kaggle ilichukua **{total} dakika**")
    os.makedirs(OUT_DIR, exist_ok=True)
    run_kaggle_cmd(['kernels', 'output', RETRAIN_SLUG, '-p', OUT_DIR])
    print("📂 Faili zilizopakuliwa:")
    for dp, _dn, fns in os.walk(OUT_DIR):
        for fn in sorted(fns):
            p = os.path.join(dp, fn)
            print(f"   - {os.path.relpath(p, OUT_DIR)}: {os.path.getsize(p):,} bytes")


def backup_current_release():
    """Hifadhi toleo la sasa (best-effort). Toleo za zamani pia zinabaki kwenye Kaggle."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    for f in RELEASE_FILES:
        run_kaggle_cmd(['datasets', 'download', DATASET_SLUG, '-f', f,
                        '-p', BACKUP_DIR, '--unzip'], allow_fail=True)
    got = [f for f in RELEASE_FILES if os.path.isfile(os.path.join(BACKUP_DIR, f))]
    if len(got) == len(RELEASE_FILES):
        print("💾 Toleo la sasa limehifadhiwa kwa rollback (artifact 'prev-release').")
        return True
    print(f"::warning::Backup haijakamilika ({len(got)}/{len(RELEASE_FILES)}). "
          "Rollback kupitia artifact haitawezekana; toleo la zamani bado liko kwenye Kaggle Versions.")
    return False


def publish_dir(src_dir, notes):
    if os.path.isdir(STAGE_DIR):
        shutil.rmtree(STAGE_DIR)
    os.makedirs(STAGE_DIR)
    for f in RELEASE_FILES:
        shutil.copy2(os.path.join(src_dir, f), os.path.join(STAGE_DIR, f))
    meta = {"title": DATASET_SLUG.split('/')[-1], "id": DATASET_SLUG,
            "licenses": [{"name": "CC0-1.0"}]}
    with open(os.path.join(STAGE_DIR, 'dataset-metadata.json'), 'w') as f:
        json.dump(meta, f)

    print(f"📤 Kuchapisha toleo jipya la {DATASET_SLUG} ...")
    run_kaggle_cmd(['datasets', 'version', '-p', STAGE_DIR, '-m', notes])

    ready = False
    for _ in range(20):
        time.sleep(30)
        out = run_kaggle_cmd(['datasets', 'status', DATASET_SLUG], allow_fail=True)
        print(f"   hali ya dataset: {(out or '').strip()}")
        if out and 'ready' in out.lower():
            ready = True
            break
    if not ready:
        print("::warning::Dataset bado haijaonyesha 'ready' - angalia Kaggle moja kwa moja.")
        return False

    # Uthibitisho: pakua MANIFEST.json ya dataset na ulinganishe na tuliyochapisha
    os.makedirs('verify', exist_ok=True)
    run_kaggle_cmd(['datasets', 'download', DATASET_SLUG, '-f', 'MANIFEST.json',
                    '-p', 'verify', '--unzip'], allow_fail=True)
    vp = os.path.join('verify', 'MANIFEST.json')
    if os.path.isfile(vp):
        mine = read_json(os.path.join(src_dir, 'MANIFEST.json'))
        theirs = read_json(vp)
        same = (mine.get('n_markets') == theirs.get('n_markets')
                and mine.get('bundle_revision') == theirs.get('bundle_revision'))
        print(("✅ Uthibitisho: MANIFEST ya dataset inalingana na tuliyochapisha."
               if same else "::warning::MANIFEST ya dataset HAILINGANI na tuliyochapisha - angalia!"))
        return same
    print("::warning::Uthibitisho wa MANIFEST haukuweza kufanyika.")
    return True


def do_rollback():
    d = 'rollback_files'
    ok, why, man = check_release_dir(d)
    if not ok:
        print(f"❌ Rollback imesimama: {why}")
        write_summary(f"### Rollback IMESHINDWA: {why}")
        sys.exit(1)
    stamp = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    publish_dir(d, f"ROLLBACK {stamp}: {why}")
    write_summary(f"### ROLLBACK imechapishwa ({why})")
    print("✅ Rollback imekamilika. Nightly ijayo itatumia toleo hili.")


def do_retrain():
    run_retrain_notebook()

    gate_path = find_file(OUT_DIR, 'release_gate.json')
    if not gate_path:
        print("❌ release_gate.json haipo kwenye output. Sichapishi chochote.")
        finish('missing_gate', "release_gate.json haipo - HAKUNA kilichochapishwa", 1)
    with open(gate_path, 'r', errors='replace') as f:
        gate_text = f.read()
    print("\n===== release_gate.json (sehemu ya mwanzo) =====")
    print(gate_text[:3000])
    print("===== mwisho =====\n")

    ready, why, unknown = gate_is_ready(gate_text)
    print(("✅ " if ready else "🛑 ") + why)
    if unknown:
        finish('gate_unknown', f"{why} - HAKUNA kilichochapishwa (angalia script)", 1)
    if not ready:
        kind = gate_fail_kind(gate_text)
        if kind == 'stale':
            print("ℹ️ Gate imezuia kwa sababu ya DATA YA ZAMANI tu. Nightly ikisasisha features_full.parquet "
                  "(usi-Quick Save notebook ya nightly), retrain itajaribu tena yenyewe siku zinazofuata.")
            finish('stale', f"gate imezuia kwa data ya zamani - production inabaki ilivyo; itajaribu tena kesho ({why})", 1)
        finish('blocked', f"gate imezuia - production inabaki ilivyo ({why})", 1)

    man_path = find_file(OUT_DIR, 'MANIFEST.json')
    if not man_path:
        print("❌ MANIFEST.json haipo kwenye output. Sichapishi chochote.")
        finish('missing_manifest', "MANIFEST.json haipo - HAKUNA kilichochapishwa", 1)
    release_dir = os.path.dirname(man_path)
    ok, why2, man = check_release_dir(release_dir)
    print(("✅ " if ok else "❌ ") + "Ukaguzi wa release: " + why2)
    if not ok:
        finish('release_check_failed', f"ukaguzi wa release umeshindwa ({why2}) - HAKUNA kilichochapishwa", 1)

    backed = backup_current_release()
    prev_man = None
    if os.path.isfile(os.path.join(BACKUP_DIR, 'MANIFEST.json')):
        try:
            prev_man = read_json(os.path.join(BACKUP_DIR, 'MANIFEST.json'))
        except Exception:
            prev_man = None
    if prev_man and isinstance(prev_man.get('n_markets'), int) and man['n_markets'] < prev_man['n_markets']:
        msg = f"masoko yamepungua ({prev_man['n_markets']} -> {man['n_markets']})"
        print(f"🛑 {msg}. Sichapishi.")
        finish('markets_dropped', f"{msg} - HAKUNA kilichochapishwa", 1)

    if not PUBLISH:
        print("🧪 JARIBIO KAVU (publish=no): kila kitu kiko sawa, ila HAKUNA kilichochapishwa.")
        finish('dry_run_ok', f"jaribio kavu limefaulu ({why2}); haikuchapishwa kwa kuwa publish=no", 0)

    stamp = datetime.datetime.utcnow().strftime('%Y-%m-%d')
    notes = f"Auto retrain {stamp}: {why2}"
    good = publish_dir(release_dir, notes)
    detail = f"toleo jipya limechapishwa ({why2}) | backup: {'ndiyo' if backed else 'hapana'} | uthibitisho: {'sawa' if good else 'angalia'}"
    if not good:
        finish('publish_unverified', detail, 1)
    print("✅ Imekamilika. Nightly ijayo itatumia toleo jipya.")
    finish('published', detail, 0)


def main():
    print(f"MODE={MODE} | PUBLISH={PUBLISH} | notebook={RETRAIN_SLUG} | dataset={DATASET_SLUG}")
    try:
        if MODE == 'rollback':
            do_rollback()
            write_attempt('rollback_done', 'rollback imekamilika')
        else:
            do_retrain()
    except SystemExit as e:
        if not _ATTEMPT['written']:
            write_attempt('error', f"imeishia kabla ya hitimisho (exit {e.code})")
        raise


if __name__ == '__main__':
    main()