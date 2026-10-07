# Báo cáo Day 6: Auto-label 2D box từ box 3D và điểm LiDAR

> Thay **mọi** ô có chữ ĐIỀN nằm trong ngoặc vuông bằng nội dung của bạn, xoá luôn cả dấu ngoặc vuông. Lệnh `python tools/check_submission.py` sẽ báo FAIL nếu còn sót bất kỳ chỗ nào.

- **Họ tên:** Đặng Hữu Tâm
- **MSSV:** 2A202602940
- **Lớp:** [ĐIỀN]
- **Link repo:** https://github.com/tam253211-a11y/DangHuuTam-2A202602940-Track4-Day21
- **Topic:** F — Auto-label support
- **Dataset:** data/kitti_mini (chính), data/nuscenes_mini_subset (so sánh), data/synthetic (test)
- **Các frame đã dùng:** toàn bộ 20 frame kitti_mini; toàn bộ 80 keyframe nuScenes

> Hãy viết ngắn: mỗi mục từ 3 đến 8 dòng, ưu tiên số liệu và hình ảnh.

## 1. Claim

Claim nháp: 2D box tạo từ min/max điểm LiDAR nằm trong box 3D khớp label 2D tốt hơn (IoU cao hơn) so với chiếu 8 góc box 3D; và lệch yaw 1° làm IoU trung bình giảm hơn 0.2, đủ để gắn cờ "label cần review" với ngưỡng IoU < 0.7.

## 2. Evidence

Bảng hoặc plot số liệu, kèm ảnh/video demo. Ghi rõ đường dẫn file trong `results/`.

| Cấu hình / mức perturb | Metric 1 | Metric 2 | Ghi chú |
|---|---|---|---|
| [ĐIỀN] | | | |

![demo](../results/figures/[ĐIỀN].png)

## 3. Failure case

Nêu khi nào hệ thống hoặc phương pháp fail, vì sao fail, và liên hệ tới lớp nào trong 6 lớp debug: I/O, Geometry, Time, Preprocess, Model, Metric.

![failure](../results/figures/fail_[ĐIỀN].png)

[ĐIỀN]

## 4. Khuyến nghị nếu triển khai thật

Use-case cụ thể (ADAS / robot / drone), trade-off và bước tiếp theo.

[ĐIỀN]

## 5. Cách chạy lại

Các lệnh tái tạo lại toàn bộ kết quả từ repo sạch.

```bash
[ĐIỀN]
```

## 6. Khai báo sử dụng AI

Ghi rõ đã dùng công cụ AI nào, dùng vào việc gì, và bạn đã tự kiểm chứng kết quả đó bằng cách nào. Nếu không dùng AI, ghi "Không sử dụng". Xem quy định ở `RULES.md` mục 2.

| Công cụ | Dùng cho việc gì | Bạn đã kiểm chứng thế nào |
|---|---|---|
| [ĐIỀN] | | |
