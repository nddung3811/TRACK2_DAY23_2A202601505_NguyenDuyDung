# Runbook — Region chính down

Phạm vi: chỉ phục vụ local `127.0.0.1`. On-call không được chuyển traffic nếu Region phụ chưa `/readyz` 200. Chỉ lệnh ở bước 2 kích hoạt orchestration; các bước 3-6 đọc kết quả để xác minh, không gọi lại restore, failover hoặc runbook.

| # | Bước | Lệnh | Biết là xong khi | Ai làm |
|---|---|---|---|---|
| 1 | Xác nhận outage | `for i in 1 2 3; do python chaos/kill_region.py status; sleep 1; done` | Region A `ready:false` cả 3 lần; Region B vẫn `alive:true`, `ready:true` | on-call |
| 2 | Mở incident, bấm giờ RTO và chạy orchestration một lần | `python dr/runbook.py --primary a --target b --backend fs` | xác nhận `y`; tiến trình kết thúc với `"ok": true` và chỉ một event `thong_bao_incident` cho outage hiện tại | incident commander |
| 3 | Xác minh restore state ở Region B | `python -c "import json,pathlib; e=[json.loads(x) for x in pathlib.Path('reports/runbook-run.jsonl').read_text().splitlines()]; print(next(x for x in reversed(e) if x.get('name')=='scale_gpu_pool'))"` | `failover_ok:true`; `failover_result` có `restored`, `rpo_seconds`, `docs_lost` và `embed_model_version` | DR operator |
| 4 | Xác minh pool đã scale và Region B ready | `python -c "import json,pathlib; e=[json.loads(x) for x in pathlib.Path('reports/runbook-run.jsonl').read_text().splitlines()]; print(next(x for x in reversed(e) if x.get('name')=='verify_state_replica'))"` | state cho thấy `pool_state:full`, `weights:true` và vector count lớn hơn 0 | platform engineer |
| 5 | Xác minh DNS/LB đã cutover | `curl http://127.0.0.1:8080/edge/state` | response có `"active_region":"b"`; cutover chỉ xảy ra sau `4_wait_ready` | incident commander |
| 6 | Xác minh golden signals | `python -c "import json,pathlib; e=[json.loads(x) for x in pathlib.Path('reports/runbook-run.jsonl').read_text().splitlines()]; print(next(x for x in reversed(e) if x.get('name')=='verify_golden_signals'))"` | event ghi 10 request thật, `error_rate:0.0` và `p95_latency_ms` | on-call |
| 7 | Đo RTO + postmortem | `python tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300` | `valid:true`, `warnings:[]`, `rto_verdict:PASS` | incident commander |

Mặc định runbook hỏi xác nhận `y/N`; chỉ dùng `--auto` trong CI hoặc drill có phê duyệt.

## Rollback (failover ngược)

Chỉ trả traffic về Region A khi Region A đã được khôi phục, `/readyz` trả 200 liên tiếp trong 5 phút, state đã đồng bộ lại và incident commander chấp thuận. Nếu Region B có error rate vượt 1% trong 5 phút hoặc p95 vượt SLO đã công bố, on-call escalates cho incident commander; incident commander là người duy nhất có quyền kích hoạt rollback. Không thực hiện rollback tự động để tránh flap hai chiều.
