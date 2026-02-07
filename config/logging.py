"""日志配置 - 使用 structlog 结构化日志"""

import logging
import sys
from pathlib import Path

import structlog


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


def setup_logging(log_level: str = "INFO", enable_file_logging: bool = True) -> None:
    """配置结构化日志系统"""

    level = getattr(logging, log_level)
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
    if enable_file_logging:
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)

        file_handler = logging.FileHandler(
            log_dir / "log",
            encoding="utf-8",
        )
        file_handler.setLevel(level)

        file_renderer = _build_renderer(colors=False)
        file_formatter = structlog.stdlib.ProcessorFormatter(
            processor=file_renderer,
            foreign_pre_chain=shared_processors,
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    """获取日志记录器"""
    return structlog.get_logger(name)
