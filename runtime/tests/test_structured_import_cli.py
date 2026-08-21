from __future__ import annotations

import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch


RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))

import goutoujunshi_cli  # noqa: E402


class StructuredImportCliTests(unittest.TestCase):
    def test_immutable_archive_is_idempotent_private_and_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            archive_root = root / "archive"
            source.write_bytes(b"locked source\n")

            target, digest, first = goutoujunshi_cli._archive_immutable_file(
                source, archive_root
            )
            repeated, repeated_digest, second = goutoujunshi_cli._archive_immutable_file(
                source, archive_root
            )

            self.assertEqual(first, "created")
            self.assertEqual(second, "already_exists")
            self.assertEqual((target, digest), (repeated, repeated_digest))
            self.assertEqual(os.stat(archive_root).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(target).st_mode & 0o777, 0o400)
            self.assertEqual(os.stat(target.with_name(target.name + ".sha256")).st_mode & 0o777, 0o400)

            os.chmod(target, 0o600)
            target.write_bytes(b"tampered archive\n")
            with self.assertRaises(FileExistsError):
                goutoujunshi_cli._archive_immutable_file(source, archive_root)

    def test_atomic_manifest_output_refuses_different_existing_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            self.assertEqual(
                goutoujunshi_cli._write_bytes_no_replace(path, b"{}\n", mode=0o600),
                "created",
            )
            self.assertEqual(
                goutoujunshi_cli._write_bytes_no_replace(path, b"{}\n", mode=0o600),
                "already_exists",
            )
            with self.assertRaises(FileExistsError):
                goutoujunshi_cli._write_bytes_no_replace(path, b'{"changed":true}\n', mode=0o600)

    def test_structured_import_preflights_person_before_creating_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            manifest = root / "manifest.json"
            archive_root = root / "archive"
            source.write_text("source\n", encoding="utf-8")
            manifest.write_text("{}\n", encoding="utf-8")

            with patch.object(
                goutoujunshi_cli,
                "preflight_structured_file",
                side_effect=LookupError("person is ambiguous"),
            ), self.assertRaisesRegex(LookupError, "ambiguous"):
                goutoujunshi_cli.command_import_structured(
                    Namespace(
                        source=str(source),
                        manifest=str(manifest),
                        owner="owner",
                        archive_root=str(archive_root),
                    )
                )

            self.assertFalse(archive_root.exists())


if __name__ == "__main__":
    unittest.main()
