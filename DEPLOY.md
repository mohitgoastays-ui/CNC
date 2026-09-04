# Deploying Relief Studio

The site is static. `netlify.toml` and `build-site.mjs` are already configured —
you should not need to type any build settings into the Netlify dashboard.

## What gets published

`build-site.mjs` assembles `site/` from the repo root:

| URL | Source |
|---|---|
| `/` | `index.html` (landing) |
| `/app/` | `phase3-relief-studio.html` (the product) |
| `/rnd/` | `phase0/1/2-*.html` (R&D snapshots, excluded from robots.txt) |

**Only `site/` is published.** `backend/`, `tests/`, `deploy/` and `docs/` stay
in the repo but are never served — publishing the repo root would hand out the
Python sources and the deploy config as plain text.

## Option A — GitHub + Netlify (auto-deploy on every push)

1. Create an **empty** repo on GitHub. Do not add a README or .gitignore;
   this repo already has both.

2. Push:

   ```bash
   git remote add origin https://github.com/USERNAME/REPO.git
   git push -u origin main
   ```

   You will be asked to authenticate. Use a Personal Access Token as the
   password (GitHub → Settings → Developer settings → Personal access tokens →
   Fine-grained, `Contents: Read and write` on this repo only).

3. Netlify → **Add new site** → **Import an existing project** → GitHub →
   pick the repo → **Deploy**.

   Build command and publish directory are read from `netlify.toml`:

   ```
   command = "node build-site.mjs"
   publish = "site"
   ```

Every later `git push` redeploys automatically.

## Option B — drag and drop (fastest, no GitHub)

```bash
node build-site.mjs
```

Then drag the generated `site/` folder onto <https://app.netlify.com/drop>.

No account linking, no CLI. You lose auto-deploy — re-drag after each change.

## Before you make it public

Two things are deliberately unfinished, and both are visible to visitors:

- **Pro and Team plans cannot be purchased.** The billing backend exists but is
  not wired to the client. The pricing cards are labelled "In development" and
  their buttons are inert. Do not re-enable them until checkout actually works.
- **The G-code has never been cut on a physical machine.** The export panel
  carries a warning about this, about the `M6` tool change, and about the work
  origin. Keep it until a real test cut has been done.

## Custom domain

Netlify → Domain settings → Add a domain. TLS is provisioned automatically.
If you point `relief.karvixai.com` here, update `FRONTEND_URL` in
`deploy/.env.example` to match before deploying the backend.

## Verifying a deploy

```bash
node run_tests.py     # 118 tests: 83 browser + 35 server
node build-site.mjs   # then open site/index.html locally
```
