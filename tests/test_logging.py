"""config/logging.py 配置驱动测试

验证日志目录、文件名与轮转策略从 Settings.logging.* 读取，
以及 setup_logging 同时支持新的 settings 入参与旧的 关键字参数 调用方式。
"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.logging import setup_logging
from config.settings import (
    LoggingConfig,
    LoggingFilesConfig,
    Settings,
    StorageConfig,
)

_NAMED_LOGGERS = ("websocket.raw", "llm.raw")


def _all_file_handlers() -> list[TimedRotatingFileHandler]:
    """收集 root 与原始日志命名 logger 上的所有 TimedRotatingFileHandler。"""
    loggers = [logging.getLogger()] + [logging.getLogger(name) for name in _NAMED_LOGGERS]
    return [h for lg in loggers for h in lg.handlers if isinstance(h, TimedRotatingFileHandler)]


def _settings(tmp_path: Path, **log_overrides) -> Settings:
    return Settings(
        enable_file_logging=True,
        enable_ws_raw_log=True,
        enable_llm_raw_log=True,
        logging=LoggingConfig(log_dir=tmp_path, **log_overrides),
    )


@pytest.fixture(autouse=True)
def _restore_loggers():
    """每个测试后恢复全局 logger 状态，避免污染其他测试。"""
    root = logging.getLogger()
    saved_root_handlers = list(root.handlers)
    saved_root_level = root.level
    saved_named: dict[str, tuple[list, int, bool]] = {}
    for name in _NAMED_LOGGERS:
        lg = logging.getLogger(name)
        saved_named[name] = (list(lg.handlers), lg.level, lg.propagate)
    yield
    root.handlers = saved_root_handlers
    root.setLevel(saved_root_level)
    for name, (handlers, level, propagate) in saved_named.items():
        lg = logging.getLogger(name)
        lg.handlers = handlers
        lg.setLevel(level)
        lg.propagate = propagate


def test_default_logging_config_preserves_original_behavior() -> None:
    """默认 LoggingConfig 应与原始硬编码值一致。"""
    cfg = LoggingConfig()
    assert cfg.log_dir == Path("logs")
    assert cfg.files.app == "app.log"
    assert cfg.files.websocket_raw == "websocket.log"
    assert cfg.files.llm_raw == "llm.log"
    assert cfg.rotation_when == "midnight"
    assert cfg.rotation_interval == 1
    assert cfg.rotation_backup_count == 30


def test_default_storage_config_preserves_original_paths() -> None:
    """默认 StorageConfig 应与原始硬编码路径一致。"""
    cfg = StorageConfig()
    assert cfg.conversations_dir == Path("data/conversations")
    assert cfg.tokens_file == Path("data/tokens.json")


def test_setup_logging_uses_configured_log_dir(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    setup_logging(settings)
    handlers = _all_file_handlers()
    assert handlers
    for handler in handlers:
        assert handler.baseFilename.startswith(str(tmp_path))
    # 触发一次写入以确保文件落盘
    logging.getLogger("websocket.raw").info("ws")
    logging.getLogger("llm.raw").info("llm")
    assert (tmp_path / "app.log").exists()
    assert (tmp_path / "websocket.log").exists()
    assert (tmp_path / "llm.log").exists()


def test_setup_logging_uses_configured_file_names(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        files=LoggingFilesConfig(app="my.log", websocket_raw="ws.log", llm_raw="lm.log"),
    )
    setup_logging(settings)
    logging.getLogger("websocket.raw").info("ws")
    logging.getLogger("llm.raw").info("llm")
    assert (tmp_path / "my.log").exists()
    assert (tmp_path / "ws.log").exists()
    assert (tmp_path / "lm.log").exists()
    assert not (tmp_path / "app.log").exists()


def test_setup_logging_uses_configured_rotation(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        rotation_when="h",
        rotation_interval=6,
        rotation_backup_count=7,
    )
    setup_logging(settings)
    handlers = _all_file_handlers()
    assert handlers
    for handler in handlers:
        # TimedRotatingFileHandler 内部会将 when 规范化为大写
        assert handler.when == "h".upper()
        assert handler.backupCount == 7


def test_setup_logging_accepts_explicit_settings(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    setup_logging(settings)
    assert any(
        handler.baseFilename.startswith(str(tmp_path)) for handler in _all_file_handlers()
    )


def test_setup_logging_backward_compat_kwargs(tmp_path: Path, monkeypatch) -> None:
    """cli.py 旧调用方式（传 log_level/enable_* 关键字，不传 settings）仍可用。"""
    fake = Settings(enable_file_logging=True, logging=LoggingConfig(log_dir=tmp_path))
    monkeypatch.setattr("config.logging.get_settings", lambda: fake)
    setup_logging(
        log_level="INFO",
        enable_file_logging=True,
        enable_ws_raw_log=False,
        enable_llm_raw_log=False,
    )
    handlers = _all_file_handlers()
    assert any(handler.baseFilename.startswith(str(tmp_path)) for handler in handlers)
    # 关闭 ws/llm 原始日志后，只剩 app 文件 handler
    assert len(handlers) == 1


def test_setup_logging_skips_log_dir_when_file_logging_disabled(tmp_path: Path) -> None:
    log_dir = tmp_path / "no_logs"
    settings = Settings(enable_file_logging=False, logging=LoggingConfig(log_dir=log_dir))
    setup_logging(settings)
    assert not _all_file_handlers()
    assert not log_dir.exists()
