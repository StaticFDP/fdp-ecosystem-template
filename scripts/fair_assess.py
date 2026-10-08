#!/usr/bin/env python3
"""Walk a FAIR Data Point level by level and run FAIR Champion tests on every node.

Starting from an FDP (or a web page that links its metadata with rel="describedby"),
the walk follows the containment links of the FDP specification and DCAT:

    ldp:contains, fdp:metadataCatalog, fdp:hasCatalog, dcat:catalog,
    dcat:dataset, dcat:service, dcat:distribution

Every node gets a level from its rdf:type (fdp, catalog, dataset, distribution,
service). A node is tested when it can be dereferenced: its own IRI resolves, or it
is the main subject of a document we fetched (then the document URL is the GUID).
Nodes that do not resolve are reported as such and not sent to the test service.

    python3 scripts/fair_assess.py --start <siteUrl> --rewrite <baseIri>=<siteUrl>fdp/ --depth catalog --out fair-report

(Without --start/--rewrite the values from fdp.config.json are used.)

By default every resource is assessed with one call to a FAIR Champion
*algorithm* (all current metrics at once; --algorithm), and the FTR JSON-LD
report of each call is kept in <out>/ftr/. --per-test runs the tests in
fair/tests.txt one by one instead.

Assessments are incremental: each resource carries a fingerprint (SHA-256 of the
document that describes it). Resources whose fingerprint matches the previous
report (--previous, by default the one published on the site) keep their results
unless those are older than --max-age-days; --full re-tests everything.

Outputs report.json and summary.md in --out. The test service is shared
infrastructure: keep --workers low.
"""
import argparse
import datetime
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import DCAT, DCTERMS, RDF, RDFS

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fdpconfig import BASE_IRI, ROOT, SITE_URL  # noqa: E402
FDPO = Namespace("https://w3id.org/fdp/fdp-o#")
LDP = Namespace("http://www.w3.org/ns/ldp#")
TEST_API = "https://tests.ostrails.eu/tests/assess/test/"
# All current OSTrails core metrics in one call (Mark Wilkinson, FAIR Champion).
ALGORITHM = "https://w3id.org/FAIR-Champion/assess/algorithm/d/1UvHnRkKy3KZMlWdIdB7rz0LrBZJXpPibAgmTXJwRO_0"
LEVELS = ["fdp", "catalog", "dataset", "distribution", "service"]
DEPTH = {"fdp": 0, "catalog": 1, "dataset": 2, "distribution": 3}
LINKS = [LDP.contains, FDPO.metadataCatalog, FDPO.hasCatalog, DCAT.catalog, DCAT.dataset, DCAT.service, DCAT.distribution]
RDF_TYPES = ("text/turtle", "application/rdf+xml", "application/ld+json", "application/n-triples", "application/trig", "text/n3")


def curl(url, accept="text/turtle, application/rdf+xml;q=0.9, application/ld+json;q=0.8, */*;q=0.1", timeout=40):
    """GET with redirects. Returns (status, final_url, content_type, link_header, body) or None."""
    try:
        out = subprocess.run(["curl", "-sSL", "-m", str(timeout), "-H", f"Accept: {accept}", "-D", "-",
                              "-w", "\n__END__%{http_code} %{url_effective}", url],
                             capture_output=True, text=True, timeout=timeout + 10)
    except subprocess.TimeoutExpired:
        return None
    raw = out.stdout
    if "__END__" not in raw:
        return None
    raw, tail = raw.rsplit("\n__END__", 1)
    status, final = tail.split(" ", 1)
    # Several header blocks when redirected: keep the last one.
    blocks = re.split(r"\r?\n\r?\n", raw)
    head_idx = max(i for i, b in enumerate(blocks) if b.startswith("HTTP/")) if any(b.startswith("HTTP/") for b in blocks) else -1
    headers = blocks[head_idx] if head_idx >= 0 else ""
    body = "\n\n".join(blocks[head_idx + 1:]) if head_idx >= 0 else raw
    ctype = (re.search(r"(?im)^content-type:\s*([^;\r\n]+)", headers) or [None, ""])[1].strip().lower()
    link = " ".join(re.findall(r"(?im)^link:\s*(.+)$", headers))
    return int(status), final, ctype, link, body


def describedby(resp):
    """Signposting: rel=describedby / alternate RDF link in the Link header or HTML head."""
    _, final, _, link, body = resp
    cands = re.findall(r'<([^>]+)>\s*;[^,]*rel="?(?:describedby|alternate)"?[^,]*type="?(' + "|".join(map(re.escape, RDF_TYPES)) + ')', link)
    for tag in re.findall(r"<link\b[^>]*>", body[:200000], re.I):
        if re.search(r'rel="?(describedby|alternate)"?', tag, re.I) and re.search("|".join(map(re.escape, RDF_TYPES)), tag, re.I):
            href = re.search(r'href="([^"]+)"', tag)
            if href:
                cands.append((href.group(1), ""))
    if not cands:
        return None
    from urllib.parse import urljoin
    return urljoin(final, cands[0][0])


class Walker:
    def __init__(self, rewrite, max_depth, max_nodes):
        self.rewrite, self.max_depth, self.max_nodes = rewrite, max_depth, max_nodes
        self.g = Graph()
        self.docs = {}        # fetched document URL → main subject IRI (or None)
        self.fingerprints = {}  # fetched document URL → SHA-256 of its body
        self.nodes = {}       # node IRI → info

    def url(self, iri):
        for a, b in self.rewrite:
            if iri.startswith(a):
                return b + iri[len(a):]
        return iri

    def fetch(self, url):
        """Fetch RDF at url (following describedby from HTML). Returns (graph, doc_url) or (None, reason)."""
        resp = curl(url)
        if not resp:
            return None, "no response"
        status, final, ctype, _, body = resp
        if status >= 400:
            return None, f"HTTP {status}"
        if not any(t in ctype for t in RDF_TYPES) and not url.endswith(".ttl"):
            alt = describedby(resp)
            if not alt:
                return None, f"not RDF ({ctype or 'no content type'}) and no describedby link"
            resp = curl(alt)
            if not resp or resp[0] >= 400:
                return None, f"describedby link {alt} did not resolve"
            status, final, ctype, _, body = resp
        self.fingerprints[final] = hashlib.sha256(body.encode()).hexdigest()
        fmt = "json-ld" if "json" in ctype else "xml" if "xml" in ctype else "turtle"
        g = Graph()
        try:
            g.parse(data=body, format=fmt, publicID=final)
        except Exception as e:  # noqa: BLE001 - report, keep walking
            return None, f"unparseable RDF: {str(e)[:120]}"
        return g, final

    def level(self, s):
        types = set(self.g.objects(s, RDF.type))
        if types & {FDPO.FAIRDataPoint, FDPO.MetadataService}:
            return "fdp"
        if DCAT.Catalog in types:
            return "catalog"
        if DCAT.Dataset in types:
            return "dataset"
        if DCAT.Distribution in types:
            return "distribution"
        if DCAT.DataService in types:
            return "service"
        return None

    def main_subject(self, g, doc_url, hint=None):
        """The subject a document is about: the hint if described; else the top of its containment tree,
        preferring an FDP over a catalog over anything else that links to children."""
        if hint and (URIRef(hint), RDF.type, None) in g:
            return URIRef(hint)
        linking = {s for s, p, _ in g if p in LINKS}
        contained = {o for s, p, o in g if p in LINKS}
        tops = (linking - contained) or linking
        for types in ((FDPO.FAIRDataPoint, FDPO.MetadataService), (DCAT.Catalog,)):
            found = sorted(str(s) for s in tops if any((s, RDF.type, t) in g for t in types))
            if found:
                return URIRef(found[0])
        if tops:
            return URIRef(sorted(map(str, tops))[0])
        same = URIRef(doc_url)
        return same if (same, None, None) in g else None

    def add(self, iri, parent, depth, guid=None, doc=None, note=None):
        if iri in self.nodes or len(self.nodes) >= self.max_nodes:
            return False
        s = URIRef(iri)
        title = next((str(o) for p in (DCTERMS.title, RDFS.label) for o in self.g.objects(s, p)), None)
        self.nodes[iri] = {"iri": iri, "level": self.level(s), "title": title, "parent": parent, "depth": depth,
                           "guid": guid, "document": doc, "note": note, "fingerprint": self.fingerprints.get(doc),
                           "results": {}}
        return True

    def walk(self, start):
        g, doc = self.fetch(start)
        if g is None:
            raise SystemExit(f"Cannot read metadata at {start}: {doc}")
        self.g += g
        root = self.main_subject(g, doc, start)
        self.docs[doc] = str(root)
        self.add(str(root), None, 0, guid=start, doc=doc)
        queue = deque([(root, 0)])
        while queue:
            s, depth = queue.popleft()
            # Our own index lists the catalog documents with ldp:contains and stubs with fdp:hasCatalog;
            # when a node has ldp:contains, follow only that to avoid visiting each catalog twice.
            preds = [LDP.contains] if (s, LDP.contains, None) in self.g else LINKS
            children = [o for p in preds for o in self.g.objects(s, p) if isinstance(o, URIRef)]
            for child in children:
                c = str(child)
                if c in self.nodes:
                    continue
                child_depth = depth + (0 if self.level(child) in (None, "fdp") and (s, LDP.contains, child) in self.g else 1)
                if child_depth > self.max_depth:
                    continue
                # Dereference the child: it may be a document (ldp:contains) or an IRI described elsewhere.
                cg, cdoc = self.fetch(self.url(c))
                if cg is not None:
                    self.g += cg
                    subject = self.main_subject(cg, cdoc, c)
                    if subject is not None and str(subject) != c and (s, LDP.contains, child) in self.g:
                        # A contained document: test the document URL as the GUID of its main subject.
                        self.add(str(subject), str(s), child_depth, guid=self.url(c), doc=cdoc)
                        queue.append((subject, child_depth))
                        continue
                    self.add(c, str(s), child_depth, guid=self.url(c), doc=cdoc)
                else:
                    self.add(c, str(s), child_depth, note=f"not dereferenceable: {cdoc}")
                queue.append((child, child_depth))
        return self.nodes


def run_test(test, guid):
    body = json.dumps({"resource_identifier": guid})
    try:
        raw = subprocess.run(["curl", "-sS", "-m", "300", "-X", "POST", "-H", "Content-Type: application/json",
                              "-H", "Accept: application/json", "-d", body, TEST_API + test],
                             capture_output=True, text=True, timeout=320).stdout
        d = json.loads(raw)
    except Exception as e:  # noqa: BLE001 - the service sometimes errors; record it
        return {"value": "error", "summary": f"test service error: {str(e)[:100]}"}
    res = next((n for n in d.get("@graph", []) if "TestResult" in str(n.get("@type"))), {})
    val = lambda v: v.get("@value") if isinstance(v, dict) else v
    value = val(res.get("prov:value") or res.get("value")) or "error"
    log = val(res.get("ftr:log") or res.get("log")) or ""
    verdicts = [l.strip() for l in log.splitlines() if re.match(r"\s*(SUCCESS|FAILURE|INDETERMINATE)", l)]
    return {"value": value, "summary": (verdicts[-1] if verdicts else "")[:300]}


def test_id(url):
    """Short id of a test, e.g. test_FM_F1_M_IdentUnique, from its IRI."""
    return url.rstrip("/").rsplit("/", 1)[-1]


def run_algorithm(algorithm, guid):
    """All metrics in one call. Returns ({test id: result}, [test descriptions], FTR JSON-LD string)."""
    try:
        raw = subprocess.run(["curl", "-sS", "-m", "900", "-L", "--post301", "--post302", "--post303", "-X", "POST",
                              "-H", "Content-Type: application/json", "-H", "Accept: application/json",
                              "-d", json.dumps({"guid": guid}), algorithm],
                             capture_output=True, text=True, timeout=920).stdout
        d = json.loads(raw)
    except Exception as e:  # noqa: BLE001 - record and carry on
        return {}, [], None, f"assessment service error: {str(e)[:120]}"
    by_ref = {t["reference"]: t for t in d.get("tests", [])}
    narrative = {}
    for c, text in zip(d.get("conditions", []), d.get("narratives", [])):
        narrative[c.get("condition", "").split("_", 1)[-1]] = text
    results, tests = {}, []
    for ref, r in (d.get("test_results") or {}).items():
        t = by_ref.get(ref, {})
        tid = test_id(t.get("testid", ref))
        results[tid] = {"value": r.get("result", "error"), "summary": (narrative.get(ref) or "")[:300]}
        tests.append({"id": tid, "url": t.get("testid", ""), "title": t.get("title", ref), "reference": ref})
    return results, tests, d.get("resultset"), None


def load_previous(source):
    """The previous report, from a file or URL; {} when there is none."""
    if not source or source == "none":
        return {}
    try:
        if source.startswith("http"):
            resp = curl(source, accept="application/json")
            return json.loads(resp[4]) if resp and resp[0] == 200 else {}
        return json.loads(Path(source).read_text())
    except Exception:  # noqa: BLE001 - no previous report means a full assessment
        return {}


def markdown(report):
    sym = {"pass": "✅", "fail": "❌", "indeterminate": "➖", "error": "⚠️"}
    lines = [f"# FAIR assessment of {report['start']}", "",
             f"{report['generated']} · {len(report['tests'])} OSTrails tests · depth: {report['depth']} · "
             f"{report.get('assessedNow', 0)} resources assessed in this run, {report.get('reused', 0)} unchanged and reused", ""]
    tested = [n for n in report["nodes"] if n["results"]]
    lines += ["| Level | Resource | Pass | Fail | Indet. | Error | Assessed |", "|---|---|---|---|---|---|---|"]
    for n in tested:
        c = Counter(r["value"] for r in n["results"].values())
        when = (n.get("assessedAt") or "")[:10] + (" (unchanged)" if n.get("reused") else "")
        lines.append(f"| {n['level'] or '?'} | [{(n['title'] or n['iri'])[:70]}]({n['guid']}) | {c['pass']} | {c['fail']} | {c['indeterminate']} | {c['error']} | {when} |")
    skipped = [n for n in report["nodes"] if not n["results"]]
    if skipped:
        by = Counter(n["level"] or "?" for n in skipped)
        lines += ["", f"**Not tested ({len(skipped)}):** " + ", ".join(f"{v} {k}" for k, v in by.items())
                  + " — their IRIs do not resolve (e.g. " + (skipped[0]["note"] or "") + ")."]
    lines += ["", "## Per test", "", "| Test | " + " | ".join((n["level"] or "?")[:4] + str(i + 1) for i, n in enumerate(tested)) + " |",
              "|---|" + "---|" * len(tested)]
    for t in report["tests"]:
        lines.append(f"| [{t.get('reference') or t['id'].replace('test_FM_', '')}]({t['url']}) | " + " | ".join(sym.get(n["results"].get(t["id"], {}).get("value"), "") for n in tested) + " |")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default=SITE_URL, help="FDP root, or a web page linking it with rel=describedby (default: siteUrl)")
    ap.add_argument("--rewrite", action="append", default=[], help="FROM=TO prefix rewrite used to fetch and test IRIs")
    ap.add_argument("--depth", choices=list(DEPTH), default="catalog")
    ap.add_argument("--algorithm", default=ALGORITHM, help="FAIR Champion algorithm URL (all metrics in one call)")
    ap.add_argument("--per-test", action="store_true", help="call the tests in --tests one by one instead of the algorithm")
    ap.add_argument("--tests", default=str(ROOT / "fair" / "tests.txt"))
    ap.add_argument("--previous", default=None, help="previous report.json (file or URL; default: <siteUrl>fair-report.json, 'none' to skip)")
    ap.add_argument("--max-age-days", type=int, default=30, help="re-test unchanged resources whose results are older than this")
    ap.add_argument("--full", action="store_true", help="re-test every resource, changed or not")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--max-nodes", type=int, default=300)
    ap.add_argument("--out", default="fair-report")
    args = ap.parse_args()

    rewrite = [tuple(r.split("=", 1)) for r in args.rewrite] or [(BASE_IRI, SITE_URL.rstrip("/") + "/fdp/")]
    walker = Walker(rewrite, DEPTH[args.depth], args.max_nodes)
    nodes = list(walker.walk(args.start).values())
    print(f"walked {len(nodes)} nodes: " + ", ".join(f"{v} {k}" for k, v in Counter(n['level'] or '?' for n in nodes).items()), file=sys.stderr)

    now = datetime.datetime.now(datetime.timezone.utc)
    previous = {} if args.full else load_previous(args.previous or (SITE_URL + "fair-report.json" if args.start == SITE_URL else "none"))
    prev_nodes = {n.get("guid"): n for n in previous.get("nodes", []) if n.get("guid")}
    tests = previous.get("tests", []) if previous.get("mode", "algorithm" if not args.per_test else "tests") == ("tests" if args.per_test else "algorithm") else []

    def reusable(n):
        p = prev_nodes.get(n["guid"])
        if not p or not p.get("results") or not n.get("fingerprint") or p.get("fingerprint") != n["fingerprint"]:
            return None
        if any(r.get("summary", "").startswith(("test service error", "assessment service error")) for r in p["results"].values()):
            return None
        try:
            age = now - datetime.datetime.fromisoformat(p.get("assessedAt") or previous["generated"])
        except (KeyError, ValueError):
            return None
        return p if age.days < args.max_age_days else None

    todo = []
    for n in nodes:
        if not n["guid"]:
            continue
        p = reusable(n)
        if p:
            n.update(results=p["results"], assessedAt=p.get("assessedAt") or previous["generated"], reused=True)
        else:
            todo.append(n)
    print(f"assessing {len(todo)} resources; {sum(1 for n in nodes if n.get('reused'))} unchanged and reused …", file=sys.stderr)

    out = Path(args.out)
    (out / "ftr").mkdir(parents=True, exist_ok=True)
    stamp = now.isoformat(timespec="seconds")
    if args.per_test:
        names = [l.strip() for l in Path(args.tests).read_text().splitlines() if l.strip() and not l.startswith("#")]
        tests = [{"id": t, "url": f"https://tests.ostrails.eu/tests/{t}"} for t in names]
        jobs = [(n, t) for n in todo for t in names]
        with ThreadPoolExecutor(args.workers) as ex:
            for (n, t), r in zip(jobs, ex.map(lambda j: run_test(j[1], j[0]["guid"]), jobs)):
                n["results"][t] = r
        for n in todo:
            n["assessedAt"] = stamp
    else:
        with ThreadPoolExecutor(args.workers) as ex:
            for n, (results, described, ftr, err) in zip(todo, ex.map(lambda n: run_algorithm(args.algorithm, n["guid"]), todo)):
                n["assessedAt"] = stamp
                n["results"] = results or {"algorithm": {"value": "error", "summary": err or "no results"}}
                if described and len(described) >= len(tests):
                    tests = described
                if ftr:
                    (out / "ftr" / (hashlib.sha1(n["guid"].encode()).hexdigest()[:16] + ".jsonld")).write_text(ftr)
                    n["ftr"] = f"ftr/{hashlib.sha1(n['guid'].encode()).hexdigest()[:16]}.jsonld"
    for n in nodes:
        c = Counter(r["value"] for r in n["results"].values())
        n["score"] = {"pass": c["pass"], "fail": c["fail"], "indeterminate": c["indeterminate"], "error": c["error"], "total": len(n["results"])}

    report = {"generated": stamp, "start": args.start, "depth": args.depth,
              "mode": "tests" if args.per_test else "algorithm",
              "service": TEST_API if args.per_test else args.algorithm,
              "assessedNow": len(todo), "reused": sum(1 for n in nodes if n.get("reused")),
              "tests": tests, "nodes": nodes}
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    (out / "summary.md").write_text(markdown(report))
    print((out / "summary.md").read_text())


if __name__ == "__main__":
    main()
