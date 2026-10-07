"""Phần 02 — tự kiểm chứng kiến thức nền bằng số liệu thật (không cần đoán).

Mỗi mục in ra câu hỏi "Dự đoán trước" của guide và đáp án tính từ dữ liệu trong repo.

    python -m src.theory_check
"""
from __future__ import annotations

import sys

import numpy as np

from starter.datasets import load_frame
from starter.projection import cam_to_image, perturb_extrinsic, velo_to_cam


def title(s: str) -> None:
    print(f"\n=== {s} ===")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    kitti = load_frame("data/kitti_mini", "000011")
    syn = load_frame("data/synthetic", "000000")
    calib = kitti["calib"]
    f = calib.P2[0, 0]

    title("2.1 load_frame trả về gì")
    print(f"points {kitti['points'].shape} {kitti['points'].dtype}  (x, y, z, intensity)")
    print(f"image  {kitti['image'].shape} BGR uint8")
    print(f"calib  P2 {calib.P2.shape}, R0_rect {calib.R0_rect.shape}, Tr_velo_to_cam {calib.Tr_velo_to_cam.shape}")
    print(f"labels {len(kitti['labels'])} object, ví dụ: {kitti['labels'][0].type} location={kitti['labels'][0].location}")
    _, _, m = cam_to_image(velo_to_cam(kitti["points"][:, :3], calib), calib.P2, kitti["image"].shape)
    print(f"Dự đoán: bao nhiêu % điểm nằm trong ảnh camera trước? -> {m.sum()}/{len(m)} = {m.mean():.1%} (đáp án C)")

    title("2.2 Hai hệ trục")
    p = velo_to_cam(np.array([[10.0, 0, 0]]), calib)[0]
    print(f"LiDAR KITTI (10, 0, 0) = 10 m phía trước -> camera {p.round(2)}: 'phía trước' là x ở LiDAR, z ở camera")
    print("Box 3D trong label nằm trong camera frame; location = tâm ĐÁY, y hướng xuống nên nóc box ở y = -h")
    print("nuScenes LiDAR: x phải, y trước -> 'pitch' (quay quanh y) của perturb_extrinsic thực chất là roll")

    title("2.3 Phép chiếu, kiểm tra bằng calib synthetic 000000")
    pts = np.array([[10.0, 0, 0], [-10.0, 0, 0]])
    cam = velo_to_cam(pts, syn["calib"])
    raw = np.hstack([cam, np.ones((2, 1))]) @ syn["calib"].P2.T
    uv_raw = raw[:, :2] / raw[:, 2:3]
    uv, _, mask = cam_to_image(cam, syn["calib"].P2, syn["image"].shape)
    for i, name in enumerate(["(10,0,0) trước xe", "(-10,0,0) sau xe"]):
        print(f"{name}: z_cam={cam[i, 2]:+.2f}  pixel nếu KHÔNG lọc=({uv_raw[i, 0]:.0f}, {uv_raw[i, 1]:.0f})  "
              f"giữ lại sau lọc={bool(mask[i])}")
    print("Dự đoán: quên lọc z_cam<=0 thì sao? -> điểm sau xe hiện gần tâm ảnh, không báo lỗi (đáp án C)")
    t = np.array([[10.0, 0, 0, 0]]) @ calib.T_cam_velo.T
    print(f"Thiếu số 1 (dùng 0): (10,0,0) -> camera {t[0, :3].round(2)}  (mất phần dịch t)")

    title("2.4 Calibration lệch: xoay vs dịch (f = %.1f px)" % f)
    for z in (10, 50):
        print(f"vật ở {z} m: yaw 1° dịch ≈ {f * np.tan(np.deg2rad(1)):.1f} px | dịch ngang 5 cm ≈ {f * 0.05 / z:.1f} px")
    pts_k = kitti["points"][:, :3]
    pts_k = pts_k[np.isfinite(pts_k).all(axis=1)]
    print("Tỉ lệ điểm LiDAR trong 3D box rơi đúng vào 2D box GT, frame 000011 (nhiều người đi bộ) vs 000008 (đông xe):")
    from src.autolabel import points_in_box3d
    for fid in ("000008", "000011"):
        fr = load_frame("data/kitti_mini", fid)
        p = fr["points"][:, :3]
        p = p[np.isfinite(p).all(axis=1)]
        pc = velo_to_cam(p, fr["calib"])
        row = []
        for yaw in (0, 1, 3):
            cp = perturb_extrinsic(fr["calib"], yaw_deg=yaw)
            hit = tot = 0
            for obj in fr["labels"]:
                inside = points_in_box3d(pc, obj, 0.0)
                uv, _, _ = cam_to_image(velo_to_cam(p[inside], cp), cp.P2, fr["image"].shape)
                x1, y1, x2, y2 = obj.bbox
                hit += int(((uv[:, 0] >= x1) & (uv[:, 0] <= x2) & (uv[:, 1] >= y1) & (uv[:, 1] <= y2)).sum())
                tot += len(uv)            # chỉ đếm điểm chiếu được vào ảnh (xe bị cắt ở mép có nhiều điểm ngoài ảnh)
            row.append(f"yaw {yaw}°: {hit / max(tot, 1):.1%}")
        print(f"  {fid}: " + " | ".join(row))

    title("2.6 Lỗi Metric: % điểm trong ảnh không phát hiện được drift")
    for yaw in (0, 1, 3):
        cp = perturb_extrinsic(calib, yaw_deg=yaw)
        _, _, m = cam_to_image(velo_to_cam(kitti["points"][:, :3], cp), cp.P2, kitti["image"].shape)
        print(f"yaw {yaw}°: {m.sum()} điểm trong ảnh")
    print("6 lớp debug theo thứ tự: I/O -> Geometry -> Time -> Preprocess -> Model -> Metric")


if __name__ == "__main__":
    main()
