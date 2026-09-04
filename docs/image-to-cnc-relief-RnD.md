# Image → CNC Relief Web Software — R&D Reference

**Scope confirmed:** Single photo/image input → production-usable CNC file (STL / heightmap / toolpath) for **2.5D relief carving, engraving, signage** (wood, stone, acrylic, lithophane). Not precision mechanical/parametric CAD.

---

## 1. Two Core Technical Approaches — Comparison

| | **A. Grayscale/Luminosity Heightmap** (deterministic) | **B. AI Monocular Depth Estimation** (generative) |
|---|---|---|
| How it works | Pixel brightness → Z-height directly (dark = low, light = high, or user-defined curve) | Neural network (e.g. depth-estimation models, or full "image-to-3D" models like those behind Meshy/Tripo/Hitem3D) predicts a depth map / full mesh from the photo |
| Accuracy / predictability | **High** — fully deterministic, same input always gives same output, user controls the mapping curve | **Variable** — model "guesses" structure; can hallucinate depth, especially on reflections, shadows, busy backgrounds |
| Production readiness | This is what real, shipped CNC products use today — **Vectric PhotoVCarve/VCarve, ArtCAM, ArtClip3D** all work this way | Newer AI tools (Hitem3D, Meshy, Tripo AI, Neural4D) market "single photo → STL" but are aimed at 3D printing/art, not dimensionally controlled carving; outputs need manual cleanup for CAM use |
| Best for | Portraits, lithophanes, signage, logos, engraving, most relief carving | Turning a product photo/character into a full free-standing 3D-ish object where some "creative" depth is acceptable |
| Control given to user | Depth curve, contrast, gamma, blur/smoothing, invert, masking specific regions | Limited — mostly post-hoc mesh cleanup, not depth control |

**Recommendation:** Since your use-case is CNC carving/relief/signage (not fantasy 3D objects), the **grayscale-heightmap pipeline is the correct production backbone** — it's what every real commercial tool in this space (PhotoVCarve, ArtCAM, ArtClip3D) is built on. AI depth estimation can be layered in as an **optional "smart depth" mode** (useful for portraits/organic subjects where pure luminosity mapping looks flat), but it should never be the only path if "production accuracy" is the promise.

---

## 2. Existing Players — What They Do

| Product | Approach | Notes |
|---|---|---|
| **Vectric PhotoVCarve / VCarve / Aspire** | Grayscale → heightmap → toolpath (V-groove, 3D grayscale, lithophane modes) | Industry standard for this exact use-case; Windows desktop only, not a web product |
| **ArtCAM / ArtClip3D** | Grayscale conversion → G-code | Older, still used in sign/engraving shops |
| **LightBurn** | Vector + raster → laser/engrave paths | Laser-focused, not depth-carving |
| **Hitem3D** | AI-generated 3D relief from photo, exports STL, then refine in "Sculptor"/"Designer" tools | AI depth-estimation-based; explicitly markets CNC relief use-case — closest AI-era competitor to study |
| **Meshy / Tripo AI / Neural4D / PNGtoSTL** | AI photo→mesh (general purpose 3D, not carving-specific) | Web-based, fast, but built for 3D printing/game assets — depth not calibrated for CNC depth-of-cut |

**Gap you can fill:** None of the modern AI tools are *web-based + purpose-built for CNC production* with deterministic control. Vectric-style tools are precise but desktop-only, dated UX, no cloud/collaboration. A web app combining deterministic heightmap control (Vectric-grade precision) with a modern browser-based UI is a real gap.

---

## 3. Recommended Pipeline (Module-Wise)

**Module 1 — Image Ingestion & Pre-processing**
- Upload (JPG/PNG/WEBP), resolution check (need high-res source — output quality is capped by input resolution)
- Auto grayscale conversion + manual contrast/brightness/gamma controls
- Background removal / masking tool (so carve area is isolated from background)
- Optional: AI-assisted subject isolation (segmentation model) — separate from depth generation

**Module 2 — Depth/Heightmap Generation**
- Primary: luminosity-to-height mapping engine (dark→low/light→high, invertible, adjustable curve — linear/gamma/custom spline)
- Blur/smoothing controls to avoid toolpath noise from pixel-level jaggies
- Optional secondary mode: AI depth-estimation overlay for organic subjects (clearly labeled "AI-assisted, review before carving")
- Live 3D preview (WebGL) of the heightmap before export

**Module 3 — Material & Machine Parameters**
- Material size (X/Y/Z), max carve depth, safety margins
- Tool selection (ball-nose, V-bit, flat end mill) — this determines what depth resolution is actually achievable
- Depth clamping to match real tool/material limits (critical for "production accuracy" — this is where most amateur tools fail)

**Module 4 — Toolpath / CAM Generation**
- Convert heightmap → toolpath (raster/roughing + finishing passes) — this is the CAM engine, the hardest part to build well
- V-groove mode (like PhotoVCarve) for line-based engraving
- 3D relief mode (ball-nose stepover-based)
- Output: G-code (machine-specific post-processors) + STL export for review/3D printing/other CAM software

**Module 5 — Export & Interop**
- STL, OBJ (mesh)
- G-code (with post-processor profiles per common CNC controllers — GRBL, Mach3, LinuxCNC, Fanuc-style)
- Project file (re-editable)

**Module 6 — Web Platform Layer**
- Browser-based 3D preview (Three.js/WebGL)
- Cloud processing for depth-map + toolpath generation (heavy compute — Python backend, not client-side)
- User accounts, project storage, job history

---

## 4. Tech Stack Direction

- **Frontend:** React + Three.js (WebGL) for live heightmap/3D preview, canvas-based image editing (grayscale curve, masking)
- **Backend processing:** Python (OpenCV/Pillow for image processing, NumPy for heightmap math, a toolpath/CAM library or custom G-code generator)
- **Optional AI layer:** Depth-estimation model (e.g. open-source monocular depth models) served via API, used only as an assist mode
- **File formats to support:** input JPG/PNG/WEBP → output STL/OBJ + G-code

---

## 5. Key Accuracy Principle for "Production Level"

The screenshot's format list (STEP/IGES/SAT etc.) belongs to a *different* problem (parametric mechanical CAD) — not relevant here. For CNC relief work, "production accuracy" actually means:
1. Deterministic, repeatable depth mapping (not AI guesswork)
2. Depth output correctly clamped to real tool geometry and material thickness
3. High source-image resolution (garbage in → garbage carved)
4. User control over the depth curve, not a black-box AI decision

This is achievable and is a proven, shippable category — unlike single-photo → parametric CAD, which is still research-stage.

---

## Next Step
Once you confirm this direction, next R&D phase: pick specific open-source components (depth-map math, toolpath/G-code generation libraries) and sketch the actual system architecture (frontend/backend/API layers, cloud compute for heavy processing).
