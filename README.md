# trending-rank-decompose

Splits every GitHub Trending rank move into **two exact, additive parts** — churn and peer effect — so you can tell an earned climb from a *carried* one. Needed now because "trending rose 5 places" is the most quoted and least verified number in the dataset ecosystem.

Upstream: https://github.com/linmoxi33/github-trending-dataset

## What the upstream is, and what it lacks

`github-trending-dataset` archives 28 daily snapshots (2026-10-19 .. 2026-11-15) with `repo`, `stars_period`, `stars_total` and the captured order. Its reader exposes exactly five commands — `stats`, `top`, `timeline`, `diff`, `windows` ([query.py L595-L621](https://github.com/linmoxi33/github-trending-dataset/blob/de4a06f1fba32cfcbf4b2a334cd8a7abf8f1bcff/scripts/query.py#L595-L621)) and the README advertises "coverage, rankings, timelines, day-to-day diffs" ([README L20](https://github.com/linmoxi33/github-trending-dataset/blob/de4a06f1fba32cfcbf4b2a334cd8a7abf8f1bcff/README.md#L20)). None of them decomposes a rank change, and none checks whether the captured order is reproducible from `stars_period` at all. So the archive can tell you a repo moved, but not **why**, and cannot tell you whether "moved up" means "beat its peers" or "its peers left".

## What this tool does

For each pair of consecutive snapshots it computes, for every retained repo:

```
delta_rank   = churn_effect + peer_effect
churn_effect = debuts ranked above it today  -  exits ranked above it yesterday
peer_effect  = change in surviving peers ranked above it
```

The identity is asserted item by item. A **climb** (`delta_rank < 0`) with `peer_effect >= 0` is a *carried climb*: the repo gained places without gaining ground on any repo that survived with it. The tool also reports the adjacent-pair inversion rate and Spearman rho between captured position and `stars_period`.

It does **not** model GitHub's ranking formula (the data shows it is not `stars_period`), does not predict tomorrow, and does not judge whether a repo "deserves" its place.

## Install / run

Python 3.8+, no dependencies, no network:

```bash
python3 trending_rank_decompose.py <archive-dir-or-snapshot.json...> [--json] [--top 10] [--max-carried-rate 0.25]
./bin/trending-rank-decompose examples/in
```

`examples/in` holds two real snapshots (2026-11-14, 2026-11-15); `examples/out/report.json` is the exact `--json` output.

## Result on the full real archive (28 snapshots, 27 transitions)

```
snapshots 28 (2026-10-19 .. 2026-11-15)   retained pairs 172
climbs 79   carried climbs 13   carried rate 0.1646
adjacent pairs 332   stars_period inversions 156 (46.99%)   spearman -0.186657
replay_sha256 12995266bacecf5cda12f2c99b3e5add17122022c1169826ae2f1d46ad07a863
```

Measured facts, not estimates: **47.0% of adjacent pairs have `stars_period` increasing down the list**, and the overall position/period Spearman is only **-0.187**. The captured order is therefore not reconstructible from the dataset's own star field, which is why churn/peer decomposition — not a period-implied counterfactual — is the only sound way to attribute movement here. 13 of 79 climbs (16.5%) were carried, e.g. `ranxianglei/billion-context` rising 13→8 with `peer_effect +1`.

## JSON output and exit codes

`--json` prints one object: `snapshots`, `transitions_count`, `totals`, `carried_climb_rate`, `order` (inversions, Spearman, per-snapshot), `transitions`, `carried_climbs`, `replay_sha256` (SHA-256 of the canonical payload, stable across runs).

| exit | meaning |
| --- | --- |
| 0 | report produced, carried climb rate ≤ `--max-carried-rate` |
| 1 | carried climb rate above the threshold (finding) |
| 2 | usage error, unreadable/invalid snapshot, fewer than 2 snapshots |

## 本轮创新

First tool to decompose Trending rank movement into exact churn and survivor-relative components and to publish a carried-climb rate (16.5% over 172 real retained pairs), plus the first measured evidence that the archive's order is not `stars_period`-sorted (46.99% inversions, rho -0.187). The ledger's prior work predicts tomorrow (`forecast-bench`) or audits a single repo; this explains *today's* movement and falsifies an assumption the dataset's own `top` command implies.

## Limits

- Rank is the captured 0-based position; ties are impossible in a list, so no tie handling is needed.
- Only the `daily` list is read; `weekly`/`monthly` ordering is ignored.
- `peer_effect` is a *relative-position* quantity, not a causal star effect.
- The tool trusts `stars_period` as an integer; snapshots with missing fields are rejected as usage errors.
- Windows shorter than two snapshots cannot be analysed by construction.
