# ORCID sign-in form (optional)

People without a GitHub account can add a data source by signing in with ORCID. The form asks the same questions as the GitHub issue form, *Add a data source*. On submit, a small Cloudflare Worker opens an issue with the same sections and the `data-source` label. The *Add data source from issue* workflow then generates the catalog and opens a pull request, as it does for the GitHub form.

The issue records who submitted it (`### Submitted by`, with the ORCID iD and name). `scripts/contributors.py` credits that person, not the account that owns the token, when `orcidForm.account` in `fdp.config.json` is set.

Ported from [LeanFDP/staticfdp](https://github.com/LeanFDP/staticfdp) (ORCID OAuth, signed session cookie, honeypot, optional Codeberg mirror). The form only asks ORCID for `/authenticate`: the iD and the name.

## Set it up

You need a Cloudflare account (the free plan is enough) and Node.js.

1. **Register an ORCID app** at <https://orcid.org/developer-tools>. Set the redirect URI to `https://<your-worker>.workers.dev/auth/orcid/callback`. Note the client ID and secret.
2. **Create a GitHub token**: a fine-grained token with *Issues: read and write* on this repository only. A separate bot account is best, because every form issue is opened by this account.
3. **Configure** `worker/wrangler.toml`: set `GITHUB_REPO`, `SITE_NAME` and `SITE_URL`.
4. **Add the secrets and deploy:**

   ```bash
   cd worker
   npx wrangler secret put GITHUB_TOKEN
   npx wrangler secret put ORCID_CLIENT_ID
   npx wrangler secret put ORCID_CLIENT_SECRET
   npx wrangler secret put SESSION_SECRET      # any random string of 32+ characters
   npx wrangler deploy
   ```

5. **Link it from the site:** in `fdp.config.json`, set `orcidForm.url` to the worker URL, and `orcidForm.account` to the GitHub login of the token's account. The viewer then shows *Add with ORCID* next to *Add a data source*.

## Codeberg

To mirror every issue to Codeberg or another Forgejo instance, set `FORGEJO_REPO` (and `FORGEJO_BASE_URL` if it is not codeberg.org) in `wrangler.toml`, and add a `FORGEJO_TOKEN` secret with issue write access. The mirror is written in parallel and never blocks the GitHub issue.

## Routes

| Route | What it does |
|---|---|
| `GET /` | The form, or the ORCID sign-in page when nobody is signed in |
| `GET /auth/orcid` | Starts ORCID sign-in |
| `GET /auth/orcid/callback` | ORCID redirect target; sets an 8-hour signed session cookie |
| `GET /auth/logout` | Clears the session |
| `POST /submit` | Opens the issue |
