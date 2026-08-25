# Runbook — Region chính down

Phạm vi: bare mode local, primary A, standby B. Incident Commander (IC) duyệt failover/failback; on-call không sửa `edge/active_region` bằng tay.

| # | Bước | Lệnh copy-paste | Biết là xong khi | Owner |
|---|---|---|---|---|
| 1 | Xác nhận outage | `python3 chaos/kill_region.py status --backend bare` | A không ready qua 3 probe; B còn alive | SRE on-call |
| 2 | Mở incident, bấm giờ | `python3 dr/runbook.py --primary a --target b --backend fs` | Trả lời `y`; log có `thong_bao_incident` | SRE + IC |
| 3 | Restore và scale | Runbook bước 2 tự gọi failover đúng một lần; CI dùng `python3 dr/runbook.py --primary a --target b --backend fs --auto` | Failover log có bước 1→3, RPO và model version | ML Platform |
| 4 | Chờ readiness | `curl -sf localhost:8002/readyz` | HTTP 200, `ready:true`, vectors > 0, pool `full` | ML Platform |
| 5 | Xác minh cutover | `curl -s localhost:8080/edge/state` | Sau `4_wait_ready` có `5_dns_cutover`; active region là B | SRE |
| 6 | Golden signals | `for i in $(seq 1 10); do curl -sf localhost:8002/v1/infer; done` | 10/10 HTTP 200 từ B; error 0%; p95 < 100ms | SRE + ML Platform |
| 7 | Đo và postmortem | `python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300` | `valid:true`, `warnings:[]`, `PASS`, RPO không null | IC |

## Stop conditions và rollback

- Dừng, không cutover nếu snapshot thiếu, model version sai, B không ready trong 60s hoặc golden signals lỗi trước cutover.
- Nếu đã cutover mà error rate B > 1% hoặc p95 > 500ms liên tục 5 phút, IC đánh giá rollback.
- Chỉ IC phê duyệt failback; A phải ready ba lần, state đã reconcile và không còn ingest conflict.
- Failback bằng quy trình an toàn: `python3 dr/runbook.py --primary b --target a --backend fs`; sau đó chạy lại golden signals.
- Cooldown tối thiểu 15 phút để tránh hai region flap qua lại.
