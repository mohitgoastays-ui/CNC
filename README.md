# Relief Studio

**Image → production-accurate CNC relief carving files, entirely in the browser.**

The only web tool that goes from a single photo to material- and tool-aware, carve-ready G-code with deterministic precision — no server for 95% of jobs, no upload, works offline.

---

## What's in this repo

```
relief-studio/
├── phase3-relief-studio.html   ← THE PRODUCT (open in any browser)
├── phase0-dropcutter-spike.html   R&D proof-of-concept
├── phase1-heightmap-engine.html   Heightmap + 3D preview standalone
├── phase2-cam-engine.html         CAM engine standalone
├── backend/
│   ├── app/main.py                FastAPI: auth, projects, jobs, billing
│   ├── workers/
│   │   ├── heavy_toolpath.py      NumPy server-side CAM (large jobs)
│   │   ├── ai_depth.py            GPU depth estimation (optional)
│   │   └── runner.py              Redis job queue consumer
│   └── requirements.txt
├── deploy/
│   ├── docker-compose.yml         Full stack: Postgres + Redis + MinIO + API + workers
│   ├── Dockerfile.api
│   └── Dockerfile.worker
├── tests/
│   └── test_suite.py              45-test automated QA suite
└── docs/
    ├── image-to-cnc-relief-RnD.md
    ├── image-to-cnc-relief-architecture.md
    ├── competitor-teardown.md
    ├── cam-engine-decision.md
    └── project-phases-roadmap.md
```

## Quick start

**Client-side product (no install):**
Open `phase3-relief-studio.html` in any modern browser. That's it.

**Server escalation (for heavy jobs / AI depth / cloud persistence):**
```bash
cd deploy
docker compose up -d
# API at http://localhost:8000
# MinIO console at http://localhost:9001 (minioadmin/minioadmin)
```

## Phase map

| Phase | What | Status |
|-------|------|--------|
| 0 | R&D spike — prove browser drop-cutter works | Built |
| 1 | Heightmap engine + WebGL 3D + mesh exports | Built, tested |
| 2 | CAM engine — roughing, finishing, V-bit, scallop | Built, tested |
| 3 | Unified product app (all above merged) | Built, tested |
| 4 | Server escalation — heavy jobs + AI depth | Backend only — **not wired to the client** |
| 5 | Auth, billing, cloud persistence | Backend only — **no UI** |
| 6 | QA test suite, post-processors | 115 tests against real code |

## Test results

```
python run_tests.py
```

**Browser algorithms — 80 tests** (`tests/test_browser_algorithms.mjs`)
Extracts the real `@core-start`/`@core-end` region out of
`phase3-relief-studio.html` and executes it under Node. No reimplementation.
- No-gouge across 7 surface types × 3 tool types, plus 7 V-bit angles (15°–170°)
- Roughing emits real cutting passes and the cutter disc clears the finished surface
- Fine tools (down to Ø0.2mm) resolve instead of riding the stock
- Mesh manifold: every edge shared by exactly 2 triangles, positive signed volume
- Determinism: same input → byte-identical G-code
- Program size bounded, and any clamp is reported rather than silent
- Hostile numeric inputs produce no NaN/Infinity

**Server workers — 35 tests** (`tests/test_suite.py`)
Imports the real worker modules.
- Drop-cutter no-gouge for ball / flat / V-bit
- Grid-scale parity with the browser convention
- Heightfield validation rejects missing / wrong-length / NaN input
- G-code depth clamping, dialects, determinism

### A note on the previous suite

The earlier `tests/test_suite.py` reimplemented every algorithm in Python and
asserted against its own reimplementation. Its mirror of `surfZ()` rounded the
grid index; the shipped JavaScript did not. That one difference is why a
completely dead roughing pass — 12 corner plunges, zero cutting distance —
passed a "40/40 audit" for the life of the project. Several other tests built a
G-code string inside the test and then asserted on the string they had just
written. Those numbers should not be trusted; these ones execute the product.

## Architecture

```
DEFAULT PATH (95% of jobs, $0 server cost):
  Browser: image → Canvas heightmap → JS drop-cutter → G-code + STL

ESCALATION PATH (only when needed):
  Large job OR AI depth → upload → Redis queue → worker → download
```

## Export formats

**In scope (honest, accurate):**
STL (binary/ASCII) · OBJ · PLY · 3MF · glTF · SVG · DXF · depth PNG · G-code/NC (GRBL/Mach3/LinuxCNC)

**Out of scope by design (would be fake):**
STEP · IGES · SAT · SLDPRT · IPT · CATPart · F3D · DWG · FBX — these are parametric/proprietary formats a heightmap can't honestly produce.

## Competitive position

> imagetostl.com = honest but stops at raw mesh, no machining.
> imagetostl.org = AI hype, overclaims accuracy, non-deterministic.
> Relief Studio = the only web tool that goes image → controlled heightmap → material/tool-aware, watertight, carve-ready toolpaths/G-code, with deterministic precision.

## Known limitations

Stated plainly, because the whole positioning rests on being the honest option.

- **The client does not talk to the backend.** `phase3-relief-studio.html`
  contains no network calls at all. Phases 4–5 exist as server code with tests,
  but nothing in the UI submits a job, signs in, or saves to the cloud. The
  escalation path is not reachable by a user today.
- **In-browser grid is capped at 600px**, and total program size is capped at
  600k points per operation. When a requested stepover or stepdown would exceed
  that, it is clamped and the UI says so — the achieved value, not the requested
  one, is what the scallop figure and the G-code reflect.
- **AI depth is server-side only** and has no UI entry point.
- **No email verification or password reset** on the auth routes.
- Roughing is a Z-level raster with a tool-radius-aware keep-out region. It is
  not adaptive/trochoidal clearing.

## License

Proprietary — KarvixAI.
