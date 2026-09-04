# CAM/Toolpath Engine — Build vs Open-Source + The Hosting-Load Decision

This is the highest-risk module. Two separate questions get answered here:
1. **What generates the toolpath/mesh?** (build custom vs. adapt open-source)
2. **WHERE does the heavy compute run?** (this is the real "hosting load" question, and the `lithophane-studio` repo you sent is the key clue)

---

## PART A — The Big Lesson from `lithophane-studio`

That repo is small (3 commits, 3 stars) but it teaches the single most important architectural insight for your cost/hosting problem:

> **It does the ENTIRE image → watertight STL/OBJ pipeline 100% in the browser. Zero backend. Hosted free on GitHub Pages.**

How it pulls that off:
- **Three.js** for 3D rendering/preview
- **Canvas API** for image processing → heightmap
- Custom **watertight mesh generation** (surface + base + walls) written in JS
- Custom STL/OBJ exporters in JS
- **No upload, no sign-up, no server** — the user's own CPU/GPU does all the work

**Why this matters for YOU:** Your original architecture doc assumed heavy compute on backend workers (queue + worker pool + object storage). That's the *safe, scalable* design — but it's also the *expensive* one, and for a big chunk of your pipeline **it may be unnecessary.** lithophane-studio proves the heightmap→mesh half can run entirely on the client for free.

This directly answers your worry ("hosting pe load na aaye, kaam smoothly chale"): **push as much compute to the browser as possible; keep the server for only what the browser genuinely can't do.**

---

## PART B — Which Parts Can Go Client-Side vs. Must Stay Server-Side

| Pipeline stage | Client-side feasible? | Verdict |
|---|---|---|
| Image upload/preview | ✅ Yes | Client (no upload needed at all for many cases) |
| Grayscale / contrast / gamma / blur / invert | ✅ Yes (Canvas API) | **Client** |
| Background removal (simple) | ✅ Yes (Canvas) | **Client** |
| Background removal (AI segmentation) | ⚠️ Maybe (WASM/ONNX in browser) or server | Client if model is small, else server |
| Heightmap → mesh (surface+base+walls) | ✅ Yes (proven by lithophane-studio) | **Client** |
| 3D preview (orbit/zoom/wireframe) | ✅ Yes (Three.js) | **Client** |
| STL/OBJ export | ✅ Yes (proven) | **Client** |
| **AI depth estimation** (organic subjects) | ⚠️ Heavy model | **Server (GPU)** — or optional WASM for a small model |
| **Toolpath / G-code generation** (roughing+finishing, tool geometry) | ⚠️ Compute-heavy, precision-critical | **Server** (or WASM — see Part C) |
| Mesh repair / decimation for huge meshes | ⚠️ Depends on size | Client for small, server for large |

**Key takeaway:** The 3D-printing crowd's whole pipeline (image→STL) can be free/client-side. What makes YOUR product different — **the CAM/toolpath layer** — is the part most likely to need real compute. So your hosting cost is concentrated in exactly one module, not the whole app. That's good: it's a small, controllable surface to optimize.

---

## PART C — Build Custom vs. Open-Source for the CAM Engine

### The open-source options (verified current status)

| Library | What it is | Status (2026) | Language | Fit |
|---|---|---|---|---|
| **OpenCAMLib (aewallin)** | 3D CAM library — cutter-projection algorithms against triangulated surfaces (drop-cutter, waterline, etc.) | Last real release 2023.01.11 but **still actively packaged & rebuilt in 2026** (Arch rebuilt Dec 2025, Debian/Ubuntu carry current builds); LGPL-2.1 | **C++ with Python bindings** | **Best fit** — it does exactly the drop-cutter/waterline math a heightmap CAM needs |
| **PyCAM** | Older pure-Python CAM app/library | **Effectively abandoned** (no meaningful maintenance for years, Python 2-era codebase) | Python | Avoid as a dependency; can read for algorithms only |
| Custom heightmap-specific generator | Write your own raster drop-cutter for the 2.5D relief case | You maintain it | Your choice (Python/JS/Rust/WASM) | Viable because 2.5D relief is a *constrained* problem |

### The nuance most people miss

Full 3D CAM (arbitrary meshes, undercuts, 5-axis) is **genuinely hard** — that's what OpenCAMLib exists for, and rebuilding it would be foolish.

**BUT your use-case is 2.5D relief carving from a heightmap.** That is a *much* narrower problem:
- The surface is a **heightfield** (one Z per XY), not an arbitrary mesh — no undercuts, no overhangs.
- Toolpath = raster or offset passes where, at each XY, you compute the lowest safe Z for the tool shape (ball-nose/V-bit/flat) touching the heightmap. This is the **"drop-cutter on a heightfield"** problem.
- On a regular grid heightfield, this is a **well-defined, parallelizable convolution-like operation** — not the general polyhedral-surface problem.

So you have three realistic strategies:

**Strategy 1 — Adapt OpenCAMLib (server-side)**
- Use its battle-tested drop-cutter/waterline algorithms via Python bindings.
- Pro: proven correctness, handles edge cases, saves months.
- Con: C++/Boost dependency, must run server-side (heavier hosting), build/deploy complexity.

**Strategy 2 — Custom heightfield drop-cutter (can be client-side via WASM)**
- Because heightfield CAM is constrained, you can write a focused drop-cutter yourself in Rust/C compiled to **WebAssembly**, running in the browser.
- Pro: **zero server compute cost** (like lithophane-studio but extended to toolpaths), instant, scales infinitely for free, deterministic.
- Con: you own the algorithm and its correctness; more upfront engineering; very large jobs may exceed browser memory.

**Strategy 3 — Hybrid (recommended)**
- **Client-side (WASM/JS):** everything up to and including mesh + a *preview-quality* toolpath, plus small/medium final jobs.
- **Server-side (OpenCAMLib or your WASM core on a worker):** only large/high-detail final jobs, or the optional AI-depth mode.
- Route by job size: small job → browser; huge job → queue a server worker.

---

## PART D — Recommended Decision (for low hosting load + smooth operation)

**Architecture verdict: Client-heavy hybrid.**

```
DEFAULT PATH (95% of jobs, $0 server cost):
  Browser: image → Canvas heightmap → WASM drop-cutter → G-code + STL
  (Three.js live preview throughout)

ESCALATION PATH (only when needed):
  Large/ultra-detail job OR AI-depth requested
     → upload to server → queue → worker (OpenCAMLib / GPU) → download
```

**Why this is the right call for your stated concern:**
1. **Hosting load stays near-zero for the common case** — the browser does the work, exactly like lithophane-studio hosts free on GitHub Pages. You only pay for compute on the minority of heavy jobs.
2. **Scales for free** — 10,000 users converting normal images cost you almost nothing because it's their CPU.
3. **Deterministic + offline-capable** — client-side heightfield CAM is repeatable (your "production accuracy" promise) and even works without a connection.
4. **Server is a small, optional escalation** — not the backbone. Cheaper, simpler ops, fewer things to break.

**On build-vs-buy specifically:**
- Do **NOT** rebuild general 3D CAM — that's OpenCAMLib's job.
- **DO** build a focused **heightfield drop-cutter** (Strategy 2) because your 2.5D problem is narrow enough to own, and owning it is what unlocks the free client-side path.
- Keep **OpenCAMLib server-side as the escalation/heavy-job engine** so you get proven algorithms where correctness on hard cases matters, without paying its cost on every job.
- **Avoid PyCAM as a runtime dependency** (abandoned) — read its source for algorithm reference only.

---

## PART E — Tech Stack Implication (updated from architecture doc)

| Layer | Original plan | Updated for low-hosting-load |
|---|---|---|
| Heightmap+mesh | Python worker | **Move to browser** (Canvas + Three.js + JS/WASM) |
| Toolpath/G-code | Python worker (custom/OpenCAMLib) | **Custom drop-cutter in Rust→WASM (client)**; OpenCAMLib on server only for heavy jobs |
| AI depth (optional) | GPU worker | Keep server GPU (unavoidable) — but make it opt-in |
| Storage/queue/DB | Always on | **Only on the escalation path**; default path needs none |
| Hosting | Full backend from day 1 | **Static frontend (CDN/GitHub Pages-style) + a thin, autoscaling worker used rarely** |

Net effect: your day-1 product can launch almost entirely as a **static web app** (cheap/free hosting), with the server component added as a small escalation service — the inverse of the heavyweight backend most competitors carry.

---

## PART F — Risks to Watch

- **Browser memory ceiling** on very large heightfields (10,000px+) — mitigate with tiling, or route those to the server path.
- **WASM development effort** — writing a correct drop-cutter in Rust/C is real work; budget an R&D spike to prototype it on a heightfield before committing.
- **G-code post-processor variety** — GRBL/Mach3/LinuxCNC/Fanuc dialects differ; build a small post-processor abstraction (this part is light, do it once).
- **Determinism validation** — must test that the same image+settings always yields byte-identical G-code (your accuracy promise depends on it).

---

## Next Step
Prototype spike: a minimal **heightfield drop-cutter in the browser** (JS first, then Rust→WASM if perf demands) that takes a grayscale image + a ball-nose tool spec and emits a simple raster-finishing G-code path — proving the free client-side CAM path works end-to-end before building the full product around it.
