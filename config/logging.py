"""日志配置 - 使用 structlog 结构化日志"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import structlog

from config.settings import Settings, get_settings


def _build_shared_processors() -> list:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]


def _build_renderer(colors: bool) -> structlog.types.Processor:
    return structlog.dev.ConsoleRenderer(
        colors=colors,
        pad_level=False,
        pad_event=False,
    )


def _create_daily_file_handler(
    log_path: Path,
    level: int,
    formatter: logging.Formatter,
    when: str = "midnight",
    interval: int = 1,
    backup_count: int = 30,
) -> TimedRotatingFileHandler:
    """创建按天轮转的文件处理器。"""
    handler = TimedRotatingFileHandler(
        filename=log_path,
        when=when,
        interval=interval,
        backupCount=backup_count,
        encoding="utf-8",
        utc=True,
    )
    handler.suffix = "%Y-%m-%d"

    def _namer(default_name: str) -> str:
        # 例如 app.log.2026-02-09 -> app-2026-02-09.log
        if ".log." in default_name:
            prefix, date = default_name.rsplit(".log.", 1)
            return f"{prefix}-{date}.log"
        return default_name

    handler.namer = _namer
    handler.setLevel(level)
    handler.setFormatter(formatter)
    return handler


def _setup_named_logger(
    logger_name: str,
    level: int,
    handler: logging.Handler,
) -> None:
    named_logger = logging.getLogger(logger_name)
    named_logger.setLevel(level)
    named_logger.handlers.clear()
    named_logger.addHandler(handler)
    named_logger.propagate = True


def setup_logging(
    settings: Settings | None = None,
    log_level: str | None = None,
    enable_file_logging: bool | None = None,
    enable_ws_raw_log: bool | None = None,
    enable_llm_raw_log: bool | None = None,
) -> None:
    """配置结构化日志系统

    Args:
        settings: 配置对象；为 None 时使用 get_settings()。日志路径、文件名与轮转
            策略从 settings.logging.* 读取，日志级别与开关优先使用显式参数（向后
            兼容 cli.py 旧调用方式），未提供时回退到 settings 顶层扁平字段。
    """
    settings = settings or get_settings()
    level_name = log_level if log_level is not None else settings.log_level
    file_logging = (
        enable_file_logging if enable_file_logging is not None else settings.enable_file_logging
    )
    ws_raw_enabled = (
        enable_ws_raw_log if enable_ws_raw_log is not None else settings.enable_ws_raw_log
    )
    llm_raw_enabled = (
        enable_llm_raw_log if enable_llm_raw_log is not None else settings.enable_llm_raw_log
    )

    level = getattr(logging, level_name)
    shared_processors = _build_shared_processors()

    # structlog -> 标准 logging
    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # 配置标准库 logging
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()

    console_renderer = (
        _build_renderer(colors=True)
        if sys.stdout.isatty()
        else structlog.processors.JSONRenderer()
    )

    console_formatter = structlog.stdlib.ProcessorFormatter(
        processor=console_renderer,
        foreign_pre_chain=shared_processors,
    )
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # 文件日志 - 与控制台风格一致（去除颜色）
    if file_logging:
        log_cfg = settings.logging
        log_dir = Path(log_cfg.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        file_renderer = _build_renderer(colors=False)
        file_formatter = structlog.stdlib.ProcessorFormatter(
            processor=file_renderer,
            foreign_pre_chain=shared_processors,
        )

        raw_file_formatter = structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(),
            foreign_pre_chain=shared_processors,
        )

        app_handler = _create_daily_file_handler(
            log_dir / log_cfg.files.app,
            level,
            file_formatter,
            when=log_cfg.rotation_when,
            interval=log_cfg.rotation_interval,
            backup_count=log_cfg.rotation_backup_count,
        )
        root_logger.addHandler(app_handler)

        # WebSocket 原始日志
        if ws_raw_enabled:
            ws_raw_handler = _create_daily_file_handler(
                log_dir / log_cfg.files.websocket_raw,
                level,
                raw_file_formatter,
                when=log_cfg.rotation_when,
                interval=log_cfg.rotation_interval,
                backup_count=log_cfg.rotation_backup_count,
            )
            _setup_named_logger("websocket.raw", level, ws_raw_handler)

        # LLM 原始日志
        if llm_raw_enabled:
            llm_raw_handler = _create_daily_file_handler(
                log_dir / log_cfg.files.llm_raw,
                level,
                raw_file_formatter,
                when=log_cfg.rotation_when,
                interval=log_cfg.rotation_interval,
                backup_count=log_cfg.rotation_backup_count,
            )
            _setup_named_logger("llm.raw", level, llm_raw_handler)
    else:
        # 避免禁用文件日志时仍保留历史 handler
        for logger_name in ("websocket.raw", "llm.raw"):
            named_logger = logging.getLogger(logger_name)
            named_logger.handlers.clear()

    # 如果禁用了原始日志，将日志级别设置为更高以阻止日志输出
    if not ws_raw_enabled:
        ws_raw_logger = logging.getLogger("websocket.raw")
        ws_raw_logger.setLevel(logging.CRITICAL)
    if not llm_raw_enabled:
        llm_raw_logger = logging.getLogger("llm.raw")
        llm_raw_logger.setLevel(logging.CRITICAL)


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    """获取日志记录器"""
    return structlog.get_logger(name)
