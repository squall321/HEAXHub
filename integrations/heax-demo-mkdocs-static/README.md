# heax-demo-mkdocs-static

A HEAXHub demo integration that ships a **pre-built MkDocs documentation
site**. The portal serves it via Caddy's `file_server` — no Python or
MkDocs runtime is needed on the portal side.

## Layout

```
heax-demo-mkdocs-static/
  .portal/manifest.yaml   # HEAXHub integration manifest (stack: mkdocs_static)
  mkdocs.yml              # MkDocs source config (reference only, not used at runtime)
  site/                   # <-- what `mkdocs build` produced; served as-is
    index.html
    api/index.html
    guide/index.html
    css/heax-docs.css
  README.md
```

## How the build works

The operator runs `mkdocs build` on their machine **before committing**:

```bash
mkdocs build --clean --strict
```

That command takes the source `docs/` (not shipped here) plus `mkdocs.yml`
and writes the rendered HTML/CSS/JS into `site/`. We then commit `site/`
so the portal can serve it directly.

For this demo the `site/` directory is hand-crafted to mimic what real
MkDocs Material output looks like (header, sidebar, content area, inline
styling under `css/`).

## How HEAXHub serves it

The manifest declares:

```yaml
build:
  stack: mkdocs_static
  root: site
launch:
  mode: service
```

The portal's `mkdocs_static` stack resolver generates a Caddy config that
points `file_server` at the `site/` directory and exposes it under the
integration's route. No build step runs on the portal — the artifact is
already final.

## Rebuilding

1. Edit the MkDocs source (`docs/*.md`, `mkdocs.yml`).
2. Run `mkdocs build`.
3. Commit the updated `site/` directory.
4. Re-register / re-deploy the integration in the portal.
