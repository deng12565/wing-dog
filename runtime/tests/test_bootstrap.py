import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from runtime import bootstrap


class BootstrapConfigTests(unittest.TestCase):
    @staticmethod
    def _vision_patch_fixture(root: Path) -> tuple[Path, Path, str, str]:
        agent_root = root / "hermes-agent"
        backup_dir = root / "backups"
        (agent_root / "gateway").mkdir(parents=True)
        (agent_root / "hermes_cli").mkdir()
        (agent_root / "hermes_cli" / "__init__.py").write_text(
            '__version__ = "test-version"\n', encoding="utf-8"
        )
        source = '''class Runner:
    async def enrich(self, user_text, image_paths):
        enriched_parts = []
        for path in image_paths:
            enriched_parts.append(path)
        # Combine: vision descriptions first, then the user's original text
        return user_text
'''
        target = agent_root / "gateway" / "run.py"
        target.write_text(source, encoding="utf-8", newline="\n")
        original_sha = bootstrap.hashlib.sha256(source.encode("utf-8")).hexdigest()
        patched = bootstrap._patch_hermes_vision_source(source).encode("utf-8")
        patched_sha = bootstrap.hashlib.sha256(patched).hexdigest()
        return agent_root, backup_dir, original_sha, patched_sha

    def test_global_config_enables_owner_only_unmentioned_user_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.yaml"
            env_path = root / ".env"
            config_path.write_text(
                "platform_toolsets:\n  feishu: [existing]\nplatforms:\n  feishu: {}\n",
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
                ["existing", "hermes-feishu", "goutoujunshi-user"],
            )
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
            (profile_home / ".env").write_text(
                "GOUTOUJUNSHI_MILVUS_MANAGED=true\nCUSTOM_PROFILE_VALUE=kept\n",
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
            self.assertNotIn("skills", config["agent"]["disabled_toolsets"])
            self.assertIn("file", config["agent"]["disabled_toolsets"])
            self.assertIn("terminal", config["agent"]["disabled_toolsets"])
            self.assertEqual(config["compression"]["min_tail_user_messages"], 3)
            self.assertTrue(config["compression"]["abort_on_summary_failure"])
            profile_values = bootstrap.load_dotenv(profile_home / ".env")
            self.assertNotIn("GOUTOUJUNSHI_OLLAMA_URL", profile_values)
            self.assertNotIn("GOUTOUJUNSHI_MILVUS_MANAGED", profile_values)
            self.assertEqual(profile_values["CUSTOM_PROFILE_VALUE"], "kept")

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

    def test_hermes_vision_patch_is_guarded_atomic_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent_root, backup_dir, original_sha, patched_sha = self._vision_patch_fixture(
                Path(directory)
            )

            result = bootstrap.install_hermes_vision_patch(
                agent_root,
                backup_dir,
                expected_version="test-version",
                expected_sha256=original_sha,
                expected_patched_sha256=patched_sha,
            )
            repeated = bootstrap.install_hermes_vision_patch(
                agent_root,
                backup_dir,
                expected_version="test-version",
                expected_sha256=original_sha,
                expected_patched_sha256=patched_sha,
            )

            installed = (agent_root / "gateway" / "run.py").read_text(encoding="utf-8")
            self.assertEqual(result["status"], "patched")
            self.assertEqual(repeated["status"], "already_patched")
            self.assertIn("asyncio.Semaphore(concurrency)", installed)
            self.assertNotIn("image_url: {path}", installed)
            self.assertEqual(len(list(backup_dir.glob("gateway-run.py.*.bak"))), 1)

    def test_hermes_vision_patch_refuses_unknown_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent_root, backup_dir, _, patched_sha = self._vision_patch_fixture(Path(directory))
            target = agent_root / "gateway" / "run.py"
            original = target.read_bytes()

            with self.assertRaises(RuntimeError):
                bootstrap.install_hermes_vision_patch(
                    agent_root,
                    backup_dir,
                    expected_version="test-version",
                    expected_sha256="0" * 64,
                    expected_patched_sha256=patched_sha,
                )

            self.assertEqual(target.read_bytes(), original)

    def test_hermes_vision_patch_restores_source_when_final_check_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent_root, backup_dir, original_sha, patched_sha = self._vision_patch_fixture(
                Path(directory)
            )
            target = agent_root / "gateway" / "run.py"
            original = target.read_bytes()
            real_sha = bootstrap._sha256_file

            def fail_installed_verification(path: Path) -> str:
                digest = real_sha(path)
                if path.resolve() == target.resolve() and digest == patched_sha:
                    return "f" * 64
                return digest

            with patch.object(bootstrap, "_sha256_file", side_effect=fail_installed_verification):
                with self.assertRaises(RuntimeError):
                    bootstrap.install_hermes_vision_patch(
                        agent_root,
                        backup_dir,
                        expected_version="test-version",
                        expected_sha256=original_sha,
                        expected_patched_sha256=patched_sha,
                    )

            self.assertEqual(target.read_bytes(), original)

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
