"""测试提示词模板功能"""

import pytest
from datetime import datetime


class TestPromptTemplate:
    """测试提示词模板数据结构"""

    def test_prompt_template_creation(self):
        """测试 PromptTemplate 创建"""
        from services.agent.prompt import PromptTemplate

        template = PromptTemplate(
            name="test",
            description="测试模板",
            content="Hello {player_name}",
            variables={"custom_greeting": "你好"},
        )

        assert template.name == "test"
        assert template.description == "测试模板"
        assert template.content == "Hello {player_name}"
        assert template.variables["custom_greeting"] == "你好"


class TestBuiltinTemplates:
    """测试内置模板"""

    def test_builtin_templates_exist(self):
        """测试内置模板存在"""
        from services.agent.prompt import BUILTIN_TEMPLATES

        assert "default" in BUILTIN_TEMPLATES
        assert "concise" in BUILTIN_TEMPLATES
        assert "detailed" in BUILTIN_TEMPLATES

    def test_default_template_content(self):
        """测试默认模板内容包含必要变量"""
        from services.agent.prompt import BUILTIN_TEMPLATES

        template = BUILTIN_TEMPLATES["default"]
        assert "{player_name}" in template.content
        assert "{provider}" in template.content
        assert "{model}" in template.content
        assert "{tool_usage}" in template.content

    def test_concise_template_content(self):
        """测试简洁模板内容"""
        from services.agent.prompt import BUILTIN_TEMPLATES

        template = BUILTIN_TEMPLATES["concise"]
        assert "{player_name}" in template.content
        assert "{tool_usage}" in template.content

    def test_detailed_template_content(self):
        """测试详细模板内容"""
        from services.agent.prompt import BUILTIN_TEMPLATES

        template = BUILTIN_TEMPLATES["detailed"]
        assert "{player_name}" in template.content
        assert "{server_time}" in template.content
        assert "{context_length}" in template.content
        assert "{tool_usage}" in template.content


class TestPromptManager:
    """测试提示词管理器"""

    def test_prompt_manager_singleton(self):
        """测试单例模式"""
        from services.agent.prompt import get_prompt_manager, PromptManager

        manager1 = get_prompt_manager()
        manager2 = get_prompt_manager()
        assert manager1 is manager2

    def test_list_templates(self):
        """测试列出模板"""
        from services.agent.prompt import get_prompt_manager

        manager = get_prompt_manager()
        templates = manager.list_templates()

        assert "default" in templates
        assert "concise" in templates
        assert "detailed" in templates

    def test_get_template(self):
        """测试获取模板"""
        from services.agent.prompt import get_prompt_manager

        manager = get_prompt_manager()
        template = manager.get_template("default")

        assert template is not None
        assert template.name == "default"

    def test_get_nonexistent_template(self):
        """测试获取不存在的模板"""
        from services.agent.prompt import get_prompt_manager

        manager = get_prompt_manager()
        template = manager.get_template("nonexistent")

        assert template is None

    def test_set_connection_template(self):
        """测试设置连接模板"""
        from services.agent.prompt import get_prompt_manager

        manager = get_prompt_manager()
        conn_id = "test-connection-123"

        # 设置模板
        result = manager.set_connection_template(conn_id, "concise")
        assert result is True

        # 获取当前模板
        current = manager.get_connection_template(conn_id)
        assert current == "concise"

    def test_set_invalid_template(self):
        """测试设置无效模板"""
        from services.agent.prompt import get_prompt_manager

        manager = get_prompt_manager()
        conn_id = "test-connection-456"

        result = manager.set_connection_template(conn_id, "invalid_template")
        assert result is False

    def test_set_connection_variable(self):
        """测试设置连接变量"""
        from services.agent.prompt import get_prompt_manager

        manager = get_prompt_manager()
        conn_id = "test-connection-789"

        result = manager.set_connection_variable(conn_id, "custom_greeting", "你好，冒险家！")
        assert result is True

        # 验证变量设置
        variables = manager.get_connection_variables(conn_id)
        assert "custom_greeting" in variables
        assert variables["custom_greeting"] == "你好，冒险家！"

    def test_set_connection_variable_auto_prefix(self):
        """测试设置变量时自动添加 custom_ 前缀"""
        from services.agent.prompt import get_prompt_manager

        manager = get_prompt_manager()
        conn_id = "test-connection-abc"

        manager.set_connection_variable(conn_id, "greeting", "Hello")
        variables = manager.get_connection_variables(conn_id)

        # 应该自动添加 custom_ 前缀
        assert "custom_greeting" in variables

    def test_build_system_prompt(self):
        """测试构建系统提示词"""
        from services.agent.prompt import get_prompt_manager

        manager = get_prompt_manager()
        conn_id = "test-connection-prompt"

        # 先设置模板
        manager.set_connection_template(conn_id, "default")

        # 构建提示词
        prompt = manager.build_system_prompt(
            connection_id=conn_id,
            player_name="TestPlayer",
            provider="deepseek",
            model="deepseek-chat",
            context_length=5,
        )

        # 验证变量替换
        assert "TestPlayer" in prompt
        assert "deepseek" in prompt
        assert "deepseek-chat" in prompt

    def test_build_system_prompt_with_custom_variables(self):
        """测试使用自定义变量构建提示词"""
        from services.agent.prompt import get_prompt_manager

        manager = get_prompt_manager()
        conn_id = "test-connection-custom"

        # 设置模板和自定义变量
        manager.set_connection_template(conn_id, "default")
        manager.set_connection_variable(conn_id, "custom_greeting", "你好，冒险家！")

        # 构建提示词
        prompt = manager.build_system_prompt(
            connection_id=conn_id,
            player_name="TestPlayer",
            provider="deepseek",
            model="deepseek-chat",
            context_length=5,
        )

        # 验证自定义变量替换
        assert "你好，冒险家！" in prompt

    def test_clear_connection(self):
        """测试清理连接数据"""
        from services.agent.prompt import get_prompt_manager

        manager = get_prompt_manager()
        conn_id = "test-connection-clear"

        # 设置数据
        manager.set_connection_template(conn_id, "detailed")
        manager.set_connection_variable(conn_id, "custom_test", "value")

        # 清理
        manager.clear_connection(conn_id)

        # 验证已清理
        assert manager.get_connection_template(conn_id) == "default"

    def test_register_custom_template(self):
        """测试注册自定义模板"""
        from services.agent.prompt import PromptManager, PromptTemplate

        manager = PromptManager()
        template = PromptTemplate(
            name="custom",
            description="自定义模板",
            content="自定义内容: {player_name}",
        )

        result = manager.register_template(template)
        assert result is True

        # 验证已注册
        assert manager.get_template("custom") is not None

    def test_register_duplicate_template(self):
        """测试注册重复模板"""
        from services.agent.prompt import PromptManager, PromptTemplate

        manager = PromptManager()
        template = PromptTemplate(
            name="default",  # 已存在
            description="重复模板",
            content="内容",
        )

        result = manager.register_template(template)
        assert result is False


class TestVariableReplacement:
    """测试变量替换"""

    def test_all_builtin_variables(self):
        """测试所有内置变量"""
        from services.agent.prompt import get_prompt_manager

        manager = get_prompt_manager()
        conn_id = "test-vars"

        manager.set_connection_template(conn_id, "detailed")

        # 构建提示词（使用详细模板，它有所有变量）
        prompt = manager.build_system_prompt(
            connection_id=conn_id,
            player_name="Player123",
            provider="openai",
            model="gpt-4",
            context_length=10,
        )

        # 验证所有内置变量都被替换
        assert "Player123" in prompt
        assert "openai" in prompt
        assert "gpt-4" in prompt
        assert "10" in prompt  # context_length

    def test_context_length_variable(self):
        """测试上下文长度变量"""
        from services.agent.prompt import get_prompt_manager

        manager = get_prompt_manager()
        conn_id = "test-context"

        manager.set_connection_template(conn_id, "detailed")

        # 不同上下文长度
        for length in [0, 5, 20, 100]:
            prompt = manager.build_system_prompt(
                connection_id=conn_id,
                player_name="Test",
                provider="deepseek",
                model="chat",
                context_length=length,
            )
            assert str(length) in prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
