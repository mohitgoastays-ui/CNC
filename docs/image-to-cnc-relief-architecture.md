# Image → CNC Relief Web Software — System Architecture (Frontend / Backend / API)

Follow-up to `image-to-cnc-relief-RnD.md`. This covers the actual system design — layers, data flow, and the specific decisions that need to be made before coding starts.

---

## 0. Why This Isn't a Simple CRUD App

Two things make this architecture different from a normal web app:
1. **Heavy compute** (image → heightmap → toolpath/G-code on a 10,000px image) cannot run inside a normal HTTP request — it will time out. It needs an **async job pipeline**.
2. **Large files** (high-res source images, STL meshes, G-code) need **object storage + CDN**, not database blobs.

Everything below is designed around those two constraints.

---

## 1. High-Level Layer Map

```
[ Browser (React + Three.js) ]
        |
        v
[ API Gateway / BFF ]  ---- auth, rate-limit, request validation
        |
        v
[ Core API Service ]  ---- projects, users, job orchestration (sync, fast)
        |
        v
[ Job Queue (Redis/RabbitMQ/SQS) ]
        |
        v
[ Worker Pool: Preprocess -> Heightmap -> Toolpath -> Export ]  ---- async, heavy compute
        |
        v
[ Object Storage (S3-compatible) ] <---> [ CDN ]
        |
        v
[ Postgres DB ]  ---- metadata only, never files
```

---

## 2. Frontend Layer

**Framework:** React (Next.js if you want SSR/SEO for marketing pages + app in one codebase; plain React+Vite if it's app-only behind login).

**Module-wise breakdown:**

| Module | Responsibility | Key tech |
|---|---|---|
| Upload & Image Editor | Drag-drop upload, crop, grayscale/contrast/gamma curve editor, masking brush | Canvas API / `fabric.js` for masking-brush UX |
| 3D Preview | Live render of generated heightmap as a mesh, orbit/zoom, depth-scale slider | Three.js (WebGL) — reads a lightweight preview mesh, NOT the full-res export mesh |
| Material/Machine Setup | Form for stock size, tool type, max depth, safety margins | Plain React forms, client-side validation before job submit |
| Job Status | Shows "processing…" progress bar, since heavy jobs are async | WebSocket or polling against `/jobs/{id}` (see API section) |
| Export Panel | Download STL / G-code / project file; pick post-processor profile | Signed URL download from object storage (never proxy large files through your API server) |
| Project Dashboard | List saved projects, re-open, versioning | Standard REST-backed list/detail views |

**Key architectural decision — preview vs. real output:**
Generate a **low-res preview mesh in-browser or via a fast API call** (for instant feedback while user adjusts curves), but run the **full-resolution heightmap + toolpath generation as a backend async job** only when the user clicks "Generate"/"Export." This avoids sending huge files back and forth on every slider tweak.

---

## 3. API Layer

**Two-tier API, not one:**

### 3a. Core API (synchronous, fast)
Handles anything that should respond in under ~1 second:
- Auth (`/auth/login`, `/auth/refresh`)
- Project CRUD (`/projects`, `/projects/{id}`)
- Upload initiation (`/uploads` → returns a pre-signed URL so the browser uploads **directly to object storage**, not through your API server)
- Job submission (`/projects/{id}/jobs` → enqueues work, returns `job_id` immediately)
- Job status (`/jobs/{id}` → returns `queued | processing | done | failed` + progress %)

### 3b. Job/Worker API (internal only, not public)
Workers pull from the queue and call internal endpoints or write directly to storage + DB — this should **not** be exposed to the browser at all.

**Why split like this:** if heightmap/toolpath generation is slow (large image, complex toolpath), a single synchronous API would either time out or force you to hold HTTP connections open for minutes — bad for scaling. The async pattern (submit → poll/websocket → download) is the standard approach for this class of problem (same pattern video-transcoding or render-farm products use).

**Real-time update option:** WebSocket (or Server-Sent Events) pushing job progress is a nicer UX than polling, but polling every 2-3s is simpler to build first — treat WebSocket as a v2 upgrade, not a launch blocker.

---

## 4. Backend Processing Layer (the actual product)

This is the part that differentiates you from a generic web app — the module-wise pipeline from the R&D doc, now as **separate worker services** connected by the queue:

| Worker | Input | Output | Notes |
|---|---|---|---|
| **Preprocess Worker** | Raw uploaded image | Cleaned grayscale image (masked, normalized) | Python + OpenCV/Pillow; CPU-only, cheap, fast |
| **Heightmap Worker** | Cleaned grayscale image + curve params | Heightmap array (numpy) + low-res preview mesh | Deterministic math (luminosity → Z) — this is your core IP, keep it in-house, not a 3rd-party API |
| **AI Depth Worker (optional, v2)** | Same image | Alternate depth map | GPU-backed, separate autoscaling pool — isolate this because GPU instances are expensive; don't let it block the core deterministic path |
| **Toolpath/CAM Worker** | Heightmap + material/tool params | G-code + full-res STL | Hardest module to build well — either build a custom raster-to-toolpath generator or evaluate existing open-source CAM libraries (e.g. pycam-style approaches) before building from scratch |
| **Export Worker** | Final files | Uploaded to object storage, signed URL generated | Also generates G-code variants per post-processor (GRBL/Mach3/LinuxCNC) |

**Language choice:** Python is the right call for all of this (OpenCV, NumPy, and most CAM/geometry libraries are Python-first). The Core API layer can also be Python (FastAPI is a good fit — async-native, fast to build, same language as workers so your team isn't context-switching between Node and Python).

If you specifically want a Node.js API layer for team/hiring reasons, that's fine too — it would just talk to the Python workers via the queue, same architecture. This is a team-preference decision, not a technical blocker either way.

---

## 5. Data & Storage Layer

| Store | What lives here | Why |
|---|---|---|
| **Postgres** | Users, projects, job metadata, machine/tool presets | Structured, relational, small |
| **Object Storage (S3-compatible)** | Uploaded images, generated heightmaps, STL files, G-code | Large binary files — never put these in Postgres |
| **CDN** (in front of object storage) | Preview thumbnails, downloadable exports | Fast delivery, offloads your API server |
| **Redis** | Job queue + job status cache | Fast, simple, standard for this pattern |

---

## 6. Deployment Shape (Early Stage)

Don't over-engineer this at launch:
- **Start:** Core API + workers as separate Docker containers on a single cloud provider (Render/Railway/Fly.io/AWS ECS) — no need for full Kubernetes yet.
- **Scale trigger:** When toolpath jobs start queueing up under real user load, add autoscaling to the worker pool specifically (queue depth is a clean autoscaling signal — this is where the async architecture pays off).
- **GPU workers (if you add AI depth mode later):** keep completely separate from CPU workers — different instance type, different scaling rules, spin up/down on demand since GPU time is the most expensive line item.

---

## 7. Security/Reliability Notes Specific to This Product

- Validate uploaded images server-side (file type, dimension caps) before they hit the preprocessing worker — large/malformed images are the most likely abuse vector.
- Every project's files should live under a **user/project-scoped storage path** — never a shared bucket root — so signed URLs can't leak across accounts.
- Job queue needs a **dead-letter queue** for failed jobs (e.g., corrupt image, out-of-memory on a huge file) so failures don't silently vanish — surface them back to the user as "failed: reason."

---

## Next Step
Pick the toolpath/CAM generation approach specifically (build custom raster-to-toolpath vs. evaluate open-source geometry/CAM libraries) — this is the single highest-risk, highest-effort module and deserves its own R&D pass before you commit engineering time.
