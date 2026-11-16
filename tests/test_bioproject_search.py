"""Offline end-to-end coverage: bulk XML -> enriched chunks -> search -> export."""
from contextlib import closing, redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from test_chunked_search import load, prepare, FakeS3, ROOT

enricher = load("bioproject_enricher", ROOT / "scripts/enrich_bioprojects.py")
builder = load("bioproject_keyword_builder", ROOT / "scripts/build_keyword_index.py")

XML = """<PackageSet xmlns="https://example.test/bioproject">
<Package><Project><Project><ProjectID><ArchiveID accession="PRJNA264"/>
<SecondaryArchiveID accession="PRJEB99"/></ProjectID><ProjectDescr>
<Title>Antibiotic resistance</Title>
<Description>&lt;p&gt;Clinical isolates with &lt;i&gt;methicillin&lt;/i&gt; resistance.\nCafé samples: 100% positive.&lt;/p&gt;</Description>
<Publication><Title>THIS IS A DIFFERENT TITLE</Title></Publication>
</ProjectDescr></Project></Project></Package>
<Package><Project><Project><ProjectID><ArchiveID accession="PRJDB2"/></ProjectID>
<ProjectDescr><Title>Soil microbiome</Title><Description>Environmental study</Description></ProjectDescr>
</Project></Project></Package>
<Package><Project><Project><ProjectID><ArchiveID accession="PRJNA3"/></ProjectID>
<ProjectDescr><Title>100% literal _title 'OR 1=1'</Title></ProjectDescr>
</Project></Project></Package></PackageSet>"""


class BioProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.columns = ["assembly_accession", "organism_name", "group", "bioproject", "genome_size", "isolate"]
        self.original_rows = [
            ["GCA_1.1", "Staphylococcus aureus", "bacteria", "PRJNA264", "100", "A"],
            ["GCA_2.1", "Escherichia coli", "bacteria", "PRJNA264", "200", "B"],
            ["GCA_3.1", "Bacillus subtilis", "bacteria", "PRJDB2", "300", "methicillin"],
            ["GCA_4.1", "Staphylococcus epidermidis", "bacteria", "PRJNA404", "400", "D"],
            ["GCA_5.1", "Test virus", "viral", "na", "500", "E"],
            ["GCA_6.1", "Other organism", "bacteria", "PRJNA264; PRJDB2", "600", "F"],
            ["GCA_7.1", "Alias organism", "bacteria", "PRJEB99", "700", "G"],
            ["GCA_8.1", "Another organism", "bacteria", "PRJNA3", "800", "H"],
        ]
        source = self.root / "source.tsv"
        source.write_text("#" + "\t".join(self.columns) + "\n" + "\n".join("\t".join(r) for r in self.original_rows) + "\n", encoding="utf-8")
        self.xml = self.root / "bioproject.xml"
        self.xml.write_text(XML, encoding="utf-8")
        self.chunks, self.enriched, self.index = [self.root / n for n in ("chunks", "enriched", "keywords")]
        with redirect_stderr(io.StringIO()):
            self.source_manifest = prepare.prepare(str(source), self.chunks, rows_per_chunk=1)
            self.original_files = {str(p.relative_to(self.chunks)): p.read_bytes() for p in self.chunks.rglob("*") if p.is_file()}
            self.manifest = enricher.enrich(self.chunks, self.xml, self.enriched)
            self.report = builder.build(self.enriched, self.index)
        self.fake = FakeS3({"index/" + str(p.relative_to(self.enriched)): p.read_bytes() for p in self.enriched.rglob("*") if p.is_file()})
        self.key = "index/keywords/organism_names.sqlite"
        self.fake.objects[self.key] = (self.index / "organism_names.sqlite").read_bytes()
        old = sys.modules.get("boto3")
        sys.modules["boto3"] = types.SimpleNamespace(client=lambda *a, **k: self.fake)
        try:
            self.api = load("bioproject_api", ROOT / "lambda/assembly_grasp.py")
        finally:
            if old is None:
                del sys.modules["boto3"]
            else:
                sys.modules["boto3"] = old
        self.api.MANIFEST_KEY = "index/manifest.json"
        self.api.KEYWORD_INDEX_KEY = self.key
        self.cache_ids = [self.manifest["dataset_id"]]

    def tearDown(self):
        for dataset_id in self.cache_ids:
            fingerprint = hashlib.sha256((self.key + dataset_id).encode()).hexdigest()[:24]
            (Path(tempfile.gettempdir()) / ("assembly-keywords-" + fingerprint + ".sqlite")).unlink(missing_ok=True)
        self.temp.cleanup()

    def request(self, **body):
        response = self.api.lambda_handler({"body": json.dumps({"query": "methicillin", "search_mode": "bioproject", "format": "json", **body})}, None)
        return response["statusCode"], json.loads(response["body"])

    def test_enrichment_preserves_originals_and_reports_missing_projects(self):
        self.assertNotEqual(self.manifest["dataset_id"], self.source_manifest["dataset_id"])
        self.assertEqual(self.manifest["parent_dataset_id"], self.source_manifest["dataset_id"])
        self.assertEqual(self.manifest["total_rows"], len(self.original_rows))
        self.assertEqual(self.manifest["columns"], self.columns + enricher.ADDED_COLUMNS)
        for key, content in self.original_files.items():
            self.assertEqual((self.chunks / key).read_bytes(), content)
        self.assertEqual(self.xml.read_text(encoding="utf-8"), XML)
        self.assertEqual((self.enriched / "missing_bioprojects.txt").read_text(), "PRJNA404\n")
        self.assertEqual(self.manifest["bioproject_enrichment"]["rows_with_project_text"], 6)
        for group in self.manifest["groups"].values():
            for chunk in group["chunks"]:
                self.assertEqual(chunk["bytes"], (self.enriched / chunk["key"]).stat().st_size)

    def test_bulk_xml_namespaces_nested_wrappers_html_and_secondary_accessions(self):
        records = dict((a, (t, d)) for a, t, d in enricher.project_records(self.xml))
        self.assertEqual(records["PRJNA264"][0], "Antibiotic resistance")
        self.assertNotIn("DIFFERENT", records["PRJNA264"][0])
        self.assertEqual(records["PRJNA264"][1], "Clinical isolates with methicillin resistance. Café samples: 100% positive.")
        self.assertEqual(records["PRJEB99"], records["PRJNA264"])
        self.assertEqual(records["PRJNA3"][1], "")

    def test_project_keyword_search_counts_pages_and_matches_only_project_text(self):
        status, first = self.request(page_size=2)
        self.assertEqual(status, 200)
        self.assertEqual(first["search_mode"], "bioproject")
        self.assertEqual(first["total"], 4)
        self.assertEqual(first["pages"], 2)
        _, second = self.request(page_size=2, page=2, dataset_id=first["dataset_id"])
        self.assertCountEqual([r[0] for r in first["rows"] + second["rows"]], ["GCA_1.1", "GCA_2.1", "GCA_6.1", "GCA_7.1"])
        self.assertEqual(self.fake.keys.count(self.key), 1)

    def test_title_phrase_literal_unicode_and_no_cross_field_match(self):
        self.assertEqual(self.request(query="ANTIBIOTIC RESISTANCE")[1]["total"], 4)
        self.assertEqual(self.request(query="café")[1]["total"], 4)
        self.assertEqual(self.request(query="_title 'OR 1=1'")[1]["total"], 1)
        self.assertEqual(self.request(query="resistance Clinical")[1]["total"], 0)
        self.assertEqual(self.request(query="Staphylococcus")[1]["total"], 0)

    def test_metadata_filters_before_counts_pages_and_full_tsv_export(self):
        rules = [{"column": "genome_size", "op": "gte", "value": "200"}]
        _, result = self.request(filters=rules, page_size=1)
        self.assertEqual(result["total"], 3)
        _, last = self.request(filters=rules, page_size=1, page=3)
        self.assertEqual(len(last["rows"]), 1)
        response = self.api.lambda_handler({"body": json.dumps({"query": "methicillin", "search_mode": "bioproject", "format": "tsv", "filters": rules})}, None)
        self.assertEqual(response["statusCode"], 200)
        lines = response["body"].splitlines()
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[0].endswith("bioproject_title\tbioproject_description"))
        for line in lines[1:]:
            self.assertEqual(len(line.split("\t")), len(self.columns) + 2)
            self.assertIn("Clinical isolates", line)

    def test_project_filters_work_in_name_and_organism_keyword_modes(self):
        rules = [{"column": "bioproject_description", "op": "contains", "value": "methicillin"}]
        _, result = self.request(search_mode="name", query="Staphylococcus", filters=rules)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0][-2], "Antibiotic resistance")
        _, result = self.request(search_mode="keyword", query="coli", filters=rules)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0][0], "GCA_2.1")
        _, missing = self.request(search_mode="name", query="Test", filters=[{"column": "bioproject_description", "op": "missing"}])
        self.assertEqual(missing["total"], 1)
        self.assertEqual(missing["rows"][0][-2:], ["", ""])

    def test_enriched_default_name_fast_page_includes_metadata(self):
        _, result = self.request(search_mode="name", query="Staphylococcus")
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["rows"][0][-2], "Antibiotic resistance")
        self.assertEqual(result["rows"][1][-2:], ["", ""])

    def test_capabilities_missing_index_and_dataset_pin(self):
        self.assertEqual(self.request(operation="schema")[1]["search_modes"], ["name", "keyword", "bioproject"])
        self.assertEqual(self.request(dataset_id="old-dataset")[0], 409)
        self.api.KEYWORD_INDEX_KEY = ""
        self.assertEqual(self.request()[0], 501)
        self.assertEqual(self.request(operation="schema")[1]["search_modes"], ["name"])

    def test_catalog_stores_shared_text_once_and_checks_index_capability(self):
        database = self.index / "organism_names.sqlite"
        with closing(sqlite3.connect(str(database))) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM names").fetchone()[0], 8)
            self.assertLess(conn.execute("SELECT count(*) FROM projects").fetchone()[0], 8)
            conn.execute("DELETE FROM metadata WHERE key='search_modes'")
            conn.commit()
        self.fake.objects[self.key] = database.read_bytes()
        self.assertEqual(self.request()[0], 409)

    def test_reuses_cache_and_refuses_overwrite_and_stale_cache(self):
        cache = Path(str(self.xml) + ".assemblygrasp.sqlite")
        with patch.object(enricher, "project_records", side_effect=AssertionError("should reuse cache")):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(enricher.project_cache(self.xml, cache), cache)
        with self.assertRaisesRegex(ValueError, "already exists"):
            enricher.enrich(self.chunks, self.xml, self.enriched)
        self.xml.write_text(XML + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "another XML snapshot"):
            enricher.project_cache(self.xml, cache)

    def test_bad_xml_or_row_counts_do_not_publish_partial_output(self):
        bad = self.root / "bad.xml"
        bad.write_text(XML[:-20], encoding="utf-8")
        with self.assertRaises(ET.ParseError):
            enricher.enrich(self.chunks, bad, self.root / "bad-output")
        self.assertFalse(Path(str(bad) + ".assemblygrasp.sqlite").exists())
        self.assertFalse((self.root / "bad-output").exists())
        key = next(iter(self.source_manifest["groups"].values()))["chunks"][0]["key"]
        (self.chunks / key).write_text("#header\n")
        with redirect_stderr(io.StringIO()), self.assertRaisesRegex(ValueError, "Row count"):
            enricher.enrich(self.chunks, self.xml, self.root / "bad-output")
        self.assertFalse((self.root / "bad-output").exists())


if __name__ == "__main__":
    unittest.main()
