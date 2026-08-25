"""BƯỚC 3c — SINH VIÊN VIẾT. Tự động hoá runbook §4 "Runbook: Region Chính Down".

7 bước trên slide, mỗi bước 1 dòng log có ts. Log này CHÍNH LÀ timeline của postmortem.
  1 xac_nhan_outage          — probe cả 2 region, đừng tin 1 lần fail (dùng nhiều lần
                              hoặc gọi health_checker.probe nếu đã viết xong 3a)
  2 thong_bao_incident       — ts của dòng này là mốc "operator biết tin", LUÔN LUÔN
                              SAU t_outage trong chaos-events (không thể trùng — operator
                              không thể biết ngay giây outage xảy ra). Ghi cả 2 ts vào
                              log để postmortem tính được "độ trễ thông báo".
  3 scale_gpu_pool           — gọi HÀM `failover.failover(...)` MỘT LẦN DUY NHẤT. Hàm
                              đó tự làm đủ 5 bước con (verify/restore/scale/wait/cutover)
                              và tự ghi log riêng vào reports/failover-events.jsonl.
  4 verify_state_replica     — KHÔNG gọi lại failover — chỉ ĐỌC kết quả (vector count +
                              weights ở region phụ) từ dict mà bước 3 trả về, để log vào
                              runbook-run.jsonl cho postmortem đọc 1 chỗ duy nhất.
  5 dns_cutover              — cũng chỉ đọc lại: kết quả cutover có ok hay không.
  6 verify_golden_signals    — 10 request thật vào region phụ: p95 latency + error rate
  7 post_incident            — elapsed_s + lệnh đo RTO

BÁN TỰ ĐỘNG, KHÔNG FULL-AUTO (§4: "failover đầu tiên nên là bán tự động — alert +
1-click confirm — tránh flapping gây failover 2 chiều liên tục"). Mặc định phải hỏi
người vận hành confirm; --auto chỉ dùng trong CI/khi chấm điểm.

Chạy:  python dr/runbook.py --primary a --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from dr import failover as fo  # noqa: E402

LOG = pathlib.Path("reports/runbook-run.jsonl")
CHAOS_LOG = pathlib.Path("chaos/chaos-events.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def step(n, name, **kw):
    """TODO: ghi 1 dòng {ts, iso, step, name, ...} vào LOG."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    event = {"ts": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
             "step": n, "name": name, **kw}
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")
    return event


def confirm(auto: bool, msg: str) -> bool:
    """TODO: auto=True -> True; ngược lại hỏi y/N. Đừng bỏ hàm này đi."""
    return True if auto else input(f"{msg} [y/N] ").strip().lower() == "y"


def _jsonl(path: pathlib.Path) -> list[dict]:
    """Đọc các JSON object hợp lệ từ log append-only."""
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _current_outage(primary: str) -> dict | None:
    """Trả về outage đang hoạt động gần nhất của primary trong chaos log."""
    for event in reversed(_jsonl(CHAOS_LOG)):
        if event.get("region") != primary or event.get("action") not in {"kill", "restore"}:
            continue
        return event if event["action"] == "kill" else None
    return None


def _incident_already_announced(primary: str, outage_ts: float) -> bool:
    """Không chạy failover lần hai cho cùng một outage."""
    for event in _jsonl(LOG):
        if event.get("name") != "thong_bao_incident" or event.get("primary") != primary:
            continue
        recorded_outage = event.get("outage_ts")
        if recorded_outage is not None and abs(float(recorded_outage) - outage_ts) < 0.001:
            return True
        # Tương thích log cũ chưa có outage_ts: detection thuộc outage này nghĩa là incident đã chạy.
        detected_ts = event.get("health_detected_ts")
        if recorded_outage is None and detected_ts is not None and float(detected_ts) >= outage_ts:
            return True
    return False


def _health_detection(primary: str, outage_ts: float, timeout: float = 90.0) -> dict | None:
    """Chờ detection của đúng region và xảy ra sau outage hiện tại."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for event in reversed(_jsonl(pathlib.Path("reports/health-events.jsonl"))):
            if event.get("event") == "state_change" and event.get("region") == primary \
                    and event.get("to") == "UNHEALTHY" and event.get("ts", 0) >= outage_ts:
                return event
        time.sleep(0.25)
    return None


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    """TODO: 7 bước ở trên."""
    if primary == target or primary not in URL or target not in URL:
        raise ValueError("primary va target phai la hai region khac nhau")
    observations = []
    for _ in range(3):
        try:
            response = httpx.get(f"{URL[primary]}/readyz", timeout=2.0)
            observations.append(response.status_code == 200)
        except httpx.HTTPError:
            observations.append(False)
    step(1, "xac_nhan_outage", primary=primary, target=target,
         probes_ready=observations, outage_confirmed=not any(observations))
    if any(observations) or not confirm(auto, f"Xac nhan failover {primary} -> {target}?"):
        return {"ok": False, "error": "outage_chua_duoc_xac_nhan_hoac_operator_tu_choi"}

    outage = _current_outage(primary)
    if not outage:
        return {"ok": False, "error": "khong_tim_thay_outage_hien_tai_trong_chaos_log"}
    outage_ts = float(outage["ts"])
    if _incident_already_announced(primary, outage_ts):
        return {"ok": False, "error": "incident_nay_da_duoc_xu_ly"}

    detected = _health_detection(primary, outage_ts)
    if not detected:
        return {"ok": False, "error": "health_checker_chua_phat_hien_outage_hien_tai"}
    step(2, "thong_bao_incident", primary=primary, target=target,
         outage_ts=outage_ts, outage_iso=outage.get("iso"),
         health_detected_ts=detected["ts"],
         outage_to_detection_s=round(detected["ts"] - outage_ts, 2),
         notification_delay_s=round(time.time() - outage_ts, 2))

    outcome = fo.failover(target, backend, wait=60.0)
    step(3, "scale_gpu_pool", failover_ok=outcome.get("ok"), failover_result=outcome)
    target_state = outcome.get("target_state", outcome.get("restored", {}))
    step(4, "verify_state_replica", target=target, state=target_state,
         docs_lost=outcome.get("docs_lost"), rpo_seconds=outcome.get("rpo_seconds"))
    step(5, "dns_cutover", target=target, ok=outcome.get("cutover", False))
    if not outcome.get("ok"):
        step(7, "post_incident", ok=False, error=outcome.get("error"))
        return outcome

    samples = []
    for _ in range(10):
        started = time.time()
        try:
            response = httpx.get(f"{URL[target]}/v1/infer", timeout=3.0)
            samples.append((response.status_code == 200, (time.time() - started) * 1000))
        except httpx.HTTPError:
            samples.append((False, (time.time() - started) * 1000))
    latencies = sorted(latency for _, latency in samples)
    p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
    error_rate = sum(1 for ok, _ in samples if not ok) / len(samples)
    step(6, "verify_golden_signals", requests=len(samples), p95_latency_ms=round(p95, 1),
         error_rate=error_rate)
    step(7, "post_incident", ok=True, elapsed_s=round(time.time() - outage_ts, 2),
         rto_command="python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300")
    return {**outcome, "golden_signals": {"p95_latency_ms": round(p95, 1), "error_rate": error_rate}}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--primary", default="a")
    p.add_argument("--target", default="b")
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--auto", action="store_true")
    a = p.parse_args()
    print(json.dumps(run(a.primary, a.target, a.backend, a.auto), indent=2))
