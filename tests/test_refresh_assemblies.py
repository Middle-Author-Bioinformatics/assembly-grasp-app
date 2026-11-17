"""Refresh integration tests with local source fixtures and mocked AWS CLI."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest.mock import patch

from test_chunked_search import load, ROOT

refresh = load("refresh_pipeline", ROOT / "scripts/refresh_assemblies.py")


class FakeAWS:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail
        self.config = {"RevisionId": "revision-1", "EphemeralStorage": {"Size": 2048},
                       "Environment": {"Variables": {"ASSEMBLY_BUCKET": "assembly-grasp-bucket",
                       "ASSEMBLY_MANIFEST_KEY": "old/manifest.json", "ASSEMBLY_KEYWORD_INDEX_KEY": "old/keywords.sqlite",
                       "UNRELATED_SECRET": "retain-this-value", "MAX_RESULT_BYTES": "5000000"}}}

    def run(self, arguments, document=None):
        self.calls.append((arguments, deepcopy(document)))
        if self.fail and self.fail(arguments):
            raise RuntimeError("Simulated AWS failure")
        if arguments[:2] == ["lambda", "get-function-configuration"]:
            return json.dumps(self.config)
        if arguments[:2] == ["lambda", "update-function-configuration"]:
            if document["RevisionId"] != self.config["RevisionId"]:
                raise RuntimeError("Revision conflict")
            self.config["Environment"] = deepcopy(document["Environment"])
        return ""


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        columns = ["assembly_accession", "organism_name", "bioproject", "group", "genome_size"]
        genbank = "#" + "\t".join(columns) + "\nGCA_1.1\tTest one\tPRJNA1\tbacteria\t100\nGCA_2.1\tTest virus\tPRJNA2\tviral\t200\n"
        refseq = "#" + "\t".join(reversed(columns)) + "\n100\tbacteria\tPRJNA1\tTest one\tGCF_1.1\n"
        xml = "<PackageSet><Package><Project><Project><ProjectID><ArchiveID accession='PRJNA1'/></ProjectID><ProjectDescr><Title>Fresh project</Title><Description>New metadata</Description></ProjectDescr></Project></Project></Package></PackageSet>"
        self.sources = {refresh.ASSEMBLY_URLS["genbank"]: genbank.encode(),
                        refresh.ASSEMBLY_URLS["refseq"]: refseq.encode(), refresh.BIOPROJECT_URL: xml.encode()}
        self.downloads = []

        def download(url, destination):
            self.downloads.append(url)
            content = self.sources[url]
            Path(destination).write_bytes(content)
            return {"url": url, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content), "file": Path(destination).name}

        self.patch = patch.object(refresh, "download", side_effect=download)
        self.patch.start()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.run, self.report = refresh.prepare_run(self.root, "assembly-grasp-bucket", "assembly-index")

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_complete_fresh_pipeline_preserves_pairs_and_builds_matching_indexes(self):
        self.assertEqual(set(self.downloads), set(self.sources))
        self.assertEqual(self.report["status"], "prepared")
        self.assertEqual(self.report["rows"], 3)
        self.assertIn(self.run.name, self.report["manifest_key"])
        manifest = json.loads((self.run / "dataset/manifest.json").read_text())
        self.assertIn("group", manifest["columns"])
        self.assertIn("bioproject_description", manifest["columns"])
        catalog = json.loads((self.run / "keyword-v1/keyword_index.json").read_text())
        self.assertEqual(catalog["dataset_id"], manifest["dataset_id"])
        self.assertIn("bioproject", catalog["search_modes"])
        records = []
        for group in manifest["groups"].values():
            for chunk in group["chunks"]:
                records.extend(line.split("\t") for line in (self.run / "dataset" / chunk["key"]).read_text().splitlines() if line and not line.startswith("#"))
        self.assertCountEqual([r[0] for r in records], ["GCA_1.1", "GCA_2.1", "GCF_1.1"])
        self.assertEqual(next(r for r in records if r[0] == "GCF_1.1")[-2:], ["Fresh project", "New metadata"])
        refresh.validate_prepared(self.run, self.report)

    def test_repeated_refreshes_have_distinct_local_and_s3_versions(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            second, report = refresh.prepare_run(self.root, "assembly-grasp-bucket", "assembly-index")
        self.assertNotEqual(second, self.run)
        self.assertNotEqual(report["prefix"], self.report["prefix"])
        self.assertNotEqual(report["dataset_id"], self.report["dataset_id"])
        self.assertTrue((self.run / "dataset/manifest.json").exists())

    def test_upload_orders_chunks_catalog_then_manifest_and_is_idempotent(self):
        aws = FakeAWS()
        with redirect_stdout(io.StringIO()):
            refresh.publish(self.run, self.report, aws)
        self.assertEqual(self.report["status"], "uploaded")
        self.assertEqual([args[:2] for args, _ in aws.calls], [["s3", "sync"], ["s3", "sync"], ["s3", "cp"]])
        self.assertIn("--exclude", aws.calls[0][0])
        self.assertTrue(aws.calls[-1][0][3].endswith("/manifest.json"))
        self.assertFalse(any("--delete" in args for args, _ in aws.calls))
        with redirect_stdout(io.StringIO()):
            refresh.publish(self.run, self.report, aws)
        self.assertEqual(len(aws.calls), 3)

    def test_each_upload_failure_blocks_activation_and_can_retry_without_rebuilding(self):
        for fail_at in (1, 2, 3):
            report = deepcopy(self.report)
            aws = FakeAWS()
            def failing(arguments, document=None):
                aws.calls.append((arguments, document))
                if len(aws.calls) == fail_at:
                    raise RuntimeError("Upload failed")
                return ""
            aws.run = failing
            with redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "Upload failed"):
                refresh.publish(self.run, report, aws)
            self.assertEqual(report["status"], "prepared")
            with self.assertRaisesRegex(ValueError, "Upload must finish"):
                refresh.activate(self.run, report, aws, "assembly-grasp")
            self.assertTrue(all(args[0] == "s3" for args, _ in aws.calls))
        with redirect_stdout(io.StringIO()):
            refresh.publish(self.run, self.report, FakeAWS())
        self.assertEqual(self.report["status"], "uploaded")

    def test_tampering_is_rejected_before_upload(self):
        (self.run / "keyword-v1/organism_names.sqlite").write_bytes(b"corrupt")
        aws = FakeAWS()
        with self.assertRaisesRegex(ValueError, "changed or is incomplete"):
            refresh.publish(self.run, self.report, aws)
        self.assertEqual(aws.calls, [])

    def test_activation_changes_both_keys_and_preserves_other_variables_with_revision_guard(self):
        aws = FakeAWS()
        with redirect_stdout(io.StringIO()):
            refresh.publish(self.run, self.report, aws)
            refresh.activate(self.run, self.report, aws, "assembly-grasp")
        env = aws.config["Environment"]["Variables"]
        self.assertEqual(env["ASSEMBLY_MANIFEST_KEY"], self.report["manifest_key"])
        self.assertEqual(env["ASSEMBLY_KEYWORD_INDEX_KEY"], self.report["keyword_key"])
        self.assertEqual(env["UNRELATED_SECRET"], "retain-this-value")
        self.assertEqual(env["MAX_RESULT_BYTES"], "5000000")
        update = next(doc for args, doc in aws.calls if args[:2] == ["lambda", "update-function-configuration"])
        self.assertEqual(update["RevisionId"], "revision-1")
        previous = json.loads((self.run / "previous-dataset.json").read_text())
        self.assertEqual(previous["variables"]["ASSEMBLY_MANIFEST_KEY"], "old/manifest.json")
        self.assertNotIn("UNRELATED_SECRET", previous["variables"])
        self.assertEqual(self.report["status"], "active")

    def test_activation_requires_enough_temp_space_and_no_update_in_progress(self):
        self.report["status"] = "uploaded"
        for changes, expected in [({"EphemeralStorage": {"Size": 1}}, "ephemeral storage"),
                                  ({"LastUpdateStatus": "InProgress"}, "configuration is updating")]:
            aws = FakeAWS()
            aws.config.update(changes)
            with self.assertRaisesRegex(RuntimeError, expected):
                refresh.activate(self.run, self.report, aws, "assembly-grasp")
            self.assertFalse(any(args[:2] == ["lambda", "update-function-configuration"] for args, _ in aws.calls))

    def test_failed_activation_keeps_previous_settings_and_records_uncertain_state(self):
        self.report["status"] = "uploaded"
        aws = FakeAWS(fail=lambda args: args[:2] == ["lambda", "update-function-configuration"])
        with self.assertRaises(RuntimeError):
            refresh.activate(self.run, self.report, aws, "assembly-grasp")
        self.assertEqual(self.report["status"], "activation_requested")
        self.assertTrue((self.run / "previous-dataset.json").exists())
        self.assertEqual(aws.config["Environment"]["Variables"]["ASSEMBLY_MANIFEST_KEY"], "old/manifest.json")

    def test_bad_or_empty_downloaded_summary_never_prepares_a_publishable_dataset(self):
        for content in (b"<html>upstream error</html>", b"#assembly_accession\torganism_name\n"):
            self.sources[refresh.ASSEMBLY_URLS["genbank"]] = content
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), self.assertRaises(ValueError):
                refresh.prepare_run(self.root, "assembly-grasp-bucket", "assembly-index", source="genbank", skip_bioprojects=True)
        failed = [json.loads(p.read_text()) for p in self.root.glob("*/refresh.json")]
        self.assertEqual(sum(r["status"] == "preparation_failed" for r in failed), 2)

    def test_explicit_source_only_and_without_bioprojects(self):
        self.downloads.clear()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            run, report = refresh.prepare_run(self.root, "assembly-grasp-bucket", "assembly-index", source="refseq", skip_bioprojects=True)
        self.assertEqual(self.downloads, [refresh.ASSEMBLY_URLS["refseq"]])
        self.assertEqual(report["rows"], 1)
        manifest = json.loads((run / "dataset/manifest.json").read_text())
        self.assertNotIn("bioproject_description", manifest["columns"])

    def test_prepare_only_cli_never_calls_aws_or_changes_live_settings(self):
        arguments = ["refresh_assemblies.py", "--prepare-only", "--workdir", str(self.root / "offline")]
        with patch("sys.argv", arguments), patch.object(refresh, "AWS", side_effect=AssertionError("No AWS for prepare-only")), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            refresh.main()
        reports = list((self.root / "offline").glob("*/refresh.json"))
        self.assertEqual(len(reports), 1)
        self.assertEqual(json.loads(reports[0].read_text())["status"], "prepared")

    def test_publish_cli_uploads_and_activates_existing_run_without_downloading_again(self):
        self.downloads.clear()
        aws = FakeAWS()
        arguments = ["refresh_assemblies.py", "--publish", str(self.run), "--activate", "--function-name", "assembly-grasp"]
        with patch("sys.argv", arguments), patch.object(refresh, "AWS", return_value=aws), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            refresh.main()
        report = json.loads((self.run / "refresh.json").read_text())
        self.assertEqual(report["status"], "active")
        self.assertEqual(self.downloads, [])
        self.assertEqual([args[0] for args, _ in aws.calls[:3]], ["s3", "s3", "s3"])

    def test_refresh_lock_excludes_concurrent_runs(self):
        with refresh.refresh_lock(self.root):
            with self.assertRaisesRegex(RuntimeError, "Another refresh"):
                with refresh.refresh_lock(self.root):
                    self.fail("Concurrent lock was acquired")
        with refresh.refresh_lock(self.root):
            pass

    def test_aws_configuration_input_is_private_and_removed_without_exposing_variables(self):
        captured = []
        secret = {"Environment": {"Variables": {"TOKEN": "sensitive"}}}
        def runner(command, **kwargs):
            self.assertNotIn("sensitive", " ".join(command))
            name = command[command.index("--cli-input-json") + 1][len("file://"):]
            path = Path(name)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(json.loads(path.read_text()), secret)
            captured.append(path)
            return types.SimpleNamespace(returncode=0, stdout="{}", stderr="")
        with patch.object(refresh.shutil, "which", return_value="/usr/bin/aws"), patch.object(refresh.subprocess, "run", side_effect=runner):
            aws = refresh.AWS("us-east-2")
            aws.run(["lambda", "update-function-configuration"], secret)
        self.assertFalse(captured[0].exists())


class DownloadTests(unittest.TestCase):
    def test_atomic_download_retries_truncated_responses_and_records_hash(self):
        class Response(io.BytesIO):
            status = 200
            headers = {"Content-Length": "8", "ETag": "source-version"}
        responses = [Response(b"short"), Response(b"complete")]
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "source.tsv"
            with patch.object(refresh.urllib.request, "urlopen", side_effect=responses), patch.object(refresh.time, "sleep"), redirect_stderr(io.StringIO()):
                result = refresh.download("https://example.test/source", destination)
            self.assertEqual(destination.read_bytes(), b"complete")
            self.assertEqual(result["sha256"], hashlib.sha256(b"complete").hexdigest())
            self.assertFalse(Path(str(destination) + ".partial").exists())
            with self.assertRaises(ValueError):
                refresh.download("https://example.test/source", destination)


if __name__ == "__main__":
    unittest.main()
