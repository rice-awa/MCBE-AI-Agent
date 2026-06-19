from core.session import DEFAULT_CONVERSATION_ID, DEFAULT_PLAYER_KEY
from models.constants import DEFAULT_PLAYER_DISPLAY_NAME
from services.agent.prompt import PromptManager
from services.websocket.connection import ConnectionState


def test_default_player_key_single_source():
    """所有模块应引用同一匿名玩家键。"""
    assert DEFAULT_PLAYER_KEY == "__anonymous__"
    assert PromptManager.DEFAULT_PLAYER_KEY == DEFAULT_PLAYER_KEY
    assert ConnectionState.get_player_session.__defaults__ is None  # 不使用默认值覆盖


def test_default_conversation_id_single_source():
    """所有模块应引用同一默认对话 ID。"""
    assert DEFAULT_CONVERSATION_ID == "default"


def test_default_player_display_name():
    """默认玩家显示名应为 Player。"""
    assert DEFAULT_PLAYER_DISPLAY_NAME == "Player"
