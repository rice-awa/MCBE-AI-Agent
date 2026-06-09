"""运行时 Harness 提示注入测试。"""

from config.settings import Settings
from services.agent.harness import render_runtime_harness_prompt
from services.agent.prompt import PromptManager, PromptTemplate, TOOL_USAGE_GUIDE


def test_runtime_harness_prompt_renders_decision_tree_and_cards() -> None:
    prompt = render_runtime_harness_prompt()

    assert "工具意图决策" in prompt
    assert "工具卡片" in prompt
    assert "run_minecraft_command [改变世界/高]" in prompt


def test_system_prompt_uses_runtime_harness_by_default() -> None:
    manager = PromptManager()

    prompt = manager.build_system_prompt(
        connection_id="runtime-harness-default",
        player_name="TestPlayer",
        provider="deepseek",
        model="deepseek-chat",
        settings=Settings(),
    )

    assert "工具意图决策" in prompt
    assert "run_minecraft_command [改变世界/高]" in prompt


def test_system_prompt_falls_back_to_old_tool_usage_guide() -> None:
    manager = PromptManager()
    settings = Settings(runtime_harness_prompt_enabled=False)

    prompt = manager.build_system_prompt(
        connection_id="runtime-harness-disabled",
        player_name="TestPlayer",
        provider="deepseek",
        model="deepseek-chat",
        settings=settings,
    )

    assert TOOL_USAGE_GUIDE in prompt
    assert "工具意图决策" not in prompt


def test_custom_template_receives_runtime_harness_tool_usage() -> None:
    manager = PromptManager()
    manager.register_template(
        PromptTemplate(
            name="runtime-custom",
            description="自定义运行时 Harness 模板",
            content="自定义前缀\n{tool_usage}\n玩家={player_name}",
        )
    )
    manager.set_session_template("runtime-harness-custom", "TestPlayer", "runtime-custom")

    prompt = manager.build_system_prompt(
        connection_id="runtime-harness-custom",
        player_name="TestPlayer",
        provider="deepseek",
        model="deepseek-chat",
        settings=Settings(),
    )

    assert prompt.startswith("自定义前缀")
    assert "工具意图决策" in prompt
    assert "玩家=TestPlayer" in prompt
