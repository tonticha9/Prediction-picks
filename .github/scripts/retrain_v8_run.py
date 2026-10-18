#!/usr/bin/env python3
"""Weekly retrain ya v8: push retrain-v8 -> subiri -> gate -> chapisha champion (dataset v8-champion)."""
import os, sys, json, time, shutil, subprocess, pathlib, datetime

USER = os.environ["KAGGLE_USERNAME"]
KERNEL = os.environ.get("RETRAIN_KERNEL") or f"{USER}/retrain-v8"
DATASET = os.environ.get("CHAMPION_DATASET") or f"{USER}/v8-champion"
FORCE = os.environ.get("FORCE_PUBLISH", "0").strip() == "1"
MAX_WAIT_MIN, POLL_SEC = 70, 60

ROOT = pathlib.Path("kaggle_retrain")
KDIR, ODIR, PDIR = ROOT / "kernel", ROOT / "output", ROOT / "publish"
LOG = pathlib.Path("shadow/retrain_log.csv")
SUMMARY = os.environ.get("GITHUB_STEP_SUMMARY")
UTC = datetime.timezone.utc


def say(m):
    print(m, flush=True)


def md(m):
    if SUMMARY:
        with open(SUMMARY, "a", encoding="utf-8") as f:
            f.write(m + "\n")


def sh(args, timeout=1800):
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def die(msg):
    say(f"❌ {msg}")
    md(f"### ❌ Weekly retrain v8 imeshindwa\n{msg}")
    sys.exit(1)


def dataset_exists():
    rc, out = sh(["kaggle", "datasets", "status", DATASET])
    return rc == 0 and "ready" in out.lower()


def parse_utc(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def fetch_report(started):
    shutil.rmtree(ODIR, ignore_errors=True)
    ODIR.mkdir(parents=True)
    rc, out = sh(["kaggle", "kernels", "output", KERNEL, "-p", str(ODIR)])
    gp = ODIR / "v8_gate_report.json"
    if rc != 0 or not gp.exists():
        return None
    g = json.loads(gp.read_text(encoding="utf-8"))
    try:
        if parse_utc(g["created_utc"]) < started:
            return None  # output ya zamani, run mpya bado haijaisha
    except Exception:
        return None
    return g


def main():
    has_ds = dataset_exists()
    say(f"Champion dataset {DATASET}: {'ipo' if has_ds else 'haipo bado (itaundwa)'}")

    shutil.rmtree(ROOT, ignore_errors=True)
    KDIR.mkdir(parents=True)
    rc, out = sh(["kaggle", "kernels", "pull", KERNEL, "-p", str(KDIR), "-m"])
    mp = KDIR / "kernel-metadata.json"
    if rc != 0 or not mp.exists():
        die(f"kernels pull imeshindwa:\n{out}")
    meta = json.loads(mp.read_text(encoding="utf-8"))
    ds = list(meta.get("dataset_sources") or [])
    if has_ds and DATASET not in ds:
        ds.append(DATASET)  # ili notebook ione summary ya champion ya zamani
    meta["dataset_sources"] = ds
    mp.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    started = datetime.datetime.now(UTC)
    rc, out = sh(["kaggle", "kernels", "push", "-p", str(KDIR)])
    say(out)
    if rc != 0 or "error" in out.lower():
        die(f"kernels push imeshindwa:\n{out}")

    say(f"⏳ Kusubiri {KERNEL} (upeo dakika {MAX_WAIT_MIN})...")
    time.sleep(45)
    gate, t0 = None, time.time()
    while time.time() - t0 < MAX_WAIT_MIN * 60:
        rc, out = sh(["kaggle", "kernels", "status", KERNEL])
        low = out.lower()
        say(f"   [{int((time.time()-t0)/60)} dk] {out}")
        if "error" in low or "cancel" in low:
            die(f"Kernel imeisha vibaya:\n{out}")
        if "complete" in low:
            gate = fetch_report(started)
            if gate:
                break
            say("   output bado ya zamani, nasubiri...")
        time.sleep(POLL_SEC)
    if not gate:
        die("Muda umeisha au hakuna v8_gate_report.json mpya.")

    s = gate["summary"]
    accepted = bool(gate["accepted"]) or FORCE
    say(f"GATE: accepted={gate['accepted']} force={FORCE} version={gate['version']}")

    rows = "\n".join(f"| {'✅' if c['ok'] else '❌'} | {c['name']} | {c['detail']} |" for c in gate["checks"])
    md(f"## Weekly retrain v8: {gate['version']}\n"
       f"data hadi **{s['data_end']}** | wastani wa skill **{s['mean_skill']:.4f}** | "
       f"wastani wa ECE **{s['mean_ece']:.4f}** | features {s['n_features']}\n\n"
       f"| | Ukaguzi | Thamani |\n|---|---|---|\n{rows}\n")

    published = False
    if accepted:
        shutil.rmtree(PDIR, ignore_errors=True)
        PDIR.mkdir(parents=True)
        shutil.copy(ODIR / "v8_bundle.joblib", PDIR / "v8_bundle.joblib")
        shutil.copy(ODIR / "v8_passA_summary.json", PDIR / "v8_passA_summary.json")
        (PDIR / "dataset-metadata.json").write_text(json.dumps({
            "title": DATASET.split("/")[1], "id": DATASET,
            "licenses": [{"name": "CC0-1.0"}]}, indent=2), encoding="utf-8")
        if has_ds:
            rc, out = sh(["kaggle", "datasets", "version", "-p", str(PDIR), "-m",
                          f"{gate['version']} (data {s['data_end']})"])
        else:
            rc, out = sh(["kaggle", "datasets", "create", "-p", str(PDIR)])
        say(out)
        if rc != 0 or "error" in out.lower():
            die(f"Kupakia champion kumeshindwa:\n{out}")
        published = True
        md("### ✅ Champion mpya imechapishwa kwenye dataset `v8-champion`. Nightly inayofuata itaitumia.")
    else:
        failed = "; ".join(c["name"] for c in gate["failed"])
        md(f"### ⚠️ Gate imekataa. Champion ya zamani inabaki.\nSababu: {failed}")
        print(f"::warning::Retrain v8 imekataliwa na gate: {failed}")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    new = not LOG.exists()
    with open(LOG, "a", encoding="utf-8") as f:
        if new:
            f.write("utc,version,data_end,gate_ok,published,mean_skill,mean_ece,failed\n")
        failed = "|".join(c["name"].replace(",", " ") for c in gate["failed"])
        f.write(f"{datetime.datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')},{gate['version']},"
                f"{s['data_end']},{int(gate['accepted'])},{int(published)},"
                f"{s['mean_skill']:.4f},{s['mean_ece']:.4f},{failed}\n")


if __name__ == "__main__":
    main()