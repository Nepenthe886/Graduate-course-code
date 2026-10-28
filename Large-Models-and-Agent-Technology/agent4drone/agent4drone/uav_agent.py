"""
Student baseline UAV agent using LangChain and basic simulator tools.
"""
import argparse
from getpass import getpass
import importlib.util
import json
import re
import sys
import os
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai.chat_models import base as openai_chat_base
from llm_io_recorder import RecordedChatOllama as ChatOllama, RecordedChatOpenAI as ChatOpenAI, LLMIORecorder
from experiment_tracking import ACTIVE_RUN, TOOL_CALL_ID, STORE, guarded_record, create_run, start_run, finish_run, result_artifacts, now

from uav_api_client import UAVAPIClient
from uav_langchain_tools import create_uav_tools
from logging_config import get_logger
from template.system_prompt import build_system_prompt


logger = get_logger(__name__)

NAV_SETTINGS_FILE = Path(__file__).resolve().parent / "nav_settings.json"
NAV_MODULE_ENTRY = Path("tools") / "nav_tools.py"


def _load_nav_settings() -> Dict[str, Any]:
    """读取导航模块注入配置（路径相对本文件目录）。"""
    if not NAV_SETTINGS_FILE.is_file():
        return {"enabled": False}
    try:
        data = json.loads(NAV_SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("无法读取 nav_settings.json: %s", exc)
        return {"enabled": False}
    data.pop("_comment", None)
    return data


def patch_openai_compatible_reasoning_content() -> None:
    """Preserve DeepSeek thinking-mode reasoning_content across tool calls."""
    if getattr(openai_chat_base, "_uav_reasoning_content_patched", False):
        return

    original_to_message = openai_chat_base._convert_dict_to_message
    original_to_dict = openai_chat_base._convert_message_to_dict

    def convert_dict_to_message_with_reasoning(message_dict: Any) -> Any:
        message = original_to_message(message_dict)
        if (
            isinstance(message, AIMessage)
            and isinstance(message_dict, dict)
            and "reasoning_content" in message_dict
        ):
            message.additional_kwargs["reasoning_content"] = message_dict["reasoning_content"]
        return message

    def convert_message_to_dict_with_reasoning(message: Any, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        message_dict = original_to_dict(message, *args, **kwargs)
        if isinstance(message, AIMessage):
            reasoning_content = message.additional_kwargs.get("reasoning_content")
            if reasoning_content is not None:
                message_dict["reasoning_content"] = reasoning_content
        return message_dict

    openai_chat_base._convert_dict_to_message = convert_dict_to_message_with_reasoning
    openai_chat_base._convert_message_to_dict = convert_message_to_dict_with_reasoning
    openai_chat_base._uav_reasoning_content_patched = True


patch_openai_compatible_reasoning_content()


def sanitize_for_log(value: Any, max_length: int = 1000) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = " ".join(text.split())
    if len(text) > max_length:
        return f"{text[:max_length]}..."
    return text


def empty_token_usage() -> Dict[str, int]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "llm_calls": 0,
    }


def int_token(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def token_usage_from_dict(usage: Dict[str, Any]) -> Dict[str, int]:
    prompt_tokens = int_token(usage.get("input_tokens", usage.get("prompt_tokens")))
    completion_tokens = int_token(usage.get("output_tokens", usage.get("completion_tokens")))
    total_tokens = int_token(usage.get("total_tokens"))
    if not total_tokens:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "llm_calls": 1,
    }


def token_usage_from_message(message: Any) -> Optional[Dict[str, int]]:
    usage = getattr(message, "usage_metadata", None)
    if isinstance(usage, dict):
        return token_usage_from_dict(usage)

    response_metadata = getattr(message, "response_metadata", None)
    if isinstance(response_metadata, dict):
        token_usage = response_metadata.get("token_usage")
        if isinstance(token_usage, dict):
            return token_usage_from_dict(token_usage)

    return None


def merge_token_usage(totals: Dict[str, int], usage: Dict[str, int]) -> None:
    totals["prompt_tokens"] += usage["prompt_tokens"]
    totals["completion_tokens"] += usage["completion_tokens"]
    totals["total_tokens"] += usage["total_tokens"]
    totals["llm_calls"] += usage["llm_calls"]


def format_token_usage_for_log(token_usage: Dict[str, Any]) -> str:
    return (
        f"prompt_tokens={int_token(token_usage.get('prompt_tokens'))} "
        f"completion_tokens={int_token(token_usage.get('completion_tokens'))} "
        f"total_tokens={int_token(token_usage.get('total_tokens'))} "
        f"llm_calls={int_token(token_usage.get('llm_calls'))}"
    )


def token_usage_from_llm_result(response: Any) -> Dict[str, int]:
    totals = empty_token_usage()

    llm_output = getattr(response, "llm_output", None)
    if isinstance(llm_output, dict):
        for key in ("token_usage", "usage"):
            usage = llm_output.get(key)
            if isinstance(usage, dict):
                merge_token_usage(totals, token_usage_from_dict(usage))
        if totals["llm_calls"]:
            return totals

    for generation_group in getattr(response, "generations", []) or []:
        for generation in generation_group or []:
            message = getattr(generation, "message", None)
            if message is None:
                continue
            usage = token_usage_from_message(message)
            if usage:
                merge_token_usage(totals, usage)

    return totals


class ToolCallLoggingCallback(BaseCallbackHandler):
    """Associate requests with model tool IDs without altering tool execution."""
    run_inline = True

    def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        TOOL_CALL_ID.set(kwargs.get('tool_call_id') or str(run_id))

    def on_tool_end(self, output, *, run_id, **kwargs):
        TOOL_CALL_ID.set(None)

    def on_tool_error(self, error, *, run_id, **kwargs):
        TOOL_CALL_ID.set(None)


class ProviderTokenLoggingCallback(BaseCallbackHandler):
    def __init__(self, command_id: str, provider: str, model: str):
        self.command_id = command_id
        self.provider = provider
        self.model = model

    def on_llm_end(self, response: Any, **kwargs: Any) -> Any:
        token_usage = token_usage_from_llm_result(response)
        context = ACTIVE_RUN.get()
        if context:
            guarded_record(lambda: STORE.event(context['run_id'], 'llm_completed', token_usage=token_usage,
                           usage_available=bool(token_usage.get('llm_calls'))))
        logger.info(
            "Provider LLM call completed command_id=%s provider=%s model=%s %s",
            self.command_id,
            self.provider,
            self.model,
            format_token_usage_for_log(token_usage),
        )


def load_llm_settings(settings_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Load LLM settings from JSON file.

    Relative paths are resolved against this module's directory so the CLI,
    service and GUI all find the same llm_settings.json regardless of cwd.
    """
    try:
        path = Path(settings_path) if settings_path else Path(__file__).resolve().with_name("llm_settings.json")
        if not path.is_absolute():
            path = Path(__file__).resolve().parent / path
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"Warning: Could not load LLM settings from {settings_path or 'llm_settings.json'}: {e}")
    return None


def bool_setting(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def prompt_user_for_llm_config() -> Dict[str, Any]:
    """Prompt user to select LLM provider and model."""
    settings = load_llm_settings()

    if not settings or "provider_configs" not in settings:
        print("⚠️  No llm_settings.json found or invalid format. Using command line arguments.")
        return {}

    provider_configs = settings["provider_configs"]
    selected_provider = settings.get("selected_provider", "")

    print("\n" + "=" * 60)
    print("🤖 LLM Provider Configuration")
    print("=" * 60)

    providers = list(provider_configs.keys())
    print("\nAvailable providers:")
    for i, provider in enumerate(providers, 1):
        config = provider_configs[provider]
        default_marker = " (selected in settings)" if provider == selected_provider else ""
        print(f"  {i}. {provider}{default_marker}")
        print(f"     Type: {config.get('type', 'unknown')}")
        print(f"     Base URL: {config.get('base_url', 'N/A')}")
        print(f"     Requires API Key: {config.get('requires_api_key', False)}")

    print(f"\nSelect a provider (1-{len(providers)}) [default: {selected_provider or providers[0]}]: ", end="")
    provider_choice = input().strip()

    if not provider_choice:
        chosen_provider = selected_provider if selected_provider in providers else providers[0]
    else:
        try:
            idx = int(provider_choice) - 1
            chosen_provider = providers[idx] if 0 <= idx < len(providers) else (selected_provider or providers[0])
        except ValueError:
            chosen_provider = selected_provider or providers[0]

    config = provider_configs[chosen_provider]
    print(f"\n✅ Selected provider: {chosen_provider}")

    default_models = config.get("default_models", [])
    default_model = config.get("default_model", "")

    if default_models:
        print("\nAvailable models:")
        for i, model in enumerate(default_models, 1):
            default_marker = " (default)" if model == default_model else ""
            print(f"  {i}. {model}{default_marker}")
        print(f"  {len(default_models) + 1}. Custom model (enter manually)")

        print(f"\nSelect a model (1-{len(default_models) + 1}) [default: {default_model}]: ", end="")
        model_choice = input().strip()

        if not model_choice:
            chosen_model = default_model
        else:
            try:
                idx = int(model_choice) - 1
                if 0 <= idx < len(default_models):
                    chosen_model = default_models[idx]
                elif idx == len(default_models):
                    print("Enter custom model name: ", end="")
                    chosen_model = input().strip() or default_model
                else:
                    chosen_model = default_model
            except ValueError:
                chosen_model = default_model
    else:
        print(f"\nEnter model name [default: {default_model}]: ", end="")
        chosen_model = input().strip() or default_model

    print(f"✅ Selected model: {chosen_model}")

    provider_type = config.get("type", "ollama")
    if provider_type == "openai-compatible":
        if "api.openai.com" in config.get("base_url", ""):
            llm_provider = "openai"
        else:
            llm_provider = "openai-compatible"
    else:
        llm_provider = provider_type

    api_key = str(config.get("api_key", "") or "").strip()
    if config.get("requires_api_key", False) and not api_key:
        print("\n⚠️  This provider requires an API key.")
        api_key = getpass("Enter API key (or press Enter to use environment variable): ").strip()

    result = {
        "llm_provider": llm_provider,
        "llm_model": chosen_model,
        "llm_base_url": config.get("base_url"),
        "llm_api_key": api_key if api_key else None,
        "provider_name": chosen_provider,
    }

    print("\n" + "=" * 60)
    print("✅ Configuration complete!")
    print("=" * 60)
    print(f"Provider: {chosen_provider}")
    print(f"Type: {llm_provider}")
    print(f"Model: {chosen_model}")
    print(f"Base URL: {config.get('base_url')}")
    print("=" * 60 + "\n")

    return result


class UAVControlAgent:
    """Intelligent agent for controlling UAVs using natural language."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        uav_api_key: Optional[str] = None,
        llm_provider: str = "ollama",
        llm_model: str = "llama2",
        llm_api_key: Optional[str] = None,
        llm_base_url: Optional[str] = None,
        temperature: float = 0.1,
        verbose: bool = True,
        debug: bool = False,
        toolchain_json_recording: bool = False,
        llm_io_recording: bool = True,
    ):
        self.client = UAVAPIClient(base_url, api_key=uav_api_key)
        self.verbose = verbose
        self.debug = debug
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.max_iterations = 150
        self.max_empty_response_retries = 2
        self.toolchain_json_recording = toolchain_json_recording
        self.llm_io_recording = llm_io_recording
        self.temperature = temperature
        logger.info(
            "Initializing UAVControlAgent provider=%s model=%s base_url=%s",
            llm_provider,
            llm_model,
            base_url,
        )

        if self.debug:
            print("\n" + "=" * 60)
            print("🔧 UAV Agent Initialization - Debug Mode")
            print("=" * 60)
            print(f"UAV API Server: {base_url}")
            print(f"LLM Provider: {llm_provider}")
            print(f"LLM Model: {llm_model}")
            print(f"Temperature: {temperature}")
            print(f"Verbose: {verbose}")
            print()

        if self.debug:
            print("🔌 Testing UAV API connection...")
        session = None
        try:
            session = self.client.get_current_session()
            logger.info(
                "Connected to UAV API session_id=%s task=%s",
                session.get("id"),
                session.get("task"),
            )
            if self.debug:
                print("✅ Connected to UAV API")
                print(f"   Session: {session.get('name', 'Unknown')}")
                print(f"   Task: {session.get('task', 'Unknown')}")
                print()
        except Exception as e:
            logger.warning("Could not connect to UAV API at %s: %s", base_url, e)
            if self.debug:
                print(f"⚠️  Warning: Could not connect to UAV API: {e}")
                print(f"   Make sure the UAV server is running at {base_url}")
                print()

        self.llm = self._create_llm(
            llm_provider=llm_provider,
            llm_model=llm_model,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            temperature=temperature,
        )

        if self.debug:
            print("🔧 Creating UAV control tools...")
        self.tools = self._create_tools()
        logger.info("Created %d UAV control tools", len(self.tools))
        if self.debug:
            print(f"✅ Created {len(self.tools)} tools")
            print(f"   Tools: {', '.join([tool.name for tool in self.tools[:5]])}...")
            print()

        if self.debug:
            print("🤖 Creating modern agent runtime...")
        self.agent = self._create_agent_runtime()
        logger.info("Agent runtime created")
        if self.debug:
            print("✅ Agent runtime created")
            print()

        if self.debug:
            print("🔄 Refreshing session context...")
        self.session_context: Dict[str, Any] = {}
        self.refresh_session_context()

        if self.debug:
            print("=" * 60)
            print("✅ UAV Agent Initialization Complete!")
            print("=" * 60)
            print()

    def _create_llm(
        self,
        llm_provider: str,
        llm_model: str,
        llm_api_key: Optional[str],
        llm_base_url: Optional[str],
        temperature: float,
    ) -> Any:
        if self.debug:
            print(f"🤖 Initializing LLM provider: {llm_provider}")

        if llm_provider == "ollama":
            if self.debug:
                print(f"   Using Ollama with model: {llm_model}")
                print("   Ollama URL: http://localhost:11434 (default)")
            llm = ChatOllama(model=llm_model, temperature=temperature)
            if self.debug:
                print("✅ Ollama LLM initialized")
                print()
            return llm

        if llm_provider not in ["openai", "openai-compatible"]:
            raise ValueError(
                f"Unknown LLM provider: {llm_provider}. "
                "Use 'ollama', 'openai', or 'openai-compatible'"
            )

        if not llm_api_key:
            raise ValueError(
                f"API key is required for {llm_provider} provider. "
                "Use --llm-api-key or set environment variable."
            )

        if llm_provider == "openai":
            final_base_url = llm_base_url or "https://api.openai.com/v1"
            provider_name = "OpenAI"
        else:
            if not llm_base_url:
                raise ValueError("llm_base_url is required for openai-compatible provider")
            final_base_url = llm_base_url
            provider_name = "OpenAI-Compatible API"

        if self.debug:
            print(f"   Provider: {provider_name}")
            print(f"   Base URL: {final_base_url}")
            print(f"   Model: {llm_model}")

        llm = ChatOpenAI(
            model=llm_model,
            temperature=temperature,
            api_key=llm_api_key,
            base_url=final_base_url,
            timeout=120,       # 单次请求 120s 超时，避免无限等待
            max_retries=1,     # 只重试 1 次，失败尽快暴露
        )
        if self.debug:
            print(f"✅ {provider_name} LLM initialized")
            print()
        return llm

    def _create_tools(self) -> List[Any]:
        tools = create_uav_tools(self.client)
        tools.extend(self._create_nav_tools())
        return tools

    def _auto_map_from_session(self) -> Optional[str]:
        """按 UAV 后端当前会话 id 自动选地图（nav_settings.json 的 map 为空/auto 时使用）。"""
        try:
            session = self.client.get_current_session()
        except Exception as exc:  # noqa: BLE001
            logger.warning("无法获取当前会话，导航地图退回默认值: %s", exc)
            return None
        if not isinstance(session, dict):
            return None
        session_id = str(session.get("id") or "").strip()
        if not session_id:
            return None
        sessions_dir = Path(__file__).resolve().parents[2] / "MultiUAV-Plat-v0.46_Windows" / "UAVSessions"
        for path in sorted(sessions_dir.glob(f"*_{session_id}.json")):
            logger.info("导航地图自动跟随当前会话: %s（%s）", session.get("name"), path.name)
            return str(path)
        logger.warning("会话 %s 在 %s 下没有对应地图文件", session_id, sessions_dir)
        return None

    def _create_nav_tools(self) -> List[Any]:
        """注入 Location_amd_Navigation 的定位/导航工具（失败则跳过，不影响主流程）。"""
        settings = _load_nav_settings()
        if not settings.get("enabled"):
            return []
        module_dir = (NAV_SETTINGS_FILE.parent / str(settings.get("module_dir", ""))).resolve()
        entry = module_dir / NAV_MODULE_ENTRY
        if not entry.is_file():
            logger.warning("导航模块入口不存在，跳过注入: %s", entry)
            return []

        spec = importlib.util.spec_from_file_location("_locnav_tools", entry)
        if spec is None or spec.loader is None:
            logger.warning("无法加载导航模块: %s", entry)
            return []
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(module_dir))
        try:
            spec.loader.exec_module(module)
        finally:
            if str(module_dir) in sys.path:
                sys.path.remove(str(module_dir))

        if not hasattr(module, "create_nav_tools"):
            logger.warning("导航模块缺少 create_nav_tools")
            return []

        map_setting = settings.get("map")
        if map_setting and str(map_setting).lower() not in ("auto", "session"):
            map_path = str((NAV_SETTINGS_FILE.parent / str(map_setting)).resolve())
        else:
            map_path = self._auto_map_from_session()
        drone_name = settings.get("drone") or None
        frames = sys.modules.get("src.frames")
        pose_cls = getattr(frames, "Pose", None) if frames is not None else None

        def pose_provider():
            """用实时 UAV API 数据构造导航模块的 Pose。"""
            if pose_cls is None or not drone_name:
                return None
            try:
                drones = self.client.list_drones()
            except Exception:  # noqa: BLE001
                return None
            needle = str(drone_name).strip().lower()
            for drone in drones:
                if (str(drone.get("id", "")).lower() == needle
                        or str(drone.get("name", "")).strip().lower() == needle):
                    position = drone.get("position") or {}
                    return pose_cls(
                        x=float(position.get("x", 0.0)),
                        y=float(position.get("y", 0.0)),
                        z=float(position.get("z", 0.0)),
                        yaw_deg=float(drone.get("heading") or 0.0),
                    )
            return None

        nav_tools = module.create_nav_tools(
            map_path=map_path,
            drone_name=drone_name,
            pose_provider=pose_provider if pose_cls is not None else None,
        )
        if isinstance(nav_tools, list):
            logger.info("已注入导航工具 %d 个（模块 %s）", len(nav_tools), module_dir)
            return list(nav_tools)
        logger.info("导航模块返回函数表（无 langchain），跳过注入")
        return []

    def _build_system_prompt(self) -> str:
        return build_system_prompt(self.tools) + self._context_block()

    def _context_block(self) -> str:
        """注入实时实体清单 + 一个真实 tool_call 示例。

        小模型（8B/4bit）常见问题：忘记先调 list_drones、把显示名当 id。
        这里把事实直接放进 prompt，等价于替模型做掉"查询"这一步。
        """
        lines = ["", "", "CURRENT ENTITY ROSTER (use these ids directly; never pass a display name):"]
        drones = []
        try:
            drones = self.client.list_drones() or []
        except Exception as exc:  # noqa: BLE001
            lines.append(f"- (roster unavailable: {exc})")
        if drones:
            for drone in drones[:20]:
                pos = drone.get("position") or {}
                lines.append(
                    "- drone name={name!r} id={id} status={status} battery={battery} position=({x},{y},{z})".format(
                        name=drone.get("name"),
                        id=drone.get("id"),
                        status=drone.get("status"),
                        battery=drone.get("battery_level"),
                        x=pos.get("x"), y=pos.get("y"), z=pos.get("z"),
                    )
                )
            example_id = drones[0].get("id")
            example = (
                '<tool_call>\n'
                '{"name": "take_off", "arguments": {"input_json": '
                '"{\\"drone_id\\": \\"%s\\", \\"altitude\\": 15.0}"}}\n'
                '</tool_call>' % example_id
            )
            lines.extend([
                "",
                "EXAMPLE (correct format; tool arguments are a JSON *string*):",
                example,
                "",
                "ENFORCED RULES:",
                "1. Use the exact id from the roster above for every drone_id argument.",
                "2. After each tool call, read the tool result before deciding the next step.",
                "3. Verify with get_drone_status / get_nearby_entities before writing [TASK DONE].",
            ])
        return "\n".join(lines)

    def _create_agent_runtime(self) -> Any:
        prompt = self._build_system_prompt()
        if ACTIVE_RUN.get():
            import hashlib
            guarded_record(lambda: STORE.save(ACTIVE_RUN.get()['run_id'], prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest()))
        return create_agent(
            model=self.llm,
            tools=self.tools,
            system_prompt=prompt,
        )

    def _recursion_limit_for_steps(self) -> int:
        """Allow enough graph turns to complete the configured number of tool steps."""
        return max(25, self.max_iterations * 3)

    def refresh_session_context(self) -> None:
        """Refresh session context information."""
        try:
            session = self.client.get_current_session()
            self.session_context = {
                "session_id": session.get("id"),
                "task_type": session.get("task"),
                "task_description": session.get("task_description"),
                "status": session.get("status"),
            }
        except Exception as e:
            logger.warning("Could not refresh session context: %s", e)
            if self.verbose:
                print(f"Warning: Could not refresh session context: {e}")

    def get_session_summary(self) -> str:
        """Get a summary of the current session."""
        try:
            session = self.client.get_current_session()
            progress = self.client.get_task_progress()
            drones = self.client.list_drones()

            summary = f"""
=== Current Session Summary ===
Session: {session.get('name', 'Unknown')}
Task: {session.get('task', 'Unknown')} - {session.get('task_description', '')}
Status: {session.get('status', 'Unknown')}

Progress: {progress.get('progress_percentage', 0)}% ({progress.get('status_message', 'Unknown')})
Completed: {progress.get('is_completed', False)}

Drones: {len(drones)} available
"""
            for drone in drones:
                summary += (
                    f"  - {drone.get('name')} ({drone.get('id')}): {drone.get('status')}, "
                    f"Battery: {drone.get('battery_level', 0):.1f}%\n"
                )
            return summary.strip()
        except Exception as e:
            return f"Error getting session summary: {e}"

    def _normalize_tool_input(self, args: Any) -> Any:
        if isinstance(args, dict) and "input_json" in args and len(args) == 1:
            raw = args.get("input_json")
            if isinstance(raw, str):
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return raw
        return args

    def _message_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if text:
                        parts.append(str(text))
            return "\n".join(part for part in parts if part).strip()
        if content is None:
            return ""
        return str(content)

    def _extract_intermediate_steps(self, messages: List[Any]) -> List[Any]:
        steps: List[Any] = []
        pending: Dict[str, Any] = {}

        for message in messages:
            tool_calls = getattr(message, "tool_calls", None) or []
            if tool_calls:
                log_text = self._message_text(getattr(message, "content", ""))
                for tool_call in tool_calls:
                    tool_name = tool_call.get("name", "unknown_tool")
                    tool_input = self._normalize_tool_input(tool_call.get("args"))
                    action = SimpleNamespace(
                        tool=tool_name,
                        tool_input=tool_input,
                        log=log_text or f"Preparing to call tool '{tool_name}'",
                        message_log=None,
                    )
                    pending[tool_call.get("id", f"{tool_name}-{len(steps)}")] = action

            tool_call_id = getattr(message, "tool_call_id", None)
            if tool_call_id and tool_call_id in pending:
                action = pending.pop(tool_call_id)
                observation = self._message_text(getattr(message, "content", ""))
                steps.append((action, observation))

        for action in pending.values():
            steps.append((action, ""))

        return steps

    @staticmethod
    def _empty_token_usage() -> Dict[str, int]:
        return empty_token_usage()

    @staticmethod
    def _int_token(value: Any) -> int:
        return int_token(value)

    def _token_usage_from_message(self, message: Any) -> Optional[Dict[str, int]]:
        return token_usage_from_message(message)

    def _extract_token_usage(self, messages: List[Any]) -> Dict[str, int]:
        totals = self._empty_token_usage()
        for message in messages:
            usage = self._token_usage_from_message(message)
            if not usage:
                continue
            merge_token_usage(totals, usage)
        return totals

    def _extract_tool_names(self, steps: List[Any]) -> List[str]:
        tool_names: List[str] = []
        for step in steps:
            action = step[0] if isinstance(step, (list, tuple)) and step else step
            tool_name = getattr(action, "tool", None)
            if tool_name:
                tool_names.append(str(tool_name))
        return tool_names

    def _ingest_stream_messages(
        self,
        messages: List[Any],
        pending: Dict[str, Any],
        steps: List[Any],
    ) -> bool:
        updated = False

        for message in messages:
            tool_calls = getattr(message, "tool_calls", None) or []
            if tool_calls:
                log_text = self._message_text(getattr(message, "content", ""))
                for tool_call in tool_calls:
                    tool_call_id = tool_call.get("id", f"{tool_call.get('name', 'tool')}-{len(steps)}")
                    if tool_call_id in pending:
                        continue
                    tool_name = tool_call.get("name", "unknown_tool")
                    tool_input = self._normalize_tool_input(tool_call.get("args"))
                    pending[tool_call_id] = SimpleNamespace(
                        tool=tool_name,
                        tool_input=tool_input,
                        log=log_text or f"Preparing to call tool '{tool_name}'",
                        message_log=None,
                    )
                    steps.append((pending[tool_call_id], ""))
                    updated = True

            tool_call_id = getattr(message, "tool_call_id", None)
            if tool_call_id and tool_call_id in pending:
                observation = self._message_text(getattr(message, "content", ""))
                for idx in range(len(steps) - 1, -1, -1):
                    action, existing_observation = steps[idx]
                    if action is pending[tool_call_id]:
                        if existing_observation != observation:
                            steps[idx] = (action, observation)
                            updated = True
                        break

        return updated

    def _stream_updates_to_steps(
        self,
        command: str,
        config: Dict[str, Any],
        step_callback: Optional[Any],
    ) -> Dict[str, Any]:
        pending: Dict[str, Any] = {}
        steps: List[Any] = []
        collected_messages: List[Any] = []
        seen_message_keys: set[str] = set()
        limit_reached = False

        def message_key(message: Any) -> str:
            message_id = getattr(message, "id", None)
            if message_id:
                return str(message_id)
            tool_call_id = getattr(message, "tool_call_id", None)
            content = self._message_text(getattr(message, "content", ""))
            tool_calls = getattr(message, "tool_calls", None) or []
            return f"{message.__class__.__name__}:{tool_call_id}:{content}:{tool_calls}"

        try:
            for chunk in self.agent.stream(
                {"messages": [{"role": "user", "content": command}]},
                config=config,
                stream_mode="updates",
            ):
                for data in chunk.values():
                    messages = data.get("messages", [])
                    for message in messages:
                        key = message_key(message)
                        if key not in seen_message_keys:
                            seen_message_keys.add(key)
                            collected_messages.append(message)
                    if self._ingest_stream_messages(messages, pending, steps) and step_callback:
                        step_callback(list(steps))
                    if len(steps) >= self.max_iterations:
                        limit_reached = True
                        break
                if limit_reached:
                    break
        except Exception as e:
            return {
                "messages": collected_messages,
                "intermediate_steps": list(steps),
                "error": e,
            }

        return {
            "messages": collected_messages,
            "intermediate_steps": list(steps),
            "stopped_due_to_step_limit": limit_reached,
        }

    def _extract_final_output(self, messages: List[Any]) -> str:
        for message in reversed(messages):
            if getattr(message, "tool_call_id", None):
                continue
            if getattr(message, "tool_calls", None):
                continue
            content = self._message_text(getattr(message, "content", ""))
            if content:
                return content
        return ""

    def _last_observation_preview(self, steps: List[Any], max_length: int = 500) -> str:
        if not steps:
            return ""
        last_step = steps[-1]
        observation = ""
        if isinstance(last_step, (list, tuple)) and len(last_step) >= 2:
            observation = self._message_text(last_step[1])
        else:
            observation = self._message_text(last_step)
        return sanitize_for_log(observation, max_length=max_length)

    def _last_response_message(self, messages: List[Any]) -> Optional[Any]:
        for message in reversed(messages):
            if getattr(message, "tool_call_id", None):
                continue
            return message
        return None

    def _message_finish_reason(self, message: Any) -> Optional[str]:
        response_metadata = getattr(message, "response_metadata", None)
        if isinstance(response_metadata, dict):
            for key in ("finish_reason", "stop_reason"):
                if response_metadata.get(key):
                    return str(response_metadata[key])
            message_info = response_metadata.get("message")
            if isinstance(message_info, dict) and message_info.get("finish_reason"):
                return str(message_info["finish_reason"])
        additional_kwargs = getattr(message, "additional_kwargs", None)
        if isinstance(additional_kwargs, dict):
            for key in ("finish_reason", "stop_reason"):
                if additional_kwargs.get(key):
                    return str(additional_kwargs[key])
        return None

    def _log_empty_terminal_response(
        self,
        command_id: str,
        messages: List[Any],
        steps: List[Any],
        token_usage: Dict[str, Any],
        retry_index: int,
    ) -> None:
        message = self._last_response_message(messages)
        response_metadata = getattr(message, "response_metadata", None) if message is not None else None
        logger.warning(
            "Agent returned empty terminal response command_id=%s retry=%d message_class=%s "
            "content=\"%s\" tool_call_count=%d finish_reason=%s response_metadata=\"%s\" "
            "steps=%d %s last_observation=\"%s\"",
            command_id,
            retry_index,
            message.__class__.__name__ if message is not None else None,
            sanitize_for_log(self._message_text(getattr(message, "content", ""))) if message is not None else "",
            len(getattr(message, "tool_calls", None) or []) if message is not None else 0,
            self._message_finish_reason(message) if message is not None else None,
            sanitize_for_log(response_metadata, max_length=500) if response_metadata is not None else "",
            len(steps),
            format_token_usage_for_log(token_usage),
            self._last_observation_preview(steps),
        )

    def _empty_response_recovery_command(
        self,
        original_command: str,
        steps: List[Any],
        retry_index: int,
    ) -> str:
        tool_names = self._extract_tool_names(steps)
        recent_tools = ", ".join(tool_names[-10:]) if tool_names else "none"
        last_observation = self._last_observation_preview(steps, max_length=1200) or "none"
        return (
            "Continue the UAV mission after an empty assistant response.\n"
            "Do not restart or repeat completed actions unless current state verification proves it is required.\n"
            "Check the current drone/session state, continue from the last completed observation, and finish only "
            "when the mission is complete.\n"
            "The final answer must end with [TASK DONE].\n\n"
            f"Original command:\n{original_command}\n\n"
            f"Recovery attempt: {retry_index}\n"
            f"Completed tool step count: {len(steps)}\n"
            f"Recent tools: {recent_tools}\n"
            f"Last completed observation: {last_observation}"
        )

    def _incomplete_output_reason(self, output: str) -> Optional[str]:
        text = (output or "").strip()
        if not text:
            return "Agent stopped without a final response; task completion was not verified."
        compact = re.sub(r"[^A-Z]", "", text.upper())
        if "TASKDONE" in compact or "任务完成" in text:
            return None
        if "TASKFAILED" in compact or "TASKFAIL" in compact:
            return "Agent reported the task as FAILED; task completion was not verified."
        return "Agent final response did not include [TASK DONE]; task completion was not verified."

    def _runtime_metadata_for_toolchain(self, empty_response_retries: int) -> Dict[str, Any]:
        session_context = getattr(self, "session_context", {}) or {}
        session_id = session_context.get("session_id")
        context = ACTIVE_RUN.get() or {}
        return {
            "run_id": context.get('run_id'),
            "experiment": context.get('experiment'),
            "provider": getattr(self, "llm_provider", None),
            "model": getattr(self, "llm_model", None),
            "uav_base_url": getattr(getattr(self, "client", None), "base_url", None),
            "session_id": session_id,
            "task_type": session_context.get("task_type"),
            "task_description": session_context.get("task_description"),
            "max_iterations": getattr(self, "max_iterations", None),
            "empty_response_retries": empty_response_retries,
        }

    def _record_toolchain_if_enabled(
        self,
        result: Dict[str, Any],
        *,
        command_id: str,
        command: str,
        started_at: str,
        completed_at: str,
        status: str,
        empty_response_retries: int,
        tool_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        if not getattr(self, "toolchain_json_recording", False):
            stop_recording = getattr(getattr(self, "client", None), "stop_api_call_recording", None)
            if callable(stop_recording):
                stop_recording()
            return result

        try:
            from toolchain_recorder import (
                tool_chain_from_steps,
                write_toolchain_json,
            )

            steps = result.get("intermediate_steps", [])
            ordered_tool_names = tool_names if tool_names is not None else self._extract_tool_names(steps)
            get_api_calls = getattr(getattr(self, "client", None), "get_api_call_records", None)
            api_calls = get_api_calls() if callable(get_api_calls) else []
            record = {
                "schema_version": 3,
                "command_id": command_id,
                "started_at": started_at,
                "completed_at": completed_at,
                "status": status,
                "success": bool(result.get("success", False)),
                "command": command,
                "output": str(result.get("output", "")),
                "token_usage": result.get("token_usage") or self._empty_token_usage(),
                "token_usage_available": bool((result.get("token_usage") or {}).get("llm_calls")),
                "runtime": self._runtime_metadata_for_toolchain(empty_response_retries),
                "tool_names": ordered_tool_names,
                "tool_chain": tool_chain_from_steps(steps),
                "api_calls": api_calls,
            }
            context = ACTIVE_RUN.get()
            run_id = context['run_id'] if context else 'rid-' + str(uuid.uuid4())
            if not context:
                STORE.save(run_id, experiment={'experiment_id': STORE.default_batch_id, 'run_id': run_id},
                           command_id=command_id, command=command, started_at=started_at,
                           execution_finished_at=completed_at, execution_status=status)
                (STORE.path(run_id) / 'requests.jsonl').touch(exist_ok=True)
                for call in api_calls:
                    STORE.append(run_id, 'requests.jsonl', call)
            directory = STORE.path(run_id)
            record.pop('api_calls', None)
            record['runtime']['run_id'] = run_id
            record['runtime']['experiment'] = STORE.read(run_id)['experiment']
            record['request_history_file'] = 'requests.jsonl'
            record['api_call_count'] = context.get('request_count', 0) if context else len(api_calls)
            path = write_toolchain_json(record, directory)
            STORE.save(run_id, toolchain_file='toolchain.json', toolchain_runtime=record['runtime'])
            return {**result, 'tool_chain_file': str(path), 'request_history_file': str(directory / 'requests.jsonl')}
        except Exception as e:
            if ACTIVE_RUN.get():
                ACTIVE_RUN.get().setdefault("recording_errors", []).append(str(e))
            logger.warning("Could not write tool-chain JSON command_id=%s error=%s", command_id, e)
            return result
        finally:
            stop_recording = getattr(getattr(self, "client", None), "stop_api_call_recording", None)
            if callable(stop_recording):
                stop_recording()

    def _run_agent_once(
        self,
        command: str,
        config: Dict[str, Any],
        step_callback: Optional[Any],
    ) -> Dict[str, Any]:
        if step_callback:
            return self._stream_updates_to_steps(command, config, step_callback)
        return self.agent.invoke(
            {"messages": [{"role": "user", "content": command}]},
            config=config,
        )

    def execute(
        self,
        command: str,
        callbacks: Optional[List[Any]] = None,
        step_callback: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Record the existing execution; preserve its result and exception policy."""
        context = ACTIVE_RUN.get()
        token = None
        tool_token = TOOL_CALL_ID.set(None)
        recorder = None
        result = None
        failure = None
        enabled = getattr(self, 'llm_io_recording', False)
        if context is None and (enabled or getattr(self, 'toolchain_json_recording', False)):
            context = guarded_record(lambda: create_run(command))
            if context:
                token = ACTIVE_RUN.set(context)
                start_run(context)
        try:
            if context:
                runtime = self._runtime_metadata_for_toolchain(0)
                runtime['temperature'] = getattr(self, 'temperature', None)
                import hashlib
                for name in ('uav_agent.py', 'uav_api_client.py', 'uav_langchain_tools.py', 'agent_api_service.py', 'template/system_prompt.py'):
                    guarded_record(lambda name=name: runtime.update({name + '_sha256': hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()}), context)
                guarded_record(lambda: STORE.save(context['run_id'], runtime=runtime), context)
                if enabled:
                    recorder = guarded_record(lambda: LLMIORecorder(context, self.llm_provider, self.llm_model), context)
                else:
                    guarded_record(lambda: STORE.save(context['run_id'], llm_io_recording=False, llm_recording_status='disabled'), context)
                context['llm_recorder'] = recorder
            run_callbacks = list(callbacks or [])
            if context:
                run_callbacks.append(ToolCallLoggingCallback())
            if recorder:
                run_callbacks.append(recorder)
            result = self._execute(command, callbacks=run_callbacks, step_callback=step_callback)
        except BaseException as exc:
            failure = exc
            raise
        finally:
            if recorder:
                guarded_record(recorder.close, context)
            if context:
                context.pop('llm_recorder', None)
            if token is not None:
                finish_run(context, result, 'failed' if failure else 'completed', failure)
                ACTIVE_RUN.reset(token)
            TOOL_CALL_ID.reset(tool_token)
        return result_artifacts(result, context) if context else result

    def _execute(
        self,
        command: str,
        callbacks: Optional[List[Any]] = None,
        step_callback: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Execute a natural language command.

        Returns:
            Dictionary with 'output', 'intermediate_steps', and 'success' keys.
        """
        command_id = (ACTIVE_RUN.get() or {}).get("command_id") or str(uuid.uuid4())
        started_at = now()
        start_api_recording = getattr(getattr(self, "client", None), "start_api_call_recording", None)
        stop_api_recording = getattr(getattr(self, "client", None), "stop_api_call_recording", None)
        if getattr(self, "toolchain_json_recording", False):
            if callable(start_api_recording):
                start_api_recording()
        elif callable(stop_api_recording):
            stop_api_recording()
        sanitized_command = sanitize_for_log(command)
        logger.info(
            "Command started command_id=%s provider=%s model=%s command_length=%d command=\"%s\"",
            command_id,
            self.llm_provider,
            self.llm_model,
            len(command),
            sanitized_command,
        )
        if self.debug:
            print(f"\n{'=' * 60}")
            print("🎯 Executing Command")
            print(f"{'=' * 60}")
            print(f"Command: {command}")
            print(f"{'=' * 60}\n")

        try:
            self.refresh_session_context()
            self.tools = self._create_tools()
            self.agent = self._create_agent_runtime()

            if self.debug:
                print("🔄 Invoking modern agent runtime...")

            config: Dict[str, Any] = {"recursion_limit": self._recursion_limit_for_steps()}
            provider_token_callback = ProviderTokenLoggingCallback(
                command_id=command_id,
                provider=self.llm_provider,
                model=self.llm_model,
            )
            config["callbacks"] = [provider_token_callback]
            if callbacks:
                config["callbacks"].extend(callbacks)

            all_intermediate_steps: List[Any] = []
            total_token_usage = self._empty_token_usage()
            empty_response_retries = 0
            current_command = command
            max_attempts = self.max_empty_response_retries + 1

            for attempt_index in range(max_attempts):
                recorder = (ACTIVE_RUN.get() or {}).get('llm_recorder')
                if recorder:
                    recorder.attempt = attempt_index + 1
                if attempt_index > 0:
                    self.agent = self._create_agent_runtime()
                    empty_response_retries += 1

                def attempt_step_callback(attempt_steps: List[Any]) -> None:
                    if step_callback:
                        step_callback(all_intermediate_steps + list(attempt_steps))

                result = self._run_agent_once(
                    current_command,
                    config,
                    attempt_step_callback if step_callback else None,
                )

                messages = result.get("messages", [])
                output = self._extract_final_output(messages)
                attempt_steps = self._extract_intermediate_steps(messages)
                if step_callback and not attempt_steps:
                    attempt_steps = result.get("intermediate_steps", [])
                all_intermediate_steps.extend(attempt_steps)
                token_usage = self._extract_token_usage(messages)
                merge_token_usage(total_token_usage, token_usage)
                tool_names = self._extract_tool_names(all_intermediate_steps)

                stream_error = result.get("error")
                if stream_error is not None:
                    logger.error(
                        "Command failed command_id=%s error=%s %s tools=%s",
                        command_id,
                        stream_error,
                        format_token_usage_for_log(total_token_usage),
                        tool_names,
                    )
                    result_payload = {
                        "success": False,
                        "output": f"Error executing command: {str(stream_error)}",
                        "intermediate_steps": all_intermediate_steps,
                        "token_usage": total_token_usage,
                        "empty_response_retries": empty_response_retries,
                    }
                    return self._record_toolchain_if_enabled(
                        result_payload,
                        command_id=command_id,
                        command=command,
                        started_at=started_at,
                        completed_at=now(),
                        status="stream_error",
                        empty_response_retries=empty_response_retries,
                        tool_names=tool_names,
                    )

                if result.get("stopped_due_to_step_limit") or len(all_intermediate_steps) >= self.max_iterations:
                    logger.warning(
                        "Command stopped at step limit command_id=%s steps=%d %s tools=%s",
                        command_id,
                        len(all_intermediate_steps),
                        format_token_usage_for_log(total_token_usage),
                        tool_names,
                    )
                    result_payload = {
                        "success": False,
                        "output": (
                            output
                            or f"Stopped after reaching the maximum of {self.max_iterations} ReAct tool step(s)."
                        ),
                        "intermediate_steps": all_intermediate_steps,
                        "token_usage": total_token_usage,
                        "empty_response_retries": empty_response_retries,
                    }
                    return self._record_toolchain_if_enabled(
                        result_payload,
                        command_id=command_id,
                        command=command,
                        started_at=started_at,
                        completed_at=now(),
                        status="step_limit",
                        empty_response_retries=empty_response_retries,
                        tool_names=tool_names,
                    )

                incomplete_reason = self._incomplete_output_reason(output)
                if incomplete_reason:
                    if not output.strip() and attempt_index < max_attempts - 1:
                        self._log_empty_terminal_response(
                            command_id,
                            messages,
                            all_intermediate_steps,
                            total_token_usage,
                            empty_response_retries,
                        )
                        current_command = self._empty_response_recovery_command(
                            command,
                            all_intermediate_steps,
                            empty_response_retries + 1,
                        )
                        continue

                    logger.warning(
                        "Command stopped without verified completion command_id=%s steps=%d reason=%s retries=%d %s tools=%s last_observation=\"%s\"",
                        command_id,
                        len(all_intermediate_steps),
                        incomplete_reason,
                        empty_response_retries,
                        format_token_usage_for_log(total_token_usage),
                        tool_names,
                        self._last_observation_preview(all_intermediate_steps),
                    )
                    result_payload = {
                        "success": False,
                        "output": (
                            f"{output.strip()}\n\n[verification] {incomplete_reason}"
                            if output and output.strip() else incomplete_reason
                        ),
                        "intermediate_steps": all_intermediate_steps,
                        "token_usage": total_token_usage,
                        "empty_response_retries": empty_response_retries,
                    }
                    return self._record_toolchain_if_enabled(
                        result_payload,
                        command_id=command_id,
                        command=command,
                        started_at=started_at,
                        completed_at=now(),
                        status="failed",
                        empty_response_retries=empty_response_retries,
                        tool_names=tool_names,
                    )

                intermediate_steps = all_intermediate_steps
                token_usage = total_token_usage
                break
            else:
                intermediate_steps = all_intermediate_steps
                token_usage = total_token_usage
                output = ""

            if step_callback:
                step_callback(intermediate_steps)

            if self.debug:
                print(f"\n{'=' * 60}")
                print("✅ Command Execution Complete")
                print(f"{'=' * 60}")
                print("Success: True")
                print(f"Intermediate steps: {len(intermediate_steps)}")
                print(f"{'=' * 60}\n")

            logger.info(
                "Command completed command_id=%s success=True steps=%d retries=%d %s tools=%s",
                command_id,
                len(intermediate_steps),
                empty_response_retries,
                format_token_usage_for_log(token_usage),
                tool_names,
            )
            result_payload = {
                "success": True,
                "output": output,
                "intermediate_steps": intermediate_steps,
                "token_usage": token_usage,
                "empty_response_retries": empty_response_retries,
            }
            return self._record_toolchain_if_enabled(
                result_payload,
                command_id=command_id,
                command=command,
                started_at=started_at,
                completed_at=now(),
                status="completed",
                empty_response_retries=empty_response_retries,
                tool_names=tool_names,
            )
        except Exception as e:
            logger.exception("Command failed command_id=%s error=%s", command_id, e)
            if self.debug:
                print(f"\n{'=' * 60}")
                print("❌ Command Execution Failed")
                print(f"{'=' * 60}")
                print(f"Error: {str(e)}")
                print(f"{'=' * 60}\n")

            result_payload = {
                "success": False,
                "output": f"Error executing command: {str(e)}",
                "intermediate_steps": [],
                "token_usage": self._empty_token_usage(),
            }
            return self._record_toolchain_if_enabled(
                result_payload,
                command_id=command_id,
                command=command,
                started_at=started_at,
                completed_at=now(),
                status="exception",
                empty_response_retries=0,
                tool_names=[],
            )

    def run_interactive(self) -> None:
        """Run the agent in interactive mode."""
        print("\n" + "=" * 60)
        print("🚁 UAV Control Agent - Interactive Mode")
        print("=" * 60)
        print("\nType 'quit', 'exit', or 'q' to stop")
        print("Type 'status' to see session summary")
        print("Type 'help' for example commands\n")

        print(self.get_session_summary())
        print("\n" + "-" * 60 + "\n")

        while True:
            try:
                user_input = input("\n🎮 Command: ").strip()

                if not user_input:
                    continue
                if user_input.lower() in ["quit", "exit", "q"]:
                    print("\n👋 Goodbye!")
                    break
                if user_input.lower() == "status":
                    print(self.get_session_summary())
                    continue
                if user_input.lower() == "help":
                    self._print_help()
                    continue

                print("\n🤖 Processing...\n")
                result = self.execute(user_input)
                if result["success"]:
                    print(f"\n✅ {result['output']}\n")
                else:
                    print(f"\n❌ {result['output']}\n")
            except KeyboardInterrupt:
                print("\n\n👋 Goodbye!")
                break
            except Exception as e:
                print(f"\n❌ Error: {e}\n")

    def _print_help(self) -> None:
        help_text = """
Example Commands:
==================

Information:
- "What drones are available?"
- "Show me the current mission status"
- "What targets do I need to visit?"
- "Check the weather conditions"
- "What's the task progress?"

Basic Control:
- "Take off drone-abc123 to 15 meters"
- "Move drone-abc123 to coordinates x=100, y=50, z=20"
- "Land drone-abc123"
- "Return all drones home"

Mission Execution:
- "Visit all targets with the first drone"
- "Search the area with available drones"
- "Complete the mission task"
- "Patrol the assigned areas"

Safety:
- "Check if there are obstacles between (0,0,10) and (100,100,10)"
- "What's nearby drone-abc123?"
- "Check battery levels"

Smart Commands:
- "Take photos at all target locations"
- "Charge any drones with low battery"
- "Survey all targets and return home"
"""
        print(help_text)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="UAV Control Agent - Natural Language Drone Control"
    )
    parser.add_argument("--base-url", default="http://localhost:8000", help="UAV API base URL")
    parser.add_argument(
        "--uav-api-key",
        default=None,
        help="API key for UAV server (defaults to USER role if not provided, or set UAV_API_KEY env var)",
    )
    parser.add_argument(
        "--llm-provider",
        default=None,
        choices=["ollama", "openai", "openai-compatible"],
        help="LLM provider (ollama, openai, or openai-compatible for DeepSeek, etc.)",
    )
    parser.add_argument(
        "--llm-model",
        default=None,
        help="LLM model name (e.g., llama2, gpt-4o-mini, deepseek-chat)",
    )
    parser.add_argument(
        "--llm-api-key",
        default=None,
        help="API key for LLM provider (or set via environment variable)",
    )
    parser.add_argument(
        "--llm-base-url",
        default=None,
        help="Custom base URL for LLM API (required for openai-compatible providers)",
    )
    parser.add_argument("--temperature", type=float, default=0.1, help="LLM temperature (0.0-1.0)")
    parser.add_argument("--command", "-c", default=None, help="Single command to execute (non-interactive)")
    parser.add_argument("--quiet", "-q", action="store_true", help="Reduce verbosity")
    parser.add_argument("--debug", "-d", action="store_true", help="Enable debug output for connection and setup info")
    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="Skip interactive provider/model selection (use command line args or defaults)",
    )

    args = parser.parse_args()

    should_prompt = (
        not args.no_prompt
        and not args.command
        and args.llm_provider is None
        and args.llm_model is None
    )

    if should_prompt:
        config = prompt_user_for_llm_config()
        if config:
            llm_provider = config.get("llm_provider", "ollama")
            llm_model = config.get("llm_model", "llama2")
            llm_base_url = config.get("llm_base_url")
            llm_api_key = config.get("llm_api_key")
        else:
            llm_provider = "ollama"
            llm_model = "llama2"
            llm_base_url = None
            llm_api_key = None
    else:
        llm_provider = args.llm_provider or "ollama"
        llm_model = args.llm_model or "llama2"
        llm_base_url = args.llm_base_url
        llm_api_key = args.llm_api_key

    if not llm_api_key:
        llm_api_key = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")

    uav_api_key = args.uav_api_key or os.getenv("UAV_API_KEY")
    settings_for_agent = load_llm_settings() or {}
    toolchain_json_recording = bool_setting(
        settings_for_agent.get("toolchain_json_recording"),
        default=False,
    )

    try:
        agent = UAVControlAgent(
            base_url=args.base_url,
            uav_api_key=uav_api_key,
            llm_provider=llm_provider,
            llm_model=llm_model,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            temperature=args.temperature,
            verbose=not args.quiet,
            debug=args.debug,
            toolchain_json_recording=toolchain_json_recording,
            llm_io_recording=bool_setting(settings_for_agent.get("llm_io_recording"), default=True),
        )
    except Exception as e:
        print(f"❌ Failed to create agent: {e}")
        print("\nMake sure:")
        print("  - The required LangChain packages are installed")
        print("  - Ollama is running (if using --llm-provider ollama)")
        print("  - OPENAI_API_KEY is set (if using --llm-provider openai)")
        print("  - UAV API server is accessible")
        return 1

    if args.command:
        result = agent.execute(args.command)
        print(result["output"])
        return 0 if result["success"] else 1

    agent.run_interactive()
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
