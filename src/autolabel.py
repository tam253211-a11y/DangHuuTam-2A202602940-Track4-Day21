"""Topic F — Auto-label support: dùng box 3D + điểm LiDAR để gợi ý / kiểm tra 2D box trên ảnh.

Hai cách tạo 2D box từ một object 3D:
  corners : chiếu 8 góc box 3D lên ảnh, lấy min/max (clip vào ảnh).
  points  : lấy các điểm LiDAR nằm trong box 3D, chiếu lên ảnh, lấy min/max.
Cả hai được so với 2D box trong label (KITTI: do người gán nhãn vẽ) bằng IoU.

Giả lập calibration drift: box 3D coi như được gán nhãn trong LiDAR frame (như pipeline thật),
nên cả hai cách đều đi qua Tr_velo_to_cam. Box được đưa về LiDAR bằng calib đúng, rồi đưa sang
camera bằng calib đã bị perturb (starter.projection.perturb_extrinsic).

Không có phép ngẫu nhiên nào -> chạy lại luôn ra đúng cùng số liệu.

Ví dụ:
    python -m src.autolabel eval  --data-root data/kitti_mini
    python -m src.autolabel sweep --data-root data/kitti_mini
    python -m src.autolabel show  --data-root data/kitti_mini --frame 000011 --yaw-deg 2 --out results/figures/x.png
    python -m src.autolabel latency --data-root data/kitti_mini
"""
from __future__ import annotations

import argparse
import platform
import sys
import time
from pathlib import Path

import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from starter.datasets import list_frames, load_frame
from starter.kitti_io import KittiCalib, KittiObject
from starter.projection import (box3d_corners_cam, cam_to_image, draw_box2d, overlay_points,
                                perturb_extrinsic, velo_to_cam)

RESULTS = Path("results")
FIGS = RESULTS / "figures"


# ----------------------------------------------------------------------------- hình học
def iou(a, b) -> float:
    if a is None or b is None:
        return 0.0
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return float(inter / union) if union > 0 else 0.0


def cam_true_to_cam_pert(pts_cam: np.ndarray, calib: KittiCalib, calib_pert: KittiCalib) -> np.ndarray:
    """Điểm trong camera frame (calib đúng) -> LiDAR frame -> camera frame theo calib bị lệch."""
    T = calib_pert.T_cam_velo @ np.linalg.inv(calib.T_cam_velo)
    pts_h = np.hstack([pts_cam, np.ones((len(pts_cam), 1))])
    return (pts_h @ T.T)[:, :3]


def box_from_uv(uv: np.ndarray, image_shape) -> np.ndarray | None:
    if len(uv) == 0:
        return None
    H, W = image_shape[:2]
    x1, y1 = np.clip(uv.min(axis=0), 0, [W - 1, H - 1])
    x2, y2 = np.clip(uv.max(axis=0), 0, [W - 1, H - 1])
    if x2 - x1 < 1 or y2 - y1 < 1:
        return None
    return np.array([x1, y1, x2, y2])


def box2d_from_corners(obj: KittiObject, calib: KittiCalib, calib_pert: KittiCalib, image_shape):
    """Cách 1. Góc nằm sau camera bị bỏ; góc ngoài ảnh được clip vào mép ảnh."""
    corners = cam_true_to_cam_pert(box3d_corners_cam(obj), calib, calib_pert)
    front = corners[:, 2] > 0.1
    if not front.any():
        return None
    proj = np.hstack([corners[front], np.ones((int(front.sum()), 1))]) @ calib_pert.P2.T
    return box_from_uv(proj[:, :2] / proj[:, 2:3], image_shape)


def points_in_box3d(pts_cam: np.ndarray, obj: KittiObject, ground_margin: float) -> np.ndarray:
    """Mask (N,) điểm nằm trong box 3D. Bỏ lớp `ground_margin` mét sát đáy để không ăn điểm mặt đường."""
    h, w, l = obj.dimensions
    c, s = np.cos(obj.rotation_y), np.sin(obj.rotation_y)
    R = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])      # object -> camera (như box3d_corners_cam)
    local = (pts_cam - obj.location) @ R                     # = R.T @ (p - loc) cho từng điểm
    return ((np.abs(local[:, 0]) <= l / 2) & (np.abs(local[:, 2]) <= w / 2)
            & (local[:, 1] >= -h) & (local[:, 1] <= -ground_margin))


def box2d_from_points(pts_velo_in: np.ndarray, calib_pert: KittiCalib, image_shape, min_points: int):
    """Cách 2. Trả về (box hoặc None, số điểm chiếu được lên ảnh)."""
    uv, _, _ = cam_to_image(velo_to_cam(pts_velo_in, calib_pert), calib_pert.P2, image_shape)
    if len(uv) < min_points:
        return None, len(uv)
    return box_from_uv(uv, image_shape), len(uv)


# ----------------------------------------------------------------------------- đánh giá
def evaluate_frame(fr: dict, calib_pert: KittiCalib | None = None, ground_margin: float = 0.1,
                   min_points: int = 5) -> list[dict]:
    calib = fr["calib"]
    calib_pert = calib_pert or calib
    pts = fr["points"][:, :3]
    pts = pts[np.isfinite(pts).all(axis=1)]
    pts_cam = velo_to_cam(pts, calib)          # chọn điểm trong box bằng calib đúng (box & điểm cùng LiDAR frame)
    rows = []
    for i, obj in enumerate(fr["labels"]):
        inside = points_in_box3d(pts_cam, obj, ground_margin)
        b_corner = box2d_from_corners(obj, calib, calib_pert, fr["image"].shape)
        b_points, n_proj = box2d_from_points(pts[inside], calib_pert, fr["image"].shape, min_points)
        gt = obj.bbox
        rows.append(dict(
            frame=fr["frame_id"], obj=i, type=obj.type,
            depth_m=round(float(obj.location[2]), 2),
            occluded=obj.occluded, truncated=round(float(obj.truncated), 2),
            gt_h_px=round(float(gt[3] - gt[1]), 1),
            n_pts_3d=int(inside.sum()), n_pts_img=n_proj,
            iou_corners=round(iou(b_corner, gt), 4),
            iou_points=round(iou(b_points, gt), 4),
            points_box_ok=b_points is not None,
        ))
    return rows


def _load(data_root: str, frame: str) -> dict:
    return load_frame(data_root, frame)


def cmd_eval(args) -> None:
    frames = args.frames or list_frames(args.data_root)
    rows = []
    for f in frames:
        fr = _load(args.data_root, f)
        rows += evaluate_frame(fr, ground_margin=args.ground_margin, min_points=args.min_points)
        if args.figures:
            render(fr, None, FIGS / f"f_overlay_{args.tag}_{f}.png")
    df = pd.DataFrame(rows)
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"f_iou_objects_{args.tag}.csv"
    df.to_csv(out, index=False)

    df["depth_bin"] = pd.cut(df["depth_m"], [0, 15, 30, 50, 200], labels=["<15m", "15-30m", "30-50m", ">50m"])
    groups = {"all": df}
    groups.update({f"occluded={k}": g for k, g in df.groupby("occluded")})
    groups.update({f"depth {k}": g for k, g in df.groupby("depth_bin", observed=True)})
    groups.update({f"type={k}": g for k, g in df.groupby("type") if len(g) >= 3})
    summ = pd.DataFrame([dict(group=k, n=len(g),
                              iou_corners_mean=round(g.iou_corners.mean(), 3),
                              iou_points_mean=round(g.iou_points.mean(), 3),
                              iou_corners_median=round(g.iou_corners.median(), 3),
                              iou_points_median=round(g.iou_points.median(), 3),
                              points_better_pct=round(100 * (g.iou_points > g.iou_corners).mean(), 1),
                              points_fail_pct=round(100 * (~g.points_box_ok).mean(), 1))
                         for k, g in groups.items()])
    summ.to_csv(RESULTS / f"f_iou_summary_{args.tag}.csv", index=False)
    print(summ.to_string(index=False))
    print(f"-> {out}")


# ----------------------------------------------------------------------------- perturb sweep
PERTURBS = [("none", 0, dict())] + \
    [("yaw", v, dict(yaw_deg=v)) for v in (0.5, 1, 2, 3)] + \
    [("pitch", v, dict(pitch_deg=v)) for v in (0.5, 1, 2, 3)] + \
    [("tx", v, dict(t_xyz_m=(0, v / 100, 0))) for v in (2, 5, 10)]   # dịch ngang (trục y LiDAR), cm


def cmd_sweep(args) -> None:
    frames = args.frames or list_frames(args.data_root)
    cache = {f: _load(args.data_root, f) for f in frames}
    rows = []
    for kind, level, kw in PERTURBS:
        for f, fr in cache.items():
            cp = perturb_extrinsic(fr["calib"], **kw)
            for r in evaluate_frame(fr, cp, args.ground_margin, args.min_points):
                rows.append(dict(perturb=kind, level=level, **r))
    df = pd.DataFrame(rows)
    df.to_csv(RESULTS / f"f_perturb_objects_{args.tag}.csv", index=False)

    # Chỉ đánh giá object mà ở calib đúng box corners đã khớp GT (IoU >= 0.7), để tách riêng ảnh hưởng của drift.
    base = df[df.perturb == "none"].set_index(["frame", "obj"])
    good = base.index[base.iou_corners >= 0.7]
    df_g = df.set_index(["frame", "obj"]).loc[lambda d: d.index.isin(good)].reset_index()

    thr = args.flag_iou
    summ = []
    for (kind, level), g in df_g.groupby(["perturb", "level"], sort=False):
        frame_med = g.groupby("frame").iou_corners.median()
        summ.append(dict(perturb=kind, level=level, n_obj=len(g),
                         iou_corners_mean=round(g.iou_corners.mean(), 3),
                         iou_points_mean=round(g.iou_points.mean(), 3),
                         iou_corners_near_mean=round(g[g.depth_m < 20].iou_corners.mean(), 3),
                         iou_corners_far_mean=round(g[g.depth_m >= 20].iou_corners.mean(), 3),
                         obj_flag_pct=round(100 * (g.iou_corners < thr).mean(), 1),
                         obj_flag_pct_points=round(100 * (g.iou_points < thr).mean(), 1),
                         frame_flag_pct=round(100 * (frame_med < thr).mean(), 1)))
    summ = pd.DataFrame(summ)
    out = RESULTS / f"f_perturb_sweep_{args.tag}.csv"
    summ.to_csv(out, index=False)
    print(f"(objects có iou_corners >= 0.7 khi calib đúng: {len(good)}/{len(base)}; ngưỡng flag IoU < {thr})")
    print(summ.to_string(index=False))
    print(f"-> {out}")

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
    for ax, kind, unit in zip(axes, ["yaw", "pitch", "tx"], ["độ", "độ", "cm"]):
        s = pd.concat([summ[summ.perturb == "none"], summ[summ.perturb == kind]])
        ax.plot(s.level, s.iou_corners_mean, "o-", label="corners (tất cả)")
        ax.plot(s.level, s.iou_corners_near_mean, "s--", label="corners (<20 m)")
        ax.plot(s.level, s.iou_corners_far_mean, "^--", label="corners (>=20 m)")
        ax.plot(s.level, s.iou_points_mean, "x:", label="points (tất cả)")
        ax.axhline(thr, color="r", lw=0.8, ls=":", label=f"ngưỡng flag {thr}")
        ax.set(title=f"Drift {kind}", xlabel=f"mức lệch ({unit})", ylabel="IoU trung bình với GT 2D", ylim=(0, 1))
        ax.grid(alpha=0.3)
    axes[0].legend(fontsize=8)
    fig.suptitle(f"IoU box 2D tự sinh vs GT theo mức calibration drift ({args.data_root})")
    fig.tight_layout()
    FIGS.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGS / f"f_perturb_sweep_{args.tag}.png", dpi=120)


# ----------------------------------------------------------------------------- hình minh hoạ
def render(fr: dict, calib_pert: KittiCalib | None, out: Path, only_obj: int | None = None,
           ground_margin: float = 0.1, min_points: int = 5, crop: bool = False) -> None:
    calib = fr["calib"]
    calib_pert = calib_pert or calib
    pts = fr["points"][:, :3]
    pts = pts[np.isfinite(pts).all(axis=1)]
    uv, depth, _ = cam_to_image(velo_to_cam(pts, calib_pert), calib_pert.P2, fr["image"].shape)
    vis = (0.6 * fr["image"]).astype(np.uint8)
    vis = overlay_points(vis, uv, depth, radius=1)
    pts_cam = velo_to_cam(pts, calib)
    for i, obj in enumerate(fr["labels"]):
        if only_obj is not None and i != only_obj:
            continue
        inside = points_in_box3d(pts_cam, obj, ground_margin)
        uv_in, _, _ = cam_to_image(velo_to_cam(pts[inside], calib_pert), calib_pert.P2, fr["image"].shape)
        for u, v in uv_in.astype(int):
            cv2.circle(vis, (int(u), int(v)), 2, (255, 255, 255), -1)
        b_c = box2d_from_corners(obj, calib, calib_pert, fr["image"].shape)
        b_p, _ = box2d_from_points(pts[inside], calib_pert, fr["image"].shape, min_points)
        vis = draw_box2d(vis, obj.bbox, (0, 255, 0), f"#{i} {obj.type}")
        if b_c is not None:
            vis = draw_box2d(vis, b_c, (255, 128, 0))
        if b_p is not None:
            vis = draw_box2d(vis, b_p, (0, 0, 255), f"IoU p={iou(b_p, obj.bbox):.2f} c={iou(b_c, obj.bbox):.2f}"
                             if only_obj is not None else None)
    cv2.putText(vis, "GT 2D: xanh la | corners: xanh duong | LiDAR points: do (diem trang = trong box 3D)",
                (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    if crop and only_obj is not None:
        x1, y1, x2, y2 = fr["labels"][only_obj].bbox
        H, W = vis.shape[:2]
        pad = 60
        vis = vis[max(0, int(y1) - pad):min(H, int(y2) + pad), max(0, int(x1) - pad):min(W, int(x2) + pad)]
        vis = cv2.resize(vis, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), vis)


def cmd_show(args) -> None:
    fr = _load(args.data_root, args.frame)
    cp = perturb_extrinsic(fr["calib"], args.roll_deg, args.pitch_deg, args.yaw_deg, (args.tx, args.ty, args.tz))
    render(fr, cp, Path(args.out), args.obj, args.ground_margin, args.min_points, args.crop)
    for r in evaluate_frame(fr, cp, args.ground_margin, args.min_points):
        if args.obj is None or r["obj"] == args.obj:
            print(r)
    print(f"-> {args.out}")


# ----------------------------------------------------------------------------- latency
def cmd_latency(args) -> None:
    frames = args.frames or list_frames(args.data_root)
    cache = {f: _load(args.data_root, f) for f in frames}
    times = []
    for rep in range(args.repeats + 1):
        t0 = time.perf_counter()
        for fr in cache.values():
            evaluate_frame(fr)
        dt = (time.perf_counter() - t0) / len(cache) * 1000
        if rep > 0:                       # bỏ lần chạy đầu (warm-up)
            times.append(dt)
    t = np.array(times)
    df = pd.DataFrame(dict(run=np.arange(1, len(t) + 1), ms_per_frame=t.round(3)))
    df.to_csv(RESULTS / f"f_latency_{args.tag}.csv", index=False)
    print(f"{len(t)} lần (đã bỏ lần đầu), {len(cache)} frame/lần, CPU: {platform.processor() or platform.machine()}")
    print(f"ms/frame (cả 2 cách, mọi object): p50={np.percentile(t, 50):.2f}  p95={np.percentile(t, 95):.2f}")


# ----------------------------------------------------------------------------- CLI
def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Topic F: sinh/kiểm tra 2D box từ box 3D và điểm LiDAR")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--data-root", default="data/kitti_mini", help="data/kitti_mini, data/synthetic, data/nuscenes_mini_subset")
        p.add_argument("--frames", nargs="*", help="danh sách frame id (mặc định: tất cả)")
        p.add_argument("--tag", default="kitti", help="hậu tố tên file kết quả")
        p.add_argument("--ground-margin", type=float, default=0.1, help="bỏ lớp điểm sát đáy box 3D (m)")
        p.add_argument("--min-points", type=int, default=5, help="số điểm tối thiểu để cách points tạo box")

    p = sub.add_parser("eval", help="IoU của 2 cách với GT 2D box trên mọi object")
    common(p)
    p.add_argument("--figures", action="store_true", help="lưu ảnh overlay cho từng frame")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("sweep", help="IoU theo mức calibration drift (yaw / pitch / dịch ngang)")
    common(p)
    p.add_argument("--flag-iou", type=float, default=0.7, help="IoU dưới ngưỡng này -> gắn cờ 'label cần review'")
    p.set_defaults(func=cmd_sweep)

    p = sub.add_parser("show", help="vẽ một frame (có thể perturb), tuỳ chọn chỉ 1 object")
    common(p)
    p.add_argument("--frame", required=True)
    p.add_argument("--obj", type=int, help="chỉ số object trong label")
    p.add_argument("--crop", action="store_true", help="cắt và phóng to quanh object")
    p.add_argument("--out", required=True)
    for k in ("roll-deg", "pitch-deg", "yaw-deg", "tx", "ty", "tz"):
        p.add_argument(f"--{k}", type=float, default=0.0)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("latency", help="đo p50/p95 thời gian xử lý mỗi frame")
    common(p)
    p.add_argument("--repeats", type=int, default=20)
    p.set_defaults(func=cmd_latency)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
