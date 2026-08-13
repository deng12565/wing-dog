from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))

import goutoujunshi  # noqa: E402


class FakeContext:
    def __init__(self) -> None:
        self.tools: list[tuple[str, str]] = []
        self.hooks: list[str] = []

    def register_tool(self, *, name: str, toolset: str, **_: object) -> None:
        self.tools.append((name, toolset))

    def register_hook(self, name: str, _: object) -> None:
        self.hooks.append(name)


class PluginSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        with goutoujunshi._LOCK:
            goutoujunshi._SESSION_BINDINGS.clear()
            goutoujunshi._SESSION_OWNERS.clear()
            goutoujunshi._SESSION_MEDIA.clear()
            goutoujunshi._SESSION_PROMPTS.clear()
            goutoujunshi._SESSION_TURN_METRICS.clear()

    def tearDown(self) -> None:
        self.setUp()

    def test_context_prompt_fails_closed_when_attachment_is_missing(self) -> None:
        binding = {
            "id": 1,
            "chat_id": "chat-1",
            "owner_key": "owner-1",
            "current_channel": "微信",
        }
        with patch.object(goutoujunshi.repository, "recent_context", return_value={}), patch.object(
            goutoujunshi.repository, "list_user_memory", return_value=[]
        ), patch.object(goutoujunshi, "_token", return_value="test-token"), patch.object(
            goutoujunshi, "_user_token", return_value="user-token"
        ):
            prompt = goutoujunshi._context_prompt(binding, "session-1", "微信", "message-1", [])
        self.assertNotIn("本轮附件数量", prompt)
        self.assertIn("只有当前消息实际带入的附件才能分析", prompt)
        self.assertIn("禁止用导出冒充导入", prompt)

    def test_context_prompt_is_stable_across_messages_and_attachments(self) -> None:
        binding = {
            "id": 1,
            "chat_id": "chat-1",
            "owner_key": "owner-1",
            "current_channel": "微信",
        }
        with patch.object(goutoujunshi.repository, "recent_context", return_value={}), patch.object(
            goutoujunshi.repository, "list_user_memory", return_value=[]
        ), patch.object(goutoujunshi, "_token", return_value="test-token"), patch.object(
            goutoujunshi, "_user_token", return_value="user-token"
        ):
            first = goutoujunshi._context_prompt(
                binding, "session-1", "微信", "message-1", ["document.md"]
            )
            second = goutoujunshi._context_prompt(
                binding, "session-1", "抖音", "message-2", []
            )
        self.assertEqual(first, second)
        self.assertNotIn("message-1", first)

    def test_relationship_command_aliases_are_supported(self) -> None:
        for text in ("/relation status", "/relationship status"):
            with self.subTest(text=text):
                match = goutoujunshi.RELATION_COMMAND.search(text)
                self.assertIsNotNone(match)
                self.assertEqual(match.group(1), "status")

    def test_user_memory_command_is_supported(self) -> None:
        match = goutoujunshi.USER_COMMAND.search("/me remember --today 今天居家办公")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "remember --today 今天居家办公")

    def test_relationship_and_user_tools_are_isolated(self) -> None:
        context = FakeContext()
        goutoujunshi.register(context)
        self.assertEqual(len(context.tools), 5)
        relationship = {name for name, toolset in context.tools if toolset == "goutoujunshi"}
        user = {name for name, toolset in context.tools if toolset == "goutoujunshi-user"}
        self.assertEqual(relationship, {"relationship_commit_turn", "relationship_search_events"})
        self.assertEqual(user, goutoujunshi.USER_TOOL_NAMES)
        names = relationship | user
        self.assertNotIn("terminal", names)
        self.assertNotIn("read_file", names)
        self.assertNotIn("web_search", names)
        self.assertEqual(
            set(context.hooks),
            {
                "pre_gateway_dispatch",
                "post_llm_call",
                "post_api_request",
                "post_tool_call",
                "on_session_end",
                "on_session_reset",
                "on_session_finalize",
            },
        )

    def test_search_tool_contract_is_bounded_and_drafts_are_opt_in(self) -> None:
        properties = goutoujunshi.SCHEMAS["relationship_search_events"]["parameters"]["properties"]
        self.assertEqual(properties["query"]["maxLength"], 500)
        self.assertEqual(properties["limit"]["default"], 8)
        self.assertEqual(properties["limit"]["maximum"], 20)
        self.assertFalse(properties["include_drafts"]["default"])

    def test_commit_tool_accepts_bounded_write_time_search_enrichment(self) -> None:
        event_schema = goutoujunshi.SCHEMAS["relationship_commit_turn"]["parameters"]["properties"]["events"]["items"]
        enrichment_schema = event_schema["properties"]["search_enrichment"]
        self.assertEqual(enrichment_schema["properties"]["summary"]["maxLength"], 240)
        self.assertEqual(enrichment_schema["properties"]["concepts"]["maxItems"], 8)
        self.assertFalse(enrichment_schema["additionalProperties"])

    def test_turn_end_keeps_prompt_until_real_session_cleanup(self) -> None:
        with goutoujunshi._LOCK:
            goutoujunshi._SESSION_PROMPTS["session-1"] = {
                "prompt": "stable",
                "owner_id": "owner-1",
                "relationship_id": 7,
            }
            goutoujunshi._SESSION_TURN_METRICS["session-1"] = {
                "started": goutoujunshi.time.monotonic(),
                "image_count": 0,
                "prompt_reused": True,
                "agent_cache_candidate": True,
                "api_request_ids": {"turn:api:1"},
                "tool_request_ids": set(),
                "tool_calls": 0,
                "api_duration_ms": 10.0,
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_tokens": 80,
                "cache_write_tokens": 0,
            }

        goutoujunshi.on_session_end(session_id="session-1", completed=True)

        self.assertIn("session-1", goutoujunshi._SESSION_PROMPTS)
        self.assertNotIn("session-1", goutoujunshi._SESSION_TURN_METRICS)
        goutoujunshi._clear_session_state(session_id="session-1")
        self.assertNotIn("session-1", goutoujunshi._SESSION_PROMPTS)

    def test_user_and_relationship_tokens_cannot_be_exchanged(self) -> None:
        binding = {"id": 7, "chat_id": "chat-1"}
        secret = "s" * 48
        with patch.dict(
            "os.environ",
            {"GOUTOUJUNSHI_OWNER_ID": "owner-1", "GOUTOUJUNSHI_TOKEN_SECRET": secret},
        ):
            user_token = goutoujunshi._user_token("owner-1", "session-1", "feishu:test")
            relationship_token = goutoujunshi._token(binding, "session-1")
            with self.assertRaises(PermissionError):
                goutoujunshi._verify_token(user_token, "session-1")
            with self.assertRaises(PermissionError):
                goutoujunshi._verify_user_token(relationship_token, "session-1")

    def test_legacy_relationship_token_survives_gateway_reload(self) -> None:
        binding = {"id": 7, "chat_id": "chat-1"}
        secret = "s" * 48
        payload = json.dumps(
            {
                "kind": "relationship",
                "chat_id": "chat-1",
                "relationship_id": 7,
                "session_id": "session-1",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
        legacy_token = ".".join(
            base64.urlsafe_b64encode(part).decode("ascii").rstrip("=")
            for part in (payload, signature)
        )
        with patch.dict(
            "os.environ",
            {"GOUTOUJUNSHI_OWNER_ID": "owner-1", "GOUTOUJUNSHI_TOKEN_SECRET": secret},
        ), patch.object(goutoujunshi.repository, "get_binding", return_value=binding):
            with goutoujunshi._LOCK:
                goutoujunshi._SESSION_BINDINGS["session-1"] = binding
            try:
                self.assertEqual(goutoujunshi._verify_token(legacy_token, "session-1"), binding)
            finally:
                with goutoujunshi._LOCK:
                    goutoujunshi._SESSION_BINDINGS.pop("session-1", None)

    def test_model_inserted_token_separator_is_tolerated(self) -> None:
        binding = {"id": 7, "chat_id": "chat-1"}
        secret = "s" * 48
        with patch.dict(
            "os.environ",
            {"GOUTOUJUNSHI_OWNER_ID": "owner-1", "GOUTOUJUNSHI_TOKEN_SECRET": secret},
        ), patch.object(goutoujunshi.repository, "get_binding", return_value=binding):
            token = goutoujunshi._token(binding, "session-1")
            corrupted = token[: len(token) // 2] + "." + token[len(token) // 2 :]
            with goutoujunshi._LOCK:
                goutoujunshi._SESSION_BINDINGS["session-1"] = binding
            try:
                self.assertEqual(goutoujunshi._verify_token(corrupted, "session-1"), binding)
            finally:
                with goutoujunshi._LOCK:
                    goutoujunshi._SESSION_BINDINGS.pop("session-1", None)

    def test_user_token_uses_current_server_message_source(self) -> None:
        secret = "s" * 48
        with patch.dict(
            "os.environ",
            {"GOUTOUJUNSHI_OWNER_ID": "owner-1", "GOUTOUJUNSHI_TOKEN_SECRET": secret},
        ):
            token = goutoujunshi._user_token("owner-1", "session-1", "feishu:old-message")
            with goutoujunshi._LOCK:
                goutoujunshi._SESSION_OWNERS["session-1"] = {
                    "owner_id": "owner-1",
                    "source_ref": "feishu:current-message",
                }
            try:
                claims = goutoujunshi._verify_user_token(token, "session-1")
                self.assertEqual(claims["source_ref"], "feishu:current-message")
            finally:
                with goutoujunshi._LOCK:
                    goutoujunshi._SESSION_OWNERS.pop("session-1", None)

    def test_unbound_owner_group_loads_only_user_context(self) -> None:
        source = SimpleNamespace(
            platform="feishu", chat_id="new-chat", user_id="owner-1", profile="default"
        )
        event = SimpleNamespace(
            text="我最近在做一个新项目",
            source=source,
            media_urls=[],
            message_id="message-1",
            channel_prompt="existing",
        )
        session_store = SimpleNamespace(
            get_or_create_session=lambda _: SimpleNamespace(session_id="session-1")
        )
        gateway = SimpleNamespace(adapters={})
        with patch.dict(
            "os.environ",
            {"GOUTOUJUNSHI_OWNER_ID": "owner-1", "GOUTOUJUNSHI_TOKEN_SECRET": "s" * 48},
        ), patch.object(goutoujunshi.repository, "get_binding", return_value=None), patch.object(
            goutoujunshi.repository, "is_managed_chat", return_value=False
        ), patch.object(
            goutoujunshi.repository,
            "list_user_memory",
            return_value=[
                {
                    "id": 1,
                    "category": "work_school",
                    "content": "用户在做一个软件项目",
                    "lifespan": "persistent",
                    "expires_at": None,
                }
            ],
        ):
            result = goutoujunshi.pre_gateway_dispatch(
                event=event, gateway=gateway, session_store=session_store
            )
        self.assertEqual(result["action"], "allow")
        self.assertIn("用户在做一个软件项目", event.channel_prompt)
        self.assertIn("当前群尚未绑定具体人物", event.channel_prompt)
        self.assertEqual(event.auto_skill, "goutoujunshi")

    def test_unbound_specific_relationship_request_is_blocked_before_llm(self) -> None:
        source = SimpleNamespace(
            platform="feishu", chat_id="new-chat", user_id="owner-1", profile="default"
        )
        event = SimpleNamespace(
            text="她这样回复我，我应该怎么回",
            source=source,
            media_urls=[],
            message_id="message-1",
        )
        session_store = SimpleNamespace(
            get_or_create_session=lambda _: SimpleNamespace(session_id="session-1")
        )
        gateway = SimpleNamespace(adapters={})
        with patch.dict(
            "os.environ",
            {"GOUTOUJUNSHI_OWNER_ID": "owner-1", "GOUTOUJUNSHI_TOKEN_SECRET": "s" * 48},
        ), patch.object(goutoujunshi.repository, "get_binding", return_value=None), patch.object(
            goutoujunshi.repository, "is_managed_chat", return_value=False
        ), patch.object(goutoujunshi, "_schedule_reply") as reply:
            result = goutoujunshi.pre_gateway_dispatch(
                event=event, gateway=gateway, session_store=session_store
            )
        self.assertEqual(result["action"], "skip")
        reply.assert_called_once()
        self.assertIn("未记录、未分析", reply.call_args.args[2])

    def test_bound_owner_group_auto_loads_skill_and_relationship_context(self) -> None:
        binding = {
            "id": 7,
            "chat_id": "chat-1",
            "owner_key": "owner-1",
            "current_channel": "微信",
        }
        source = SimpleNamespace(
            platform="feishu", chat_id="chat-1", user_id="owner-1", profile="goutoujunshi"
        )
        event = SimpleNamespace(
            text="她刚刚回复了",
            source=source,
            media_urls=[],
            message_id="message-1",
            channel_prompt="",
        )
        session_store = SimpleNamespace(
            get_or_create_session=lambda _: SimpleNamespace(session_id="session-1")
        )
        with patch.dict(
            "os.environ",
            {"GOUTOUJUNSHI_OWNER_ID": "owner-1", "GOUTOUJUNSHI_TOKEN_SECRET": "s" * 48},
        ), patch.object(goutoujunshi.repository, "get_binding", return_value=binding), patch.object(
            goutoujunshi.repository, "recent_context", return_value={"profile": {"id": 7}}
        ), patch.object(
            goutoujunshi.repository, "list_user_memory", return_value=[]
        ), patch.object(
            goutoujunshi.repository,
            "apply_next_message_draft_rule",
            return_value={"action": "none", "changed": False},
        ):
            result = goutoujunshi.pre_gateway_dispatch(
                event=event,
                gateway=SimpleNamespace(adapters={}),
                session_store=session_store,
            )

        self.assertEqual(result["action"], "allow")
        self.assertEqual(event.auto_skill, "goutoujunshi")
        self.assertIn("当前权威上下文", event.channel_prompt)

    def test_bound_session_reuses_exact_prompt_bytes(self) -> None:
        binding = {
            "id": 7,
            "chat_id": "chat-1",
            "owner_key": "owner-1",
            "current_channel": "微信",
        }
        source = SimpleNamespace(
            platform="feishu", chat_id="chat-1", user_id="owner-1", profile="goutoujunshi"
        )
        session_store = SimpleNamespace(
            get_or_create_session=lambda _: SimpleNamespace(session_id="stable-session")
        )
        context = {
            "recent_events": [],
            "context_stats": {"events": 0, "event_chars": 0},
        }
        prompts = []
        with patch.dict(
            "os.environ",
            {"GOUTOUJUNSHI_OWNER_ID": "owner-1", "GOUTOUJUNSHI_TOKEN_SECRET": "s" * 48},
        ), patch.object(goutoujunshi.repository, "get_binding", return_value=binding), patch.object(
            goutoujunshi.repository, "recent_context", return_value=context
        ) as recent, patch.object(
            goutoujunshi.repository, "list_user_memory", return_value=[]
        ), patch.object(
            goutoujunshi.repository,
            "apply_next_message_draft_rule",
            return_value={"action": "none", "changed": False},
        ):
            for message_id, media in (("message-1", []), ("message-2", ["shot.png"])):
                event = SimpleNamespace(
                    text="test",
                    source=source,
                    media_urls=media,
                    message_id=message_id,
                    channel_prompt="",
                )
                result = goutoujunshi.pre_gateway_dispatch(
                    event=event,
                    gateway=SimpleNamespace(adapters={}),
                    session_store=session_store,
                )
                self.assertEqual(result["action"], "allow")
                prompts.append(event.channel_prompt)
        self.assertEqual(prompts[0], prompts[1])
        recent.assert_called_once()

    def test_next_owner_message_uses_explicit_channel_and_not_sent_rule(self) -> None:
        binding = {"id": 7, "chat_id": "chat-1", "owner_key": "owner-1", "current_channel": "微信"}
        source = SimpleNamespace(
            platform="feishu", chat_id="chat-1", user_id="owner-1", profile="goutoujunshi"
        )
        event = SimpleNamespace(
            text="抖音：上一句还没发",
            source=source,
            media_urls=[],
            message_id="message-1",
            channel_prompt="",
        )
        session_store = SimpleNamespace(
            get_or_create_session=lambda _: SimpleNamespace(session_id="session-1")
        )
        with patch.dict(
            "os.environ",
            {"GOUTOUJUNSHI_OWNER_ID": "owner-1", "GOUTOUJUNSHI_TOKEN_SECRET": "s" * 48},
        ), patch.object(goutoujunshi.repository, "get_binding", return_value=binding), patch.object(
            goutoujunshi.repository, "recent_context", return_value={}
        ), patch.object(goutoujunshi.repository, "list_user_memory", return_value=[]), patch.object(
            goutoujunshi.repository,
            "apply_next_message_draft_rule",
            return_value={"action": "corrected_not_sent", "changed": True},
        ) as draft_rule:
            result = goutoujunshi.pre_gateway_dispatch(
                event=event, gateway=SimpleNamespace(adapters={}), session_store=session_store
            )
        self.assertEqual(result["action"], "allow")
        self.assertEqual(draft_rule.call_args.kwargs["channel"], "抖音")
        self.assertTrue(draft_rule.call_args.kwargs["denies_sending"])

    def test_session_commands_do_not_confirm_drafts(self) -> None:
        binding = {"id": 7, "chat_id": "chat-1", "owner_key": "owner-1", "current_channel": "微信"}
        source = SimpleNamespace(
            platform="feishu", chat_id="chat-1", user_id="owner-1", profile="goutoujunshi"
        )
        session_store = SimpleNamespace(
            get_or_create_session=lambda _: SimpleNamespace(session_id="session-1")
        )
        with patch.dict(
            "os.environ",
            {"GOUTOUJUNSHI_OWNER_ID": "owner-1", "GOUTOUJUNSHI_TOKEN_SECRET": "s" * 48},
        ), patch.object(goutoujunshi.repository, "get_binding", return_value=binding), patch.object(
            goutoujunshi.repository, "recent_context", return_value={}
        ), patch.object(goutoujunshi.repository, "list_user_memory", return_value=[]), patch.object(
            goutoujunshi.repository, "apply_next_message_draft_rule"
        ) as draft_rule:
            event = SimpleNamespace(
                text="/new", source=source, media_urls=[], message_id="message-1", channel_prompt=""
            )
            result = goutoujunshi.pre_gateway_dispatch(
                event=event, gateway=SimpleNamespace(adapters={}), session_store=session_store
            )
        self.assertEqual(result["action"], "allow")
        draft_rule.assert_not_called()

    def test_not_sent_detection_does_not_match_common_unrelated_words(self) -> None:
        self.assertIsNone(goutoujunshi.DRAFT_NOT_SENT.search("我今天没发烧，也没发现异常"))
        self.assertIsNotNone(goutoujunshi.DRAFT_NOT_SENT.search("上一句我还没发"))
        self.assertIsNotNone(goutoujunshi.DRAFT_NOT_SENT.search("回复我改了版本"))

    def test_commit_turn_uses_server_source_and_invalidates_only_other_sessions(self) -> None:
        binding = {
            "id": 7,
            "chat_id": "chat-1",
            "owner_key": "owner-1",
            "current_channel": "微信",
        }
        with goutoujunshi._LOCK:
            goutoujunshi._SESSION_OWNERS["session-1"] = {
                "owner_id": "owner-1",
                "source_ref": "feishu:current",
            }
            goutoujunshi._SESSION_PROMPTS["session-1"] = {
                "prompt": "current",
                "owner_id": "owner-1",
                "relationship_id": 7,
            }
            goutoujunshi._SESSION_PROMPTS["session-2"] = {
                "prompt": "other",
                "owner_id": "owner-1",
                "relationship_id": 7,
            }
        result_payload = {
            "event_ids": [11],
            "confirmed_draft_id": None,
            "draft_id": 12,
            "snapshot_version": None,
            "changed": True,
        }
        with patch.object(goutoujunshi, "_binding_for_tool", return_value=binding), patch.object(
            goutoujunshi.repository, "commit_turn", return_value=result_payload
        ) as commit:
            response = json.loads(
                goutoujunshi.handle_commit_turn(
                    {
                        "binding_token": "token",
                        "events": [{"event_type": "received", "content": "hello", "channel": "微信"}],
                        "draft": {"content": "reply", "channel": "微信"},
                    },
                    task_id="session-1",
                )
            )
        self.assertTrue(response["ok"])
        self.assertEqual(commit.call_args.kwargs["source_ref"], "feishu:current")
        with goutoujunshi._LOCK:
            self.assertIn("session-1", goutoujunshi._SESSION_PROMPTS)
            self.assertNotIn("session-2", goutoujunshi._SESSION_PROMPTS)

    def test_non_owner_is_rejected_in_unbound_group(self) -> None:
        source = SimpleNamespace(
            platform="feishu", chat_id="new-chat", user_id="someone-else", profile="default"
        )
        event = SimpleNamespace(text="hello", source=source, media_urls=[], message_id="message-1")
        with patch.dict("os.environ", {"GOUTOUJUNSHI_OWNER_ID": "owner-1"}):
            result = goutoujunshi.pre_gateway_dispatch(
                event=event, gateway=SimpleNamespace(adapters={})
            )
        self.assertEqual(result, {"action": "skip", "reason": "relationship-owner-only"})

    def test_ephemeral_media_is_registered_then_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            image = home / "cache" / "images" / "shot.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"temporary")
            with patch.dict("os.environ", {"HERMES_HOME": str(home)}):
                goutoujunshi._register_ephemeral_media([str(image)])
                registry = home / "state" / "goutoujunshi-media.json"
                self.assertTrue(registry.exists())
                goutoujunshi._delete_ephemeral_media([str(image)])
                self.assertFalse(image.exists())
                self.assertEqual(registry.read_text(encoding="utf-8"), "[]")

    def test_database_error_fails_closed(self) -> None:
        source = SimpleNamespace(
            platform="feishu", chat_id="chat-1", user_id="owner-1", profile="goutoujunshi"
        )
        event = SimpleNamespace(text="hello", source=source, media_urls=[], message_id="message-1")
        gateway = SimpleNamespace(adapters={})
        with patch.dict("os.environ", {"GOUTOUJUNSHI_OWNER_ID": "owner-1"}), patch.object(
            goutoujunshi.repository, "get_binding", side_effect=RuntimeError("database down")
        ):
            result = goutoujunshi.pre_gateway_dispatch(event=event, gateway=gateway)
        self.assertEqual(result["action"], "skip")
        self.assertEqual(result["reason"], "relationship-fail-closed")

    def test_archived_managed_chat_does_not_fall_back(self) -> None:
        source = SimpleNamespace(
            platform="feishu", chat_id="chat-1", user_id="owner-1", profile="goutoujunshi"
        )
        event = SimpleNamespace(text="hello", source=source, media_urls=[], message_id="message-1")
        gateway = SimpleNamespace(adapters={})
        with patch.object(goutoujunshi.repository, "get_binding", return_value=None), patch.object(
            goutoujunshi.repository, "is_managed_chat", return_value=True
        ):
            result = goutoujunshi.pre_gateway_dispatch(event=event, gateway=gateway)
        self.assertEqual(result["action"], "skip")


if __name__ == "__main__":
    unittest.main()
