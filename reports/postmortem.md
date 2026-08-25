# Postmortem - DR Drill Lab 23

## 1. Timeline

| ISO time | Event | Evidence |
|---|---|---|
| 2026-08-25T13:23:14Z | Region A outage begins | `chaos/chaos-events.jsonl:4` |
| 2026-08-25T13:23:14Z + 1.6s | First user impact | `reports/drill-2-withdr.jsonl:12` |
| 2026-08-25T13:23:29Z | Health check alerts Region A UNHEALTHY | `reports/health-events.jsonl:2` |
| 2026-08-25T13:23:29Z | Operator confirms cutover | `reports/runbook-run.jsonl:2` |
| 2026-08-25T13:23:42Z | First request OK from Region B; incident resolved | `reports/drill-2-withdr.jsonl:22` |

## 2. RTO/RPO versus target - where is the gap?

- RTO target: 300s; measured: 28.7s; gap: 271.3s better than target.
- RPO target: 300s; measured: 8.0s (4 documents lost); gap: 292.0s better than target.
- Longest step: health-check detection at 14.8s. This is a consequence of interval 5.0s and threshold 3, not an individual operator error.

## 3. Root cause - five whys

1. Users saw errors because Region A stopped returning inference while edge still cached Region A.
2. Edge did not switch immediately because three failed probes are required to reject transient failures.
3. Region B could not serve immediately because it started without weights/vector DB and with pool state `warm`.
4. Region B recovered because replication produced a snapshot before outage; failover restored, scaled, and checked readiness in order.
5. RTO remained 28.7s because detection floor, GPU warm-up, and DNS TTL are real system delays.

## 4. Action items

| # | Action item | Owner | Deadline | Expected RTO/RPO reduction |
|---|---|---|---|---|
| 1 | Evaluate 3s interval with threshold 3 in five chaos drills | SRE | 2026-09-01 | About 6s RTO; higher probe and flap risk |
| 2 | Keep Region B warm pool and measure cost | Platform | 2026-09-08 | About 8s RTO |
| 3 | Reduce replication interval from 30s to 15s | Data platform | 2026-09-08 | RPO maximum about 15s |

## 5. Required questions

1. `interval x threshold` is 5.0s x 3 = 15.0s, around 52.3% of the 28.7s RTO.
2. Reducing interval to 1s while keeping threshold 3 could reduce the detection floor by about 12s, but increases probes, false-positive risk, and flapping risk; run chaos tests first.
3. With a six-hour outage and permanent loss of Region A, `docs_lost=4` means four documents ingested after the latest snapshot cannot be recovered from the replica. Identify affected customers/events and replay or notify as appropriate.
