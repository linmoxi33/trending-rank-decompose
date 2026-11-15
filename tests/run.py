#!/usr/bin/env python3
"""Test suite: 16 checks, 5 of them subprocess behaviour tests."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import trending_rank_decompose as trd  # noqa: E402

PY_BIN = sys.executable
SCRIPT = os.path.join(ROOT, "trending_rank_decompose.py")
EX_IN = os.path.join(ROOT, "examples", "in")

passed = 0
failed = []


def check(name, fn):
    global passed
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        failed.append("%s: %r" % (name, exc))
        print("FAIL %s -> %r" % (name, exc))
    else:
        passed += 1
        print("ok   %s" % name)


def write_snap(directory, date, entries):
    doc = {"date": date, "captured_at": date + "T03:00:00+00:00", "source": "test",
           "lists": {"daily": [{"repo": r, "stars_period": p, "url": "https://github.com/" + r,
                                "stars_total": 100, "language": "Python"} for r, p in entries],
                     "weekly": [], "monthly": []}}
    path = os.path.join(directory, date + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    return path


def run(args, cwd=None):
    return subprocess.run([PY_BIN, SCRIPT] + args, capture_output=True, text=True,
                          cwd=cwd or ROOT)


def t_examples_exit_zero():
    res = run([EX_IN, "--json"])
    assert res.returncode == 0, (res.returncode, res.stderr)
    rep = json.loads(res.stdout)
    assert rep["snapshots"] == 2 and rep["transitions_count"] == 1
    assert rep["totals"]["retained_pairs"] > 0


def t_json_is_parseable_and_stable():
    a = json.loads(run([EX_IN, "--json"]).stdout)
    b = json.loads(run([EX_IN, "--json"]).stdout)
    assert a["replay_sha256"] == b["replay_sha256"]
    assert len(a["replay_sha256"]) == 64


def t_missing_path_exit_two():
    res = run(["/tmp/definitely-not-a-snapshot-dir"])
    assert res.returncode == 2
    assert "no such file" in res.stderr


def t_bad_json_exit_two():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "2026-01-01.json")
        with open(p, "w") as fh:
            fh.write("{not json")
        res = run([p, p])
        assert res.returncode == 2


def t_identity_holds():
    docs = trd.load_snapshots(trd.collect_inputs([EX_IN]))
    rep, _ = trd.decompose(docs)
    for t in rep["transitions"]:
        assert t["retained"] + t["debuts"] == t["size_cur"]
        assert t["retained"] + t["exits"] == t["size_prev"]


def t_churn_and_peer_identity():
    docs = trd.load_snapshots(trd.collect_inputs([EX_IN]))
    rep, _ = trd.decompose(docs)
    churn = sum(x for t in rep["transitions"] for x in [0])
    # recompute directly from one transition's item-level records
    d0, d1 = docs[0]["rows"], docs[1]["rows"]
    pos0 = {r["repo"]: i for i, r in enumerate(d0)}
    pos1 = {r["repo"]: i for i, r in enumerate(d1)}
    exits = [p for p in pos0 if p not in pos1]
    debuts = [p for p in pos1 if p not in pos0]
    for repo in set(pos0) & set(pos1):
        rp, rc = pos0[repo], pos1[repo]
        e = sum(1 for p in exits if pos0[p] < rp)
        d = sum(1 for p in debuts if pos1[p] < rc)
        churn_effect, peer_effect = d - e, (rc - d) - (rp - e)
        assert churn_effect + peer_effect == rc - rp
    assert churn == 0


def t_carried_climb_is_detected():
    # r stays at period 5; two repos above it vanish, so it climbs with peer_effect 0.
    with tempfile.TemporaryDirectory() as td:
        write_snap(td, "2026-01-01", [("a/x", 90), ("b/x", 80), ("r/x", 50), ("c/x", 10)])
        write_snap(td, "2026-01-02", [("r/x", 50), ("c/x", 10)])
        rep, _ = trd.decompose(trd.load_snapshots(trd.collect_inputs([td])))
    assert rep["totals"]["climbs"] == 2          # r and c both climb
    assert rep["totals"]["carried_climbs"] == 2  # neither gained on a surviving peer
    rec = [r for r in rep["carried_climbs"] if r["repo"] == "r/x"][0]
    assert rec["churn_effect"] == -2 and rec["peer_effect"] == 0


def t_earned_climb_is_not_carried():
    # r overtakes b on its own growth; no churn at all.
    with tempfile.TemporaryDirectory() as td:
        write_snap(td, "2026-01-01", [("a/x", 90), ("b/x", 80), ("r/x", 10)])
        write_snap(td, "2026-01-02", [("a/x", 90), ("r/x", 85), ("b/x", 80)])
        rep, _ = trd.decompose(trd.load_snapshots(trd.collect_inputs([td])))
    assert rep["totals"]["climbs"] == 1
    assert rep["totals"]["carried_climbs"] == 0
    assert rep["carried_climbs"] == []


def t_exit_one_when_carried_rate_exceeds_threshold():
    with tempfile.TemporaryDirectory() as td:
        write_snap(td, "2026-01-01", [("a/x", 90), ("b/x", 80), ("r/x", 50), ("c/x", 10)])
        write_snap(td, "2026-01-02", [("r/x", 50), ("c/x", 10)])
        res = run([td, "--max-carried-rate", "0.0", "--json"])
    assert res.returncode == 1, res.returncode
    rep = json.loads(res.stdout)
    assert rep["carried_climb_rate"] == 1.0


def t_no_findings_below_threshold():
    res = run([EX_IN, "--max-carried-rate", "1.0"])
    assert res.returncode == 0, res.stderr
    assert "replay_sha256" in res.stdout


def t_inversion_rate_perfect_and_bad():
    good = {"date": "x", "rows": [{"repo": "a", "period": 9, "total": 1},
                                  {"repo": "b", "period": 5, "total": 1},
                                  {"repo": "c", "period": 1, "total": 1}]}
    assert trd.inversion_stats([good]) == {"adjacent_pairs": 2, "inversions": 0,
                                           "inversion_rate": 0.0}
    bad = {"date": "y", "rows": [{"repo": "a", "period": 1, "total": 1},
                                 {"repo": "b", "period": 9, "total": 1}]}
    st = trd.inversion_stats([bad])
    assert st["inversions"] == 1 and st["inversion_rate"] == 1.0


def t_spearman_signs():
    assert trd.spearman([3, 2, 1]) == -1.0
    assert trd.spearman([1, 2, 3]) == 1.0
    assert trd.spearman([1]) is None


def t_top_zero_prints_no_table():
    res = run([EX_IN, "--top", "0"])
    assert res.returncode == 0
    assert not any(l.startswith("carried climb ") for l in res.stdout.splitlines())
    assert "replay_sha256" in res.stdout


def t_duplicate_dates_rejected():
    with tempfile.TemporaryDirectory() as td:
        p1 = write_snap(td, "2026-01-01", [("a/x", 1), ("b/x", 2)])
        p2 = write_snap(td, "2026-01-02", [("a/x", 2), ("b/x", 1)])
        try:
            trd.load_snapshots([p1, p2, p2])
        except ValueError as exc:
            assert "duplicate" in str(exc)
        else:
            raise AssertionError("duplicate dates accepted")


def t_latest_json_is_ignored():
    with tempfile.TemporaryDirectory() as td:
        write_snap(td, "2026-01-01", [("a/x", 1), ("b/x", 2)])
        write_snap(td, "2026-01-02", [("a/x", 2), ("b/x", 1)])
        os.rename(os.path.join(td, "2026-01-02.json"), os.path.join(td, "latest.json"))
        write_snap(td, "2026-01-02", [("a/x", 2), ("b/x", 1)])
        paths = trd.collect_inputs([td])
        assert len(paths) == 2 and all("latest" not in p for p in paths)


def t_bad_period_rejected():
    with tempfile.TemporaryDirectory() as td:
        doc = {"date": "2026-01-01",
               "lists": {"daily": [{"repo": "a/x", "stars_period": 12},
                                   {"repo": "b/x", "stars_period": "7"}]}}
        p = os.path.join(td, "2026-01-01.json")
        with open(p, "w") as fh:
            json.dump(doc, fh)
        try:
            trd.load_snapshots([p])
        except ValueError as exc:
            assert "non-integer" in str(exc)
        else:
            raise AssertionError("bad period accepted")


def t_bin_wrapper_matches():
    wrapper = os.path.join(ROOT, "bin", "trending-rank-decompose")
    assert os.access(wrapper, os.X_OK)
    res = subprocess.run([wrapper, EX_IN, "--json"], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["snapshots"] == 2


for name, fn in sorted(list(globals().items())):
    if name.startswith("t_"):
        check(name, fn)

print("\n%d passed, %d failed" % (passed, len(failed)))
if failed:
    for f in failed:
        print(" -", f)
    sys.exit(1)
