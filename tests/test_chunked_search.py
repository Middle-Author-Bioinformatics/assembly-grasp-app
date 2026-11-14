"""Offline integration tests with generated NCBI-style fixtures and an in-memory S3."""
from contextlib import redirect_stderr
import gzip
import importlib.util
import io
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = load("prepare", ROOT / "scripts/prepare_assembly_chunks.py")


class FakeS3:
    def __init__(self, objects):
        self.objects = objects
        self.keys = []

    def get_object(self, Bucket, Key, **kwargs):
        self.keys.append(Key)
        return {"Body": io.BytesIO(self.objects[Key]), "ETag": '"fixture-1"'}


class ChunkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        # Deliberately reordered columns and interleaved genera, with Unicode.
        self.columns = ["organism_name", "assembly_level", "assembly_accession", "genome_size", "isolate"]
        self.data = []
        for i in range(125):
            self.data.append(["Geobacter sulfurreducens PCA", "Complete Genome" if i % 2 == 0 else "Scaffold", "GCA_%03d" % i, str(1000 + i), "café"])
            if i % 7 == 0:
                self.data.append(["Escherichia coli", "Contig", "OTHER_%03d" % i, "na", "B"])
        self.data.append(["Geobacter metallireducens", "Complete Genome", "GCA_last", "na", "A"])
        self.data.append(["Geobacterium sp.", "Contig", "PREFIX_FALSE", "Inf", "A"])
        self.text = "## Generated test fixture\n# " + "\t".join(self.columns) + "\n" + "\n".join("\t".join(r) for r in self.data) + "\n"
        self.source = self.root / "source.tsv"
        self.source.write_text(self.text, encoding="utf-8")
        self.out = self.root / "chunks"
        with redirect_stderr(io.StringIO()):
            self.manifest = prepare.prepare(str(self.source), self.out, rows_per_chunk=7, max_open_files=1)
        self.fake = FakeS3({"index/" + str(p.relative_to(self.out)): p.read_bytes() for p in self.out.rglob("*") if p.is_file()})
        self.fake.objects["source.tsv"] = self.text.encode()
        previous = sys.modules.get("boto3")
        sys.modules["boto3"] = types.SimpleNamespace(client=lambda *a, **k: self.fake)
        try:
            self.api = load("api", ROOT / "lambda/assembly_grasp.py")
        finally:
            if previous is None:
                del sys.modules["boto3"]
            else:
                sys.modules["boto3"] = previous
        self.api.MANIFEST_KEY = "index/manifest.json"
        self.api.ASSEMBLY_KEY = "source.tsv"

    def tearDown(self):
        self.temp.cleanup()

    def request(self, **body):
        body = {"query": "Geobacter", "format": "json", **body}
        response = self.api.lambda_handler({"body": json.dumps(body)}, None)
        return response["statusCode"], json.loads(response["body"])

    def test_row_preservation_and_chunk_limits(self):
        self.assertEqual(self.source.read_text(), self.text)
        recovered = []
        for path in self.out.rglob("*.tsv"):
            lines = path.read_text().splitlines()
            rows = [x.split("\t") for x in lines if not x.startswith("#")]
            self.assertLessEqual(len(rows), 7)
            self.assertLessEqual(path.stat().st_size, self.manifest["bytes_per_chunk"])
            self.assertTrue(lines[0].startswith("##"))
            recovered.extend(rows)
        self.assertCountEqual(recovered, self.data)
        self.assertEqual(self.manifest["total_rows"], len(self.data))

    def test_pagination_has_every_matching_row_exactly_once(self):
        status, first = self.request(page_size=25)
        self.assertEqual(status, 200)
        self.assertEqual(first["total"], 126)
        self.assertEqual(first["pages"], 6)
        # The unfiltered fast path reads only the page's chunk range.
        self.assertLess(first["scanned"], first["total"])
        rows = []
        for page in range(1, first["pages"] + 1):
            status, result = self.request(page=page, page_size=25, dataset_id=first["dataset_id"])
            self.assertEqual(status, 200)
            rows.extend(result["rows"])
        self.assertEqual(rows, [r for r in self.data if r[0].split()[0] == "Geobacter"])
        other_chunks = ["index/" + c["key"] for c in self.manifest["groups"]["escherichia"]["chunks"]]
        self.assertTrue(set(other_chunks).isdisjoint(self.fake.keys))

    def test_combined_filters_precede_pagination(self):
        filters = [{"column": "assembly_level", "op": "eq", "value": "complete genome"},
                   {"column": "genome_size", "op": "gte", "value": "1100"}]
        status, result = self.request(filters=filters, page_size=5, page=2)
        expected = [r for r in self.data if r[0].startswith("Geobacter ") and r[1] == "Complete Genome" and r[3] != "na" and int(r[3]) >= 1100]
        self.assertEqual(status, 200)
        self.assertEqual(result["total"], len(expected))
        self.assertEqual(result["rows"], expected[5:10])

    def test_missing_and_text_filters(self):
        status, result = self.request(filters=[{"column": "genome_size", "op": "missing"}])
        self.assertEqual(status, 200)
        self.assertEqual(result["rows"][0][2], "GCA_last")
        status, result = self.request(filters=[{"column": "isolate", "op": "contains", "value": "CAFÉ"}])
        self.assertEqual(result["total"], 125)
        self.assertFalse(self.api._passes(["nan"], [(0, "gte", 0)]))
        self.assertFalse(self.api._passes(["Inf"], [(0, "lte", 100)]))

    def test_species_and_absent_genus(self):
        self.assertEqual(self.request(query="GEObacter SULFURREducens")[1]["total"], 125)
        self.assertEqual(self.request(query="NoSuchGenus")[1]["total"], 0)

    def test_invalid_inputs_and_dataset_pin(self):
        for bad in [{"page": 0}, {"page_size": 101}, {"page": 1.5}, {"filters": {}},
                    {"filters": [{"column": "genome_size", "op": [], "value": "3"}]},
                    {"filters": [{"column": "missing_column", "op": "eq", "value": "a"}]},
                    {"filters": [{"column": "genome_size", "op": "gte", "value": "NaN"}]}]:
            self.assertEqual(self.request(**bad)[0], 400)
        self.assertEqual(self.request(dataset_id="other-version")[0], 409)

    def test_schema_and_original_file_fallback(self):
        status, schema = self.request(operation="schema")
        self.assertEqual(status, 200)
        self.assertEqual(schema["columns"], self.columns)
        self.assertIn("Complete Genome", schema["facets"]["assembly_level"])
        self.api.MANIFEST_KEY = ""
        status, result = self.request(page_size=100)
        self.assertEqual(status, 200)
        self.assertEqual(result["total"], 126)
        self.assertFalse(result["indexed"])

    def test_large_full_download_does_not_limit_pages(self):
        self.api.MAX_RESULT_BYTES = 1500
        self.assertEqual(self.request(page_size=5)[0], 200)
        response = self.api.lambda_handler({"body": json.dumps({"query": "Geobacter", "format": "tsv"})}, None)
        self.assertEqual(response["statusCode"], 413)
        # A filtered small download still preserves comments and all columns.
        response = self.api.lambda_handler({"queryStringParameters": {"query": "Geobacter metallireducens"}, "requestContext": {"http": {"method": "GET"}}}, None)
        self.assertEqual(response["statusCode"], 200)
        self.assertIn("## Generated test fixture", response["body"])
        self.assertIn("GCA_last", response["body"])

    def test_gzip_input_and_safe_failure(self):
        compressed = self.root / "source.tsv.gz"
        with gzip.open(compressed, "wt", encoding="utf-8") as handle:
            handle.write(self.text)
        with redirect_stderr(io.StringIO()):
            second = prepare.prepare(str(compressed), self.root / "gzip-output", 9)
        self.assertEqual(second["total_rows"], len(self.data))
        with self.assertRaises(ValueError):
            prepare.prepare(str(self.source), self.out)
        broken = self.root / "broken.tsv"
        broken.write_text(self.text + "bad\trow\n")
        with self.assertRaises(ValueError):
            prepare.prepare(str(broken), self.root / "bad-output")
        self.assertFalse((self.root / "bad-output").exists())

    def test_byte_limit_and_stdin_cli(self):
        with redirect_stderr(io.StringIO()):
            prepare.prepare(str(self.source), self.root / "byte-limited", rows_per_chunk=999, bytes_per_chunk=400)
        for path in (self.root / "byte-limited").rglob("*.tsv"):
            self.assertLessEqual(path.stat().st_size, 400)
        proc = subprocess.run([sys.executable, str(ROOT / "scripts/prepare_assembly_chunks.py"),
                               "--input", "-", "--outdir", str(self.root / "stdin-output")],
                              input=self.text, text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads((self.root / "stdin-output/manifest.json").read_text())["total_rows"], len(self.data))

    def combined_sections(self):
        first, second = self.data[0], self.data[2]
        text = (self.text + "## Additional source table\n#" + "\t".join(self.columns) + "\n" +
                "\t".join(first) + "\n# Comment within data\n" +
                "\ufeff" + "\t".join(reversed(self.columns)) + "\n" +
                "\t".join(reversed(second)) + "\n")
        return text, [first, second]

    def test_repeated_headers_comments_and_reordered_sections(self):
        text, appended = self.combined_sections()
        source = self.root / "combined.tsv"
        source.write_text(text, encoding="utf-8")
        output = self.root / "combined-index"
        with redirect_stderr(io.StringIO()):
            manifest = prepare.prepare(str(source), output, rows_per_chunk=11, max_open_files=1)
        recovered = []
        for path in output.rglob("*.tsv"):
            recovered.extend(line.split("\t") for line in path.read_text().splitlines() if not line.startswith("#"))
        self.assertCountEqual(recovered, self.data + appended)
        self.assertEqual(manifest["total_rows"], len(self.data) + 2)
        self.assertEqual(manifest["source_header_count"], 3)
        self.assertEqual(len(manifest["section_comments"]), 2)
        self.assertEqual(source.read_text(), text)
        self.fake.objects.update({"combined/" + str(p.relative_to(output)): p.read_bytes() for p in output.rglob("*") if p.is_file()})
        self.api.MANIFEST_KEY = "combined/manifest.json"
        self.api._manifest.cache_clear()
        status, response = self.request(page_size=100)
        self.assertEqual(status, 200)
        self.assertEqual(response["total"], 128)
        status, last = self.request(page=2, page_size=100)
        self.assertEqual(last["rows"][-2:], appended)

    def test_fallback_also_handles_concatenated_tables(self):
        text, appended = self.combined_sections()
        self.fake.objects["source.tsv"] = text.encode("utf-8")
        self.api.MANIFEST_KEY = ""
        status, response = self.request(page=2, page_size=100)
        self.assertEqual(status, 200)
        self.assertEqual(response["total"], 128)
        self.assertEqual(response["rows"][-2:], appended)

    def test_incompatible_section_headers_fail_without_partial_output(self):
        for suffix, columns in [("changed", self.columns[:-1] + ["different_column"]),
                                ("duplicate", self.columns + [self.columns[0]])]:
            source = self.root / (suffix + ".tsv")
            source.write_text(self.text + "#" + "\t".join(columns) + "\n")
            output = self.root / (suffix + "-index")
            with self.assertRaisesRegex(ValueError, "incompatible columns|duplicate column"):
                prepare.prepare(str(source), output)
            self.assertFalse(output.exists())

    def test_column_label_as_a_data_value_is_preserved(self):
        row = ["Geobacter sp.", "Contig", "GCA_marker", "1000", "organism_name"]
        text = self.text + "\t".join(row) + "\n"
        source = self.root / "label-value.tsv"
        source.write_text(text)
        with redirect_stderr(io.StringIO()):
            manifest = prepare.prepare(str(source), self.root / "label-value-index")
        self.assertEqual(manifest["total_rows"], len(self.data) + 1)
        self.fake.objects["source.tsv"] = text.encode()
        self.api.MANIFEST_KEY = ""
        status, response = self.request(filters=[{"column": "isolate", "op": "eq", "value": "organism_name"}])
        self.assertEqual(status, 200)
        self.assertEqual(response["rows"], [row])


if __name__ == "__main__":
    unittest.main()

