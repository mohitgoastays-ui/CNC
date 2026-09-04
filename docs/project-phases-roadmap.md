# Image → CNC Relief Web Software — Master Phased Roadmap

**Product:** Web software that converts a single photo/image into production-usable CNC relief/carving files (heightmap → STL + toolpath/G-code) for wood/stone/acrylic/signage/lithophane.

**Guiding principles (locked from R&D):**
1. **Deterministic core** (pixel-luminosity heightmap), AI depth only as opt-in assist — this is the "production accuracy" promise.
2. **Client-heavy hybrid** — push compute to the browser (like lithophane-studio); server is a rare escalation, not the backbone → **near-zero hosting load.**
3. **Own the CAM/toolpath layer** — that's the gap neither competitor (imagetostl.com / .org) fills; it's the whole differentiator.
4. Build a focused **heightfield drop-cutter** ourselves; use **OpenCAMLib server-side** only for heavy jobs; **avoid PyCAM** (abandoned).

Companion docs: `image-to-cnc-relief-RnD.md`, `image-to-cnc-relief-architecture.md`, `competitor-teardown.md`, `cam-engine-decision.md`.

---

## Phase Overview (at a glance)

| Phase | Name | Goal | Server needed? |
|---|---|---|---|
| 0 | R&D Spike | Prove client-side drop-cutter works end-to-end | No |
| 1 | Core Heightmap Engine (client) | Image → adjustable heightmap → watertight STL + live 3D | No |
| 2 | CAM/Toolpath Engine (client) | Heightmap → material/tool-aware G-code (the differentiator) | No |
| 3 | Web App Shell + UX | Real product UI, projects, export panel | Minimal |
| 4 | Server Escalation Layer | Big jobs + optional AI depth | Yes (thin) |
| 5 | Accounts, Billing, Persistence | SaaS layer | Yes |
| 6 | Polish, Scale, Launch | Post-processors, QA, marketing site | Yes |

Phases 0–3 deliver a **launchable free product** with almost no hosting cost. Phases 4–6 add the paid/scale layer.

---

## PHASE 0 — R&D Spike (Proof of Concept)

**Goal:** Before building the product, prove the riskiest assumption — that a correct CNC toolpath can be generated in the browser from a heightmap.

**Modules:**
- **Minimal heightfield drop-cutter**
  - Input: a grayscale image + one tool spec (ball-nose radius) + max depth
  - Compute: for each XY on a grid, find lowest safe Z where the tool touches the heightfield
  - Output: a simple raster finishing pass as G-code
- **Sanity render**
  - Plot the toolpath over the heightmap to visually confirm correctness
- **Language decision checkpoint**
  - Start in plain JS; measure speed on a 1000×1000 grid
  - If too slow → prototype the same in Rust→WASM and re-measure

**Exit criteria:**
- A grayscale image produces valid, visually-correct G-code entirely in the browser
- Same input → identical output (determinism confirmed)
- Performance acceptable (or WASM path proven)

**Why first:** This one spike de-risks the entire "free client-side CAM" strategy. If it fails, the whole hosting-cost advantage changes — better to know now.

---

## PHASE 1 — Core Heightmap Engine (Client-Side)

**Goal:** Image → clean, adjustable heightmap → watertight STL + live 3D preview. Fully in-browser.

**Modules:**
- **Module 1.1 — Image ingestion**
  - Drag-drop / file picker (JPG/PNG/WEBP)
  - No upload — read into Canvas locally
  - High-res handling: full-res for final, downsampled for live preview
- **Module 1.2 — Image processing (Canvas API)**
  - Grayscale conversion
  - Brightness, contrast, gamma, blur controls
  - Invert, mirror
  - Background removal (simple threshold first)
- **Module 1.3 — Heightmap mapping**
  - Luminosity → Z with adjustable curve (linear/gamma/custom)
  - **Fix competitor bug B3:** always produce a continuous, watertight surface (no "black pixel dropped → holes"); dropping dark areas is opt-in only
  - **Fix competitor bug B2:** edge-aware smoothing/denoise BEFORE mapping, not a bolt-on toggle
- **Module 1.4 — Mesh generation**
  - Surface + base + walls (watertight), like lithophane-studio
  - Resolution/detail control with polygon-budget awareness
- **Module 1.5 — Live 3D preview (Three.js)**
  - Orbit, zoom, wireframe, depth-scale slider
- **Module 1.6 — STL/OBJ export**
  - Binary STL + OBJ, generated client-side

**Exit criteria:** A user can drop an image, tune the heightmap, see it live in 3D, and download a clean watertight STL — no server involved.

---

## PHASE 2 — CAM / Toolpath Engine (Client-Side) — THE DIFFERENTIATOR

**Goal:** Turn the heightmap into **material- and tool-aware, carve-ready G-code.** This is what neither competitor does.

**Modules:**
- **Module 2.1 — Material & machine setup**
  - Stock size (X/Y/Z), origin/zero position, safety margins
  - Units (mm/inch)
- **Module 2.2 — Tool library**
  - Ball-nose, V-bit, flat end mill — diameter, angle, flute specs
  - **Fix competitor gap:** depth is clamped to real stock thickness + tool geometry (not arbitrary "black=0, white=10mm")
- **Module 2.3 — Toolpath strategies**
  - Roughing pass (clear bulk material, larger stepdown)
  - Finishing pass (raster / offset, small stepover, follows heightfield)
  - V-groove mode (PhotoVCarve-style line engraving)
  - Lithophane mode (thickness-based, backlit)
- **Module 2.4 — Drop-cutter core** (from Phase 0, productionized)
  - JS or Rust→WASM depending on Phase 0 result
  - Tiling for large heightfields to respect browser memory
- **Module 2.5 — G-code generation + preview**
  - Emit G-code with feeds/speeds placeholders
  - Toolpath visualization overlaid on the 3D model (simulate the cut)
- **Module 2.6 — Determinism guarantee**
  - Same image + settings → byte-identical G-code (validated in tests)

**Exit criteria:** A user goes image → material/tool setup → downloadable G-code that runs on a real CNC and carves correctly. **At this point the product does something no competitor does.**

---

## PHASE 3 — Web App Shell + UX

**Goal:** Wrap Phases 1–2 into a real, polished product people want to use.

**Modules:**
- **Module 3.1 — App framework & layout**
  - React app (Vite), clean workspace UI (editor + 3D preview + settings panels)
  - Follow `frontend-design` principles — distinctive, not templated
- **Module 3.2 — Guided workflow**
  - Step flow: Upload → Adjust → Material/Tool → Preview → Export
  - Sensible defaults so beginners get a good result in one click
- **Module 3.3 — Project state (local first)**
  - Save/reload project settings in-browser (IndexedDB) — no account needed yet
  - **Fix competitor gap:** real iteration without credit-burn or short retention
- **Module 3.4 — Export panel**
  - STL, OBJ, G-code (per-machine profile), project file
- **Module 3.5 — Responsive + performance**
  - Works on mid-range laptops; graceful handling of large images (warn + downsample/tile)

**Exit criteria:** A launchable, free, static web app (CDN/GitHub-Pages-style hosting) with the full deterministic pipeline. **Hosting cost ≈ near zero.**

---

## PHASE 4 — Server Escalation Layer (Thin Backend)

**Goal:** Handle only what the browser genuinely can't — without turning into a heavyweight backend.

**Modules:**
- **Module 4.1 — Job API + queue**
  - Submit → queue → poll/websocket → download (async pattern)
  - Only triggered for oversized/ultra-detail jobs
- **Module 4.2 — Worker: heavy toolpath**
  - Server-side drop-cutter (reuse WASM core) OR **OpenCAMLib** for hard cases
- **Module 4.3 — Worker: AI depth (opt-in, GPU)**
  - Monocular depth-estimation model for organic subjects (portraits etc.)
  - **Clearly labeled "AI-assisted, review before machining"** — never the default (keeps the honesty edge over imagetostl.org)
  - Separate autoscaling GPU pool (spin up/down; most expensive line item)
- **Module 4.4 — Object storage + CDN**
  - Only for escalation-path files; signed URLs, scoped per user/project
- **Module 4.5 — Routing logic**
  - Client decides: small job → browser; big job / AI → server

**Exit criteria:** Heavy and AI jobs work, but 95% of traffic still never touches the server → cost stays low.

---

## PHASE 5 — Accounts, Billing & Persistence (SaaS Layer)

**Goal:** Turn it into a business.

**Modules:**
- **Module 5.1 — Auth & accounts**
- **Module 5.2 — Cloud project storage + versioning** (Postgres metadata + object storage files)
- **Module 5.3 — Billing model**
  - **Fix competitor gap:** don't charge per-iteration (imagetostl.org's mistake). Free client-side path stays free; charge for AI-depth, heavy server jobs, cloud storage, or a pro subscription — value-based, not iteration-tax
- **Module 5.4 — Usage dashboard / history / "My Creations"**

**Exit criteria:** Users can sign up, save projects to cloud, and pay for premium features.

---

## PHASE 6 — Polish, Scale & Launch

**Goal:** Production hardening and go-to-market.

**Modules:**
- **Module 6.1 — G-code post-processors**
  - GRBL, Mach3, LinuxCNC, Fanuc-style dialects (small abstraction layer)
- **Module 6.2 — QA & determinism test suite**
  - Automated tests: known image → expected G-code hash
  - Real-machine carve validation on sample materials
- **Module 6.3 — Reliability**
  - Dead-letter queue for failed server jobs, error surfacing to user
  - Input validation / abuse protection (dimension caps, file-type checks)
- **Module 6.4 — Marketing site + SEO**
  - Format-specific landing pages (learn from imagetostl.com's SEO breadth)
  - Honest positioning: "production-accurate, tool-aware, carve-ready — not AI guesswork"
- **Module 6.5 — Docs, tutorials, examples**

**Exit criteria:** Stable, marketed, scalable product with the CAM/toolpath moat competitors lack.

---

## Dependency Order (what blocks what)

```
Phase 0 (spike) ──► Phase 1 (heightmap) ──► Phase 2 (CAM) ──► Phase 3 (app)
                                                                   │
                                                                   ├─► Phase 4 (server escalation)
                                                                   │        │
                                                                   │        └─► Phase 5 (SaaS) ──► Phase 6 (launch)
```

- Phases 0→3 are strictly sequential and deliver the free MVP.
- Phase 4 can start in parallel once Phase 2's core exists (server reuses the same drop-cutter).
- Phases 5–6 are business/scale layers on top.

---

## The One-Line Strategy (keep this visible)

> Launch a **free, client-side, deterministic** image→carve tool (near-zero hosting), win on the **CAM/toolpath + material-awareness** layer no competitor has, and monetize only the **heavy/AI/cloud** extras — never taxing iteration.

---

## Immediate Next Action
Start **Phase 0 — the R&D spike** (browser heightfield drop-cutter). Once you say go, we build a minimal working prototype: grayscale image + ball-nose spec → visible toolpath + G-code, running entirely in the browser, to prove the whole strategy before committing to the full build.
