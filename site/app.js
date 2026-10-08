'use strict';
// Static FDP browser: fetches the index Turtle, follows rdfs:seeAlso to every
// sub-catalog, parses everything client-side with N3.js and renders
// index → catalog → dataset → distributions. No server, no build step.
//
// ?index=<url> browses another FDP laid out the same way.
//
// An index entry whose rdfs:seeAlso points at a live FAIR Data Point (not a
// catalog.ttl file) is crawled link by link: FDP → catalogs → datasets and data
// services → distributions → access services. Missing access-method types are
// inferred, so live FDPs render like the curated catalogs.

const NS = {
  rdf:  'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
  rdfs: 'http://www.w3.org/2000/01/rdf-schema#',
  dcat: 'http://www.w3.org/ns/dcat#',
  dct:  'http://purl.org/dc/terms/',
  foaf: 'http://xmlns.com/foaf/0.1/',
  prov: 'http://www.w3.org/ns/prov#',
  skos: 'http://www.w3.org/2004/02/skos/core#',
  fdp:  'https://w3id.org/fdp/fdp-o#',
};
const IANA = 'https://www.iana.org/assignments/media-types/';
const EU_FILE_TYPE = 'http://publications.europa.eu/resource/authority/file-type/';
const ORDERED = new Set([NS.fdp + 'hasCatalog', NS.fdp + 'metadataCatalog', NS.dcat + 'catalog', NS.dcat + 'dataset',
                         NS.dcat + 'service', NS.dcat + 'distribution', NS.dcat + 'accessService']);
// Links followed when crawling a live FDP, and the limits that keep a slow or huge FDP from stalling the page.
const CRAWL_LINKS = [NS.fdp + 'metadataCatalog', NS.dcat + 'catalog', NS.dcat + 'dataset', NS.dcat + 'service',
                     NS.dcat + 'distribution', NS.dcat + 'accessService'];
const CRAWL_MAX_DOCS = 200;
const FETCH_TIMEOUT_MS = 12000;

// Instance settings, injected from fdp.config.json by scripts/build_static.py.
const CFG = window.FDP_CONFIG || {};
const INDEX = new URLSearchParams(location.search).get('index') || `fdp/${CFG.indexFolder || 'index'}/catalog.ttl`;
const INDEX_IS_LOCAL = !/^https?:\/\//i.test(INDEX);

const { namedNode } = N3.DataFactory;
const store = new N3.Store();
const order = new Map();      // IRI → position of first mention in a list, to keep Turtle order
const sources = new Map();    // file IRI → Turtle text
let rawBase = null;           // canonical prefix that maps to this site's root (local mode)
let repo = null;              // { org, name, branch, path } when the index lives on raw.githubusercontent.com
let indexModel = null;
let catalogs = [];
let failures = [];
let methodFilter = null;
let amBase = null;            // namespace of the access-method vocabulary, for inferred types
let contributors = null;      // contributors.json, generated from the GitHub repository at deploy time
let fairReport = null;        // fair-report.json, from the FAIR assessment workflow

const $ = id => document.getElementById(id);
const esc = s => s == null ? '' : String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const safeUrl = u => (/^https?:\/\//i.test(u || '') ? u : null);
const link = (u, text, cls) => safeUrl(u) ? `<a href="${esc(u)}" target="_blank" rel="noopener"${cls ? ` class="${cls}"` : ''}>${esc(text)}</a>` : '';
const localName = iri => (iri || '').replace(/[\/#]$/, '').split(/[\/#]/).pop();

// ── Loading ─────────────────────────────────────────────────────────────────

function fetchLocation(iri) {
  // In local mode the canonical IRIs are served from this site's fdp/ folder.
  if (rawBase && iri.startsWith(rawBase)) return 'fdp/' + iri.slice(rawBase.length);
  return iri;
}

async function load(iri) {
  if (sources.has(iri)) return;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
  let res;
  try {
    res = await fetch(fetchLocation(iri.split('#')[0]), { headers: { Accept: 'text/turtle' }, signal: ctrl.signal });
  } catch (e) {
    throw new Error(`${iri}: ${e.name === 'AbortError' ? 'timed out' : 'unreachable (network or CORS)'}`);
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${iri}`);
  const text = await res.text();
  sources.set(iri, text);
  const graph = namedNode(iri);
  await new Promise((resolve, reject) => {
    new N3.Parser({ baseIRI: iri }).parse(text, (err, q) => {
      if (err) return reject(new Error(`${localName(iri.replace(/\/catalog\.ttl$/, ''))}: ${err.message}`));
      if (!q) return resolve();
      store.addQuad(q.subject, q.predicate, q.object, graph);
      if (ORDERED.has(q.predicate.value) && !order.has(q.object.value)) order.set(q.object.value, order.size);
    });
  });
}

const objects = (s, p) => store.getObjects(namedNode(s), namedNode(p), null);
function lit(s, p) {
  const os = objects(s, p);
  if (!os.length) return null;
  return (os.find(o => o.language === 'en') || os.find(o => !o.language) || os[0]).value;
}
const iri = (s, p) => (objects(s, p).find(o => o.termType === 'NamedNode') || {}).value || null;
const iris = (s, p) => objects(s, p).filter(o => o.termType === 'NamedNode').map(o => o.value)
  .sort((a, b) => (order.has(a) ? order.get(a) : 1e9) - (order.has(b) ? order.get(b) : 1e9));
const hasType = (s, t) => store.has(namedNode(s), namedNode(NS.rdf + 'type'), namedNode(t));
const value = (s, p) => lit(s, p) || iri(s, p);

async function init() {
  const indexIri = INDEX_IS_LOCAL ? new URL(INDEX, location.href).href : INDEX;
  await load(indexIri);

  // The FDP root of the index file: a metadata service listing catalogs.
  const roots = store.getSubjects(namedNode(NS.fdp + 'hasCatalog'), null, namedNode(indexIri)).map(t => t.value);
  const root = roots[0];
  if (!root) throw new Error('No fdp:hasCatalog found in ' + INDEX);

  // Canonical IRI of the index file, e.g. <baseIri><indexFolder>/catalog.ttl. Locally, everything under
  // the canonical base is served from this site's fdp/ folder, whatever the base (GitHub raw, w3id, …).
  const canonicalIndex = root.endsWith('/') ? root + 'catalog.ttl' : root;
  const localBase = INDEX_IS_LOCAL && canonicalIndex.endsWith(INDEX.replace(/^fdp\//, ''))
    ? canonicalIndex.slice(0, -INDEX.replace(/^fdp\//, '').length) : null;
  if (localBase) {
    rawBase = localBase;
    // Re-key the already-loaded index under its canonical IRI so links and edits use it.
    sources.set(canonicalIndex, sources.get(indexIri));
    for (const q of store.getQuads(null, null, null, namedNode(indexIri)))
      store.addQuad(q.subject, q.predicate, q.object, namedNode(canonicalIndex));
  }
  const m = canonicalIndex.match(/^https:\/\/raw\.githubusercontent\.com\/([^/]+)\/([^/]+)\/([^/]+)\/(.+)$/);
  if (INDEX_IS_LOCAL && CFG.repository) {
    const [org, name] = CFG.repository.split('/');
    repo = { org, name, branch: CFG.branch || 'main' };
  } else if (m) repo = { org: m[1], name: m[2], branch: m[3] };

  indexModel = {
    iri: root, file: canonicalIndex,
    title: lit(root, NS.dct + 'title'), description: lit(root, NS.dct + 'description'),
    modified: lit(root, NS.dct + 'modified'),
    publisher: lit(iri(root, NS.dct + 'publisher') || '', NS.foaf + 'name'),
  };

  // Sub-catalog files (rdfs:seeAlso on each fdp:hasCatalog entry) and helper vocabularies
  // (rdfs:seeAlso on the root that point to .ttl files, e.g. the access-method scheme).
  const refs = iris(root, NS.fdp + 'hasCatalog').map(c => ({ ref: c, file: iri(c, NS.rdfs + 'seeAlso'), title: lit(c, NS.dct + 'title') }));
  const vocabFiles = iris(root, NS.rdfs + 'seeAlso').filter(u => /\.ttl$/.test(u));
  const results = await Promise.allSettled([...refs.filter(r => r.file).map(r => load(r.file)), ...vocabFiles.map(load)]);
  failures = results.filter(r => r.status === 'rejected').map(r => r.reason.message);

  // Entries that point at a live FAIR Data Point rather than a catalog file: crawl them.
  for (const r of refs) r.liveRoot = r.file && sources.has(r.file) ? liveRootIn(r.file) : null;
  await Promise.all(refs.filter(r => r.liveRoot).map(r => crawl(r.liveRoot).then(errs => failures.push(...errs))));

  // The access-method vocabulary is the linked file that defines a skos:ConceptScheme.
  const vocab = vocabFiles.find(f => store.getSubjects(namedNode(NS.rdf + 'type'), namedNode(NS.skos + 'ConceptScheme'), namedNode(f)).length);
  amBase = vocab ? vocab.replace(/\.ttl$/, '#') : null;
  catalogs = refs.map(r => r.liveRoot ? buildLiveCatalog(r) : buildCatalog(r)).filter(Boolean);
  [contributors, fairReport] = await Promise.all([loadContributors(), loadJson('fair-report.json')]);
  renderChrome();
  route();
}

// ── Model ───────────────────────────────────────────────────────────────────

function buildCatalog({ ref, file, title }) {
  const graph = file ? namedNode(file) : null;
  if (file && !sources.has(file)) return { key: localName(ref), ref, file, title, missing: true, datasets: [] };
  // The dcat:Catalog in that file that actually lists datasets.
  const cat = store.getSubjects(namedNode(NS.dcat + 'dataset'), null, graph).map(t => t.value)
    .find(s => hasType(s, NS.dcat + 'Catalog')) || ref;
  const key = file ? localName(file.replace(/\/catalog\.ttl$/, '')) : localName(ref);
  return {
    key, ref, file, iri: cat,
    title: lit(cat, NS.dct + 'title') || title,
    description: lit(cat, NS.dct + 'description'),
    landingPage: iri(cat, NS.dcat + 'landingPage'),
    license: iri(cat, NS.dct + 'license'),
    publisher: lit(iri(cat, NS.dct + 'publisher') || '', NS.foaf + 'name'),
    datasets: iris(cat, NS.dcat + 'dataset').map(buildDataset),
  };
}

function buildDataset(ds) {
  return {
    iri: ds, id: localName(ds),
    title: lit(ds, NS.dct + 'title'), description: lit(ds, NS.dct + 'description'),
    version: lit(ds, NS.dcat + 'version') || lit(ds, NS.dct + 'version') || lit(ds, NS.dct + 'hasVersion'),
    issued: lit(ds, NS.dct + 'issued') || (lit(ds, NS.fdp + 'metadataIssued') || '').slice(0, 10) || null,
    modified: lit(ds, NS.dct + 'modified') || (lit(ds, NS.fdp + 'metadataModified') || '').slice(0, 10) || null,
    identifier: value(ds, NS.dct + 'identifier'), landingPage: iri(ds, NS.dcat + 'landingPage'),
    license: iri(ds, NS.dct + 'license'), derivedFrom: iris(ds, NS.prov + 'wasDerivedFrom'),
    distributions: iris(ds, NS.dcat + 'distribution').map(buildDistribution),
  };
}

function buildDistribution(d) {
  const type = iri(d, NS.dct + 'type');
  const media = value(d, NS.dcat + 'mediaType');
  const format = value(d, NS.dct + 'format');
  return withInferredType({
    iri: d, id: localName(d),
    title: lit(d, NS.dct + 'title'), description: lit(d, NS.dct + 'description'),
    type, typeLabel: type ? (lit(type, NS.skos + 'prefLabel') || localName(type)) : null,
    mediaType: media && media.startsWith(IANA) ? media.slice(IANA.length) : media,
    format: format && format.startsWith(EU_FILE_TYPE) ? format.slice(EU_FILE_TYPE.length) : format,
    accessURL: iri(d, NS.dcat + 'accessURL'), downloadURL: iri(d, NS.dcat + 'downloadURL'),
    license: iri(d, NS.dct + 'license'), identifier: value(d, NS.dct + 'identifier'),
    derivedFrom: iris(d, NS.prov + 'wasDerivedFrom'),
    services: iris(d, NS.dcat + 'accessService').map(s => ({
      iri: s, title: lit(s, NS.dct + 'title'),
      endpointURL: iri(s, NS.dcat + 'endpointURL'),
      endpointDescription: iri(s, NS.dcat + 'endpointDescription'),
      conformsTo: iri(s, NS.dct + 'conformsTo'),
    })),
  });
}

// ── Contributors ────────────────────────────────────────────────────────────

// Written during the Pages build; absent when browsing another FDP or locally.
async function loadJson(name) {
  if (!INDEX_IS_LOCAL) return null;
  try {
    const res = await fetch(name, { cache: 'no-cache' });
    return res.ok ? await res.json() : null;
  } catch { return null; }
}
const loadContributors = () => loadJson('contributors.json');
const personByLogin = login => (contributors && contributors.people.find(p => p.login === login)) || { login, name: null, avatar: `https://github.com/${login}.png`, url: `https://github.com/${login}` };
const displayName = p => p.name || '@' + p.login;
const avatar = (p, size = 28) => `<img class="avatar" src="${esc(p.avatar)}${p.avatar.includes('?') ? '&' : '?'}s=${size * 2}" width="${size}" height="${size}" alt="">`;
function personSummary(p) {
  const bits = [];
  if (p.catalogs.length) bits.push(`${p.catalogs.length} catalog${p.catalogs.length !== 1 ? 's' : ''}`);
  if (p.pullRequests.length) bits.push(`${p.pullRequests.length} pull request${p.pullRequests.length !== 1 ? 's' : ''}`);
  if (p.requests.length) bits.push(`${p.requests.length} source request${p.requests.length !== 1 ? 's' : ''}`);
  if (p.commits) bits.push(`${p.commits} commit${p.commits !== 1 ? 's' : ''}`);
  return bits.join(' · ');
}

// ── Live FAIR Data Points ───────────────────────────────────────────────────

// The FDP root described in a fetched document: a subject that lists catalogs.
function liveRootIn(file) {
  const g = namedNode(file);
  const subjects = [NS.fdp + 'metadataCatalog', NS.dcat + 'catalog']
    .flatMap(p => store.getSubjects(namedNode(p), null, g).map(t => t.value));
  if (!subjects.length) return null;
  return subjects.find(x => x.replace(/\/$/, '') === file.replace(/\/$/, '')) || subjects[0];
}

// Breadth-first over the FDP's own links. Returns error messages, never throws.
async function crawl(root) {
  const seen = new Set([root]), errors = [];
  let frontier = [root];
  while (frontier.length && seen.size <= CRAWL_MAX_DOCS) {
    const next = [];
    for (const s of frontier)
      for (const p of CRAWL_LINKS)
        for (const o of iris(s, p)) if (!seen.has(o) && seen.size < CRAWL_MAX_DOCS) { seen.add(o); next.push(o); }
    const results = await Promise.allSettled(next.map(u => load(u)));
    results.forEach(r => { if (r.status === 'rejected') errors.push(r.reason.message); });
    frontier = next;
  }
  return errors.slice(0, 5);
}

function buildLiveCatalog({ ref, file, title, liveRoot: root }) {
  const fdpCatalogs = [...iris(root, NS.fdp + 'metadataCatalog'), ...iris(root, NS.dcat + 'catalog')];
  const datasets = [];
  for (const c of fdpCatalogs) {
    const group = lit(c, NS.dct + 'title') || localName(c);
    for (const ds of iris(c, NS.dcat + 'dataset')) datasets.push({ ...buildDataset(ds), group });
    const services = iris(c, NS.dcat + 'service');
    if (services.length) datasets.push({
      iri: c + '#services', id: localName(c) + '-services', group,
      title: `${group} — data services`, version: 'services',
      description: lit(c, NS.dct + 'description'), derivedFrom: [],
      distributions: services.map(serviceAsDistribution),
    });
  }
  return {
    key: localName(ref), ref, file, iri: root, live: true,
    title: title || lit(root, NS.dct + 'title'),
    description: lit(root, NS.dct + 'description'),
    landingPage: iri(root, NS.dcat + 'landingPage') || root,
    license: iri(root, NS.dct + 'license'),
    publisher: lit(iri(root, NS.dct + 'publisher') || '', NS.foaf + 'name'),
    modified: (lit(root, NS.fdp + 'metadataModified') || '').slice(0, 10) || null,
    catalogCount: fdpCatalogs.length,
    datasets,
  };
}

// A dcat:DataService listed directly under a catalog, shown as one way to get data.
function serviceAsDistribution(s) {
  const d = {
    iri: s, id: localName(s), title: lit(s, NS.dct + 'title'), description: lit(s, NS.dct + 'description'),
    mediaType: null, format: null, accessURL: iri(s, NS.dcat + 'endpointURL') || iri(s, NS.dcat + 'landingPage'),
    downloadURL: null, license: iri(s, NS.dct + 'license'), identifier: null, derivedFrom: [],
    services: [{ iri: s, title: lit(s, NS.dct + 'title'), endpointURL: iri(s, NS.dcat + 'endpointURL'),
                 endpointDescription: iri(s, NS.dcat + 'endpointDescription'), conformsTo: iri(s, NS.dct + 'conformsTo') }],
  };
  return withInferredType(d);
}

// Remote FDPs rarely type their distributions with our access-method vocabulary; guess one from the
// media type, endpoint and service, and mark it as inferred.
function withInferredType(d) {
  if (d.type || !amBase) return d;
  const url = [d.accessURL, ...d.services.map(s => s.endpointURL)].filter(Boolean).join(' ');
  const mt = (d.mediaType || '') + ' ' + (d.format || '');
  let m = 'web-portal';
  if (/sparql/i.test(mt) || /\/sparql\b|\/repositories\//i.test(url)) m = 'sparql-endpoint';
  else if (/zip|gzip|tar|bzip/i.test(mt) || d.downloadURL) m = 'bulk-download';
  else if (d.services.length || /json|csv|xml/i.test(mt)) m = 'rest-api';
  const type = amBase + m;
  return { ...d, type, inferred: true, typeLabel: lit(type, NS.skos + 'prefLabel') || m,
           accessURL: d.accessURL || (d.services[0] || {}).endpointURL || null };
}

// Where does an IRI live in the model? Used for prov:wasDerivedFrom links across catalogs.
function locate(target) {
  for (const c of catalogs) for (const ds of c.datasets) {
    if (ds.iri === target) return { c, ds, label: ds.title };
    const d = ds.distributions.find(x => x.iri === target);
    if (d) return { c, ds, label: d.title };
  }
  return null;
}

// ── Icons (monochrome, stroke = currentColor) ───────────────────────────────

// The instance logo (theme.logo in fdp.config.json), inlined by the build so it follows the theme colour.
const LOGO = (document.getElementById('fdp-logo') || {}).innerHTML ||
  '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="currentColor"/></svg>';

const ICON_PATHS = {
  'web-portal': '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.5 3.8 5.5 3.8 9s-1.3 6.5-3.8 9c-2.5-2.5-3.8-5.5-3.8-9S9.5 5.5 12 3z"/>',
  'rest-api': '<path d="M8 4c-2 0-3 1-3 3v2c0 1.5-.7 2.5-2 3 1.3.5 2 1.5 2 3v2c0 2 1 3 3 3M16 4c2 0 3 1 3 3v2c0 1.5.7 2.5 2 3-1.3.5-2 1.5-2 3v2c0 2-1 3-3 3"/>',
  'sparql-endpoint': '<circle cx="5" cy="6" r="2.5"/><circle cx="19" cy="6" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M7.5 6h9M6.3 8.2l4.4 7.6M17.7 8.2l-4.4 7.6"/>',
  'bulk-download': '<path d="M12 3v12M7 10l5 5 5-5M4 17v3h16v-3"/>',
  'cloud-object-storage': '<path d="M7 18a5 5 0 0 1-.6-9.96A6 6 0 0 1 18 9a4.5 4.5 0 0 1-.5 9H7z"/>',
  'darwin-core-archive': '<path d="M3 4h18v4H3zM5 8v12h14V8M10 12h4"/>',
  'rdf-dump': '<path d="M14 3H6v18h12V7l-4-4zM14 3v4h4"/><circle cx="9.5" cy="14" r="1.3"/><circle cx="14.5" cy="11.5" r="1.3"/><circle cx="14.5" cy="16.5" r="1.3"/>',
  'source-repository': '<circle cx="6" cy="5" r="2.2"/><circle cx="6" cy="19" r="2.2"/><circle cx="18" cy="8" r="2.2"/><path d="M6 7.2v9.6M18 10.2c0 4-6 3-11 7"/>',
  'aggregator-mirror': '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
  'oai-pmh': '<path d="M20 11a8 8 0 0 0-14.6-4.5M4 4v3h3M4 13a8 8 0 0 0 14.6 4.5M20 20v-3h-3"/>',
  'sql-query': '<ellipse cx="12" cy="5.5" rx="7.5" ry="2.5"/><path d="M4.5 5.5v13c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5v-13M4.5 12c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5"/>',
  'change-feed': '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
};
const icon = type => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON_PATHS[localName(type)] || '<circle cx="12" cy="12" r="8"/>'}</svg>`;

// ── Flattened view for search and method counts ────────────────────────────

function allDistributions() {
  return catalogs.flatMap(c => c.datasets.flatMap(ds => ds.distributions.map(d => ({ c, ds, d }))));
}
function methodsInUse() {
  const by = new Map();
  for (const x of allDistributions()) {
    if (!x.d.type) continue;
    if (!by.has(x.d.type)) by.set(x.d.type, { type: x.d.type, label: x.d.typeLabel, items: [] });
    by.get(x.d.type).items.push(x);
  }
  return [...by.values()].sort((a, b) => b.items.length - a.items.length);
}

// ── Routing ─────────────────────────────────────────────────────────────────
// #/                         index
// #/search?q=…&m=<method>    search over all distributions
// #/<catalog>                catalog
// #/<catalog>/<dataset>      dataset

window.addEventListener('hashchange', route);
function route() {
  const [path, query] = location.hash.replace(/^#\/?/, '').split('?');
  const [key, ds] = path.split('/').filter(Boolean).map(decodeURIComponent);
  if (key === 'search') showSearch(new URLSearchParams(query || ''));
  else if (key === 'contributors') showContributors();
  else if (key === 'fair') showFair();
  else if (key && ds) showDataset(key, ds);
  else if (key) showCatalog(key);
  else showIndex();
  window.scrollTo(0, 0);
}
const hrefFor = (...parts) => '#/' + parts.map(encodeURIComponent).join('/');
const searchHref = (q, m) => '#/search?' + new URLSearchParams(Object.entries({ q: q || '', m: m ? localName(m) : '' }).filter(([, v]) => v));

// ── Chrome ──────────────────────────────────────────────────────────────────

function renderChrome() {
  const name = (indexModel.title || 'FAIR Data Point').split(' — ')[0];
  document.title = name;
  const [word, ...rest] = name.split(' ');
  $('lockup').innerHTML = `${LOGO}<span>${esc(word)}${rest.length ? ` <span class="sub">${esc(rest.join(' '))}</span>` : ''}</span>`;
  $('topnav').innerHTML = `<a href="#catalogs">Catalogs</a><a href="${searchHref()}">Search</a>` +
    (contributors ? '<a href="#/contributors">Contributors</a>' : '') +
    (fairReport ? '<a href="#/fair">FAIR tests</a>' : '') +
    (repo ? link(`https://github.com/${repo.org}/${repo.name}#readme`, 'About') +
            link(`https://github.com/${repo.org}/${repo.name}/issues/new?template=add-data-source.yml`, 'Add a data source') : '');
  $('topright').innerHTML = (repo ? `<a class="btn ghost" href="https://github.com/${esc(repo.org)}/${esc(repo.name)}" target="_blank" rel="noopener">GitHub</a>` : '') +
    `<a class="btn" href="${esc(indexModel.file)}" target="_blank" rel="noopener">Turtle</a>`;
  $('footer').innerHTML = `<div><div class="lockup">${LOGO}<span>${esc(name)}</span></div>
      A static FAIR Data Point, rendered in your browser with <a href="https://github.com/rdfjs/N3.js" target="_blank" rel="noopener">N3.js</a>.</div>
    <div>${footerCredits()}
      Browse another FDP with <span class="mono">?index=&lt;url&gt;</span></div>`;
  // "Catalogs" in the top bar scrolls on the index, navigates elsewhere.
  $('topnav').querySelector('a[href="#catalogs"]').onclick = e => {
    e.preventDefault();
    if (location.hash.replace(/^#\/?/, '')) { location.hash = ''; setTimeout(() => scrollToId('catalogs'), 0); }
    else scrollToId('catalogs');
  };
}
function footerCredits() {
  if (!contributors || !contributors.people.length)
    return indexModel.publisher ? 'Curated by ' + esc(indexModel.publisher) + '<br>' : '';
  const people = contributors.people;
  const names = people.slice(0, 3).map(displayName);
  const more = people.length - names.length;
  return `<a class="credits" href="#/contributors"><span class="avatars">${people.slice(0, 6).map(p => avatar(p, 22)).join('')}</span>
    Built by ${esc(names.join(', '))}${more > 0 ? ` and ${more} more` : ''}</a><br>`;
}
const scrollToId = id => { const el = $(id); if (el) el.scrollIntoView({ behavior: 'smooth' }); };

function crumbs(parts) {
  return `<nav class="crumbs" aria-label="Breadcrumb"><a href="#">Index</a>` +
    parts.map(p => `<span>/</span>${p.href ? `<a href="${p.href}">${esc(p.label)}</a>` : `<span>${esc(p.label)}</span>`}`).join('') + '</nav>';
}

function fileButtons(file, live) {
  if (!file) return '';
  if (live) return `<div class="actions"><a class="btn ghost" href="${esc(file)}" target="_blank" rel="noopener">Live FAIR Data Point ↗</a></div>`;
  const out = [`<a class="btn ghost" href="${esc(file)}" target="_blank" rel="noopener">Turtle file</a>`];
  if (repo && file.startsWith(`https://raw.githubusercontent.com/${repo.org}/${repo.name}/${repo.branch}/`)) {
    const path = file.split(`/${repo.branch}/`).slice(1).join(`/${repo.branch}/`);
    const gh = `https://github.com/${repo.org}/${repo.name}`;
    out.push(`<a class="btn ghost" href="${esc(`${gh}/edit/${repo.branch}/${path}`)}" target="_blank" rel="noopener">Edit on GitHub</a>`);
    out.push(`<a class="btn ghost" href="${esc(`${gh}/commits/${repo.branch}/${path}`)}" target="_blank" rel="noopener">History</a>`);
  }
  return `<div class="actions">${out.join('')}</div>`;
}

// ── Index ───────────────────────────────────────────────────────────────────

function searchForm(q, m, methods, cls) {
  return `<form class="search ${cls || ''}" id="search" role="search">
    <input id="q" type="search" value="${esc(q || '')}" placeholder="Search sources, APIs, dumps, endpoints…" aria-label="Search">
    <select id="m" aria-label="Access method"><option value="">All methods</option>
      ${methods.map(x => `<option value="${esc(localName(x.type))}"${localName(x.type) === m ? ' selected' : ''}>${esc(x.label)}</option>`).join('')}</select>
    <button class="btn" type="submit">Search</button>
  </form>`;
}
function wireSearch() {
  $('search').onsubmit = e => { e.preventDefault(); location.hash = searchHref($('q').value.trim(), $('m').value); };
}

function showIndex() {
  const all = allDistributions();
  const methods = methodsInUse();

  let html = `<div class="hero-band"><div class="wrap"><div class="hero">
      <div class="mark">${LOGO}</div>
      <h1>${headlineHtml()}</h1>
      <p>${catalogs.length} source${catalogs.length !== 1 ? 's' : ''} and ${all.length} way${all.length !== 1 ? 's' : ''} to reach ${catalogs.length !== 1 ? 'them' : 'it'}, from portals and APIs to SPARQL endpoints, bulk dumps and cloud buckets, described as one FAIR Data Point.</p>
      ${searchForm('', '', methods)}
    </div></div></div><div class="wrap">`;

  if (failures.length) html += `<div class="notice error">Could not load: ${failures.map(esc).join('<br>')}</div>`;

  html += `<section><div class="section-head"><h2>Ways to get data</h2><span class="count">by access method</span></div><div class="tiles">` +
    methods.map(m => `<a class="tile" href="${searchHref('', m.type)}"><span class="disc">${icon(m.type)}</span>
      <span><span class="tile-label">${esc(m.label)}</span><br><span class="tile-n">${m.items.length} across ${new Set(m.items.map(x => x.c.key)).size} catalogs</span></span></a>`).join('') +
    '</div></section>';

  html += `<section id="catalogs"><div class="section-head"><h2>Catalogs</h2>
    <span class="count">${catalogs.length} catalogs${indexModel.modified ? ' · updated ' + esc(indexModel.modified) : ''}</span></div><div class="blocks">`;
  for (const c of catalogs) {
    const n = c.datasets.length, d = c.datasets.reduce((s, x) => s + x.distributions.length, 0);
    const types = [...new Set(c.datasets.flatMap(x => x.distributions.map(y => y.typeLabel)).filter(Boolean))];
    html += `<a class="block" href="${hrefFor(c.key)}">
      <h3>${esc(c.title || c.key)}</h3>
      <p>${c.missing ? 'Catalog file could not be loaded.' : esc(c.description || '')}</p>
      <div class="stats">${c.live ? '<span class="chip live">Live FDP</span> ' : ''}${n} dataset${n !== 1 ? 's' : ''} · ${d} ways to get it</div>
      <div class="chips">${types.map(t => `<span class="chip">${esc(t)}</span>`).join('')}</div>
    </a>`;
  }
  html += '</div></section>';
  if (contributors && contributors.people.length) {
    html += `<section id="contributors"><div class="section-head"><h2>Contributors</h2>
      <span class="count"><a href="#/contributors">All ${contributors.people.length} →</a></span></div><div class="people">` +
      contributors.people.slice(0, 8).map(p => `<a class="person" href="#/contributors">${avatar(p, 40)}
        <span><span class="person-name">${esc(displayName(p))}</span><br><span class="tile-n">${esc(personSummary(p))}</span></span></a>`).join('') +
      (repo ? `<a class="person join" href="https://github.com/${esc(repo.org)}/${esc(repo.name)}/issues/new?template=add-data-source.yml" target="_blank" rel="noopener">
        <span class="disc">+</span><span><span class="person-name">Add a data source</span><br><span class="tile-n">Fill in a form; a pull request follows</span></span></a>` : '') +
      '</div></section>';
  }
  html += turtleSection(indexModel.file) + '</div>';
  $('view').innerHTML = html;
  wireSearch();
  wireTurtle(indexModel.file);
}

// "Every way to get *biodiversity* data" → emphasis on the starred word(s).
function headlineHtml() {
  const text = CFG.headline || (indexModel && indexModel.title) || 'FAIR Data Point';
  return esc(text).replace(/\*([^*]+)\*/g, '<em>$1</em>');
}

const shortTitle = t => (t || '').split(' — ')[0];

// ── Search ──────────────────────────────────────────────────────────────────

function showSearch(params) {
  const q = (params.get('q') || '').trim();
  const m = params.get('m') || '';
  const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
  const hits = allDistributions().filter(({ c, ds, d }) => {
    if (m && localName(d.type) !== m) return false;
    const hay = [c.title, ds.title, ds.version, d.title, d.description, d.typeLabel, d.mediaType, d.format,
                 d.accessURL, ...d.services.map(s => (s.title || '') + ' ' + (s.endpointURL || ''))].join(' ').toLowerCase();
    return terms.every(t => hay.includes(t));
  });
  const methods = methodsInUse();
  const label = m ? (methods.find(x => localName(x.type) === m) || {}).label || m : '';

  let html = crumbs([{ label: 'Search' }]) + `<div class="page-head">
    <div class="eyebrow">Search</div>
    <h1>${q ? `“${esc(q)}”` : label ? esc(label) : 'All ways to get data'}</h1>
    ${searchForm(q, m, methods, 'left')}
  </div>
  <section><div class="section-head"><h2>Results</h2><span class="count">${hits.length} of ${allDistributions().length}</span></div>
  <div class="rows">${hits.map(h => distRow(h.d, h)).join('') || '<p class="row-desc" style="padding:18px 0">Nothing matches. Try fewer words.</p>'}</div></section>`;
  $('view').innerHTML = '<div class="wrap">' + html + '</div>';
  wireSearch();
}

// ── Catalog ─────────────────────────────────────────────────────────────────

function showCatalog(key) {
  const c = catalogs.find(x => x.key === key);
  if (!c) { $('view').innerHTML = '<div class="wrap">' + crumbs([]) + '<div class="notice">Unknown catalog.</div></div>'; return; }
  const meta = [c.live ? '' : link(c.landingPage, 'Website ↗'), link(c.license, 'Licence ↗'), c.publisher ? 'Publisher: ' + esc(c.publisher) : '',
                c.live ? `Read live from ${link(c.file, new URL(c.file).host + new URL(c.file).pathname.replace(/\/$/, ''))}` +
                         (c.modified ? ` · metadata modified ${esc(c.modified)}` : '') + ` · ${c.catalogCount} catalog${c.catalogCount !== 1 ? 's' : ''}` : '']
                .filter(Boolean).map(x => `<span>${x}</span>`);
  let html = crumbs([{ label: shortTitle(c.title) || key }]) + `<div class="page-head">
    <div class="eyebrow">${c.live ? 'Live FAIR Data Point' : 'Catalog'}</div><h1>${esc(c.title || key)}</h1><p>${esc(c.description || '')}</p>
    ${meta.length ? `<div class="meta">${meta.join('')}</div>` : ''}${catalogCredits(c)}${fairBadge(c.key)}${fileButtons(c.file, c.live)}</div>`;
  html += `<section><div class="section-head"><h2>Datasets</h2><span class="count">${c.datasets.length}</span></div><div class="rows">`;
  for (const ds of c.datasets) {
    const n = ds.distributions.length;
    const dates = [ds.issued && 'Issued ' + ds.issued, ds.modified && 'Modified ' + ds.modified].filter(Boolean);
    html += `<a class="row" href="${hrefFor(c.key, ds.id)}">
      <div class="row-side"><span class="label">Dataset</span><span class="version">${esc(ds.version || ds.id)}</span></div>
      <div><div class="row-title">${esc(ds.title || ds.id)}</div><div class="row-desc">${esc(ds.description || '')}</div>
        <div class="row-meta">${ds.group ? `<span>in ${esc(ds.group)}</span>` : ''}<span>${n} way${n !== 1 ? 's' : ''} to get it</span>${dates.map(d => `<span>${esc(d)}</span>`).join('')}</div></div>
    </a>`;
  }
  html += '</div></section>' + turtleSection(c.file);
  $('view').innerHTML = '<div class="wrap">' + html + '</div>';
  wireTurtle(c.file);
}

function catalogCredits(c) {
  const info = contributors && contributors.catalogs[c.key];
  if (!info) return '';
  const added = personByLogin(info.addedBy);
  const others = info.contributors.filter(l => l !== info.addedBy).map(personByLogin);
  return `<div class="meta credits-line"><span>${avatar(added, 20)} Added by <a href="${esc(added.url)}" target="_blank" rel="noopener">${esc(displayName(added))}</a> on ${esc(info.added)}</span>` +
    (others.length ? `<span>Improved by ${others.map(p => `${avatar(p, 20)} <a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(displayName(p))}</a>`).join(', ')}</span>` : '') +
    '</div>';
}

// ── FAIR assessment ─────────────────────────────────────────────────────────

const FAIR_SYMBOL = { pass: '✓', fail: '✗', indeterminate: '–', error: '!' };
// Report nodes tested by their catalog document map to the viewer's catalog keys.
const fairKey = n => ((n.guid || '').match(/\/fdp\/([^/]+)\/catalog\.ttl$/) || [])[1] || (n.depth === 0 && !n.parent ? 'index' : null);

function fairLabel(n, i) {
  const key = fairKey(n);
  if (key === 'index') return 'Index';
  const c = key && catalogs.find(x => x.key === key);
  return c ? shortTitle(c.title) : (n.title || `${n.level || 'node'} ${i + 1}`).slice(0, 18);
}

function fairBadge(key) {
  const n = fairReport && fairReport.nodes.find(x => fairKey(x) === key && x.score && x.score.total);
  if (!n) return '';
  return `<div class="meta"><a class="fair-badge" href="#/fair">FAIR tests: ${n.score.pass} of ${n.score.total} pass</a>
    <span>assessed ${esc(fairReport.generated.slice(0, 10))}</span></div>`;
}

function showFair() {
  const r = fairReport;
  if (!r) { $('view').innerHTML = '<div class="wrap">' + crumbs([]) + '<div class="notice">No FAIR assessment has been published yet.</div></div>'; return; }
  const tested = r.nodes.filter(n => n.score && n.score.total);
  const skipped = r.nodes.filter(n => !(n.score && n.score.total));
  const byLevel = {};
  skipped.forEach(n => { byLevel[n.level || 'other'] = (byLevel[n.level || 'other'] || 0) + 1; });
  const totals = tested.reduce((t, n) => { ['pass', 'fail', 'indeterminate', 'error'].forEach(k => t[k] += n.score[k]); return t; }, { pass: 0, fail: 0, indeterminate: 0, error: 0 });
  const short = id => id.replace(/^test_FM_/, '').replace(/_M_/, ' ');
  let html = crumbs([{ label: 'FAIR tests' }]) + `<div class="page-head"><div class="eyebrow">FAIR assessment</div>
    <h1>How FAIR is this FDP?</h1>
    <p>Every level of the FAIR Data Point was walked (FDP → catalogs → datasets → distributions) and each resource that resolves was
      assessed with ${r.tests.length} <a href="https://tests.ostrails.eu/" target="_blank" rel="noopener">OSTrails FAIR Champion</a> tests.
      Assessed ${esc(r.generated.slice(0, 10))}, depth: ${esc(r.depth)}.</p>
    <div class="meta"><span>${tested.length} resources tested</span><span>✓ ${totals.pass} pass</span><span>✗ ${totals.fail} fail</span>
      <span>– ${totals.indeterminate} indeterminate</span>${totals.error ? `<span>! ${totals.error} test errors</span>` : ''}</div>
    ${skipped.length ? `<p style="margin-top:10px">Not tested because their IRIs do not resolve: ${Object.entries(byLevel).map(([k, v]) => `${v} ${esc(k)}${v !== 1 ? 's' : ''}`).join(', ')}.
      Dereferenceable IRIs for every level (e.g. via w3id.org) would let them be assessed too.</p>` : ''}
    ${repo ? `<div class="actions"><a class="btn ghost" href="https://github.com/${esc(repo.org)}/${esc(repo.name)}/actions/workflows/fair.yml" target="_blank" rel="noopener">Assessment runs</a></div>` : ''}
  </div>
  <section><div class="section-head"><h2>Per test</h2><span class="count">✓ pass · ✗ fail · – indeterminate</span></div>
  <div class="fair-scroll"><table class="fair-grid"><thead><tr><th>Test</th>${tested.map((n, i) =>
      `<th title="${esc(n.title || n.iri)}"><a href="${fairKey(n) && fairKey(n) !== 'index' ? hrefFor(fairKey(n)) : '#'}">${esc(fairLabel(n, i))}</a></th>`).join('')}</tr></thead><tbody>` +
    r.tests.map(t => `<tr><th><a href="${esc(t.url)}" target="_blank" rel="noopener">${esc(short(t.id))}</a></th>` + tested.map(n => {
      const res = n.results[t.id] || {};
      return `<td class="fair-${esc(res.value || 'none')}" title="${esc(res.summary || res.value || '')}">${FAIR_SYMBOL[res.value] || ''}</td>`;
    }).join('') + '</tr>').join('') + `</tbody></table></div></section>`;
  $('view').innerHTML = '<div class="wrap">' + html + '</div>';
}

// ── Contributors page ───────────────────────────────────────────────────────

function showContributors() {
  if (!contributors) { $('view').innerHTML = '<div class="wrap">' + crumbs([]) + '<div class="notice">No contributor information for this FDP.</div></div>'; return; }
  const catLink = k => { const c = catalogs.find(x => x.key === k); return c ? `<a href="${hrefFor(k)}">${esc(shortTitle(c.title))}</a>` : null; };
  let html = crumbs([{ label: 'Contributors' }]) + `<div class="page-head"><div class="eyebrow">Contributors</div>
    <h1>The people behind this FDP</h1>
    <p>Everyone who added or improved a catalog, merged a pull request or asked for a data source through the form, taken from the
      ${repo ? `<a href="https://github.com/${esc(repo.org)}/${esc(repo.name)}" target="_blank" rel="noopener">GitHub repository</a>` : 'repository'}
      and refreshed on every deploy (${esc(contributors.generated.slice(0, 10))}). Names, Wikidata items and ORCID iDs come from
      Wikidata when the GitHub account is linked there with property
      <a href="https://www.wikidata.org/wiki/Property:P2037" target="_blank" rel="noopener">P2037 (GitHub username)</a>.</p>
    ${repo ? `<div class="actions"><a class="btn" href="https://github.com/${esc(repo.org)}/${esc(repo.name)}/issues/new?template=add-data-source.yml" target="_blank" rel="noopener">Add a data source</a>
      <a class="btn ghost" href="https://github.com/${esc(repo.org)}/${esc(repo.name)}/blob/main/CONTRIBUTING.md" target="_blank" rel="noopener">How to contribute</a></div>` : ''}
  </div><section><div class="rows">`;
  for (const p of contributors.people) {
    const cats = p.catalogs.map(catLink).filter(Boolean);
    const prs = p.pullRequests.map(x => `<a href="${esc(x.url)}" target="_blank" rel="noopener">#${x.number} ${esc(x.title)}</a>`);
    const reqs = p.requests.map(x => `<a href="${esc(x.url)}" target="_blank" rel="noopener">#${x.number} ${esc(x.title)}</a>`);
    html += `<div class="row">
      <div class="row-side">${avatar(p, 56)}</div>
      <div><div class="row-title"><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(displayName(p))}</a>
        ${p.name ? `<span class="fmt mono"> @${esc(p.login)}</span>` : ''}</div>
        <div class="row-meta">${[link(p.url, 'GitHub ↗'), p.wikidata ? link(p.wikidata, 'Wikidata ' + localName(p.wikidata) + ' ↗') : '',
          p.orcid ? link('https://orcid.org/' + p.orcid, 'ORCID ' + p.orcid + ' ↗') : ''].filter(Boolean).map(x => `<span>${x}</span>`).join('')}</div>
        <div class="row-desc">${esc(personSummary(p))}${p.since ? ` · since ${esc(p.since)}` : ''}</div>
        ${cats.length ? `<div class="row-meta"><span>Catalogs: ${cats.join(', ')}</span></div>` : ''}
        ${prs.length ? `<div class="row-meta"><span>Pull requests: ${prs.join(', ')}</span></div>` : ''}
        ${reqs.length ? `<div class="row-meta"><span>Requested: ${reqs.join(', ')}</span></div>` : ''}
      </div></div>`;
  }
  $('view').innerHTML = '<div class="wrap">' + html + '</div></section></div>';
}

// ── Dataset ─────────────────────────────────────────────────────────────────

function showDataset(key, id) {
  const c = catalogs.find(x => x.key === key);
  const ds = c && c.datasets.find(d => d.id === id);
  if (!ds) { $('view').innerHTML = '<div class="wrap">' + crumbs([]) + '<div class="notice">Unknown dataset.</div></div>'; return; }

  const meta = [];
  if (ds.issued) meta.push('Issued ' + esc(ds.issued));
  if (ds.modified) meta.push('Modified ' + esc(ds.modified));
  if (ds.identifier) meta.push(safeUrl(ds.identifier) ? link(ds.identifier, ds.identifier) : esc(ds.identifier));
  if (ds.license) meta.push(link(ds.license, 'Licence ↗'));
  if (ds.landingPage) meta.push(link(ds.landingPage, 'Website ↗'));
  meta.push(...derivedLinks(ds.derivedFrom));

  const dists = ds.distributions;
  const methods = [...new Map(dists.filter(d => d.type).map(d => [d.type, d.typeLabel])).entries()];
  let html = crumbs([{ label: shortTitle(c.title) || key, href: hrefFor(key) }, { label: ds.version || ds.id }]) +
    `<div class="page-head"><div class="eyebrow">Dataset · ${esc(ds.version || ds.id)}</div>
     <h1>${esc(ds.title || ds.id)}</h1><p>${esc(ds.description || '')}</p>
     ${meta.length ? `<div class="meta">${meta.map(x => `<span>${x}</span>`).join('')}</div>` : ''}</div>
    <div class="tabs" id="filters" role="tablist"><button class="on" data-m="">All<span class="n">${dists.length}</span></button>` +
    methods.map(([t, l]) => `<button data-m="${esc(t)}">${esc(l)}<span class="n">${dists.filter(d => d.type === t).length}</span></button>`).join('') +
    `</div><div class="rows" id="dists" style="border-top:none"></div>`;
  $('view').innerHTML = '<div class="wrap">' + html + '</div>';

  let filter = null;
  const render = () => { $('dists').innerHTML = dists.filter(d => !filter || d.type === filter).map(d => distRow(d)).join(''); };
  $('filters').querySelectorAll('button').forEach(b => b.onclick = () => {
    filter = b.dataset.m || null;
    $('filters').querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
    render();
  });
  render();
}

function derivedLinks(list) {
  return list.map(t => {
    const hit = locate(t);
    return hit ? `Derived from <a href="${hrefFor(hit.c.key, hit.ds.id)}">${esc(hit.label)}</a>`
               : 'Derived from ' + (safeUrl(t) ? link(t, localName(t)) : esc(t));
  });
}

// One distribution as a list row. `ctx` (search results) adds where it lives.
function distRow(d, ctx) {
  const how = (d.description || '').replace(/^How to get it:\s*/i, '');
  const fmt = [...new Set([d.mediaType, d.format].filter(Boolean))].join(' · ');
  const title = safeUrl(d.accessURL)
    ? `<a href="${esc(d.accessURL)}" target="_blank" rel="noopener">${esc(d.title || d.id)} ↗</a>` : esc(d.title || d.id);
  const links = [link(d.downloadURL, 'Download ↓'), d.license ? link(d.license, 'Licence ↗') : ''].filter(Boolean);
  const where = ctx ? [`<a href="${hrefFor(ctx.c.key)}">${esc(shortTitle(ctx.c.title))}</a>`,
                       `<a href="${hrefFor(ctx.c.key, ctx.ds.id)}">${esc(ctx.ds.title)}</a>`] : [];
  const services = d.services.map(s => s.endpointURL
    ? `<div class="endpoint mono"><div class="ep-title">${esc(s.title || 'Service')}</div>${esc(s.endpointURL)}
        <div class="ep-links">${[link(s.endpointDescription, 'Docs ↗'), link(s.conformsTo, 'Protocol ↗')].filter(Boolean).join('')}</div></div>`
    : `<div class="endpoint mono">${esc(localName(s.iri))}</div>`).join('');
  return `<div class="row">
    <div class="row-side"><span class="label" style="display:flex;gap:6px;align-items:center">
      <span style="width:14px;height:14px;display:inline-flex">${icon(d.type)}</span>${esc(d.typeLabel || 'Distribution')}</span>
      ${d.inferred ? '<span class="fmt" title="The source does not state an access method; this one was inferred from its media type and endpoint">inferred</span>' : ''}
      ${fmt ? `<span class="fmt mono">${esc(fmt)}</span>` : ''}</div>
    <div><div class="row-title">${title}</div>
      ${how ? `<div class="row-desc">${esc(how)}</div>` : ''}
      ${services}
      ${(where.length || links.length || d.derivedFrom.length) ? `<div class="row-meta">${[...where, ...derivedLinks(d.derivedFrom), ...links].map(x => `<span>${x}</span>`).join('')}</div>` : ''}
    </div></div>`;
}

// ── Turtle source ───────────────────────────────────────────────────────────

function turtleSection() {
  return `<section><div class="tabs" id="ttl-tabs"><button class="on" data-tab="hide">Summary</button><button data-tab="raw">Turtle source</button></div>
          <div id="raw-box" hidden></div></section>`;
}
function wireTurtle(file) {
  $('ttl-tabs').querySelectorAll('button').forEach(b => b.onclick = () => {
    $('ttl-tabs').querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
    $('raw-box').hidden = b.dataset.tab !== 'raw';
    if (b.dataset.tab === 'raw') $('raw-box').innerHTML = `<pre class="ttl">${esc(sources.get(file) || 'Not loaded.')}</pre>`;
  });
}

init().catch(e => {
  $('view').innerHTML = `<div class="wrap"><div class="notice error">Could not load the FAIR Data Point: ${esc(e.message)}</div></div>`;
});
