# memexponent.net

Source content (mostly Markdown) for the
[memexponent.net](https://memexponent.net) site.

```
uv sync
uv run post setup
uv run poe render_preview
```

Drafts live on per-post `draft/*` branches in the private `drafts`
remote; see `docs/POST.md`.

Writing a post:

- `uv run post draft new --edit` from a clean `main` starts `draft/<slug>`
- Write, preview with `uv run poe render_preview`, save with `uv run post draft save`
- `uv run post draft switch <slug>` resumes a draft, bringing in `main`'s changes
- `uv run post publish draft <slug> --push` publishes it
- `uv run poe render_production`, then `uv run poe publish`, deploys the site
