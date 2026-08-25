# Postmortem — DR Drill Lab 23

## 1. Tóm tắt và ảnh hưởng

Region A bị network partition mô phỏng bằng `SIGSTOP`. Trong drill có DR, 12 request thất bại trước khi Region B tiếp quản. Hệ thống phục hồi sau 23.4s, thấp hơn mục tiêu 300s. RPO là 2.02s và 1 document. Không có double outage; recovery được phục vụ bởi B.

## 2. Timeline

| ISO time | Sự kiện | Evidence |
|---|---|---|
| 2026-08-25T04:21:12Z | Outage A bắt đầu | `chaos/chaos-events.jsonl:7` |
| 2026-08-25T04:21:12Z | User đầu tiên gặp lỗi (+0.3s) | `reports/drill-2-withdr.jsonl:23` |
| 2026-08-25T04:21:22Z | Operator xác nhận outage | `reports/runbook-run.jsonl:1` |
| 2026-08-25T04:21:26Z | Health checker phát `UNHEALTHY` | `reports/health-events.jsonl:2` |
| 2026-08-25T04:21:26Z | Restore; RPO 2.02s/1 document | `reports/failover-events.jsonl:2` |
| 2026-08-25T04:21:33Z | B ready và DNS cutover | `reports/failover-events.jsonl:5` |
| 2026-08-25T04:21:35Z | Request đầu thành công từ B | `reports/drill-2-withdr.jsonl:35` |

## 3. RTO/RPO và gap analysis

- RTO mục tiêu 300s; đo được 23.4s; gap còn dư 276.6s.
- RPO mục tiêu 300s; đo được 2.02s/1 document; gap còn dư 297.98s.
- Health-check detection lớn nhất: 14.4s, khoảng 61.5% RTO.
- Warm-up khoảng 6.5s; DNS TTL 2.4s; restore filesystem khoảng 0.1s.
- PASS theo SLO không có nghĩa zero impact: vẫn có 12 request lỗi.

## 4. Root cause — 5 whys

1. Request lỗi vì edge vẫn gửi tới A trong khi A không phản hồi.
2. Edge chưa chuyển ngay vì health checker cần ba lỗi liên tiếp để chống flapping.
3. Sau detection vẫn phải chờ vì B cần restore state và warm pool.
4. B cần restore vì kiến trúc active-passive không giữ B full và đồng bộ tức thời.
5. Mất 1 document vì ingest sau snapshot gần nhất chưa được replicate.

Nếu là outage thật, điểm dễ thất bại nhất là snapshot thiếu hoặc embedding model version không khớp. Failover phải abort trước cutover nếu restore/ready check thất bại.

## 5. Những gì hoạt động và khoảng trống

- Anti-flap threshold hoạt động; runbook chờ alert rồi mới failover.
- Chỉ cutover sau `/readyz=200`; golden signals đạt 10/10, p95 28.61ms: `reports/runbook-run.jsonl:6`.
- Khoảng trống: detection chiếm phần lớn RTO; warm standby vẫn có warm-up và replication lag.
- Health checker/runbook là process local đơn; production cần HA riêng và circuit breaker/cooldown.

## 6. Action items

| # | Action item | Owner | Deadline | Tác động dự kiến |
|---|---|---|---|---|
| 1 | Thử interval 3s/threshold 3 qua 5 chaos run | SRE | 7 ngày | Detection floor 15s → 9s |
| 2 | Replicate mỗi 10s và theo dõi I/O | Data Platform | 14 ngày | Giảm RPO lý thuyết khoảng 20s |
| 3 | Giữ một worker warm ở B | ML Platform | 14 ngày | Giảm khoảng 6.5s warm-up |
| 4 | Thêm circuit breaker/cooldown failback | SRE | 21 ngày | Ngăn flapping hai chiều |

## 7. Câu hỏi bắt buộc

1. `interval × threshold = 5s × 3 = 15s`. Detection thực đo 14.4s, chiếm khoảng 61.5% RTO 23.4s; sai lệch nhỏ do outage không trùng đầu chu kỳ poll.
2. Interval 1s, threshold 3 cho floor 3s, lý thuyết giảm khoảng 12s. Đổi lại probe load cao hơn và nhạy hơn với lỗi thoáng qua.
3. `docs_lost=1` là một thay đổi khách hàng có ở primary nhưng không có trong snapshot. Nếu primary mất vĩnh viễn, cần replay từ nguồn khác hoặc chấp nhận mất và thông báo khách hàng.
