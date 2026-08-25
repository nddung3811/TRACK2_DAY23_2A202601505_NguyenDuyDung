# RTO/RPO Evidence - Lab 23

All figures below were measured from the local drill on 2026-08-25, not copied from GUIDE.

## 1. Drill 1 - no DR baseline

| Metric | Value | Measurement | Evidence |
|---|---:|---|---|
| t_outage | 2026-08-25T13:21:18Z | chaos killed Region A | `chaos/chaos-events.jsonl:1` |
| First failed request | +2.2s | first `ok:false` after t_outage | `reports/drill-1-nodr.jsonl:14` |
| Later successful request | None | no `ok:true` after outage | `reports/measure-drill-1.json:25` |
| RTO | NO_RECOVERY | `tools/measure_rto.py` | `reports/measure-drill-1.json:25` |

The baseline had 12 failed requests and no recovery until manual restoration of Region A.

## 2. Drill 2 - with DR

| Milestone | Seconds from t_outage | Measurement | Evidence |
|---|---:|---|---|
| t_outage | 0.0s | `action:kill` | `chaos/chaos-events.jsonl:4` |
| User first error | 1.6s | first `ok:false` | `reports/drill-2-withdr.jsonl:12` |
| Health check detects outage | 14.8s | `to:UNHEALTHY, region:a` | `reports/health-events.jsonl:2` |
| Snapshot restore complete | 16.7s | `step:2_restore_snapshot` | `reports/failover-events.jsonl:2` |
| Secondary region ready | 25.1s | `step:4_wait_ready` | `reports/failover-events.jsonl:4` |
| DNS cutover | 25.1s | `step:5_dns_cutover` | `reports/failover-events.jsonl:5` |
| **Measured RTO** | **28.7s** | first `ok:true` from Region B | `reports/drill-2-withdr.jsonl:22` |

| Metric | Measured | Target | Verdict |
|---|---:|---:|---|
| RTO - Inference API | 28.7s | 300s | PASS |
| RPO - Vector DB | 8.0s / 4 documents | 300s | PASS |

The measurement confirms a valid drill with no warnings and recovery served by Region B: `reports/measure-drill-2.json:2`.

## 3. RTO breakdown

| Component | Seconds | Source | Reduction option |
|---|---:|---|---|
| Health-check detection | 14.8s | actual polling; config is 5.0s x 3 = 15.0s | lower interval/threshold with flap controls |
| Snapshot restore plus orchestration | 1.9s | detection to `2_restore_snapshot` | automate alert-to-runbook and reduce snapshot size |
| GPU pool warm-up | 8.4s | `waited_s` at `4_wait_ready` | keep a warm pool or optimize warm-up |
| DNS/LB TTL cache | 3.6s | 28.7s recovery minus 25.1s cutover | lower TTL and use suitable retry/backoff |

The total is approximately 28.7s because every timeline milestone is rounded to one decimal place. Health-check config is recorded in `reports/health-events.jsonl:2`; RPO 8.0s and 4 lost documents are recorded in `reports/failover-events.jsonl:2`.
