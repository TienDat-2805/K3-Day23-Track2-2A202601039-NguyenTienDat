# RTO/RPO Evidence — Lab 23

## 1. Drill 1 — không có DR

| Chỉ số | Giá trị | Cách đo | Evidence |
|---|---:|---|---|
| t_outage | 2026-08-25T04:12:17Z | Chaos kill Region A | `chaos/chaos-events.jsonl:1` |
| Request fail đầu tiên | +0.3s | Dòng `ok:false` đầu sau outage | `reports/drill-1-nodr.jsonl:17` |
| Số request lỗi | 18 | Công cụ đếm request lỗi | `reports/measure-drill-1.json:28` |
| Request thành công sau lỗi | Không có | `rto_measured_s:null` | `reports/measure-drill-1.json:23` |
| RTO | NO_RECOVERY | Không có request phục hồi | `reports/measure-drill-1.json:25` |

## 2. Drill 2 — có DR

| Mốc | +giây từ t_outage | Cách đo | Evidence |
|---|---:|---|---|
| t_outage | 0.0s | Chaos kill Region A | `chaos/chaos-events.jsonl:7` |
| User thấy lỗi đầu tiên | 0.3s | Dòng `ok:false` đầu | `reports/drill-2-withdr.jsonl:23` |
| Health checker phát hiện | 14.4s | Đủ ba probe lỗi liên tiếp | `reports/health-events.jsonl:2` |
| Snapshot restore xong | 14.5s | Event `2_restore_snapshot` | `reports/failover-events.jsonl:2` |
| Region B ready | 21.0s | `/readyz` trả 200 | `reports/failover-events.jsonl:4` |
| DNS cutover | 21.0s | Event `5_dns_cutover` | `reports/failover-events.jsonl:5` |
| Request đầu thành công từ B | 23.4s | Dòng `ok:true`, `served_by:b` | `reports/drill-2-withdr.jsonl:35` |

| Chỉ số | Đo được | Mục tiêu | Verdict | Evidence |
|---|---:|---:|---|---|
| RTO — Inference API | 23.4s | 300s | PASS | `reports/measure-drill-2.json:20` |
| RPO — Vector DB | 2.02s / 1 document | 300s | PASS | `reports/failover-events.jsonl:2` |
| Request lỗi | 12 | — | Đã đo | `reports/measure-drill-2.json:25` |
| Region phục hồi | B | Khác A | PASS | `reports/measure-drill-2.json:6` |

## 3. Phân rã RTO

| Thành phần | Thời gian | Evidence | Cách giảm |
|---|---:|---|---|
| Health-check detection | 14.4s | Cấu hình 5.0s × 3, floor 15.0s tại `reports/health-events.jsonl:2` | Giảm interval/threshold, đổi lại tăng probe load và nguy cơ flapping |
| Snapshot restore | 0.1s | Detection → restore tại `reports/failover-events.jsonl:2` | Storage nhanh hơn, snapshot nhỏ hơn |
| GPU pool warm-up | 6.5s | `waited_s=6.55` tại `reports/failover-events.jsonl:4` | Giữ warm capacity, đổi lại tăng chi phí |
| DNS/LB TTL cache | 2.4s | Cutover → success tại `reports/drill-2-withdr.jsonl:35` | Giảm TTL hoặc dùng global LB chủ động |
| **Tổng** | **23.4s** | RTO tại `reports/measure-drill-2.json:20` | — |

Drill có `valid:true`, `warnings:[]`; cấu hình health checker là `interval_s=5.0`, `threshold=3`, detection floor `15.0s`: `reports/measure-drill-2.json:2`. Snapshot lưu đúng embedding model version: `reports/failover-events.jsonl:2`.
