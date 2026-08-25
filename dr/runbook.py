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
from dr.health_checker import probe  # noqa: E402

LOG = pathlib.Path("reports/runbook-run.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def step(n, name, **kw):
    """Append one timestamped runbook step."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.time(),
           "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "step": n, "name": name, **kw}
    with LOG.open("a", encoding="utf-8") as log:
        log.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False), flush=True)
    return rec


def confirm(auto: bool, msg: str) -> bool:
    """Keep the normal path semi-automatic; CI may opt in with --auto."""
    if auto:
        return True
    return input(f"{msg} [y/N] ").strip().lower() in {"y", "yes"}


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    """Execute the seven-step operator workflow exactly once."""
    if primary == target or primary not in URL or target not in URL:
        raise ValueError("primary and target must be distinct known regions")

    started = time.time()
    checks = []
    for attempt in range(3):
        primary_ready, primary_reason = probe(primary, 2.0)
        target_alive = False
        try:
            target_alive = httpx.get(f"{URL[target]}/healthz", timeout=1.0).status_code == 200
        except httpx.HTTPError:
            pass
        checks.append({"attempt": attempt + 1, "primary_ready": primary_ready,
                       "primary_reason": primary_reason, "target_alive": target_alive})
        if attempt < 2:
            time.sleep(2.0)
    outage_confirmed = all(not item["primary_ready"] for item in checks)
    target_alive = all(item["target_alive"] for item in checks)
    step(1, "xac_nhan_outage", primary=primary, target=target,
         outage_confirmed=outage_confirmed, target_alive=target_alive, checks=checks)
    if not outage_confirmed or not target_alive:
        return {"ok": False, "failed_step": 1, "reason": "outage_not_confirmed_or_target_down"}

    if not confirm(auto, f"Region {primary} đã fail 3 lần; failover sang {target}?"):
        step(2, "thong_bao_incident", confirmed=False, primary=primary, target=target)
        return {"ok": False, "failed_step": 2, "reason": "operator_cancelled"}

    incident = step(2, "thong_bao_incident", confirmed=True, primary=primary,
                    target=target, operator_delay_s=round(time.time() - started, 2))

    # The operator confirmation and the monitoring alert are independent signals.
    # Do not begin an automated cutover until the anti-flap health checker has
    # emitted its threshold-backed UNHEALTHY transition for the primary.
    health_log = pathlib.Path("reports/health-events.jsonl")
    alert_deadline = time.monotonic() + 30.0
    alert = None
    while time.monotonic() < alert_deadline:
        if health_log.exists():
            for line in health_log.read_text().splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if (event.get("event") == "state_change"
                        and event.get("region") == primary
                        and event.get("to") == "UNHEALTHY"
                        and event.get("ts", 0) >= started):
                    alert = event
                    break
        if alert:
            break
        time.sleep(0.25)
    if alert is None:
        step(7, "post_incident", ok=False, elapsed_s=round(time.time() - started, 2),
             reason="health_checker_alert_timeout")
        return {"ok": False, "failed_step": 2, "reason": "health_checker_alert_timeout"}

    result = fo.failover(target, backend, wait=60.0)
    step(3, "scale_gpu_pool", failover_called_once=True, failover_ok=result.get("ok", False))
    if not result.get("ok"):
        step(7, "post_incident", ok=False, elapsed_s=round(time.time() - started, 2),
             failed_step=result.get("failed_step"), reason=result.get("reason"))
        return result

    state = result.get("state", {})
    step(4, "verify_state_replica", target=target, weights=bool(state),
         vector_count=state.get("vectors", {}).get("count"),
         embed_model_version=result.get("restored", {}).get("embed_model_version"),
         rpo_seconds=result.get("rpo", {}).get("rpo_seconds"),
         docs_lost=result.get("rpo", {}).get("docs_lost"))
    step(5, "dns_cutover", ok=result.get("active_region") == target,
         active_region=result.get("active_region"))

    latencies = []
    errors = 0
    served_by = []
    for _ in range(10):
        t0 = time.monotonic()
        try:
            response = httpx.get(f"{URL[target]}/v1/infer", timeout=3.0)
            body = response.json()
            if response.status_code != 200 or body.get("region") != target:
                errors += 1
            served_by.append(body.get("region"))
        except (httpx.HTTPError, ValueError):
            errors += 1
        latencies.append((time.monotonic() - t0) * 1000)
    ordered = sorted(latencies)
    p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
    step(6, "verify_golden_signals", requests=10,
         p95_latency_ms=round(p95, 2), error_rate=round(errors / 10, 2),
         served_by=served_by, ok=errors == 0)

    elapsed = round(time.time() - started, 2)
    command = ("python3 tools/measure_rto.py --loadgen "
               "reports/drill-2-withdr.jsonl --target-rto 300")
    step(7, "post_incident", ok=errors == 0, elapsed_s=elapsed,
         incident_ts=incident["ts"], measure_command=command)
    return {"ok": errors == 0, "target": target, "elapsed_s": elapsed,
            "p95_latency_ms": round(p95, 2), "error_rate": round(errors / 10, 2),
            "failover": result}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--primary", default="a")
    p.add_argument("--target", default="b")
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--auto", action="store_true")
    a = p.parse_args()
    print(json.dumps(run(a.primary, a.target, a.backend, a.auto), indent=2))
