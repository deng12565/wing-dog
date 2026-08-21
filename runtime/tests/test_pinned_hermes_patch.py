from __future__ import annotations

import ast
import asyncio
import os
import sys
import unittest
from contextlib import contextmanager
from contextvars import ContextVar
from enum import Enum
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


RUNTIME = Path(__file__).resolve().parents[1]
PROJECT = RUNTIME.parent
sys.path.insert(0, str(PROJECT))

from runtime import bootstrap  # noqa: E402


class _MessageType(Enum):
    TEXT = "text"
    COMMAND = "command"
    PHOTO = "photo"


def _event(
    message_type: _MessageType,
    *,
    text: str = "",
    media: list[str] | None = None,
    sender: str = "owner",
    sender_alt: str = "owner-alt",
    reply: str = "reply-1",
    thread: str = "thread-1",
):
    return SimpleNamespace(
        message_type=message_type,
        text=text,
        media_urls=list(media or []),
        media_types=["image"] * len(media or []),
        source=SimpleNamespace(
            user_id=sender,
            user_id_alt=sender_alt,
            thread_id=thread,
        ),
        reply_to_message_id=reply,
        reply_to_text="quoted",
        timestamp=1,
        message_id="message-1",
        is_command=lambda: message_type == _MessageType.COMMAND,
    )


@unittest.skipUnless(
    os.environ.get("WING_DOG_HERMES_SOURCE_ROOT"),
    "requires the locked Hermes 0.20.4 source tree",
)
class PinnedHermesPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.agent_root = Path(os.environ["WING_DOG_HERMES_SOURCE_ROOT"]).resolve()
        inspection = bootstrap.inspect_hermes_runtime_patch(cls.agent_root)
        if inspection["version"] != bootstrap.HERMES_RUNTIME_PATCH_VERSION:
            raise RuntimeError("locked Hermes test source has the wrong version")
        if any(
            item["status"] not in {"source", "patched"}
            or item["built_patched_sha256"] != item["expected_patched_sha256"]
            for item in inspection["files"].values()
        ):
            raise RuntimeError("locked Hermes test source does not match the runtime patch")

    def _source_or_rebuilt(self, relative_path: str) -> str:
        source = (self.agent_root / relative_path).read_text(encoding="utf-8")
        hashes = bootstrap.HERMES_RUNTIME_PATCH_FILES[relative_path]
        if bootstrap.hashlib.sha256(source.encode("utf-8")).hexdigest() == hashes["patched_sha256"]:
            return source
        return bootstrap.HERMES_RUNTIME_PATCH_TRANSFORMS[relative_path](source)

    def _media_harness(self):
        source = self._source_or_rebuilt("plugins/platforms/feishu/adapter.py")
        module = ast.parse(source)
        adapter = next(
            node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "FeishuAdapter"
        )
        wanted = {
            "_batch_context_is_compatible",
            "_media_batch_is_compatible",
            "_merge_text_into_pending_photo",
            "_enqueue_media_event",
        }
        methods = [
            node
            for node in adapter.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted
        ]
        self.assertEqual({method.name for method in methods}, wanted)
        harness_node = ast.ClassDef(
            name="Harness",
            bases=[],
            keywords=[],
            body=methods,
            decorator_list=[],
        )
        namespace = {"MessageType": _MessageType}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[harness_node], type_ignores=[])), "<media-patch>", "exec"), namespace)
        harness = namespace["Harness"]()
        harness._pending_media_batches = {}
        harness._pending_media_batch_counts = {}
        harness._text_batch_max_messages = 4
        harness._text_batch_max_chars = 80
        harness._media_batch_key = lambda _incoming: "session:media:photo"
        harness._text_batch_key = lambda _incoming: "session"
        harness._merge_caption = lambda left, right: f"{left}\n{right}" if left else right
        harness.scheduled = []
        harness.flushed = []
        harness._schedule_media_batch_flush = lambda key: harness.scheduled.append(key)

        async def flush(key):
            event = harness._pending_media_batches.pop(key, None)
            harness._pending_media_batch_counts.pop(key, None)
            if event is not None:
                harness.flushed.append(event)

        harness._flush_media_batch_now = flush
        return harness

    def test_media_batch_single_multi_caption_context_and_limits(self) -> None:
        harness = self._media_harness()
        first = _event(_MessageType.PHOTO, text="image one", media=["one.png"])
        second = _event(_MessageType.PHOTO, text="image two", media=["two.png"])
        asyncio.run(harness._enqueue_media_event(first))
        asyncio.run(harness._enqueue_media_event(second))
        pending = harness._pending_media_batches["session:media:photo"]
        self.assertEqual(pending.media_urls, ["one.png", "two.png"])
        self.assertEqual(harness._pending_media_batch_counts["session:media:photo"], 2)
        self.assertEqual(len(harness.scheduled), 2)

        caption = _event(_MessageType.TEXT, text="caption after images")
        self.assertTrue(asyncio.run(harness._merge_text_into_pending_photo(caption)))
        self.assertIn("caption after images", pending.text)
        self.assertEqual(harness._pending_media_batch_counts["session:media:photo"], 3)

        incompatible_cases = (
            _event(_MessageType.TEXT, text="other sender", sender="other"),
            _event(_MessageType.TEXT, text="other reply", reply="reply-2"),
            _event(_MessageType.TEXT, text="other thread", thread="thread-2"),
        )
        for incoming in incompatible_cases:
            with self.subTest(text=incoming.text):
                fresh = _event(_MessageType.PHOTO, media=["fresh.png"])
                asyncio.run(harness._enqueue_media_event(fresh))
                self.assertFalse(asyncio.run(harness._merge_text_into_pending_photo(incoming)))
                self.assertTrue(harness.flushed)

        oversized_photo = _event(_MessageType.PHOTO, text="x" * 60, media=["large.png"])
        oversized_caption = _event(_MessageType.TEXT, text="y" * 60)
        asyncio.run(harness._enqueue_media_event(oversized_photo))
        self.assertFalse(asyncio.run(harness._merge_text_into_pending_photo(oversized_caption)))
        self.assertFalse(harness._pending_media_batches)

    def test_runtime_patch_removes_sensitive_info_log_formats_and_sets_private_modes(self) -> None:
        gateway = self._source_or_rebuilt("gateway/run.py")
        turn_context = self._source_or_rebuilt("agent/turn_context.py")
        adapter = self._source_or_rebuilt("plugins/platforms/feishu/adapter.py")
        hermes_logging = self._source_or_rebuilt("hermes_logging.py")

        self.assertNotIn("msg=%r reply_to_id=%s reply_to_text=%r", gateway)
        self.assertNotIn("history=%d msg=%r", turn_context)
        self.assertNotIn("sender=%s:%s text=%r media=%d", adapter)
        self.assertNotIn("Flushing media batch %s", adapter)
        self.assertNotIn("Flushing text batch %s", adapter)
        self.assertNotIn("Vision auto-analysis result:", gateway)
        self.assertIn("msg_hash=%s", gateway)
        self.assertIn("text_hash=%s", adapter)
        self.assertIn("os.chmod(log_dir, 0o700)", hermes_logging)
        self.assertIn("os.chmod(self.baseFilename, 0o600)", hermes_logging)
        self.assertNotIn("os.chmod(self.baseFilename, 0o660)", hermes_logging)

    def test_profile_scope_precedes_session_hook_and_onboarding_is_gated(self) -> None:
        gateway = self._source_or_rebuilt("gateway/run.py")
        self.assertLess(
            gateway.index("_profile_runtime_scope_active"),
            gateway.index('"post_gateway_session"'),
        )
        self.assertIn("first_message_intro", gateway)
        self.assertIn("return await self._handle_message(event)", gateway)
        self.assertIn("post_gateway_session failed closed for Feishu", gateway)
        self.assertIn('_platform_name == "feishu" and not _session_hook_allowed', gateway)

    def test_profile_scope_keeps_two_turn_history_in_one_store(self) -> None:
        gateway = self._source_or_rebuilt("gateway/run.py")
        module = ast.parse(gateway)
        runner = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "GatewayRunner"
        )
        handle = next(
            node
            for node in runner.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_handle_message"
        )
        scope_wrapper = next(
            statement
            for statement in handle.body
            if isinstance(statement, ast.If)
            and any(
                isinstance(child, ast.Constant)
                and child.value == "_profile_runtime_scope_active"
                for child in ast.walk(statement.test)
            )
        )
        harness_method = ast.AsyncFunctionDef(
            name="_handle_message",
            args=ast.arguments(
                posonlyargs=[],
                args=[ast.arg(arg="self"), ast.arg(arg="event")],
                kwonlyargs=[],
                kw_defaults=[],
                defaults=[],
                vararg=None,
                kwarg=None,
            ),
            body=[
                ast.Assign(
                    targets=[ast.Name(id="source", ctx=ast.Store())],
                    value=ast.Attribute(
                        value=ast.Name(id="event", ctx=ast.Load()),
                        attr="source",
                        ctx=ast.Load(),
                    ),
                ),
                scope_wrapper,
                ast.Return(
                    value=ast.Await(
                        value=ast.Call(
                            func=ast.Attribute(
                                value=ast.Name(id="self", ctx=ast.Load()),
                                attr="_run_scoped_turn",
                                ctx=ast.Load(),
                            ),
                            args=[ast.Name(id="event", ctx=ast.Load())],
                            keywords=[],
                        )
                    )
                ),
            ],
            decorator_list=[],
        )
        harness_class = ast.ClassDef(
            name="Harness",
            bases=[],
            keywords=[],
            body=[harness_method],
            decorator_list=[],
        )

        active_home = ContextVar("wing_dog_test_home", default="global")

        @contextmanager
        def profile_scope(path):
            token = active_home.set(str(path))
            try:
                yield
            finally:
                active_home.reset(token)

        namespace = {"Path": Path, "_profile_runtime_scope": profile_scope}
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=[harness_class], type_ignores=[])),
                "<profile-scope-patch>",
                "exec",
            ),
            namespace,
        )
        harness = namespace["Harness"]()
        harness.config = SimpleNamespace(multiplex_profiles=True)
        harness.stores = {}
        harness.history_counts = []

        async def run_scoped_turn(event):
            home = active_home.get()
            transcript = harness.stores.setdefault(home, [])
            harness.history_counts.append(len(transcript))
            transcript.extend(
                [
                    {"role": "user", "content": event.text},
                    {"role": "assistant", "content": "reply"},
                ]
            )
            return len(transcript)

        harness._run_scoped_turn = run_scoped_turn
        profile_home = Path("/profiles/goutoujunshi")
        profiles_package = ModuleType("hermes_cli")
        profiles_package.__path__ = []
        profiles_module = ModuleType("hermes_cli.profiles")
        profiles_module.get_profile_dir = lambda _name: profile_home
        event = SimpleNamespace(
            source=SimpleNamespace(profile="goutoujunshi"),
            text="turn",
        )

        with patch.dict(
            sys.modules,
            {"hermes_cli": profiles_package, "hermes_cli.profiles": profiles_module},
        ):
            asyncio.run(harness._handle_message(event))
            asyncio.run(harness._handle_message(event))

        self.assertEqual(harness.history_counts, [0, 2])
        self.assertEqual(set(harness.stores), {str(profile_home)})
        self.assertEqual(len(harness.stores[str(profile_home)]), 4)
        self.assertFalse(hasattr(event, "_profile_runtime_scope_active"))


if __name__ == "__main__":
    unittest.main()
