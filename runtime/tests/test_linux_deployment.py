from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import yaml

PROJECT = Path(__file__).resolve().parents[2]
SUPERVISOR_PATH = PROJECT / "deployment" / "linux" / "supervisor.py"
SPEC = importlib.util.spec_from_file_location("wing_dog_linux_supervisor", SUPERVISOR_PATH)
assert SPEC and SPEC.loader
supervisor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(supervisor)


class LinuxDeploymentTests(unittest.TestCase):
    def test_bootstrap_installs_both_packages_in_both_homes_and_tightens_permissions(self) -> None:
        bootstrap = (PROJECT / "deployment" / "linux" / "bootstrap.sh").read_text(
            encoding="utf-8"
        )
        dockerfile = (PROJECT / "deployment" / "linux" / "Dockerfile").read_text(
            encoding="utf-8"
        )

        self.assertEqual(bootstrap.count(" install-plugin \\\n"), 2)
        self.assertEqual(bootstrap.count(" install-skill \\\n"), 2)
        self.assertIn('--target-home "$hermes_home"', bootstrap)
        self.assertIn('--target-home "$profile_home"', bootstrap)
        self.assertIn('chmod 600 "$hermes_home/.env" "$profile_home/.env"', bootstrap)
        self.assertIn('chmod 700 "$log_home/logs"', bootstrap)
        self.assertIn("-type f -exec chmod 600", bootstrap)
        self.assertIn("install-hermes-runtime-patch", dockerfile)
        self.assertNotIn("install-hermes-vision-patch", dockerfile)

    def test_compose_has_isolated_database_and_no_public_ports_or_docker_socket(self) -> None:
        compose_path = PROJECT / "deployment" / "linux" / "compose.yaml"
        raw = compose_path.read_text(encoding="utf-8")
        compose = yaml.safe_load(raw)

        self.assertEqual(set(compose["services"]), {"mysql", "gateway", "backup"})
        self.assertTrue(compose["networks"]["database"]["internal"])
        self.assertNotIn("ports", compose["services"]["mysql"])
        self.assertNotIn("ports", compose["services"]["gateway"])
        self.assertNotIn("/var/run/docker.sock", raw)
        self.assertIn("HERMES_BASE_IMAGE", raw)
        self.assertIn("MYSQL_IMAGE", raw)
        self.assertNotIn("user", compose["services"]["gateway"])
        self.assertEqual(compose["services"]["gateway"]["environment"]["HERMES_UID"], "${HERMES_UID:-1000}")

    def test_restore_requires_confirmation_and_preserves_local_cold_backup(self) -> None:
        restore = (PROJECT / "deployment" / "linux" / "restore.sh").read_text(
            encoding="utf-8"
        )
        deployment_doc = (PROJECT / "documentation" / "server-deployment.md").read_text(
            encoding="utf-8"
        )

        self.assertIn('"$1" != "--confirm-replace-goutoujunshi"', restore)
        self.assertIn('migration_root="$(realpath "$WING_DOG_DATA_ROOT/migration")"', restore)
        self.assertNotIn("down -v", restore)
        self.assertIn("不注销 WSL", deployment_doc)
        self.assertIn("不删除容器或卷", deployment_doc)

    def test_supervisor_restarts_only_when_routes_change(self) -> None:
        responses = [
            {"ok": True},
            {"ok": True, "changed": True, "active_routes": 2},
            {"ok": True, "done": 1, "failed": 0},
        ]
        with patch.object(supervisor, "run_json", side_effect=responses), patch.object(
            supervisor, "gateway_action"
        ) as gateway_action, patch.object(supervisor, "clear_expired_media", return_value=3):
            result = supervisor.run_cycle(Path("cli.py"), Path("config.yaml"), Path("home"))

        gateway_action.assert_called_once_with("restart")
        self.assertEqual(result["active_routes"], 2)
        self.assertEqual(result["media_removed"], 3)

    def test_media_cleanup_is_bounded_to_hermes_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            cache = home / "cache" / "images"
            state = home / "state"
            cache.mkdir(parents=True)
            state.mkdir()
            old_allowed = cache / "old.png"
            new_allowed = cache / "new.png"
            outside = home / "outside.png"
            for path in (old_allowed, new_allowed, outside):
                path.write_bytes(b"image")
            now = datetime.now(timezone.utc)
            registry = [
                {"path": str(old_allowed), "created_at": (now - timedelta(days=2)).isoformat()},
                {"path": str(new_allowed), "created_at": now.isoformat()},
                {"path": str(outside), "created_at": (now - timedelta(days=2)).isoformat()},
            ]
            (state / "goutoujunshi-media.json").write_text(
                json.dumps(registry), encoding="utf-8"
            )

            removed = supervisor.clear_expired_media(home, now=now.timestamp())

            self.assertEqual(removed, 2)
            self.assertFalse(old_allowed.exists())
            self.assertTrue(new_allowed.exists())
            self.assertTrue(outside.exists())
            retained = json.loads(
                (state / "goutoujunshi-media.json").read_text(encoding="utf-8")
            )
            self.assertEqual([item["path"] for item in retained], [str(new_allowed)])


if __name__ == "__main__":
    unittest.main()
