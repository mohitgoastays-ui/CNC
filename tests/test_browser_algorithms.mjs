/**
 * Relief Studio — browser algorithm tests
 *
 * Run: node tests/test_browser_algorithms.mjs
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The previous suite (tests/test_suite.py) re-implemented every algorithm in
 * Python and then asserted against its own reimplementation. Its mirror of
 * surfZ() rounded the grid index; the shipped JavaScript did not. That one
 * difference is what let a completely dead roughing pass — 12 corner plunges,
 * zero cutting distance — sit behind a green "40/40 audit passed" for the whole
 * life of the project. Tests that reimplement the thing under test cannot fail
 * for the reasons that matter.
 *
 * So this file extracts the real @core-start/@core-end region out of
 * phase3-relief-studio.html and executes it. If the product changes, these
 * tests see the change.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const HERE = dirname(fileURLToPath(import.meta.url));
const PRODUCT = join(HERE, '..', 'phase3-relief-studio.html');

const html = readFileSync(PRODUCT, 'utf8');
const core = html.match(/\/\* @core-start[\s\S]*?\*\/([\s\S]*?)\/\* @core-end \*\//);
if (!core) {
  console.error('FATAL: no @core-start/@core-end markers in ' + PRODUCT);
  process.exit(2);
}

const DEFAULTS = {
  matW: 120, matH: 120, matT: 18, depth: 6, base: 3, res: 160,
  safe: 5, plunge: 400, ramp: 15, rapid: 5000,
  dwell: 3, wcs: 'G54', tcMode: 'pause', endMode: 'origin',
  rDia: 6, rStepdown: 2, rStep: 45, rAllow: 0.4, rFeed: 1800, rRpm: 16000,
  fDia: 3, fStep: 12, vAngle: 90, fFeed: 1000, fRpm: 18000,
  bri: 0, con: 0, gam: 1, blk: 0, wht: 100, blur: 1, dialect: 'grbl'
};

/* Top-level const/function in a vm script live in the context's lexical scope,
   not on the global object, so they are handed out explicitly. Verification
   loops run inside the context too: walking ~50k path points per case across
   the host/vm boundary costs far more than generating them. */
const EXPORTS = ['S', 'G', 'num', 'lum', 'boxBlur', 'buildGrid', 'pxPerMmX', 'pxPerMmY',
  'surfZ', 'dilateMaxG', 'dropCutter', 'genRoughing', 'genFinishing', 'generateOps',
  'buildMesh', 'fmt', 'buildGCode', 'fnv1a'];

const EPILOGUE = `
;globalThis.__api={${EXPORTS.join(',')},
  setGrid:(g,n)=>{S.grid=g;S.cols=n;S.rows=n;},
  makeGrid:(kind,n)=>{
    const g=new Float32Array(n*n);
    for(let y=0;y<n;y++)for(let x=0;x<n;x++){
      const cx=x/n-0.5,cy=y/n-0.5;let v;
      if(kind==='dome')v=Math.max(0,1-Math.hypot(cx,cy)*2);
      else if(kind==='flat')v=1;
      else if(kind==='black')v=0;
      else if(kind==='step')v=x<n/2?1:0;
      else if(kind==='gradient')v=x/(n-1);
      else if(kind==='ridge')v=Math.abs(Math.sin(x*0.4))*Math.abs(Math.cos(y*0.3));
      else {let s=(x*73856093)^(y*19349663);s=(s*1103515245+12345)&0x7fffffff;v=(s%1000)/1000;}
      g[y*n+x]=v;}
    return g;},
  finishGouges:(fin,md)=>{const pxX=pxPerMmX(),pxY=pxPerMmY();let g=0,nan=0;
    for(const p of fin.passes)for(const q of p.pts){
      if(!isFinite(q.z)){nan++;continue;}
      if(q.z<surfZ(q.x*pxX,q.y*pxY,md)-1e-6)g++;}
    return {gouges:g,nan};},
  roughGouges:(rough,md)=>{const pxX=pxPerMmX(),pxY=pxPerMmY(),Rmm=rough.dia/2;
    let g=0,worst=0;
    for(const p of rough.passes)for(const q of p.pts){
      const zmax=dropCutter(q.x*pxX,q.y*pxY,Rmm*pxX,Rmm,md,'flat',1);
      if(p.z<zmax-1e-6){g++;if(p.z-zmax<worst)worst=p.z-zmax;}}
    return {gouges:g,worst};},
  idleFraction:(fin)=>{let z=0,nan=0;
    for(const p of fin.passes)for(const q of p.pts){if(q.z===0)z++;if(!isFinite(q.z))nan++;}
    return {idle:z/Math.max(1,fin.npts),nan};},
  meshStats:()=>{const m=S.mesh,e=new Map();
    for(const t of m.T)for(const pair of [[t[0],t[1]],[t[1],t[2]],[t[2],t[0]]]){
      const i=pair[0],j=pair[1],k=i<j?i+'|'+j:j+'|'+i;e.set(k,(e.get(k)||0)+1);}
    const hist={};for(const v of e.values())hist[v]=(hist[v]||0)+1;
    let vol=0,degen=0,badIdx=0,nanV=0;
    for(const t of m.T){
      if(t[0]===t[1]||t[1]===t[2]||t[0]===t[2])degen++;
      for(const i of t)if(!(i>=0&&i<m.V.length))badIdx++;
      const a=m.V[t[0]],b=m.V[t[1]],c=m.V[t[2]];
      vol+=(a[0]*(b[1]*c[2]-b[2]*c[1])-a[1]*(b[0]*c[2]-b[2]*c[0])+a[2]*(b[0]*c[1]-b[1]*c[0]))/6;}
    for(const v of m.V)if(!v.every(isFinite))nanV++;
    return {hist,vol,degen,badIdx,nanV,verts:m.V.length,tris:m.T.length};}
};`;

// Compile once and instantiate per test. Re-parsing the whole core for every
// case dominated the runtime (60+ compiles of a ~9k-line script).
const SCRIPT = new vm.Script(core[1] + EPILOGUE, { filename: 'phase3-core.js' });

function loadCore(overrides = {}) {
  const fields = { ...DEFAULTS, ...overrides };
  const ctx = vm.createContext({
    console,
    performance: { now: () => Date.now() },
    $: id => ({ value: fields[id] }),
    Math, Float32Array, Int32Array, Map, isFinite, parseFloat, TextEncoder
  });
  SCRIPT.runInContext(ctx);
  return ctx.__api;
}

const N = 160;
/** Load the shipped core and seed it with a synthetic heightfield. */
function load(kind, overrides = {}, n = N) {
  const api = loadCore(overrides);
  api.setGrid(api.makeGrid(kind, n), n);
  return api;
}

/* ------------------------------------------------------------------ */
let passed = 0, failed = 0;
const failures = [];
function check(name, cond, detail) {
  if (cond) { passed++; console.log(`  ok   ${name}${detail ? '  ' + detail : ''}`); }
  else { failed++; failures.push(name); console.log(`  FAIL ${name}${detail ? '  ' + detail : ''}`); }
}
let _sec = null, _secT = 0;
function section(t) {
  if (_sec) console.log(`  [${_sec}: ${Date.now() - _secT}ms]`);
  _sec = t; _secT = Date.now();
  console.log(`\n${t}`);
}
process.on('exit', () => { if (_sec) console.log(`  [${_sec}: ${Date.now() - _secT}ms]`); });

/* ================================================================== */
section('surfZ — world-space indexing (regression: B1)');
{
  const ctx = load('dome');
  // genRoughing/genFinishing feed surfZ FLOAT indices derived from world mm.
  // A fractional index into a Float32Array yields undefined -> NaN, which made
  // every downstream comparison false and silently produced no toolpath.
  const pxX = ctx.pxPerMmX(), matW = 120;
  let bad = 0;
  for (let sx = 0; sx < N; sx++) {
    const gx = ((sx / (N - 1)) * matW) * pxX;
    if (!Number.isFinite(ctx.surfZ(gx, 0.5, 6))) bad++;
  }
  check('every fractional index returns a finite Z', bad === 0, `nan=${bad}`);
  check('clamps below range', ctx.surfZ(-99, -99, 6) === ctx.surfZ(0, 0, 6));
  check('clamps above range', ctx.surfZ(999, 999, 6) === ctx.surfZ(N - 1, N - 1, 6));
}

/* ================================================================== */
section('image orientation (regression: B11)');
{
  // Image rows run top->down; world/CNC Y runs bottom->up. Mapping image row 0
  // to grid row 0 (world Y=0) put the top of the picture at the near edge of
  // the stock, so every relief carved vertically mirrored.
  const ctx = loadCore({ res: 40, blur: 0 });
  const w = 100, h = 100, data = new Uint8Array(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const i = (y * w + x) * 4;
    const v = (y < 25 && x < 25) ? 255 : 0;   // white marker: IMAGE top-left
    data[i] = data[i + 1] = data[i + 2] = v; data[i + 3] = 255;
  }
  ctx.S.img = { data, width: w, height: h };
  ctx.buildGrid();
  ctx.buildMesh();
  const c = ctx.S.cols, r = ctx.S.rows, V = ctx.S.mesh.V;
  let sx = 0, sy = 0, n = 0;
  for (let y = 0; y < r; y++) for (let x = 0; x < c; x++) {
    const v = V[y * c + x];
    if (v[2] > -0.5) { sx += v[0]; sy += v[1]; n++; }   // raised (uncut) area
  }
  const cx = sx / n, cy = sy / n;
  check('image top maps to high world Y (not flipped)', cy > 60,
    `centroid Y=${cy.toFixed(1)} of 120mm`);
  check('image left maps to low world X (no X mirror)', cx < 60,
    `centroid X=${cx.toFixed(1)} of 120mm`);

  // and the mirror toggle must still flip X, only X
  const m = loadCore({ res: 40, blur: 0 });
  m.S.img = { data, width: w, height: h };
  m.S.mirror = 1;
  m.buildGrid(); m.buildMesh();
  let mx = 0, my = 0, mn = 0;
  const MV = m.S.mesh.V;
  for (let y = 0; y < r; y++) for (let x = 0; x < c; x++) {
    const v = MV[y * c + x];
    if (v[2] > -0.5) { mx += v[0]; my += v[1]; mn++; }
  }
  check('mirror flips X only', mx / mn > 60 && my / mn > 60,
    `centroid X=${(mx / mn).toFixed(1)} Y=${(my / mn).toFixed(1)}`);
}

/* ================================================================== */
section('aspect ratio — a square feature stays square (regression: B13)');
{
  // The grid was shaped from the stock aspect alone and the image stretched to
  // fill it, so a 720x1280 portrait on the default 120x120 stock came out
  // squashed 1.78x. With the stock fitted to the image, a square marker in the
  // source must measure square in world mm.
  const w = 720, h = 1280;
  const mk = () => {
    const data = new Uint8Array(w * h * 4);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      // 200x200 px square marker, centred
      const v = (Math.abs(x - w / 2) < 100 && Math.abs(y - h / 2) < 100) ? 255 : 0;
      data[i] = data[i + 1] = data[i + 2] = v; data[i + 3] = 255;
    }
    return data;
  };
  const measure = (matW, matH) => {
    const ctx = loadCore({ matW, matH, res: 240, blur: 0 });
    ctx.S.img = { data: mk(), width: w, height: h };
    ctx.buildGrid();
    const c = ctx.S.cols, r = ctx.S.rows, g = ctx.S.grid;
    let cols = 0, rows = 0;
    for (let x = 0; x < c; x++) if (g[Math.floor(r / 2) * c + x] > 0.5) cols++;
    for (let y = 0; y < r; y++) if (g[y * c + Math.floor(c / 2)] > 0.5) rows++;
    return { wmm: cols / (c - 1) * matW, hmm: rows / (r - 1) * matH };
  };

  const fitted = measure(120, 120 * h / w);      // stock fitted to the image
  const ratio = fitted.wmm / fitted.hmm;
  check('square marker measures square when stock matches image', Math.abs(ratio - 1) < 0.05,
    `${fitted.wmm.toFixed(1)}mm x ${fitted.hmm.toFixed(1)}mm  ratio=${ratio.toFixed(3)}`);

  const squashed = measure(120, 120);            // the old default: square stock
  const sr = squashed.wmm / squashed.hmm;
  check('mismatched stock still stretches (documented, not silent)', Math.abs(sr - 1) > 0.4,
    `ratio=${sr.toFixed(3)} — user must opt into this by overriding W/H`);
}

/* ================================================================== */
section('roughing — actually cuts, and clears the cutter (regression: B2)');
{
  const ctx = load('dome');
  const r = ctx.genRoughing();
  check('emits many passes', r.passes.length > 100, `passes=${r.passes.length}`);
  check('has real cutting distance', r.cut > 1000, `cut=${r.cut.toFixed(0)}mm`);
  check('no bare single-point plunges', r.passes.every(p => p.pts.length >= 2));
  check('every level is below Z0', r.passes.every(p => p.z < 0));
  const rg = ctx.roughGouges(r, 6);
  check('cutter never driven into the finished surface', rg.gouges === 0,
    `gouges=${rg.gouges} worst=${rg.worst.toFixed(4)}mm`);
}
{
  const ctx = load('flat');
  check('flat stock produces no roughing', ctx.genRoughing().passes.length === 0);
}

/* ================================================================== */
section('finishing — fine tools resolve (regression: B3)');
for (const dia of [3, 1, 0.5, 0.2]) {
  const ctx = load('dome', { fDia: dia, fStep: 40 });
  const f = ctx.genFinishing();
  const s = ctx.idleFraction(f);
  // Before the fix a Ø0.5mm ball left ~65% of the path riding the stock surface.
  check(`Ø${dia}mm removes material`, s.idle < 0.35 && s.nan === 0,
    `idle=${(100 * s.idle).toFixed(1)}% nan=${s.nan} minZ=${f.minZ.toFixed(3)}`);
}

/* ================================================================== */
section('finishing — no gouge across surfaces x tools');
for (const kind of ['dome', 'flat', 'black', 'step', 'gradient', 'ridge', 'noise']) {
  for (const tool of ['ball', 'flat', 'vbit']) {
    const ctx = load(kind, { fStep: 40 });
    ctx.S.fTool = tool;
    const g = ctx.finishGouges(ctx.genFinishing(), 6);
    check(`${kind}/${tool}`, g.gouges === 0 && g.nan === 0, `gouges=${g.gouges} nan=${g.nan}`);
  }
}

/* ================================================================== */
section('V-bit cone geometry across angles');
for (const angle of [15, 30, 45, 60, 90, 120, 170]) {
  const ctx = load('ridge', { vAngle: angle, fStep: 40 });
  ctx.S.fTool = 'vbit';
  const g = ctx.finishGouges(ctx.genFinishing(), 6);
  check(`${angle} deg no gouge, no NaN`, g.gouges === 0 && g.nan === 0,
    `gouges=${g.gouges} nan=${g.nan}`);
}

/* ================================================================== */
section('depth is clamped to the declared relief depth');
for (const kind of ['dome', 'black', 'step', 'noise']) {
  const f = load(kind, { fStep: 40 }).genFinishing();
  check(`${kind} minZ >= -depth`, f.minZ >= -6 - 1e-9, `minZ=${f.minZ.toFixed(4)}`);
}

/* ================================================================== */
section('mesh — watertight, outward-facing (regression: B4)');
{
  const ctx = load('dome');
  ctx.buildMesh();
  const m = ctx.meshStats();
  // Was {1: 640, 2: 77434} — 640 open edges where a 2-triangle base cap met a
  // 636-segment wall perimeter.
  check('every edge shared by exactly two triangles',
    Object.keys(m.hist).length === 1 && m.hist[2] > 0, JSON.stringify(m.hist));
  // Was negative for the walls: all 1272 wall triangles wound inward.
  check('signed volume positive (consistent outward winding)', m.vol > 0,
    `vol=${m.vol.toFixed(0)}mm3`);
  check('no degenerate triangles', m.degen === 0);
  check('no NaN vertices', m.nanV === 0);
  check('every index in range', m.badIdx === 0);
  console.log(`  (${m.verts} verts, ${m.tris} tris)`);
}

/* ================================================================== */
section('G-code — structure and safety (regression: B5)');
{
  const ctx = load('dome', { fStep: 40 });
  ctx.S.ops = { rough: ctx.genRoughing(), fin: ctx.genFinishing(), ms: 0 };
  const gc = ctx.buildGCode();
  const lines = gc.split('\n');

  check('no NaN or Infinity anywhere', !/NaN|Infinity/.test(gc));
  check('ends with M30', lines[lines.length - 1] === 'M30');
  check('declares mm and absolute mode', gc.includes('G21') && gc.includes('G90'));
  check('both operations present', gc.includes('ROUGHING') && gc.includes('FINISHING'));
  check('spindle stopped after each op', (gc.match(/^M5$/gm) || []).length === 2);

  // Entry must ramp, not plunge vertically into solid stock.
  const ri = lines.findIndex(l => l.includes('ROUGHING'));
  const firstCut = lines.slice(ri, ri + 12).find(l => /^G1 X.*Z/.test(l));
  check('roughing enters on a ramp, not a vertical plunge', !!firstCut, firstCut || 'none');

  const nums = gc.match(/-?\d+\.\d+/g) || [];
  check('all coordinates finite', nums.every(n => Number.isFinite(parseFloat(n))),
    `${nums.length} numbers`);
}

/* ================================================================== */
section('G-code — machine setup and tool change (regression: B20-B22)');
{
  const ctx = load('dome', { fStep: 40 });
  ctx.S.ops = { rough: ctx.genRoughing(), fin: ctx.genFinishing(), ms: 0 };
  const gc = ctx.buildGCode();
  const lines = gc.split('\n');

  check('declares a work offset', lines.includes('G54'));
  check('states where work zero is', /WORK ZERO.*front-left.*TOP surface/.test(gc));
  check('dwells for spindle spin-up before the first cut',
    /^G4 P3\b/m.test(gc) && lines.findIndex(l => /^G4 P3/.test(l)) < lines.findIndex(l => /^G1 /.test(l)));

  // The tool change must stop the machine, not rely on M6 being honoured.
  const m0 = lines.indexOf('M0');
  const t2 = lines.indexOf('M6 T2');
  check('pauses between the two tools', m0 > 0 && t2 > m0, `M0 at ${m0}, M6 T2 at ${t2}`);
  check('retracts before the pause', /^G0 Z5\.000$/.test(lines[m0 - 2] || ''), lines[m0 - 2]);
  check('names the tool to fit', /TOOL CHANGE: fit/.test(lines[m0 - 1] || ''));

  // End of program: clear of the work, then park.
  const end = lines.length - 1;
  check('ends with M30', lines[end] === 'M30');
  check('parks at the origin before ending', lines[end - 1] === 'G0 X0.000 Y0.000');
  check('retracts before parking', lines[end - 2] === 'G0 Z5.000');
}
{
  // Opting out must actually change the program.
  const ctx = load('dome', { fStep: 40, tcMode: 'm6', endMode: 'stay', wcs: 'none', dwell: 0 });
  ctx.S.ops = { rough: ctx.genRoughing(), fin: ctx.genFinishing(), ms: 0 };
  const gc = ctx.buildGCode(), lines = gc.split('\n');
  check('M6-only mode emits no pause', !lines.includes('M0'));
  check('no work offset when set to none', !/^G5[4-6]$/m.test(gc));
  check('no dwell when set to zero', !/^G4 /m.test(gc));
  check('retract-only mode does not park', lines[lines.length - 2] === 'G0 Z5.000');
}

/* ================================================================== */
section('G-code — post-processor dialects');
for (const [dialect, wantG17, commentChar] of
     [['grbl', false, ';'], ['mach3', true, ';'], ['linuxcnc', true, '(']]) {
  const ctx = load('dome', { dialect, fStep: 60 });
  ctx.S.ops = { rough: ctx.genRoughing(), fin: ctx.genFinishing(), ms: 0 };
  const gc = ctx.buildGCode();
  check(`${dialect}: G17 ${wantG17 ? 'present' : 'absent'}`,
    gc.split('\n').includes('G17') === wantG17);
  check(`${dialect}: ${commentChar} comments`, gc.trimStart().startsWith(commentChar));
  check(`${dialect}: has M30`, gc.includes('M30'));
}

/* ================================================================== */
section('determinism');
{
  const run = () => {
    const ctx = load('dome', { fStep: 40 });
    ctx.S.ops = { rough: ctx.genRoughing(), fin: ctx.genFinishing(), ms: 0 };
    const gc = ctx.buildGCode();
    return { gc, hash: ctx.fnv1a(gc) };
  };
  const a = run(), b = run();
  check('same input -> byte-identical G-code', a.gc === b.gc);
  check('same input -> same hash', a.hash === b.hash, `hash=${a.hash}`);

  const c = load('dome', { depth: 6.5, fStep: 40 });
  c.S.ops = { rough: c.genRoughing(), fin: c.genFinishing(), ms: 0 };
  check('different input -> different hash', c.fnv1a(c.buildGCode()) !== a.hash);
}

/* ================================================================== */
section('scallop height');
{
  const a = load('dome', { fStep: 20 }).genFinishing().scallop;
  const b = load('dome', { fStep: 60 }).genFinishing().scallop;
  check('scallop grows with stepover', b > a, `${a.toFixed(4)} -> ${b.toFixed(4)}`);
  check('scallop positive and finite', a > 0 && Number.isFinite(a));
  const flat = load('dome', { fStep: 60 });
  flat.S.fTool = 'flat';
  check('flat tool reports no scallop', flat.genFinishing().scallop === null);
}

/* ================================================================== */
section('program size is bounded (regression: B9)');
{
  // A fine tool with a tiny stepover could ask for ~2.4M points, which locks
  // the tab and emits a file no controller can load.
  const ctx = load('dome', { fDia: 0.2, fStep: 1 });
  const f = ctx.genFinishing();
  check('finishing point count bounded', f.npts <= 600000, `npts=${f.npts}`);
  check('clamping is reported, not silent', f.limited === true &&
    f.stepMm > f.reqStepMm, `req=${f.reqStepMm.toFixed(3)} used=${f.stepMm.toFixed(3)}`);

  const r = load('dome', { rStepdown: 0.05 }).genRoughing();
  check('roughing point count bounded', r.npts <= 600000, `npts=${r.npts}`);
  check('stepdown clamp reported', r.limited === true, `used=${r.stepdown.toFixed(3)}mm`);
}

/* ================================================================== */
section('input hardening');
{
  const hostile = [
    { matW: -50, matH: 0, depth: -3 },
    { fDia: 0, rStepdown: 0, fStep: 1 },
    { matW: 1e9, depth: 1e9, fStep: 500 },
    { gam: 0, con: 999, blur: 99, vAngle: 0 }
  ];
  for (const [i, ov] of hostile.entries()) {
    let threw = null, clean = false;
    try {
      const ctx = load('noise', ov);
      ctx.S.ops = { rough: ctx.genRoughing(), fin: ctx.genFinishing(), ms: 0 };
      clean = !/NaN|Infinity|undefined/.test(ctx.buildGCode());
    } catch (e) { threw = e.message; }
    check(`hostile input set ${i + 1} survives`, !threw && clean, threw || 'clean');
  }
}

/* ================================================================== */
console.log(`\n${passed} passed, ${failed} failed`);
if (failed) { console.log('\nFailures:\n  ' + failures.join('\n  ')); process.exit(1); }
process.exit(0);
