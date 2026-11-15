#!/usr/bin/env python3
"""Exact decomposition of GitHub Trending daily rank movement.

For every repository retained across two consecutive snapshots the observed rank
delta is split into exactly two additive parts:

    delta_rank = churn_effect + peer_effect

churn_effect = debuts above it today - exits above it yesterday
peer_effect  = change in the number of *surviving* peers ranked above it

A climb (rank improves) whose peer_effect >= 0 is a "carried climb": the repo did
not gain ground against the repositories that survived with it; it only moved up
because other repositories left the list or because new entries landed below it.

The tool also measures whether the captured order can be rebuilt from the
`stars_period` field at all (adjacent inversion rate + Spearman rho).

Zero dependencies, offline, deterministic.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2


def load_snapshots(paths):
    docs = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as exc:
            raise SystemExit("cannot read %s: %s" % (path, exc))
        if not isinstance(doc, dict) or "lists" not in doc or "date" not in doc:
            raise ValueError("%s: not a trending snapshot (needs date + lists)" % path)
        daily = (doc.get("lists") or {}).get("daily")
        if not isinstance(daily, list) or len(daily) < 2:
            raise ValueError("%s: daily list missing or too short" % path)
        rows = []
        for item in daily:
            repo = item.get("repo")
            period = item.get("stars_period")
            if not isinstance(repo, str) or not repo:
                raise ValueError("%s: entry without repo name" % path)
            if not isinstance(period, int) or isinstance(period, bool):
                raise ValueError("%s: %s has non-integer stars_period" % (path, repo))
            rows.append({"repo": repo, "period": period,
                         "total": item.get("stars_total")})
        docs.append({"date": doc["date"], "rows": rows})
    docs.sort(key=lambda d: d["date"])
    dates = [d["date"] for d in docs]
    if len(set(dates)) != len(dates):
        raise ValueError("duplicate snapshot dates: %s" % dates)
    return docs


def collect_inputs(targets):
    paths = []
    for target in targets:
        if os.path.isdir(target):
            for cand in sorted(glob.glob(os.path.join(target, "*.json"))):
                stem = os.path.splitext(os.path.basename(cand))[0]
                if re.match(r"^\d{4}-\d{2}-\d{2}$", stem):
                    paths.append(cand)
        elif os.path.isfile(target):
            paths.append(target)
        else:
            raise SystemExit("no such file or directory: %s" % target)
    # latest.json is a copy of the newest day; drop it to avoid a duplicate date.
    paths = [p for p in paths if os.path.basename(p) != "latest.json"]
    if len(paths) < 2:
        raise ValueError("need at least two snapshots, got %d" % len(paths))
    return paths


def inversion_stats(docs):
    pairs = inversions = 0
    for doc in docs:
        rows = doc["rows"]
        for a, b in zip(rows, rows[1:]):
            pairs += 1
            if a["period"] < b["period"]:
                inversions += 1
    return {"adjacent_pairs": pairs, "inversions": inversions,
            "inversion_rate": round(inversions / pairs, 6) if pairs else None}


def average_ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(periods):
    n = len(periods)
    if n < 2:
        return None
    x = average_ranks(periods)
    y = average_ranks(list(range(n)))  # position rank, ascending position
    mx, my = sum(x) / n, sum(y) / n
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    dx = sum((a - mx) ** 2 for a in x) ** 0.5
    dy = sum((b - my) ** 2 for b in y) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return round(num / (dx * dy), 6)


def decompose(docs):
    transitions = []
    all_carried = []
    totals = {"retained_pairs": 0, "climbs": 0, "carried_climbs": 0,
              "churn_effect_sum": 0, "peer_effect_sum": 0}
    for prev, cur in zip(docs, docs[1:]):
        pos_prev = {r["repo"]: i for i, r in enumerate(prev["rows"])}
        pos_cur = {r["repo"]: i for i, r in enumerate(cur["rows"])}
        exits = [p for p in pos_prev if p not in pos_cur]
        debuts = [p for p in pos_cur if p not in pos_prev]
        retained = [p for p in pos_cur if p in pos_prev]
        items = []
        for repo in retained:
            rp, rc = pos_prev[repo], pos_cur[repo]
            exits_above = sum(1 for p in exits if pos_prev[p] < rp)
            debuts_above = sum(1 for p in debuts if pos_cur[p] < rc)
            churn = debuts_above - exits_above
            peer = (rc - debuts_above) - (rp - exits_above)
            assert churn + peer == rc - rp, (repo, churn, peer)
            climb = rc < rp
            carried = bool(climb and peer >= 0)
            rec = {"repo": repo, "rank_prev": rp, "rank_cur": rc,
                   "delta": rc - rp, "churn_effect": churn,
                   "peer_effect": peer, "climb": climb, "carried_climb": carried}
            items.append(rec)
            if carried:
                all_carried.append(dict(rec, date_prev=prev["date"], date_cur=cur["date"]))
        t_climbs = sum(1 for i in items if i["climb"])
        t_carried = sum(1 for i in items if i["carried_climb"])
        transitions.append({
            "date_prev": prev["date"], "date_cur": cur["date"],
            "size_prev": len(prev["rows"]), "size_cur": len(cur["rows"]),
            "retained": len(items), "exits": len(exits), "debuts": len(debuts),
            "climbs": t_climbs, "carried_climbs": t_carried,
            "carried_climb_rate": round(t_carried / t_climbs, 6) if t_climbs else None,
            "carried_repos": [i["repo"] for i in items if i["carried_climb"]],
        })
        totals["retained_pairs"] += len(items)
        totals["climbs"] += t_climbs
        totals["carried_climbs"] += t_carried
        totals["churn_effect_sum"] += sum(i["churn_effect"] for i in items)
        totals["peer_effect_sum"] += sum(i["peer_effect"] for i in items)

    rate = (totals["carried_climbs"] / totals["climbs"]) if totals["climbs"] else None
    order = inversion_stats(docs)
    order["spearman_overall"] = spearman([r["period"] for d in docs for r in d["rows"]])
    order["per_snapshot_spearman"] = [
        {"date": d["date"], "spearman": spearman([r["period"] for r in d["rows"]])}
        for d in docs
    ]
    all_carried.sort(key=lambda r: (-r["delta"], r["date_cur"], r["repo"]))
    return {
        "snapshots": len(docs),
        "date_first": docs[0]["date"],
        "date_last": docs[-1]["date"],
        "transitions_count": len(transitions),
        "totals": totals,
        "carried_climb_rate": round(rate, 6) if rate is not None else None,
        "order": order,
        "transitions": transitions,
        "carried_climbs": all_carried,
    }, all_carried


def fingerprint(payload):
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def render(report, top):
    t = report["totals"]
    o = report["order"]
    out = []
    out.append("snapshots  %d (%s .. %s)" % (report["snapshots"], report["date_first"], report["date_last"]))
    out.append("transitions %d  retained pairs %d" % (report["transitions_count"], t["retained_pairs"]))
    rate = report["carried_climb_rate"]
    out.append("climbs %d  carried climbs %d  carried rate %s" % (
        t["climbs"], t["carried_climbs"], "n/a" if rate is None else "%.4f" % rate))
    out.append("churn effect sum %+d  peer effect sum %+d" % (t["churn_effect_sum"], t["peer_effect_sum"]))
    out.append("adjacent pairs %d  stars_period inversions %d (%.2f%%)  spearman %s" % (
        o["adjacent_pairs"], o["inversions"], 100.0 * o["inversion_rate"],
        o["spearman_overall"]))
    if top and report["carried_climbs"]:
        out.append("")
        out.append("%-40s %-10s %5s %5s %5s" % ("carried climb", "date", "prev", "cur", "peer"))
        for rec in report["carried_climbs"][:top]:
            out.append("%-40s %-10s %5d %5d %5d" % (
                rec["repo"][:40], rec["date_cur"], rec["rank_prev"], rec["rank_cur"], rec["peer_effect"]))
    elif top:
        out.append("no carried climbs in this window")
    out.append("")
    out.append("replay_sha256 %s" % report["replay_sha256"])
    return "\n".join(out)


def main(argv):
    parser = argparse.ArgumentParser(
        prog="trending-rank-decompose",
        description="Exact additive decomposition of GitHub Trending rank movement "
                    "into churn effect and peer effect.")
    parser.add_argument("targets", nargs="+", help="snapshot JSON file or archive directory")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--top", type=int, default=10, help="carried climbs to print (0 = none)")
    parser.add_argument("--max-carried-rate", type=float, default=0.25,
                        help="exit 1 when the overall carried climb rate exceeds this (default 0.25)")
    args = parser.parse_args(argv)
    if args.top < 0:
        parser.error("--top must be >= 0")
    if not (0.0 <= args.max_carried_rate <= 1.0):
        parser.error("--max-carried-rate must be within [0, 1]")
    try:
        docs = load_snapshots(collect_inputs(args.targets))
        report, _ = decompose(docs)
    except (ValueError, SystemExit) as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, ensure_ascii=True))
        else:
            print("error: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    report["replay_sha256"] = fingerprint({k: v for k, v in report.items() if k != "replay_sha256"})
    rate = report["carried_climb_rate"] or 0.0
    findings = rate > args.max_carried_rate
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True))
    else:
        print(render(report, args.top))
        if findings:
            print("\nverdict: carried climb rate %.4f > %.4f" % (rate, args.max_carried_rate))
    return EXIT_FINDINGS if findings else EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
