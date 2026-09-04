/**
 * Build the static site Netlify publishes.
 *
 * Source of truth stays in the repo root (one copy of each file); this script
 * assembles `site/` with clean URLs and rewrites the in-page links to match.
 *
 *   node build-site.mjs
 *
 * Only static assets are copied. backend/, tests/, deploy/ and docs/ stay in
 * the repo but are never published — publishing the repo root would serve the
 * Python sources and the deploy config as plain text.
 */
import { mkdirSync, copyFileSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';

const OUT = 'site';
rmSync(OUT, { recursive: true, force: true });
mkdirSync(join(OUT, 'app'), { recursive: true });
mkdirSync(join(OUT, 'rnd'), { recursive: true });

// Landing page -> /  (link to the app becomes a clean /app/ URL)
const landing = readFileSync('index.html', 'utf8')
  .replaceAll('href="phase3-relief-studio.html"', 'href="/app/"');
writeFileSync(join(OUT, 'index.html'), landing);

// The product -> /app/
copyFileSync('phase3-relief-studio.html', join(OUT, 'app', 'index.html'));

// R&D snapshots -> /rnd/  (kept public deliberately; they are the build log)
for (const [src, dst] of [
  ['phase0-dropcutter-spike.html', 'phase0-dropcutter-spike.html'],
  ['phase1-heightmap-engine.html', 'phase1-heightmap-engine.html'],
  ['phase2-cam-engine.html', 'phase2-cam-engine.html'],
]) copyFileSync(src, join(OUT, 'rnd', dst));

writeFileSync(join(OUT, 'robots.txt'), 'User-agent: *\nAllow: /\nDisallow: /rnd/\n');

console.log('site/ built:');
console.log('  /            landing');
console.log('  /app/        Relief Studio');
console.log('  /rnd/        phase 0-2 R&D snapshots (noindex)');
