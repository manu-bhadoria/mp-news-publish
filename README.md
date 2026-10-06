# MP News publishing

The newsroom portal (https://mp-news-dashboard.nihilycho.workers.dev/portal/) starts the `publish` workflow when a
story is published, edited or removed. The workflow rebuilds only the changed pages of the chosen sites with the
sites' own engine and uploads them to Cloudflare, then checks every link and reports back to the portal.

- `tools/publish.py`: one run (claim the work, build, deploy, check, report).
- `tools/cfdeploy.py`: Cloudflare Pages / Workers static-assets deploys from an asset manifest plus changed files.
- `tools/manifest.cjs`: the asset manifest of a built site, hashed the way wrangler does.
- Release `state`: `inputs.tar.zst` (site engine and site data) and `manifests.tar.zst` (what each site serves now),
  uploaded by the newsroom laptop after it deploys sites.

Secrets: `BUILD_KEY`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`. No passwords or keys are stored in the repo.
