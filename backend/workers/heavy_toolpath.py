"""
Heavy Toolpath Worker (Phase 4)
Runs the same drop-cutter algorithm as the browser, but for jobs too large
for client-side (>600px grid, ultra-detail). Uses NumPy for vectorized speed.

In production: pulled from Redis queue by a separate process.
In dev: called inline by FastAPI BackgroundTasks.
"""
import numpy as np
import json, os

S3_BUCKET = os.getenv("S3_BUCKET", "relief-studio-files")
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "")


def s3_client():
    """One S3 factory for every worker, honouring S3_ENDPOINT so the MinIO
    stack in deploy/docker-compose.yml actually receives the objects."""
    import boto3
    return boto3.client("s3", endpoint_url=S3_ENDPOINT or None)


def put_object(key: str, body: bytes, content_type: str, metadata=None):
    s3_client().put_object(Bucket=S3_BUCKET, Key=key, Body=body,
                           ContentType=content_type, Metadata=metadata or {})
    return key

def run_heavy_toolpath(job, db):
    """Main entry point. Reads job.settings_json, computes toolpath, uploads G-code to S3."""
    settings = json.loads(job.settings_json or "{}")

    grid_data = settings.get("heightfield")  # flattened array
    cols = settings.get("cols", 200)
    rows = settings.get("rows", 200)
    mat_w = settings.get("mat_w", 120)
    mat_h = settings.get("mat_h", 120)
    max_depth = settings.get("max_depth", 6)
    tool_type = settings.get("tool_type", "ball")
    tool_dia = settings.get("tool_dia", 3)
    stepover_pct = settings.get("stepover_pct", 12) / 100
    feed = settings.get("feed", 1000)
    rpm = settings.get("rpm", 18000)
    safe_z = settings.get("safe_z", 5)
    plunge = settings.get("plunge", 400)
    v_angle = settings.get("v_angle", 90)
    dialect = settings.get("dialect", "grbl")

    # W4 FIX: a missing or wrong-length heightfield silently became np.ones() -
    # a perfectly flat surface. The job then reported DONE and handed the user
    # syntactically valid G-code that carves nothing. For a CNC product that is
    # the worst possible failure mode: plausible output, wrong geometry, no
    # warning. Fail loudly instead.
    if not grid_data:
        raise ValueError("settings.heightfield is required (flattened row-major array of 0..1)")
    if len(grid_data) != cols * rows:
        raise ValueError(f"heightfield length {len(grid_data)} != cols*rows ({cols}*{rows}={cols * rows})")
    grid = np.asarray(grid_data, dtype=np.float32).reshape(rows, cols)
    if not np.isfinite(grid).all():
        raise ValueError("heightfield contains NaN or Infinity")
    grid = np.clip(grid, 0.0, 1.0)

    job.progress = 10
    db.commit()

    # Compute finished surface Z map
    surf_z = -(1 - grid) * max_depth   # shape (rows, cols)

    # ---- Vectorized drop-cutter ----
    # W5 FIX: this used px_per_mm = cols/mat_w while the browser maps grid node
    # gx to (gx/(cols-1))*mat_w. The two paths therefore produced DIFFERENT
    # toolpaths for identical input, breaking both the "same math as browser"
    # claim and the determinism guarantee across the escalation boundary.
    tool_r = tool_dia / 2
    px_per_mm = (cols - 1) / mat_w
    r_px = int(np.ceil(tool_r * px_per_mm))

    # Precompute tool kernel
    if tool_type == "ball":
        tip_z = ball_nose_drop(surf_z, tool_r, r_px, px_per_mm)
    elif tool_type == "vbit":
        tan_half = np.tan(np.radians(v_angle / 2))
        tip_z = vbit_drop(surf_z, tool_r, r_px, px_per_mm, tan_half)
    else:
        tip_z = flat_drop(surf_z, tool_r, r_px)

    tip_z = np.minimum(tip_z, 0)          # never above stock top
    tip_z = np.maximum(tip_z, -max_depth)  # never below the declared relief depth

    job.progress = 50
    db.commit()

    # ---- Generate toolpath (raster finishing) ----
    step_mm = max(0.03, tool_dia * stepover_pct)
    # W6 FIX: passes were snapped onto integer grid rows, so asking for a finer
    # stepover than the grid resolution emitted DUPLICATE identical passes -
    # the machine recut the same line and the requested stepover (and the
    # scallop height derived from it) was silently not honoured. Cap the pass
    # count at the grid and report the stepover actually achieved.
    n_passes = max(2, min(8000, int(mat_h / step_mm) + 1))
    if n_passes > rows:
        n_passes = rows
    effective_step = mat_h / max(1, n_passes - 1)
    mm_per_px_x = mat_w / (cols - 1)
    mm_per_px_y = mat_h / (rows - 1)

    lines = []
    cm = lambda s: f"({s})" if dialect == "linuxcnc" else f"; {s}"
    lines.append(cm(f"Relief Studio server CAM · {dialect} · {tool_type} Ø{tool_dia}mm"))
    lines.append(cm(f"grid {cols}x{rows} · stock {mat_w}x{mat_h}mm · depth {max_depth}mm"))
    lines.append(cm(f"requested stepover {step_mm:.3f}mm · achieved {effective_step:.3f}mm "
                    f"({n_passes} passes)"))
    lines.append("G21\nG90")
    if dialect != "grbl":
        lines.append("G17")
    lines.append(f"M3 S{rpm}")
    lines.append(f"G0 Z{safe_z:.3f}")

    for p in range(n_passes):
        # int() truncation makes p/(n-1)*(n-1) land on 33.99999... for some p,
        # which collapses two passes onto the same row. Round instead.
        gy = min(rows - 1, int(round(p * (rows - 1) / (n_passes - 1))))
        wy = gy * mm_per_px_y
        forward = (p % 2 == 0)
        xrange = range(cols) if forward else range(cols - 1, -1, -1)

        first = True
        for gx in xrange:
            wx = gx * mm_per_px_x
            z = float(tip_z[gy, gx])
            if not np.isfinite(z):
                z = 0
            if first:
                lines.append(f"G0 X{wx:.3f} Y{wy:.3f}")
                lines.append(f"G1 Z{z:.3f} F{plunge}")
                first = False
            else:
                lines.append(f"G1 X{wx:.3f} Y{wy:.3f} Z{z:.3f} F{feed}")
        lines.append(f"G0 Z{safe_z:.3f}")

        # Progress update every 10%
        if p % max(1, n_passes // 10) == 0:
            job.progress = 50 + int(40 * p / n_passes)
            db.commit()

    lines.append("M5\nM30")
    gcode = "\n".join(lines)

    job.progress = 95
    db.commit()

    # W7 FIX: this hardcoded the bucket name and built the client with NO
    # endpoint_url, so in the shipped docker-compose stack it ignored
    # S3_ENDPOINT, tried real AWS, failed, and silently fell back to a /tmp path
    # inside the worker container that the API could never serve. The result was
    # a download URL that could not resolve - the whole escalation path was
    # broken end to end, and `except Exception` hid it. Honour the env config
    # and let a genuine storage failure fail the job.
    result_key = f"results/{job.user_id}/{job.id}/relief.nc"
    put_object(result_key, gcode.encode("utf-8"), "text/plain")
    job.result_key = result_key


# ===========================================================================
# VECTORIZED DROP-CUTTERS (NumPy) — same math as browser, but fast
# ===========================================================================

def ball_nose_drop(surf_z, tool_r, r_px, px_per_mm):
    """Ball-nose: at each (cx,cy), find max(surf_z[gy,gx] + sqrt(R²-d²)) over
    the circular footprint, then subtract R to get tip Z."""
    rows, cols = surf_z.shape
    # W8 FIX: mm-per-pixel was derived as tool_r/r_px, but r_px is ceil()'d, so
    # this reported pixels as closer together than they are - underestimating
    # radial distance, overestimating the tool lift, and disagreeing with the
    # browser's exact grid scale. Use the real grid scale.
    mm_per_px = 1.0 / max(1e-9, px_per_mm)

    # Build radial kernel
    ky, kx = np.mgrid[-r_px:r_px + 1, -r_px:r_px + 1]
    d_px2 = kx ** 2 + ky ** 2
    mask = d_px2 <= r_px ** 2
    d_mm = np.sqrt(d_px2.astype(np.float32)) * mm_per_px
    lift = np.sqrt(np.maximum(0, tool_r ** 2 - d_mm ** 2))
    lift[~mask] = -1e30  # ignore pixels outside footprint

    # For each output pixel, max(surf_z + lift) over the kernel
    tip_z = np.full_like(surf_z, -1e30)
    for dy in range(-r_px, r_px + 1):
        for dx in range(-r_px, r_px + 1):
            if not mask[dy + r_px, dx + r_px]:
                continue
            l = lift[dy + r_px, dx + r_px]
            # Shifted surface view
            sy0, sy1 = max(0, -dy), min(rows, rows - dy)
            sx0, sx1 = max(0, -dx), min(cols, cols - dx)
            ty0, ty1 = sy0 + dy, sy1 + dy
            tx0, tx1 = sx0 + dx, sx1 + dx
            candidate = surf_z[ty0:ty1, tx0:tx1] + l
            tip_z[sy0:sy1, sx0:sx1] = np.maximum(tip_z[sy0:sy1, sx0:sx1], candidate)

    return tip_z - tool_r  # center Z → tip Z


def flat_drop(surf_z, tool_r, r_px):
    """Flat end mill: tip Z = max(surf_z) over the disc footprint."""
    rows, cols = surf_z.shape
    ky, kx = np.mgrid[-r_px:r_px + 1, -r_px:r_px + 1]
    mask = (kx ** 2 + ky ** 2) <= r_px ** 2

    tip_z = np.full_like(surf_z, -1e30)
    for dy in range(-r_px, r_px + 1):
        for dx in range(-r_px, r_px + 1):
            if not mask[dy + r_px, dx + r_px]:
                continue
            sy0, sy1 = max(0, -dy), min(rows, rows - dy)
            sx0, sx1 = max(0, -dx), min(cols, cols - dx)
            ty0, ty1 = sy0 + dy, sy1 + dy
            tx0, tx1 = sx0 + dx, sx1 + dx
            tip_z[sy0:sy1, sx0:sx1] = np.maximum(
                tip_z[sy0:sy1, sx0:sx1], surf_z[ty0:ty1, tx0:tx1]
            )
    return tip_z


def vbit_drop(surf_z, tool_r, r_px, px_per_mm, tan_half):
    """V-bit cone: tip Z = max(surf_z - d_mm/tanHalf) over disc."""
    rows, cols = surf_z.shape
    # W8 FIX: mm-per-pixel was derived as tool_r/r_px, but r_px is ceil()'d, so
    # this reported pixels as closer together than they are - underestimating
    # radial distance, overestimating the tool lift, and disagreeing with the
    # browser's exact grid scale. Use the real grid scale.
    mm_per_px = 1.0 / max(1e-9, px_per_mm)
    ky, kx = np.mgrid[-r_px:r_px + 1, -r_px:r_px + 1]
    d_px2 = kx ** 2 + ky ** 2
    mask = d_px2 <= r_px ** 2
    d_mm = np.sqrt(d_px2.astype(np.float32)) * mm_per_px
    cone_offset = d_mm / max(1e-6, tan_half)
    cone_offset[~mask] = 1e30

    tip_z = np.full_like(surf_z, -1e30)
    for dy in range(-r_px, r_px + 1):
        for dx in range(-r_px, r_px + 1):
            if not mask[dy + r_px, dx + r_px]:
                continue
            off = cone_offset[dy + r_px, dx + r_px]
            sy0, sy1 = max(0, -dy), min(rows, rows - dy)
            sx0, sx1 = max(0, -dx), min(cols, cols - dx)
            ty0, ty1 = sy0 + dy, sy1 + dy
            tx0, tx1 = sx0 + dx, sx1 + dx
            candidate = surf_z[ty0:ty1, tx0:tx1] - off
            tip_z[sy0:sy1, sx0:sx1] = np.maximum(tip_z[sy0:sy1, sx0:sx1], candidate)

    return tip_z
