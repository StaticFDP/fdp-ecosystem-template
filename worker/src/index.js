/**
 * "Add a data source" with ORCID sign-in: an optional Cloudflare Worker for an FDP ecosystem.
 *
 * Contributors without a GitHub account sign in with ORCID and fill in the same form as
 * .github/ISSUE_TEMPLATE/add-data-source.yml. The worker opens a GitHub issue with the same
 * "### Label" sections and the data-source label, so the "Add data source from issue" workflow
 * turns it into a pull request exactly as it does for the GitHub form. The issue also records
 * who submitted it ("### Submitted by" with the ORCID iD); scripts/contributors.py credits that
 * person instead of the account that owns the token.
 *
 * Ported from LeanFDP/staticfdp (worker/src/index.js, April 2026): ORCID OAuth, signed
 * session cookie, honeypot, and the optional dual-write to Codeberg/Forgejo.
 *
 * Routes:
 *   GET  /                    → the form (asks to sign in first)
 *   GET  /auth/orcid          → start ORCID sign-in
 *   GET  /auth/orcid/callback → ORCID redirect target
 *   GET  /auth/logout         → clear the session
 *   POST /submit              → open the issue
 *
 * Vars (wrangler.toml): GITHUB_REPO, SITE_NAME, SITE_URL, optional FORGEJO_BASE_URL, FORGEJO_REPO
 * Secrets (wrangler secret put): GITHUB_TOKEN, ORCID_CLIENT_ID, ORCID_CLIENT_SECRET, SESSION_SECRET,
 *   optional FORGEJO_TOKEN
 * Setup: worker/README.md
 */

const SESSION_COOKIE = 'fdp_session';
const SESSION_TTL = 60 * 60 * 8; // seconds
const ORCID = 'https://orcid.org';

const METHODS = ['web-portal', 'rest-api', 'sparql-endpoint', 'bulk-download', 'cloud-object-storage',
  'darwin-core-archive', 'rdf-dump', 'source-repository', 'aggregator-mirror', 'oai-pmh', 'sql-query', 'change-feed'];

// ── session (HMAC-signed cookie, Web Crypto only) ─────────────────────────────

const enc = s => new TextEncoder().encode(s);
const b64 = buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_');
const unb64 = s => Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0));
const hmacKey = secret => crypto.subtle.importKey('raw', enc(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign', 'verify']);

async function sign(payload, secret) {
  const data = enc(JSON.stringify({ ...payload, exp: Date.now() + SESSION_TTL * 1000 }));
  const sig = await crypto.subtle.sign('HMAC', await hmacKey(secret), data);
  return b64(data) + '.' + b64(sig);
}

async function verify(token, secret) {
  try {
    const [data, sig] = token.split('.');
    const bytes = unb64(data);
    if (!await crypto.subtle.verify('HMAC', await hmacKey(secret), unb64(sig), bytes)) return null;
    const payload = JSON.parse(new TextDecoder().decode(bytes));
    return payload.exp > Date.now() ? payload : null;
  } catch { return null; }
}

function cookie(req, name) {
  const hit = (req.headers.get('Cookie') || '').split(';').map(c => c.trim()).find(c => c.startsWith(name + '='));
  return hit ? hit.slice(name.length + 1) : '';
}

const setCookie = (name, value, maxAge) => `${name}=${value}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=${maxAge}`;
const session = (req, env) => env.SESSION_SECRET ? verify(cookie(req, SESSION_COOKIE), env.SESSION_SECRET) : null;

// ── pages ─────────────────────────────────────────────────────────────────────

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const CSS = `
:root { --fg:#17211c; --sub:#55635b; --line:#d9e1dc; --bg:#f6f8f7; --card:#fff; --accent:#1d5c38; }
@media (prefers-color-scheme: dark) { :root { --fg:#e6eee9; --sub:#a2b2a9; --line:#2a3a31; --bg:#0f1713; --card:#16211b; --accent:#7cc796; color-scheme: dark; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.55 system-ui, sans-serif; }
main { max-width: 44rem; margin: 0 auto; padding: 2rem max(16px, env(safe-area-inset-left)) 4rem; }
h1 { font-size: 1.6rem; margin: 0 0 .25rem; } p.sub { color: var(--sub); margin: 0 0 1.5rem; }
a { color: var(--accent); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 1.25rem; }
.who { display: flex; justify-content: space-between; gap: 1rem; flex-wrap: wrap; font-size: .9rem; color: var(--sub); margin-bottom: 1rem; }
label { display: block; font-weight: 600; margin: 1rem 0 .2rem; } .hint { font-size: .85rem; color: var(--sub); margin: 0 0 .35rem; }
input, select, textarea { width: 100%; font: inherit; padding: .55rem .65rem; border: 1px solid var(--line); border-radius: 6px; background: var(--bg); color: var(--fg); }
textarea { min-height: 6rem; } textarea.code { font-family: ui-monospace, Menlo, monospace; font-size: .85rem; min-height: 9rem; }
button, .btn { display: inline-block; margin-top: 1.5rem; padding: .7rem 1.2rem; border: 0; border-radius: 6px; background: var(--accent); color: var(--bg); font: 600 1rem system-ui, sans-serif; text-decoration: none; cursor: pointer; }
.hp { position: absolute; left: -9999px; }
fieldset { border: 0; padding: 0; margin: 0; }
`;

const page = (env, title, body, status = 200) => new Response(
  `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)} · ${esc(env.SITE_NAME || 'FAIR Data Point')}</title><style>${CSS}</style></head>
<body><main>${body}</main></body></html>`,
  { status, headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' } });

function signInPage(env) {
  return page(env, 'Sign in', `
<h1>Add a data source</h1>
<p class="sub">to <a href="${esc(env.SITE_URL)}">${esc(env.SITE_NAME)}</a></p>
<div class="card">
  <p>Sign in with ORCID so we can credit you for the source you add. We only read your ORCID iD and name.</p>
  <a class="btn" href="/auth/orcid">Sign in with ORCID</a>
  <p class="hint" style="margin-top:1rem">Have a GitHub account? You can also use the
  <a href="https://github.com/${esc(env.GITHUB_REPO)}/issues/new?template=add-data-source.yml">GitHub form</a>.</p>
</div>`);
}

function formPage(env, s) {
  const opt = METHODS.join(', ');
  return page(env, 'Add a data source', `
<h1>Add a data source</h1>
<p class="sub">to <a href="${esc(env.SITE_URL)}">${esc(env.SITE_NAME)}</a>. A bot turns this into a pull request; a maintainer reviews it.</p>
<div class="card">
<div class="who"><span>Signed in as <b>${esc(s.name || s.orcid)}</b> (ORCID <a href="${ORCID}/${esc(s.orcid)}">${esc(s.orcid)}</a>)</span><a href="/auth/logout">Sign out</a></div>
<form method="post" action="/submit">
  <div class="hp" aria-hidden="true"><label for="h_confirm">Leave empty</label><input id="h_confirm" name="h_confirm" tabindex="-1" autocomplete="off"></div>
  <label for="mode">Kind of source</label>
  <p class="hint">Curated: you describe every way to get the data below. Live: the source already runs its own FAIR Data Point.</p>
  <select id="mode" name="mode" required><option>Curated: describe it here</option><option>Live: link its own FAIR Data Point</option></select>
  <label for="slug">Short id</label><p class="hint">Lowercase letters, digits and hyphens; becomes the folder fdp/&lt;id&gt;-fdp/.</p>
  <input id="slug" name="slug" required pattern="[a-z0-9][a-z0-9-]{1,40}" placeholder="world-flora-online">
  <label for="title">Name</label><input id="title" name="title" required placeholder="World Flora Online — global plant taxonomy">
  <label for="description">Description</label><textarea id="description" name="description"></textarea>
  <label for="publisher">Publisher</label><input id="publisher" name="publisher">
  <label for="publisher_url">Publisher website</label><input id="publisher_url" name="publisher_url" type="url">
  <label for="license">Licence</label><p class="hint">URL of the licence, e.g. https://creativecommons.org/licenses/by/4.0/</p><input id="license" name="license" type="url">
  <label for="website">Website</label><input id="website" name="website" type="url">
  <label for="fdp_url">FAIR Data Point URL</label><p class="hint">Live sources only: the FDP root. It must return Turtle and allow cross-site reads.</p><input id="fdp_url" name="fdp_url" type="url">
  <label for="dataset_title">Dataset title</label><input id="dataset_title" name="dataset_title">
  <label for="dataset_version">Dataset version</label><input id="dataset_version" name="dataset_version" placeholder="2026-10">
  <label for="dataset_description">Dataset description</label><textarea id="dataset_description" name="dataset_description"></textarea>
  <label for="distributions">Ways to get the data</label>
  <p class="hint">Curated only. One line per way: <code>access method | title | URL | how to get it</code>. Access methods: ${esc(opt)}. Start a line with <code># title | version | description</code> to begin another dataset.</p>
  <textarea class="code" id="distributions" name="distributions" placeholder="web-portal | WFO website | https://www.worldfloraonline.org | Search a plant name."></textarea>
  <button type="submit">Submit</button>
</form>
</div>`);
}

const done = (env, url) => page(env, 'Thank you', `<h1>Thank you</h1><div class="card"><p>Your source is now
<a href="${esc(url)}">issue on GitHub</a>. A bot turns it into a pull request within a few minutes; you can follow it there.</p>
<a class="btn" href="${esc(env.SITE_URL)}">Back to ${esc(env.SITE_NAME)}</a></div>`);

const fail = (env, msg, status) => page(env, 'Something went wrong', `<h1>Something went wrong</h1><div class="card"><p>${esc(msg)}</p>
<a class="btn" href="/">Back to the form</a></div>`, status);

// ── ORCID sign-in ─────────────────────────────────────────────────────────────

function orcidStart(req, env) {
  const state = b64(crypto.getRandomValues(new Uint8Array(16)));
  const auth = new URL(ORCID + '/oauth/authorize');
  auth.searchParams.set('client_id', env.ORCID_CLIENT_ID);
  auth.searchParams.set('response_type', 'code');
  auth.searchParams.set('scope', '/authenticate');
  auth.searchParams.set('redirect_uri', new URL('/auth/orcid/callback', req.url).toString());
  auth.searchParams.set('state', state);
  return new Response(null, { status: 302, headers: { Location: auth.toString(), 'Set-Cookie': setCookie('fdp_state', state, 600) } });
}

async function orcidCallback(req, env) {
  const url = new URL(req.url);
  const code = url.searchParams.get('code');
  if (!code || !url.searchParams.get('state') || url.searchParams.get('state') !== cookie(req, 'fdp_state'))
    return fail(env, 'ORCID sign-in did not complete. Please try again.', 400);
  const resp = await fetch(ORCID + '/oauth/token', {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ client_id: env.ORCID_CLIENT_ID, client_secret: env.ORCID_CLIENT_SECRET,
      grant_type: 'authorization_code', code, redirect_uri: new URL('/auth/orcid/callback', req.url).toString() }),
  });
  if (!resp.ok) return fail(env, `ORCID did not accept the sign-in (${resp.status}).`, 502);
  const data = await resp.json();
  const token = await sign({ orcid: data.orcid, name: data.name || '' }, env.SESSION_SECRET);
  const headers = new Headers({ Location: '/' });
  headers.append('Set-Cookie', setCookie(SESSION_COOKIE, token, SESSION_TTL));
  headers.append('Set-Cookie', setCookie('fdp_state', '', 0));
  return new Response(null, { status: 302, headers });
}

// ── submit → issue ────────────────────────────────────────────────────────────

const FIELDS = [ // [form name, issue-form label] — labels must match scripts/add_source.py LABELS
  ['mode', 'Kind of source'], ['slug', 'Short id'], ['title', 'Name'], ['description', 'Description'],
  ['publisher', 'Publisher'], ['publisher_url', 'Publisher website'], ['license', 'Licence'], ['website', 'Website'],
  ['fdp_url', 'FAIR Data Point URL'], ['dataset_title', 'Dataset title'], ['dataset_version', 'Dataset version'],
  ['dataset_description', 'Dataset description'], ['distributions', 'Ways to get the data'],
];

function issueBody(fd, s) {
  const out = FIELDS.map(([name, label]) => {
    let v = String(fd.get(name) || '').trim().replace(/^###/gm, '#\u200b##');  // no forged sections
    if (name === 'distributions' && v) v = '```text\n' + v.replace(/```/g, "'''") + '\n```';
    return `### ${label}\n\n${v || '_No response_'}\n`;
  });
  out.push(`### Submitted by\n\nORCID: [${s.orcid}](${ORCID}/${s.orcid})${s.name ? ' — ' + s.name.replace(/[\r\n]/g, ' ') : ''}\n`);
  out.push('_Submitted with the ORCID form._');
  return out.join('\n');
}

async function submit(req, env, s) {
  const fd = await req.formData();
  if (fd.get('h_confirm')) return done(env, env.SITE_URL);   // honeypot: pretend it worked
  const slug = String(fd.get('slug') || '').trim().toLowerCase();
  const title = String(fd.get('title') || '').trim().replace(/[\r\n]/g, ' ');
  if (!/^[a-z0-9][a-z0-9-]{1,40}$/.test(slug) || !title)
    return fail(env, 'The short id must be 2–41 lowercase letters, digits or hyphens, and the name is required.', 400);
  const issue = { title: `Add data source: ${title.slice(0, 120)}`, labels: ['data-source'], body: issueBody(fd, s) };

  if (env.FORGEJO_TOKEN && env.FORGEJO_REPO) {   // optional mirror; never blocks the GitHub write
    fetch(`${env.FORGEJO_BASE_URL || 'https://codeberg.org'}/api/v1/repos/${env.FORGEJO_REPO}/issues`, {
      method: 'POST', headers: { Authorization: `token ${env.FORGEJO_TOKEN}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: issue.title, body: issue.body }),
    }).catch(e => console.error('Forgejo mirror failed:', e.message));
  }

  const resp = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/issues`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${env.GITHUB_TOKEN}`, Accept: 'application/vnd.github+json',
      'Content-Type': 'application/json', 'User-Agent': 'fdp-ecosystem-orcid-form' },
    body: JSON.stringify(issue),
  });
  if (!resp.ok) return fail(env, `GitHub did not accept the issue (${resp.status}). Please try again later.`, 502);
  return done(env, (await resp.json()).html_url);
}

// ── router ────────────────────────────────────────────────────────────────────

export default {
  async fetch(req, env) {
    const { pathname } = new URL(req.url);
    for (const k of ['GITHUB_REPO', 'GITHUB_TOKEN', 'ORCID_CLIENT_ID', 'ORCID_CLIENT_SECRET', 'SESSION_SECRET'])
      if (!env[k]) return fail(env, `The form is not configured yet (${k} is missing). See worker/README.md.`, 500);
    if (pathname === '/auth/orcid') return orcidStart(req, env);
    if (pathname === '/auth/orcid/callback') return orcidCallback(req, env);
    if (pathname === '/auth/logout')
      return new Response(null, { status: 302, headers: { Location: '/', 'Set-Cookie': setCookie(SESSION_COOKIE, '', 0) } });
    const s = await session(req, env);
    if (pathname === '/' && req.method === 'GET') return s ? formPage(env, s) : signInPage(env);
    if (pathname === '/submit' && req.method === 'POST') return s ? submit(req, env, s) : signInPage(env);
    return new Response('Not found', { status: 404 });
  },
};
