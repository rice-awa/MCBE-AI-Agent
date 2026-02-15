"""命令注册表 - 管理命令和别名"""

from config.settings import MinecraftCommandConfig, MinecraftConfig
from config.logging import get_logger

logger = get_logger(__name__)


class CommandRegistry:
    """命令注册表 - 管理命令和别名"""

    def __init__(self, commands_config: dict[str, str | dict]):
        self._commands: dict[str, MinecraftCommandConfig] = {}
        self._alias_map: dict[str, str] = {}  # 别名 -> 主命令前缀
        self._type_to_prefix: dict[str, str] = {}  # 命令类型 -> 主命令前缀
        self._load_commands(commands_config)

    def _load_commands(self, config: dict[str, str | dict]) -> None:
        """加载命令配置并构建别名映射"""
        for prefix, cmd in config.items():
            if isinstance(cmd, str):
                # 兼容旧格式: {prefix: type}
                cmd_config = MinecraftCommandConfig(
                    prefix=prefix,
                    type=cmd,
                    aliases=[],
                    description="",
                    usage=None
                )
            elif isinstance(cmd, dict):
                # 新格式: {prefix: {type, aliases, description, usage}}
                cmd_config = MinecraftCommandConfig(
                    prefix=prefix,
                    type=cmd.get("type", ""),
                    aliases=cmd.get("aliases", []),
                    description=cmd.get("description", ""),
                    usage=cmd.get("usage")
                )
            else:
                continue

            self._commands[prefix] = cmd_config
            self._type_to_prefix[cmd_config.type] = prefix

            # 构建别名映射
            for alias in cmd_config.aliases:
                self._alias_map[alias] = prefix

        logger.debug(
            "command_registry_loaded",
            command_count=len(self._commands),
            alias_count=len(self._alias_map),
        )

    def resolve(self, message: str) -> tuple[str | None, str]:
        """解析消息，返回 (命令类型, 内容)"""
        # 1. 尝试直接匹配
        for prefix, cmd_config in self._commands.items():
            if message.startswith(prefix):
                content = message[len(prefix):].strip()
                return cmd_config.type, content

        # 2. 尝试别名匹配
        for alias, main_prefix in self._alias_map.items():
            if message.startswith(alias):
                content = message[len(alias):].strip()
                cmd_config = self._commands[main_prefix]
                return cmd_config.type, content

        return None, message

    def add_alias(self, command_prefix: str, alias: str) -> bool:
        """动态添加别名"""
        if command_prefix not in self._commands:
            logger.warning("add_alias_command_not_found", prefix=command_prefix)
            return False

        if alias in self._alias_map:
            logger.warning("add_alias_already_exists", alias=alias)
            return False

        self._alias_map[alias] = command_prefix
        self._commands[command_prefix].aliases.append(alias)

        logger.info("alias_added", prefix=command_prefix, alias=alias)
        return True

    def remove_alias(self, alias: str) -> bool:
        """动态删除别名"""
        if alias not in self._alias_map:
            logger.warning("remove_alias_not_found", alias=alias)
            return False

        main_prefix = self._alias_map.pop(alias)
        self._commands[main_prefix].aliases.remove(alias)

        logger.info("alias_removed", prefix=main_prefix, alias=alias)
        return True

    def get_command_config(self, prefix: str) -> MinecraftCommandConfig | None:
        """获取命令配置"""
        return self._commands.get(prefix)

    def get_aliases(self, command_prefix: str) -> list[str]:
        """获取命令的所有别名"""
        cmd = self._commands.get(command_prefix)
        return cmd.aliases if cmd else []

    def list_all_commands(self) -> list[tuple[str, str, list[str]]]:
        """列出所有命令 (前缀, 类型, 别名列表)"""
        return [
            (prefix, config.type, config.aliases)
            for prefix, config in self._commands.items()
        ]

    def get_command_prefix(self, cmd_type: str) -> str | None:
        """根据命令类型获取主命令前缀"""
        return self._type_to_prefix.get(cmd_type)
