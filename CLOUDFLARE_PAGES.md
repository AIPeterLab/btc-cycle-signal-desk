# Cloudflare Pages Setup

This repo is ready for a Git-connected Cloudflare Pages deployment. Do not change the BTC signal method or the GitHub Actions refresh workflow for Cloudflare hosting.

## Project

- Cloudflare Pages project name: `btc-signal-desk`
- Git provider: GitHub
- Repository: `AIPeterLab/btc-cycle-signal-desk`
- Production branch: `main`
- Project subdomain after creation: `btc-signal-desk.pages.dev`
- Production custom domain: `btc.aipeterlab.com`

## Build Settings

- Framework preset: `None`
- Build command: leave blank
- Build output directory: `/`
- Root directory: leave blank / repository root
- Environment variables: none

This is a static dashboard. Cloudflare Pages should upload the repository root, which contains `index.html`, `data/signals.json`, and `data/signals.csv`.

## Custom Domain

After the first Pages deployment succeeds:

1. Open Cloudflare dashboard.
2. Go to Workers & Pages.
3. Open `btc-signal-desk`.
4. Go to Custom domains.
5. Select Set up a domain.
6. Enter `btc.aipeterlab.com`.
7. Confirm the DNS record Cloudflare proposes.

Expected DNS record:

```text
Type: CNAME
Name: btc
Target: btc-signal-desk.pages.dev
Proxy status: Proxied
TTL: Auto
```

If Cloudflare manages the `aipeterlab.com` zone in the same account, it should add this CNAME automatically during the custom-domain setup. If it asks you to add the record manually, add the CNAME above and then return to the Pages custom-domain screen to finish validation.

## Refresh Workflow

Keep the existing workflow unchanged:

- Daily refresh file: `.github/workflows/daily-update.yml`
- Local updater: `python scripts/update_signals.py`
- Generated dashboard data: `data/signals.json` and `data/signals.csv`

Because Cloudflare Pages is connected to GitHub, every successful refresh commit on `main` should trigger a new Cloudflare Pages production deployment.
