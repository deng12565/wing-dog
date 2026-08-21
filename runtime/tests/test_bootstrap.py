import asyncio
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from runtime import bootstrap


class BootstrapConfigTests(unittest.TestCase):
    @staticmethod
    def _runtime_patch_fixture(root: Path):
        agent_root = root / "hermes-agent"
        backup_dir = root / "backups"
        (agent_root / "hermes_cli").mkdir(parents=True)
        (agent_root / "hermes_cli" / "__init__.py").write_text(
            '__version__ = "test-version"\n', encoding="utf-8"
        )
        transforms = {}
        expected = {}
        for relative_path in bootstrap.HERMES_RUNTIME_PATCH_TRANSFORMS:
            target = agent_root / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            source = f"source:{relative_path}\n"
            suffix = f"patched:{relative_path}\n"
            target.write_text(source, encoding="utf-8", newline="\n")
            transforms[relative_path] = lambda value, suffix=suffix: value + suffix
            expected[relative_path] = {
                "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                "patched_sha256": hashlib.sha256((source + suffix).encode("utf-8")).hexdigest(),
            }
        return agent_root, backup_dir, expected, transforms

    @staticmethod
    def _runtime_probe() -> dict[str, object]:
        return {
            "plugin_discovered": True,
            "plugin_error": "",
            "tools": sorted(
                [
                    "relationship_commit_turn",
                    "relationship_search_events",
                    "relationship_web_search",
                    "user_memory_remember",
                    "user_memory_correct",
                    "user_memory_forget",
                ]
            ),
            "tool_schema_sha256": "1" * 64,
            "relationship_toolset": sorted(
                [
                    "relationship_commit_turn",
                    "relationship_search_events",
                    "relationship_web_search",
                ]
            ),
            "user_toolset": sorted(
                ["user_memory_remember", "user_memory_correct", "user_memory_forget"]
            ),
            "skill_loaded": True,
        }

    @staticmethod
    def _install_verification_packages(global_home: Path, profile_home: Path) -> None:
        project = Path(__file__).resolve().parents[2]
        for home in (global_home, profile_home):
            bootstrap.install_skill_package(project, home)
            bootstrap.install_plugin_package(project / "runtime" / "goutoujunshi", home)

    def test_global_config_enables_owner_only_unmentioned_user_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.yaml"
            env_path = root / ".env"
            config_path.write_text(
                "platform_toolsets:\n"
                "  feishu: [existing]\n"
                "  slack: [kept-toolset]\n"
                "platforms:\n"
                "  feishu: {}\n"
                "plugins:\n"
                "  enabled: [kept-plugin]\n"
                "  disabled: [goutoujunshi, kept-disabled-plugin]\n"
                "custom_global: kept\n",
                encoding="utf-8",
            )
            env_path.write_text(
                "GOUTOUJUNSHI_MODEL=gpt-5.6-terra\n"
                "GOUTOUJUNSHI_OPENAI_BASE_URL=https://example.invalid/v1\n"
                "GOUTOUJUNSHI_REASONING=high\n",
                encoding="utf-8",
            )

            bootstrap.command_configure_global(
                SimpleNamespace(config=str(config_path), source_env=str(env_path))
            )

            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            feishu = config["platforms"]["feishu"]
            self.assertFalse(feishu["require_mention"])
            self.assertFalse(feishu["extra"]["require_mention"])
            self.assertEqual(feishu["extra"]["group_policy"], "allowlist")
            self.assertEqual(feishu["extra"]["default_group_policy"], "allowlist")
            self.assertEqual(
                config["platform_toolsets"]["feishu"],
                ["goutoujunshi-user"],
            )
            self.assertEqual(config["platform_toolsets"]["slack"], ["kept-toolset"])
            self.assertEqual(config["plugins"]["enabled"], ["kept-plugin", "goutoujunshi"])
            self.assertEqual(config["plugins"]["disabled"], ["kept-disabled-plugin"])
            self.assertEqual(
                config["known_plugin_toolsets"]["feishu"],
                ["goutoujunshi", "goutoujunshi-user"],
            )
            self.assertTrue(
                set(bootstrap.GLOBAL_DISABLED_TOOLSETS).issubset(
                    config["agent"]["disabled_toolsets"]
                )
            )
            self.assertEqual(config["custom_global"], "kept")
            self.assertEqual(config["compression"]["threshold_tokens"], 64000)
            self.assertEqual(config["compression"]["proactive_prune_tokens"], 48000)
            self.assertFalse(config["compression"]["micro_compact"])

    def test_relationship_profile_has_both_isolated_toolsets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_path = root / ".env"
            env_path.write_text(
                "GOUTOUJUNSHI_MODEL=gpt-5.6-terra\n"
                "GOUTOUJUNSHI_OPENAI_BASE_URL=https://example.invalid/v1\n"
                "GOUTOUJUNSHI_REASONING=high\n"
                "GOUTOUJUNSHI_OLLAMA_URL=http://127.0.0.1:11434\n",
                encoding="utf-8",
            )
            profile_home = root / "profile"
            profile_home.mkdir()
            (profile_home / "config.yaml").write_text(
                "platform_toolsets:\n"
                "  feishu: [legacy]\n"
                "  slack: [kept-toolset]\n"
                "platforms:\n"
                "  feishu:\n"
                "    custom_adapter: kept\n"
                "  slack:\n"
                "    enabled: true\n"
                "plugins:\n"
                "  enabled: [legacy]\n"
                "  disabled: [kept-plugin, goutoujunshi]\n"
                "memory:\n"
                "  custom_memory: kept\n"
                "tools:\n"
                "  custom_tool_setting: kept\n"
                "web:\n"
                "  extract_backend: kept\n"
                "custom_profile: kept\n",
                encoding="utf-8",
            )
            (profile_home / ".env").write_text(
                "GOUTOUJUNSHI_MILVUS_MANAGED=true\n"
                "GOUTOUJUNSHI_TOKEN_SECRET=legacy-value-kept\n"
                "CUSTOM_PROFILE_VALUE=kept\n"
                "WEB_TOOLS_DEBUG=true\n",
                encoding="utf-8",
            )

            bootstrap.command_configure_profile(
                SimpleNamespace(profile_home=str(profile_home), global_env=str(env_path))
            )

            config = yaml.safe_load((profile_home / "config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(
                config["platform_toolsets"]["feishu"],
                ["goutoujunshi", "goutoujunshi-user"],
            )
            self.assertFalse(config["memory"]["enabled"])
            self.assertFalse(config["tools"]["tool_search"])
            self.assertEqual(
                config["agent"]["disabled_toolsets"], bootstrap.PROFILE_DISABLED_TOOLSETS
            )
            self.assertEqual(config["web"]["search_backend"], "ddgs")
            self.assertFalse(config["onboarding"]["first_message_intro"])
            self.assertEqual(config["plugins"]["enabled"], ["goutoujunshi"])
            self.assertEqual(
                config["known_plugin_toolsets"]["feishu"],
                ["goutoujunshi", "goutoujunshi-user"],
            )
            self.assertEqual(config["platform_toolsets"]["slack"], ["kept-toolset"])
            self.assertEqual(config["platforms"]["feishu"]["custom_adapter"], "kept")
            self.assertTrue(config["platforms"]["slack"]["enabled"])
            self.assertEqual(config["plugins"]["disabled"], ["kept-plugin"])
            self.assertEqual(config["memory"]["custom_memory"], "kept")
            self.assertEqual(config["tools"]["custom_tool_setting"], "kept")
            self.assertEqual(config["web"]["extract_backend"], "kept")
            self.assertEqual(config["custom_profile"], "kept")
            self.assertEqual(config["compression"]["min_tail_user_messages"], 3)
            self.assertTrue(config["compression"]["abort_on_summary_failure"])
            profile_values = bootstrap.load_dotenv(profile_home / ".env")
            self.assertNotIn("GOUTOUJUNSHI_OLLAMA_URL", profile_values)
            self.assertNotIn("GOUTOUJUNSHI_MILVUS_MANAGED", profile_values)
            self.assertEqual(profile_values["GOUTOUJUNSHI_TOKEN_SECRET"], "legacy-value-kept")
            self.assertEqual(profile_values["CUSTOM_PROFILE_VALUE"], "kept")
            self.assertEqual(profile_values["WEB_TOOLS_DEBUG"], "false")
            self.assertEqual(os.stat(profile_home / ".env").st_mode & 0o777, 0o600)

    def test_verify_reports_the_restricted_ddgs_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.yaml"
            env_path = root / ".env"
            profile_home = root / "profile"
            profile_home.mkdir()
            config_path.write_text("platforms:\n  feishu: {}\n", encoding="utf-8")
            env_path.write_text(
                "GOUTOUJUNSHI_MODEL=gpt-5.6-terra\n"
                "GOUTOUJUNSHI_OPENAI_BASE_URL=https://example.invalid/v1\n"
                "GOUTOUJUNSHI_REASONING=high\n",
                encoding="utf-8",
            )
            bootstrap.command_configure_global(
                SimpleNamespace(config=str(config_path), source_env=str(env_path))
            )
            bootstrap.command_configure_profile(
                SimpleNamespace(profile_home=str(profile_home), global_env=str(env_path))
            )
            self._install_verification_packages(root, profile_home)

            resolved = [
                ["goutoujunshi-user"],
                ["goutoujunshi", "goutoujunshi-user"],
            ]
            with patch.object(
                bootstrap, "_resolved_hermes_toolsets", side_effect=resolved
            ), patch.object(bootstrap, "_ddgs_importable", return_value=True), patch.object(
                bootstrap, "_profile_runtime_probe", return_value=self._runtime_probe()
            ), patch.object(
                bootstrap, "emit"
            ) as emit:
                bootstrap.command_verify(
                    SimpleNamespace(
                        config=str(config_path),
                        profile_config=str(profile_home / "config.yaml"),
                        profile_env=str(profile_home / ".env"),
                        env=str(env_path),
                    )
                )

            result = emit.call_args.args[0]
            self.assertTrue(result["ok"])
            self.assertEqual(result["global"]["feishu_toolsets"], ["goutoujunshi-user"])
            self.assertEqual(
                result["global"]["resolved_feishu_toolsets"], ["goutoujunshi-user"]
            )
            self.assertEqual(
                result["profile"]["feishu_toolsets"],
                ["goutoujunshi", "goutoujunshi-user"],
            )
            self.assertEqual(
                result["profile"]["disabled_toolsets"], bootstrap.PROFILE_DISABLED_TOOLSETS
            )
            self.assertEqual(result["profile"]["search_backend"], "ddgs")
            self.assertTrue(result["profile"]["ddgs_importable"])
            self.assertEqual(result["profile"]["web_tools_debug"], "false")
            self.assertTrue(result["global"]["runtime_probe"]["plugin_discovered"])
            self.assertTrue(result["global"]["runtime_probe"]["skill_loaded"])
            self.assertEqual(result["profile"]["plugins_enabled"], ["goutoujunshi"])

    def test_verify_rejects_native_web_exposure_and_bad_profile_search_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.yaml"
            env_path = root / ".env"
            profile_home = root / "profile"
            profile_home.mkdir()
            profile_config_path = profile_home / "config.yaml"
            profile_env_path = profile_home / ".env"
            config_path.write_text("platforms:\n  feishu: {}\n", encoding="utf-8")
            env_path.write_text(
                "GOUTOUJUNSHI_MODEL=gpt-5.6-terra\n"
                "GOUTOUJUNSHI_OPENAI_BASE_URL=https://example.invalid/v1\n"
                "GOUTOUJUNSHI_REASONING=high\n",
                encoding="utf-8",
            )
            bootstrap.command_configure_global(
                SimpleNamespace(config=str(config_path), source_env=str(env_path))
            )
            bootstrap.command_configure_profile(
                SimpleNamespace(profile_home=str(profile_home), global_env=str(env_path))
            )
            self._install_verification_packages(root, profile_home)
            base_global = config_path.read_text(encoding="utf-8")
            base_profile = profile_config_path.read_text(encoding="utf-8")
            base_profile_env = profile_env_path.read_text(encoding="utf-8")

            for case in (
                "global-native-toolset",
                "wrong-backend",
                "debug-enabled",
                "profile-native-toolset",
                "web-not-disabled",
                "resolved-toolset-leak",
                "ddgs-not-importable",
                "plugin-disabled",
                "profile-package-missing",
                "plugin-discovery-missing",
            ):
                with self.subTest(case=case):
                    config_path.write_text(base_global, encoding="utf-8")
                    profile_config_path.write_text(base_profile, encoding="utf-8")
                    profile_env_path.write_text(base_profile_env, encoding="utf-8")
                    global_config = yaml.safe_load(base_global)
                    profile_config = yaml.safe_load(base_profile)
                    if case == "global-native-toolset":
                        global_config["platform_toolsets"]["feishu"].append("hermes-feishu")
                        config_path.write_text(
                            yaml.safe_dump(global_config, sort_keys=False), encoding="utf-8"
                        )
                    elif case == "wrong-backend":
                        profile_config["web"]["search_backend"] = "tavily"
                        profile_config_path.write_text(
                            yaml.safe_dump(profile_config, sort_keys=False), encoding="utf-8"
                        )
                    elif case == "debug-enabled":
                        bootstrap.update_dotenv(
                            profile_env_path, {"WEB_TOOLS_DEBUG": "true"}
                        )
                    elif case == "profile-native-toolset":
                        profile_config["platform_toolsets"]["feishu"].append("search")
                        profile_config_path.write_text(
                            yaml.safe_dump(profile_config, sort_keys=False), encoding="utf-8"
                        )
                    elif case == "web-not-disabled":
                        profile_config["agent"]["disabled_toolsets"].remove("web")
                        profile_config_path.write_text(
                            yaml.safe_dump(profile_config, sort_keys=False), encoding="utf-8"
                        )
                    elif case == "plugin-disabled":
                        profile_config["plugins"]["disabled"].append("goutoujunshi")
                        profile_config_path.write_text(
                            yaml.safe_dump(profile_config, sort_keys=False), encoding="utf-8"
                        )

                    resolved = [
                        ["feishu_doc", "goutoujunshi-user"]
                        if case == "resolved-toolset-leak"
                        else ["goutoujunshi-user"],
                        ["goutoujunshi", "goutoujunshi-user"],
                    ]
                    packages = {
                        scope: {
                            "skill_matches": True,
                            "plugin_matches": True,
                            "skill_files": 1,
                            "plugin_files": 1,
                        }
                        for scope in ("global", "profile")
                    }
                    if case == "profile-package-missing":
                        packages["profile"]["plugin_matches"] = False
                    global_probe = self._runtime_probe()
                    profile_probe = self._runtime_probe()
                    if case == "plugin-discovery-missing":
                        profile_probe["plugin_discovered"] = False
                    with patch.object(
                        bootstrap, "_resolved_hermes_toolsets", side_effect=resolved
                    ), patch.object(
                        bootstrap,
                        "_ddgs_importable",
                        return_value=case != "ddgs-not-importable",
                    ), patch.object(
                        bootstrap,
                        "_installed_runtime_packages",
                        return_value=packages,
                    ), patch.object(
                        bootstrap,
                        "_profile_runtime_probe",
                        side_effect=[global_probe, profile_probe],
                    ), patch.object(bootstrap, "emit"):
                        with self.assertRaises(SystemExit) as error:
                            bootstrap.command_verify(
                                SimpleNamespace(
                                    config=str(config_path),
                                    profile_config=str(profile_config_path),
                                    profile_env=str(profile_env_path),
                                    env=str(env_path),
                                )
                            )
                    self.assertEqual(error.exception.code, 2)

    def test_setup_installs_ddgs_through_hermes_before_plugin_deployment(self) -> None:
        script_path = Path(__file__).parents[2] / "scripts" / "Setup-And-Start-Goutoujunshi.ps1"
        script = script_path.read_text(encoding="utf-8-sig")

        install_command = "$Python -m hermes_cli.main tools post-setup ddgs"
        deploy_command = "$Python $Bootstrap install-plugin"
        self.assertIn(install_command, script)
        self.assertLess(script.index(install_command), script.index(deploy_command))
        self.assertIn("Test-Path -LiteralPath $HermesInstallTemp -PathType Container", script)
        self.assertIn("$env:TEMP = $HermesInstallTemp", script)
        self.assertIn("$env:TMP = $HermesInstallTemp", script)
        self.assertIn("$Python -c 'import ddgs'", script)
        self.assertEqual(script.count("$Python $Bootstrap install-plugin"), 2)
        self.assertEqual(script.count("$Python $Bootstrap install-skill"), 2)
        self.assertIn("install-plugin --plugin-source (Join-Path $RuntimeRoot 'goutoujunshi') --target-home $ProfileHome", script)
        self.assertIn("install-hermes-runtime-patch", script)
        self.assertNotIn("install-hermes-vision-patch", script)
        self.assertIn("--profile-env (Join-Path $ProfileHome '.env')", script)
        self.assertNotRegex(script.lower(), r"pip\s+install[^\r\n]*ddgs")

    def test_skill_package_exactly_mirrors_runtime_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            target_home = root / "hermes"
            (project / "references" / "practical").mkdir(parents=True)
            (project / "agents").mkdir()
            (project / "scripts").mkdir()
            (project / "SKILL.md").write_text("# skill\n", encoding="utf-8")
            (project / "references" / "practical" / "reply.md").write_text(
                "reply guidance\n", encoding="utf-8"
            )
            (project / "agents" / "openai.yaml").write_text("name: agent\n", encoding="utf-8")
            (project / "scripts" / "validate.py").write_text("print('ok')\n", encoding="utf-8")
            stale = target_home / "skills" / "goutoujunshi" / "stale.txt"
            stale.parent.mkdir(parents=True)
            stale.write_text("remove me", encoding="utf-8")

            result = bootstrap.install_skill_package(project, target_home)

            installed = target_home / "skills" / "goutoujunshi"
            self.assertTrue(result["ok"])
            self.assertEqual(result["files"], 4)
            self.assertFalse((installed / "stale.txt").exists())
            self.assertEqual(
                bootstrap._runtime_manifest(installed),
                bootstrap._runtime_manifest(project),
            )

    def test_plugin_package_is_replaced_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            target_home = root / "hermes"
            source.mkdir()
            (source / "plugin.yaml").write_text("name: goutoujunshi\n", encoding="utf-8")
            (source / "__init__.py").write_text("VERSION = 2\n", encoding="utf-8")
            target = target_home / "plugins" / "goutoujunshi"
            target.mkdir(parents=True)
            (target / "plugin.yaml").write_text("name: goutoujunshi\n", encoding="utf-8")
            (target / "__init__.py").write_text("VERSION = 1\n", encoding="utf-8")
            (target / "stale.txt").write_text("stale\n", encoding="utf-8")

            result = bootstrap.install_plugin_package(source, target_home)

            self.assertTrue(result["ok"])
            self.assertEqual((target / "__init__.py").read_text(encoding="utf-8"), "VERSION = 2\n")
            self.assertFalse((target / "stale.txt").exists())

    def test_plugin_manifest_uses_server_session_authorization(self) -> None:
        manifest_path = Path(__file__).parents[1] / "goutoujunshi" / "plugin.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "1.8.0")
        self.assertNotIn(
            "GOUTOUJUNSHI_TOKEN_SECRET",
            manifest.get("requires_env", []),
        )

    def test_plugin_install_restores_previous_directory_on_verification_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            target_home = root / "hermes"
            source.mkdir()
            (source / "plugin.yaml").write_text("name: goutoujunshi\n", encoding="utf-8")
            (source / "__init__.py").write_text("VERSION = 2\n", encoding="utf-8")
            target = target_home / "plugins" / "goutoujunshi"
            target.mkdir(parents=True)
            (target / "plugin.yaml").write_text("name: goutoujunshi\n", encoding="utf-8")
            (target / "__init__.py").write_text("VERSION = 1\n", encoding="utf-8")
            real_manifest = bootstrap._plugin_manifest

            def fail_installed_verification(path: Path):
                if path.resolve() == target.resolve() and (path / "__init__.py").read_text(encoding="utf-8") == "VERSION = 2\n":
                    raise RuntimeError("verification failed")
                return real_manifest(path)

            with patch.object(bootstrap, "_plugin_manifest", side_effect=fail_installed_verification):
                with self.assertRaises(RuntimeError):
                    bootstrap.install_plugin_package(source, target_home)

            self.assertEqual((target / "__init__.py").read_text(encoding="utf-8"), "VERSION = 1\n")

    def test_hermes_runtime_patch_is_guarded_atomic_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent_root, backup_dir, expected, transforms = self._runtime_patch_fixture(
                Path(directory)
            )
            with patch.dict(bootstrap.HERMES_RUNTIME_PATCH_TRANSFORMS, transforms, clear=True):
                result = bootstrap.install_hermes_runtime_patch(
                    agent_root,
                    backup_dir,
                    expected_version="test-version",
                    expected_files=expected,
                )
                repeated = bootstrap.install_hermes_runtime_patch(
                    agent_root,
                    backup_dir,
                    expected_version="test-version",
                    expected_files=expected,
                )

            self.assertEqual(result["status"], "patched")
            self.assertEqual(repeated["status"], "already_patched")
            self.assertEqual(len(list(backup_dir.glob("*.bak"))), 4)
            for relative_path, hashes in expected.items():
                self.assertEqual(
                    bootstrap._sha256_file(agent_root / relative_path),
                    hashes["patched_sha256"],
                )

    def test_hermes_runtime_patch_refuses_unknown_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent_root, backup_dir, expected, transforms = self._runtime_patch_fixture(
                Path(directory)
            )
            target = agent_root / "gateway" / "run.py"
            original = target.read_bytes()
            expected["gateway/run.py"]["source_sha256"] = "0" * 64

            with patch.dict(bootstrap.HERMES_RUNTIME_PATCH_TRANSFORMS, transforms, clear=True):
                with self.assertRaises(RuntimeError):
                    bootstrap.install_hermes_runtime_patch(
                        agent_root,
                        backup_dir,
                        expected_version="test-version",
                        expected_files=expected,
                    )

            self.assertEqual(target.read_bytes(), original)

    def test_hermes_runtime_patch_restores_all_sources_when_final_check_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent_root, backup_dir, expected, transforms = self._runtime_patch_fixture(
                Path(directory)
            )
            target = agent_root / "gateway" / "run.py"
            originals = {
                relative_path: (agent_root / relative_path).read_bytes()
                for relative_path in expected
            }
            real_sha = bootstrap._sha256_file

            def fail_installed_verification(path: Path) -> str:
                digest = real_sha(path)
                if (
                    path.resolve() == target.resolve()
                    and digest == expected["gateway/run.py"]["patched_sha256"]
                ):
                    return "f" * 64
                return digest

            with patch.dict(
                bootstrap.HERMES_RUNTIME_PATCH_TRANSFORMS, transforms, clear=True
            ), patch.object(
                bootstrap, "_sha256_file", side_effect=fail_installed_verification
            ):
                with self.assertRaises(RuntimeError):
                    bootstrap.install_hermes_runtime_patch(
                        agent_root,
                        backup_dir,
                        expected_version="test-version",
                        expected_files=expected,
                    )

            for relative_path, original in originals.items():
                self.assertEqual((agent_root / relative_path).read_bytes(), original)

    def test_profile_session_rotation_preserves_transcripts_and_removes_only_routes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent_root = root / "agent"
            global_home = root / "home"
            profile_home = global_home / "profiles" / "goutoujunshi"
            (agent_root / "gateway").mkdir(parents=True)
            (agent_root / "hermes_cli").mkdir()
            profile_home.mkdir(parents=True)
            (agent_root / "hermes_cli" / "__init__.py").write_text(
                f'__version__ = "{bootstrap.HERMES_RUNTIME_PATCH_VERSION}"\n',
                encoding="utf-8",
            )
            session_source = b"locked session source\n"
            (agent_root / "gateway" / "session.py").write_bytes(session_source)
            for home in (global_home, profile_home):
                (home / "state.db").touch()
                (home / "sessions").mkdir(exist_ok=True)

            scope = str((global_home / "sessions").resolve())
            relation_global = "agent:goutoujunshi:feishu:group:chat-1:user-1"
            relation_profile = "agent:goutoujunshi:feishu:group:chat-2:user-1"
            unrelated = "agent:main:feishu:group:chat-other:user-1"

            class FakeEntry:
                @classmethod
                def from_dict(cls, data):
                    return SimpleNamespace(session_id=data["session_id"])

            class FakeDB:
                stores = {
                    global_home / "state.db": {
                        "routing": {
                            scope: {
                                relation_global: json.dumps({"session_id": "old-global"}),
                                unrelated: json.dumps({"session_id": "keep-global"}),
                            }
                        },
                        "sessions": {
                            "old-global": {"end_reason": None},
                            "keep-global": {"end_reason": None},
                        },
                    },
                    profile_home / "state.db": {
                        "routing": {
                            scope: {
                                relation_profile: json.dumps({"session_id": "old-profile"})
                            }
                        },
                        "sessions": {"old-profile": {"end_reason": "agent_close"}},
                    },
                }

                def __init__(self, db_path):
                    self.data = self.stores[Path(db_path)]

                def load_gateway_routing_entries(self, *, scope=""):
                    return dict(self.data["routing"].get(scope, {}))

                def delete_gateway_routing_entries(self, keys, *, scope=""):
                    for key in keys:
                        self.data["routing"].setdefault(scope, {}).pop(key, None)

                def get_session(self, session_id):
                    row = self.data["sessions"].get(session_id)
                    return dict(row) if row is not None else None

                def promote_to_session_reset(self, session_id, reason):
                    row = self.data["sessions"].get(session_id)
                    if row is None:
                        return False
                    if row["end_reason"] in (None, "agent_close", "ws_orphan_reap"):
                        row["end_reason"] = reason
                        return True
                    return False

                def close(self):
                    return None

            (global_home / "sessions" / "sessions.json").write_text(
                json.dumps(
                    {
                        "_README": "keep",
                        relation_global: {"session_id": "old-global"},
                        unrelated: {"session_id": "keep-global"},
                    }
                ),
                encoding="utf-8",
            )
            digest = hashlib.sha256(session_source).hexdigest()
            result = bootstrap.rotate_profile_sessions(
                agent_root,
                global_home,
                profile_home,
                expected_session_sha256=digest,
                session_db_class=FakeDB,
                session_entry_class=FakeEntry,
            )
            repeated = bootstrap.rotate_profile_sessions(
                agent_root,
                global_home,
                profile_home,
                expected_session_sha256=digest,
                session_db_class=FakeDB,
                session_entry_class=FakeEntry,
            )

            self.assertEqual(result["ended_sessions"], 2)
            self.assertEqual(result["removed_routes"], 2)
            self.assertEqual(repeated["ended_sessions"], 0)
            self.assertEqual(
                FakeDB.stores[global_home / "state.db"]["sessions"]["old-global"]["end_reason"],
                bootstrap.PROFILE_SESSION_MIGRATION_REASON,
            )
            self.assertEqual(
                FakeDB.stores[profile_home / "state.db"]["sessions"]["old-profile"]["end_reason"],
                bootstrap.PROFILE_SESSION_MIGRATION_REASON,
            )
            self.assertIn(
                unrelated,
                FakeDB.stores[global_home / "state.db"]["routing"][scope],
            )
            mirror = json.loads(
                (global_home / "sessions" / "sessions.json").read_text(encoding="utf-8")
            )
            self.assertNotIn(relation_global, mirror)
            self.assertIn(unrelated, mirror)
            self.assertEqual(
                os.stat(global_home / "sessions" / "sessions.json").st_mode & 0o777,
                0o600,
            )

    def test_profile_session_rotation_refuses_a_running_gateway(self) -> None:
        with patch.object(
            bootstrap, "_hermes_version", return_value=bootstrap.HERMES_RUNTIME_PATCH_VERSION
        ), patch.object(
            bootstrap, "_sha256_file", return_value=bootstrap.HERMES_SESSION_SHA256
        ), patch.object(bootstrap, "_running_pid_from_file", return_value=1234):
            with self.assertRaisesRegex(RuntimeError, "still running"):
                bootstrap.rotate_profile_sessions(
                    Path("/missing-agent"),
                    Path("/missing-home"),
                    Path("/missing-home/profiles/goutoujunshi"),
                )

    def test_profile_session_rotation_keeps_mirror_route_when_promotion_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent_root = root / "agent"
            global_home = root / "home"
            profile_home = global_home / "profiles" / "goutoujunshi"
            (agent_root / "gateway").mkdir(parents=True)
            (agent_root / "hermes_cli").mkdir()
            (global_home / "sessions").mkdir(parents=True)
            profile_home.mkdir(parents=True)
            (global_home / "state.db").touch()
            (agent_root / "hermes_cli" / "__init__.py").write_text(
                f'__version__ = "{bootstrap.HERMES_RUNTIME_PATCH_VERSION}"\n',
                encoding="utf-8",
            )
            session_source = b"locked session source\n"
            (agent_root / "gateway" / "session.py").write_bytes(session_source)
            relation_key = "agent:goutoujunshi:feishu:group:chat-1:user-1"
            sessions_file = global_home / "sessions" / "sessions.json"
            sessions_file.write_text(
                json.dumps({relation_key: {"session_id": "mirror-only"}}),
                encoding="utf-8",
            )

            class FakeEntry:
                @classmethod
                def from_dict(cls, data):
                    return SimpleNamespace(session_id=data["session_id"])

            class FailingDB:
                def __init__(self, db_path):
                    self.db_path = Path(db_path)

                def load_gateway_routing_entries(self, *, scope=""):
                    return {}

                def delete_gateway_routing_entries(self, keys, *, scope=""):
                    raise AssertionError("no database route should be removed")

                def get_session(self, session_id):
                    return {"end_reason": None}

                def promote_to_session_reset(self, session_id, reason):
                    return False

                def close(self):
                    return None

            with self.assertRaisesRegex(RuntimeError, "failed to end Hermes profile session"):
                bootstrap.rotate_profile_sessions(
                    agent_root,
                    global_home,
                    profile_home,
                    expected_session_sha256=hashlib.sha256(session_source).hexdigest(),
                    session_db_class=FailingDB,
                    session_entry_class=FakeEntry,
                )

            mirror = json.loads(sessions_file.read_text(encoding="utf-8"))
            self.assertIn(relation_key, mirror)

    def test_profile_session_rotation_keeps_database_route_when_transcript_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent_root = root / "agent"
            global_home = root / "home"
            profile_home = global_home / "profiles" / "goutoujunshi"
            (agent_root / "gateway").mkdir(parents=True)
            (agent_root / "hermes_cli").mkdir()
            profile_home.mkdir(parents=True)
            (global_home / "state.db").touch()
            (agent_root / "hermes_cli" / "__init__.py").write_text(
                f'__version__ = "{bootstrap.HERMES_RUNTIME_PATCH_VERSION}"\n',
                encoding="utf-8",
            )
            session_source = b"locked session source\n"
            (agent_root / "gateway" / "session.py").write_bytes(session_source)
            relation_key = "agent:goutoujunshi:feishu:group:chat-1:user-1"

            class FakeEntry:
                @classmethod
                def from_dict(cls, data):
                    return SimpleNamespace(session_id=data["session_id"])

            class MissingTranscriptDB:
                routes = {relation_key: json.dumps({"session_id": "missing"})}

                def __init__(self, db_path):
                    self.db_path = Path(db_path)

                def load_gateway_routing_entries(self, *, scope=""):
                    return dict(self.routes) if not scope else {}

                def delete_gateway_routing_entries(self, keys, *, scope=""):
                    for key in keys:
                        self.routes.pop(key, None)

                def get_session(self, session_id):
                    return None

                def promote_to_session_reset(self, session_id, reason):
                    raise AssertionError("a missing transcript must not be promoted")

                def close(self):
                    return None

            with self.assertRaisesRegex(RuntimeError, "transcript is missing"):
                bootstrap.rotate_profile_sessions(
                    agent_root,
                    global_home,
                    profile_home,
                    expected_session_sha256=hashlib.sha256(session_source).hexdigest(),
                    session_db_class=MissingTranscriptDB,
                    session_entry_class=FakeEntry,
                )

            self.assertIn(relation_key, MissingTranscriptDB.routes)

    def test_hermes_vision_patch_bounds_concurrency_and_preserves_order(self) -> None:
        source = '''import asyncio
import json
import time

class Runner:
    async def enrich(self, user_text, image_paths):
        analysis_prompt = "analyze"
        enriched_parts = []
        for path in image_paths:
            enriched_parts.append(path)
        # Combine: vision descriptions first, then the user's original text
        if enriched_parts:
            prefix = "\\n\\n".join(enriched_parts)
            return f"{prefix}\\n\\n{user_text}" if user_text else prefix
        return user_text
'''
        state = {"active": 0, "max_active": 0}

        async def fake_vision(*, image_url: str, user_prompt: str) -> str:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
            try:
                await asyncio.sleep(0.01 * (7 - int(image_url[-1])))
                if image_url == "p2":
                    raise RuntimeError("isolated failure")
                if image_url == "p4":
                    return json.dumps({"success": False})
                return json.dumps({"success": True, "analysis": "ok"})
            finally:
                state["active"] -= 1

        namespace = {
            "vision_analyze_tool": fake_vision,
            "sanitize_context": lambda value: value,
            "logger": SimpleNamespace(debug=lambda *args: None, error=lambda *args: None, info=lambda *args: None),
        }
        exec(bootstrap._patch_hermes_vision_source(source), namespace)

        result = asyncio.run(namespace["Runner"]().enrich("caption", [f"p{i}" for i in range(1, 7)]))

        self.assertLessEqual(state["max_active"], 3)
        positions = [result.index(f"[Image {index}") for index in range(1, 7)]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("[Image 2 could not be analyzed.]", result)
        self.assertIn("[Image 4 could not be analyzed.]", result)
        self.assertNotIn("p1", result)

    def test_gateway_display_queues_followups_and_hides_feishu_tool_progress(self) -> None:
        config = {"display": {"compact": False}}

        bootstrap._configure_gateway_display(config)

        self.assertEqual(config["display"]["busy_input_mode"], "queue")
        self.assertFalse(config["display"]["compact"])
        self.assertEqual(config["display"]["tool_progress"], "off")
        self.assertFalse(config["display"]["interim_assistant_messages"])
        self.assertFalse(config["display"]["busy_ack_detail"])
        self.assertEqual(config["display"]["platforms"]["feishu"]["tool_progress"], "off")
        self.assertFalse(config["display"]["platforms"]["feishu"]["interim_assistant_messages"])
        self.assertFalse(config["display"]["platforms"]["feishu"]["busy_ack_detail"])

    def test_prepare_secrets_defaults_to_terra_high_and_preserves_other_providers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = root / "auth.json"
            config = root / "config.toml"
            sessions = root / "sessions.json"
            env = root / ".env"
            env.write_text(
                "DEEPSEEK_API_KEY=optional-provider-secret\n"
                "GOUTOUJUNSHI_MILVUS_URL=http://127.0.0.1:19530\n"
                "GOUTOUJUNSHI_SEMANTIC_SEARCH_ENABLED=true\n",
                encoding="utf-8",
            )
            auth.write_text(json.dumps({"OPENAI_API_KEY": "test-secret"}), encoding="utf-8")
            config.write_text(
                '\n'.join(
                    [
                        'model_provider = "OpenAI"',
                        'model = "gpt-5.6-sol"',
                        'model_reasoning_effort = "xhigh"',
                        '',
                        '[model_providers.OpenAI]',
                        'base_url = "https://example.invalid/v1"',
                        'wire_api = "responses"',
                    ]
                ),
                encoding="utf-8",
            )
            sessions.write_text(
                json.dumps({"one": {"origin": {"platform": "feishu", "user_id": "owner"}}}),
                encoding="utf-8",
            )

            bootstrap.command_prepare_secrets(
                SimpleNamespace(
                    codex_auth=str(auth),
                    codex_config=str(config),
                    sessions=str(sessions),
                    env=str(env),
                    export_root=str(root / "exports"),
                )
            )

            values = bootstrap.load_dotenv(env)
            self.assertEqual(values["GOUTOUJUNSHI_MODEL"], "gpt-5.6-terra")
            self.assertEqual(values["GOUTOUJUNSHI_REASONING"], "high")
            self.assertEqual(values["DEEPSEEK_API_KEY"], "optional-provider-secret")
            self.assertNotIn("GOUTOUJUNSHI_MILVUS_URL", values)
            self.assertNotIn("GOUTOUJUNSHI_SEMANTIC_SEARCH_ENABLED", values)
            self.assertNotIn("GOUTOUJUNSHI_TOKEN_SECRET", values)

    def test_model_config_has_no_fallback(self) -> None:
        config = {
            "fallback_providers": ["deepseek"],
            "providers": {"another-provider": {"base_url": "https://another.example/v1"}},
        }
        bootstrap._configure_model(
            config,
            {
                "GOUTOUJUNSHI_MODEL": "gpt-5.6-terra",
                "GOUTOUJUNSHI_OPENAI_BASE_URL": "https://example.invalid/v1",
                "GOUTOUJUNSHI_REASONING": "high",
            },
        )

        self.assertEqual(config["model"]["default"], "gpt-5.6-terra")
        self.assertEqual(config["model"]["provider"], "openai-api")
        self.assertEqual(config["agent"]["reasoning_effort"], "high")
        self.assertEqual(config["fallback_providers"], [])
        self.assertIn("another-provider", config["providers"])
        self.assertEqual(
            config["providers"]["codex-gpt"]["extra_headers"]["User-Agent"],
            "codex_cli_rs/0.0.0",
        )

    def test_model_configuration_accepts_explicit_values(self) -> None:
        config = {}
        bootstrap._configure_model(
            config,
            {
                "GOUTOUJUNSHI_MODEL": "another-model",
                "GOUTOUJUNSHI_OPENAI_BASE_URL": "https://provider.example/v1",
                "GOUTOUJUNSHI_REASONING": "medium",
            },
        )

        self.assertEqual(config["model"]["default"], "another-model")
        self.assertEqual(config["model"]["base_url"], "https://provider.example/v1")
        self.assertEqual(config["agent"]["reasoning_effort"], "medium")

    def test_vision_uses_named_responses_route_without_storing_a_key(self) -> None:
        config = {"auxiliary": {"vision": {"extra_body": {"detail": "high"}}}}

        bootstrap._configure_vision(
            config,
            {
                "GOUTOUJUNSHI_MODEL": "gpt-5.6-terra",
                "GOUTOUJUNSHI_OPENAI_BASE_URL": "https://provider.example/v1",
            },
        )

        vision = config["auxiliary"]["vision"]
        self.assertEqual(vision["provider"], "codex-gpt")
        self.assertEqual(vision["model"], "gpt-5.6-terra")
        self.assertEqual(vision["api_mode"], "codex_responses")
        self.assertEqual(vision["api_key"], "")
        self.assertEqual(vision["base_url"], "")
        self.assertEqual(vision["extra_body"], {"detail": "high"})
        self.assertEqual(config["model"]["default_headers"]["User-Agent"], "codex_cli_rs/0.0.0")

    def test_prepare_server_secrets_is_allowlisted_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.env"
            target = root / "server-secrets"
            values = {
                "OPENAI_API_KEY": "model-secret",
                "FEISHU_APP_ID": "app-id",
                "FEISHU_APP_SECRET": "feishu-secret",
                "FEISHU_DOMAIN": "feishu",
                "FEISHU_ALLOWED_USERS": "owner-id",
                "GOUTOUJUNSHI_OWNER_ID": "owner-id",
                "GOUTOUJUNSHI_OPENAI_BASE_URL": "https://example.invalid/v1",
                "GOUTOUJUNSHI_MODEL": "gpt-5.6-terra",
                "GOUTOUJUNSHI_REASONING": "high",
                "UNRELATED_SECRET": "must-not-migrate",
            }
            source.write_text(
                "".join(f"{key}={value}\n" for key, value in values.items()),
                encoding="utf-8",
            )

            first = bootstrap.prepare_server_secrets(source, target)
            app_password = (target / "mysql-app-password").read_text(encoding="utf-8")
            root_password = (target / "mysql-root-password").read_text(encoding="utf-8")
            second = bootstrap.prepare_server_secrets(source, target)
            env = bootstrap.load_dotenv(target / "hermes.env")

            self.assertTrue(first["ok"])
            self.assertEqual(first["files"], second["files"])
            self.assertEqual(
                (target / "mysql-app-password").read_text(encoding="utf-8"),
                app_password,
            )
            self.assertEqual(
                (target / "mysql-root-password").read_text(encoding="utf-8"),
                root_password,
            )
            self.assertNotIn("UNRELATED_SECRET", env)
            self.assertNotIn("GOUTOUJUNSHI_TOKEN_SECRET", env)
            self.assertEqual(env["GOUTOUJUNSHI_DB_HOST"], "mysql")
            self.assertEqual(env["GOUTOUJUNSHI_EXPORT_ROOT"], "/opt/data/relationships")
            self.assertEqual(env["FEISHU_ALLOW_ALL_USERS"], "false")

    def test_vision_preserves_other_default_headers(self) -> None:
        config = {"model": {"default_headers": {"X-Custom": "kept"}}}

        bootstrap._configure_vision(
            config,
            {
                "GOUTOUJUNSHI_MODEL": "gpt-5.6-terra",
                "GOUTOUJUNSHI_OPENAI_BASE_URL": "https://provider.example/v1",
            },
        )

        self.assertEqual(config["model"]["default_headers"]["X-Custom"], "kept")
        self.assertEqual(config["model"]["default_headers"]["User-Agent"], "codex_cli_rs/0.0.0")


if __name__ == "__main__":
    unittest.main()
