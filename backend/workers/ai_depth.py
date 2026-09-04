"""
AI Depth Worker (Phase 4)
Optional GPU worker: runs a monocular depth estimation model on a user's photo
and returns a depth map that can replace the luminosity-based heightfield.

This is the ONLY server component that needs GPU.
Runs on a separate autoscaling pool (expensive — spin up/down on demand).

Model options (swap via AI_DEPTH_MODEL env var):
  - depth_anything_v2  (default, MIT license, good quality)
  - midas_v3           (older, lighter, faster)
  - marigold           (diffusion-based, highest quality, slowest)

Output: a float32 depth map uploaded to S3, same grid as the heightfield.
The frontend clearly labels this "AI-assisted — review before machining."
"""
import os, json, uuid
import numpy as np
from datetime import datetime

AI_DEPTH_MODEL = os.getenv("AI_DEPTH_MODEL", "depth_anything_v2")


def run_ai_depth(job, db):
    """Main entry. Reads image from S3, runs depth model, uploads depth map."""
    settings = json.loads(job.settings_json or "{}")
    image_key = settings.get("image_key", "")
    target_cols = settings.get("cols", 300)
    target_rows = settings.get("rows", 300)

    job.progress = 10
    db.commit()

    # ---- Load image from S3 ----
    if not image_key:
        raise ValueError("settings.image_key is required")
    try:
        from PIL import Image
        import io as sio
        from workers.heavy_toolpath import s3_client, S3_BUCKET

        obj = s3_client().get_object(Bucket=S3_BUCKET, Key=image_key)
        img = Image.open(sio.BytesIO(obj["Body"].read())).convert("RGB")
    except Exception as e:
        raise RuntimeError(f"Could not load image from S3: {e}")

    job.progress = 20
    db.commit()

    # ---- Run depth estimation ----
    depth_map = estimate_depth(img, target_cols, target_rows)

    job.progress = 80
    db.commit()

    # ---- Normalize to 0..1 (1 = closest = highest relief) ----
    dmin, dmax = depth_map.min(), depth_map.max()
    if dmax - dmin > 1e-6:
        depth_map = (depth_map - dmin) / (dmax - dmin)
    else:
        depth_map = np.ones_like(depth_map) * 0.5

    # ---- Upload as raw float32 + a PNG preview ----
    result_key = f"results/{job.user_id}/{job.id}/ai_depth.bin"
    preview_key = f"results/{job.user_id}/{job.id}/ai_depth_preview.png"

    # W7 FIX (as in heavy_toolpath): hardcoded bucket, no endpoint_url, and a
    # bare except that swallowed the failure into an unreachable /tmp path.
    from PIL import Image as PILImage
    import io as sio
    from workers.heavy_toolpath import put_object

    put_object(result_key, depth_map.astype(np.float32).tobytes(),
               "application/octet-stream",
               {"cols": str(target_cols), "rows": str(target_rows),
                "model": AI_DEPTH_MODEL, "advisory": "ai-assisted-review-before-machining"})

    preview = PILImage.fromarray((depth_map * 255).astype(np.uint8), mode="L")
    png_buf = sio.BytesIO()
    preview.save(png_buf, format="PNG")
    put_object(preview_key, png_buf.getvalue(), "image/png")

    job.result_key = result_key
    job.progress = 100
    db.commit()


def estimate_depth(img, target_w, target_h):
    """Run monocular depth estimation. Returns a (target_h, target_w) float array."""

    if AI_DEPTH_MODEL == "depth_anything_v2":
        return _run_depth_anything(img, target_w, target_h)
    elif AI_DEPTH_MODEL == "midas_v3":
        return _run_midas(img, target_w, target_h)
    else:
        # Fallback: luminosity-based (same as client-side, useful for testing)
        return _fallback_luminosity(img, target_w, target_h)


# W9 FIX: from_pretrained() ran on EVERY job, so each request on the
# autoscaling GPU pool re-loaded (and on a cold container re-downloaded) the
# whole model - seconds to minutes of paid GPU time per job, on the line item
# the design docs call the most expensive. Load once per process and reuse.
_MODEL_CACHE = {}


def _run_depth_anything(img, w, h):
    """Depth Anything V2 (MIT license, transformer-based)."""
    try:
        import torch
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation

        device = "cuda" if torch.cuda.is_available() else "cpu"
        if "da2" not in _MODEL_CACHE:
            name = "depth-anything/Depth-Anything-V2-Small-hf"
            _MODEL_CACHE["da2"] = (
                AutoImageProcessor.from_pretrained(name),
                AutoModelForDepthEstimation.from_pretrained(name).to(device).eval(),
            )
        processor, model = _MODEL_CACHE["da2"]

        inputs = processor(images=img, return_tensors="pt").to(device)
        with torch.no_grad():
            outputs = model(**inputs)
            depth = outputs.predicted_depth

        # Interpolate to target size
        depth = torch.nn.functional.interpolate(
            depth.unsqueeze(1), size=(h, w), mode="bicubic", align_corners=False
        ).squeeze().cpu().numpy()

        return depth.astype(np.float32)

    except ImportError:
        print("WARNING: torch/transformers not available, falling back to luminosity")
        return _fallback_luminosity(img, w, h)


def _run_midas(img, w, h):
    """MiDaS v3 (MIT license, lighter/faster than Depth Anything)."""
    try:
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        if "midas" not in _MODEL_CACHE:
            midas = torch.hub.load("intel-isl/MiDaS", "MiDaS_small")
            midas.to(device).eval()
            _MODEL_CACHE["midas"] = (midas, torch.hub.load("intel-isl/MiDaS", "transforms").small_transform)
        midas, transform = _MODEL_CACHE["midas"]

        input_batch = transform(np.array(img)).to(device)
        with torch.no_grad():
            prediction = midas(input_batch)
            prediction = torch.nn.functional.interpolate(
                prediction.unsqueeze(1), size=(h, w), mode="bicubic", align_corners=False
            ).squeeze().cpu().numpy()

        return prediction.astype(np.float32)

    except ImportError:
        return _fallback_luminosity(img, w, h)


def _fallback_luminosity(img, w, h):
    """Fallback: simple luminosity conversion (same as client-side core).
    Used when GPU/model dependencies aren't available."""
    arr = np.array(img.resize((w, h), resample=1))  # BILINEAR
    gray = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]
    return (gray / 255.0).astype(np.float32)
