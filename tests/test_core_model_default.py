from unittest.mock import patch

from services.agent.core import ChatAgentManager


def test_default_agent_model_derived_from_settings():
    manager = ChatAgentManager()
    with patch("services.agent.core.get_settings") as mock_settings:
        mock_settings.return_value.default_provider = "openai"
        mock_settings.return_value.get_provider_config.return_value.model = "gpt-4o"
        mock_settings.return_value.agent_retries = 3
        agent = manager._create_agent()
        assert str(agent.model) == "openai:gpt-4o"
