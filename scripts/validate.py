"""Parse every Turtle file under fdp/ and check the FDP/DCAT shape we rely on."""
import glob, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fdpconfig import BASE_IRI  # noqa: E402
from rdflib import Graph, Namespace, RDF

DCAT = Namespace("http://www.w3.org/ns/dcat#")
DCT = Namespace("http://purl.org/dc/terms/")
FDP = Namespace("https://w3id.org/fdp/fdp-o#")

errors, merged = [], Graph()
for path in sorted(glob.glob("fdp/**/*.ttl", recursive=True)):
    g = Graph()
    try:
        g.parse(path, format="turtle")
    except Exception as e:
        errors.append(f"{path}: parse error: {e}")
        continue
    merged += g
    print(f"ok  {path}  ({len(g)} triples)")

for d in merged.subjects(RDF.type, DCAT.Distribution):
    if not merged.value(d, DCAT.accessURL):
        errors.append(f"{d}: distribution without dcat:accessURL")
    if not merged.value(d, DCT.type):
        errors.append(f"{d}: distribution without access-method dcterms:type")
for ds in merged.subjects(RDF.type, DCAT.Dataset):
    if not list(merged.objects(ds, DCAT.distribution)):
        errors.append(f"{ds}: dataset without distributions")
    for dist in merged.objects(ds, DCAT.distribution):
        if (dist, RDF.type, DCAT.Distribution) not in merged:
            errors.append(f"{ds}: dangling distribution {dist}")
for s, o in merged.subject_objects(DCAT.accessService):
    if (o, RDF.type, DCAT.DataService) not in merged:
        errors.append(f"{s}: dangling accessService {o}")

# Index entries pointing into this repository must have a matching file.
RDFS_SEEALSO = Namespace("http://www.w3.org/2000/01/rdf-schema#").seeAlso
FDPO = Namespace("https://w3id.org/fdp/fdp-o#")
for cat in merged.objects(None, FDPO.hasCatalog):
    for target in merged.objects(cat, RDFS_SEEALSO):
        t = str(target)
        if t.startswith(BASE_IRI) and t.endswith(".ttl"):
            local = "fdp/" + t[len(BASE_IRI):]
            if not glob.glob(local):
                errors.append(f"{cat}: rdfs:seeAlso points at {local}, which does not exist")

# ldp:contains on the index must list exactly the documents its fdp:hasCatalog entries point at.
LDP = Namespace("http://www.w3.org/ns/ldp#")
for root in set(merged.subjects(FDPO.hasCatalog, None)):
    contained = {str(o) for o in merged.objects(root, LDP.contains)}
    if contained:
        expected = {str(t) for c in merged.objects(root, FDPO.hasCatalog) for t in merged.objects(c, RDFS_SEEALSO)}
        for x in sorted(expected - contained):
            errors.append(f"{root}: ldp:contains is missing {x}")
        for x in sorted(contained - expected):
            errors.append(f"{root}: ldp:contains lists {x}, which no fdp:hasCatalog entry points at")

# Every DCMI Terms and FOAF term used must exist in that vocabulary (e.g. there is no dcterms:version;
# use dcat:version). DCAT is not checked: rdflib ships DCAT 2, which lacks DCAT 3 terms such as dcat:version.
from rdflib.namespace import DCTERMS as _DCT, FOAF as _FOAF
for t in {str(x) for triple in merged for x in triple[1:]}:
    for ns in (_DCT, _FOAF):
        base = str(ns._NS)
        if t.startswith(base) and len(t) > len(base):
            try:
                getattr(ns, t[len(base):])
            except AttributeError:
                errors.append(f"{t} is not defined in {base}")

n = lambda t: len(set(merged.subjects(RDF.type, t)))
print(f"\n{n(DCAT.Catalog)} catalogs, {n(DCAT.Dataset)} datasets, {n(DCAT.Distribution)} distributions, {n(DCAT.DataService)} services")
print("\n".join(errors) or "no problems found")
sys.exit(1 if errors else 0)
