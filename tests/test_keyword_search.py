"""End-to-end keyword tests against prepared chunks, SQLite, and fake S3."""
from contextlib import redirect_stderr, closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest

from test_chunked_search import load, prepare, FakeS3, ROOT

builder = load("keyword_builder", ROOT / "scripts/build_keyword_index.py")


class KeywordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.columns = ["assembly_accession", "organism_name", "group", "genome_size", "isolate"]
        self.rows = [
            ["GCA_1.1", "Escherichia coli", "bacteria", "100", "phage only in this other column"],
            ["GCA_2.1", "Escherichia phage Alpha", "viral", "200", "A"],
            ["GCA_3.1", "Aphis PHAGE Beta", "viral", "300", "B"],
            ["GCA_4.1", "Unrelated bacteriophage Gamma", "viral", "400", "C"],
            ["GCA_5.1", "Aphis phage Delta", "viral", "na", "D"],
            ["GCA_6.1", "Test 100% virus", "viral", "500", "E"],
            ["GCA_7.1", "Test RNA virus", "viral", "600", "F"],
            ["GCA_8.1", "Test CAFÉ organism", "bacteria", "700", "G"],
        ]
        source = self.root / "source.tsv"
        source.write_text("#" + "\t".join(self.columns) + "\n" + "\n".join("\t".join(r) for r in self.rows) + "\n")
        self.chunks = self.root / "chunks"
        self.index = self.root / "keywords"
        with redirect_stderr(io.StringIO()):
            self.manifest = prepare.prepare(str(source), self.chunks, rows_per_chunk=2)
            self.report = builder.build(self.chunks, self.index)
        self.fake = FakeS3({"index/" + str(p.relative_to(self.chunks)): p.read_bytes() for p in self.chunks.rglob("*") if p.is_file()})
        self.key = "index/keyword/organism_names.sqlite"
        self.fake.objects[self.key] = (self.index / "organism_names.sqlite").read_bytes()
        old = sys.modules.get("boto3")
        sys.modules["boto3"] = types.SimpleNamespace(client=lambda *a, **k: self.fake)
        try:
            self.api = load("keyword_api", ROOT / "lambda/assembly_grasp.py")
        finally:
            if old is None:
                del sys.modules["boto3"]
            else:
                sys.modules["boto3"] = old
        self.api.MANIFEST_KEY = "index/manifest.json"
        self.api.KEYWORD_INDEX_KEY = self.key
        self.ids = [self.manifest["dataset_id"]]

    def tearDown(self):
        for dataset_id in self.ids:
            fingerprint = hashlib.sha256((self.key + dataset_id).encode()).hexdigest()[:24]
            path = Path(tempfile.gettempdir()) / ("assembly-keywords-" + fingerprint + ".sqlite")
            if path.exists():
                path.unlink()
        self.temp.cleanup()

    def request(self, **body):
        payload = {"query": "phage", "search_mode": "keyword", "format": "json", **body}
        response = self.api.lambda_handler({"body": json.dumps(payload)}, None)
        return response["statusCode"], json.loads(response["body"])

    def test_anywhere_matching_and_pages_across_different_prefixes(self):
        status, first = self.request(query="PHAGE", page_size=2)
        self.assertEqual(status, 200)
        self.assertEqual(first["total"], 4)
        self.assertEqual(first["pages"], 2)
        self.assertEqual(first["search_mode"], "keyword")
        _, second = self.request(page_size=2, page=2, dataset_id=first["dataset_id"])
        self.assertCountEqual(first["rows"] + second["rows"], self.rows[1:5])
        self.assertEqual(len(set(r[0] for r in first["rows"] + second["rows"])), 4)
        self.assertEqual(self.fake.keys.count(self.key), 1, "Warm requests should reuse the local name index")

    def test_scientific_name_mode_retains_complete_leading_words(self):
        self.assertEqual(self.request(search_mode="name", query="phage")[1]["total"], 0)
        self.assertEqual(self.request(search_mode="name", query="Escherichia")[1]["total"], 2)
        self.assertEqual(self.request(search_mode="name", query="Escher")[1]["total"], 0)
        self.assertEqual(self.request(search_mode="other")[0], 400)

    def test_filters_apply_before_pagination_and_counts(self):
        filters = [{"column": "group", "op": "eq", "value": "viral"},
                   {"column": "genome_size", "op": "gte", "value": "250"}]
        status, first = self.request(filters=filters, page_size=1)
        self.assertEqual(status, 200)
        self.assertEqual(first["total"], 2)
        _, second = self.request(filters=filters, page_size=1, page=2)
        self.assertCountEqual(first["rows"] + second["rows"], self.rows[2:4])

    def test_literal_substrings_unicode_phrases_and_empty_results(self):
        self.assertEqual(self.request(query="100%")[1]["rows"], [self.rows[5]])
        self.assertEqual(self.request(query="café")[1]["rows"], [self.rows[7]])
        self.assertEqual(self.request(query="RNA VIRUS")[1]["rows"], [self.rows[6]])
        self.assertEqual(self.request(query="no match at all")[1]["total"], 0)

    def test_export_respects_keyword_mode_and_filters(self):
        response = self.api.lambda_handler({"body": json.dumps({"query": "phage", "search_mode": "keyword", "format": "tsv", "filters": [{"column": "genome_size", "op": "gte", "value": "250"}]})}, None)
        self.assertEqual(response["statusCode"], 200)
        lines = response["body"].splitlines()
        self.assertEqual(lines[0], "#" + "\t".join(self.columns))
        self.assertCountEqual([r.split("\t") for r in lines[1:]], self.rows[2:4])

    def test_capabilities_and_missing_or_mismatched_index(self):
        self.assertEqual(self.request(operation="schema")[1]["search_modes"], ["name", "keyword"])
        self.api.KEYWORD_INDEX_KEY = ""
        self.assertEqual(self.request()[0], 501)
        self.assertEqual(self.request(operation="schema")[1]["search_modes"], ["name"])
        self.api.KEYWORD_INDEX_KEY = self.key
        changed = {**self.manifest, "dataset_id": "different-dataset"}
        self.ids.append(changed["dataset_id"])
        self.fake.objects["index/manifest.json"] = json.dumps(changed).encode()
        self.api._manifest.cache_clear()
        self.assertEqual(self.request()[0], 409)

    def test_builder_preserves_chunks_and_refuses_bad_row_counts(self):
        self.assertEqual(json.loads((self.chunks / "manifest.json").read_text()), self.manifest)
        self.assertEqual(self.report["rows"], len(self.rows))
        with closing(sqlite3.connect(str(self.index / "organism_names.sqlite"))) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM names").fetchone()[0], len(self.rows))
        with self.assertRaises(ValueError):
            builder.build(self.chunks, self.index)
        first = next(iter(self.manifest["groups"].values()))["chunks"][0]
        (self.chunks / first["key"]).write_text("#" + "\t".join(self.columns) + "\n")
        with self.assertRaisesRegex(ValueError, "Row count"):
            builder.build(self.chunks, self.root / "invalid-output")
        self.assertFalse((self.root / "invalid-output").exists())

    def test_deadline_during_first_download(self):
        context = types.SimpleNamespace(get_remaining_time_in_millis=lambda: 1000)
        response = self.api.lambda_handler({"body": json.dumps({"query": "phage", "search_mode": "keyword", "format": "json"})}, context)
        self.assertEqual(response["statusCode"], 503)


if __name__ == "__main__":
    unittest.main()
