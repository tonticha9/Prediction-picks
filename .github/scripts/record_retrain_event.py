"""
.github/scripts/record_retrain_event.py

Inaandika tukio la retrain kwenye data/retrain_log.json (tarehe, revision, idadi ya masoko,
yaliyorudishwa) PALE TU toleo jipya la dataset lilipochapishwa na retrain_and_publish.py.
Market Analysis inatumia faili hii kujua retrain ilifanyika lini na kulinganisha kabla/baada.
Haichapishi chochote kwenye Kaggle; ikikosa cha kuandika, inamaliza kimya.
"""
import os
import sys
import json
import datetime

LOG = os.path.join('data', 'retrain_log.json')


def read_json(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return json.load(f)
    except Exception:
        return None


def find_file(root, name):
    for dp, _dn, fns in os.walk(root):
        if name in fns:
            return os.path.join(dp, name)
    return None


def main():
    if not os.path.isdir('publish_stage'):
        print("ℹ️ Hakuna kilichochapishwa mzunguko huu - hakuna tukio la kuandika.")
        return
    mode = (os.environ.get('MODE') or 'retrain').strip()
    man = read_json(os.path.join('publish_stage', 'MANIFEST.json')) or {}
    gate_path = find_file('kaggle_retrain_output', 'release_gate.json')
    gate = (read_json(gate_path) if gate_path else None) or {}
    reverted = gate.get('reverted') or []
    ev = {
        'date': datetime.datetime.utcnow().strftime('%Y-%m-%d'),
        'type': 'rollback' if mode == 'rollback' else 'retrain',
        'revision': man.get('bundle_revision'),
        'n_markets': man.get('n_markets'),
        'reverted': reverted,
        'note': (f"retrain ya kiotomatiki: masoko {man.get('n_markets')}, "
                 f"{len(reverted)} yalirudishwa kwa utulivu") if mode != 'rollback'
                else 'rollback: toleo la awali limerudishwa',
    }
    log = read_json(LOG)
    log = log if isinstance(log, list) else []
    log = [e for e in log if not (isinstance(e, dict) and e.get('date') == ev['date']
                                  and e.get('type') == ev['type'])]
    log.append(ev)
    log = log[-100:]
    os.makedirs('data', exist_ok=True)
    with open(LOG, 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False, indent=1)
    print(f"✅ Tukio limeandikwa: {ev['type']} {ev['date']} (masoko {ev['n_markets']})")


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"::warning::Kuandika tukio la retrain kumeshindwa ({type(e).__name__}: {e}) - haiathiri chochote.")
    sys.exit(0)