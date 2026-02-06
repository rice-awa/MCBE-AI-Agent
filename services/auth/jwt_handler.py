"""JWT 认证处理器"""

import hashlib
import json
import time
from pathlib import Path
from typing import Optional

import jwt

from mcbe_ai_agent.config.settings import Settings
from mcbe_ai_agent.config.logging import get_logger

logger = get_logger(__name__)


class JWTHandler:
    """JWT 认证处理器"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.secret_key = settings.jwt_secret
        self.expiration = settings.jwt_expiration
        self.default_password = settings.default_password
        self.token_file = Path("data/tokens.json")
        self.tokens: list[dict[str, str]] = []
        self._load_tokens()

    def _load_tokens(self) -> None:
        """从文件加载令牌"""
        if self.token_file.exists():
            try:
                with open(self.token_file, "r", encoding="utf-8") as f:
                    self.tokens = json.load(f)
                logger.info("tokens_loaded", count=len(self.tokens))
            except Exception as e:
                logger.error("load_tokens_error", error=str(e))
                self.tokens = []
        else:
            # 确保目录存在
            self.token_file.parent.mkdir(parents=True, exist_ok=True)

    def _save_tokens(self) -> None:
        """保存令牌到文件"""
        try:
            with open(self.token_file, "w", encoding="utf-8") as f:
                json.dump(self.tokens, f, ensure_ascii=False, indent=2)
            logger.debug("tokens_saved", count=len(self.tokens))
        except Exception as e:
            logger.error("save_tokens_error", error=str(e))

    def hash_password(self, password: str) -> str:
        """对密码进行哈希处理"""
        return hashlib.sha256(password.encode()).hexdigest()

    def verify_password(self, provided_password: str) -> bool:
        """
        验证提供的密码是否正确

        Args:
            provided_password: 用户提供的密码

        Returns:
            是否匹配
        """
        if not isinstance(provided_password, str):
            return False
        return self.hash_password(provided_password) == self.hash_password(
            self.default_password
        )

    def generate_token(self) -> str:
        """
        生成 JWT 令牌

        Returns:
            JWT 令牌字符串
        """
        payload = {
            "exp": time.time() + self.expiration,
            "iat": time.time(),
        }
        token = jwt.encode(payload, self.secret_key, algorithm="HS256")
        logger.debug("token_generated")
        return token

    def verify_token(self, token: str) -> bool:
        """
        验证 JWT 令牌

        Args:
            token: JWT 令牌

        Returns:
            是否有效
        """
        try:
            jwt.decode(token, self.secret_key, algorithms=["HS256"])
            return True
        except jwt.ExpiredSignatureError:
            logger.debug("token_expired")
            return False
        except jwt.InvalidTokenError:
            logger.debug("token_invalid")
            return False

    def save_token(self, connection_uuid: str, token: str) -> None:
        """
        保存令牌

        Args:
            connection_uuid: 连接 UUID
            token: JWT 令牌
        """
        # 查找是否已有该 UUID 的令牌
        for item in self.tokens:
            if item["uuid"] == connection_uuid:
                item["token"] = token
                break
        else:
            self.tokens.append({"uuid": connection_uuid, "token": token})

        self._save_tokens()
        logger.info("token_saved", connection_uuid=connection_uuid)

    def get_stored_token(self, connection_uuid: str) -> Optional[str]:
        """
        获取存储的令牌

        Args:
            connection_uuid: 连接 UUID

        Returns:
            令牌或 None
        """
        for item in self.tokens:
            if item["uuid"] == connection_uuid:
                return item["token"]
        return None

    def is_token_valid(self, connection_uuid: str) -> bool:
        """
        检查现有令牌是否有效

        Args:
            connection_uuid: 连接 UUID

        Returns:
            是否有效
        """
        token = self.get_stored_token(connection_uuid)
        return token is not None and self.verify_token(token)

    def remove_token(self, connection_uuid: str) -> None:
        """
        移除存储的令牌

        Args:
            connection_uuid: 连接 UUID
        """
        self.tokens = [
            item for item in self.tokens if item["uuid"] != connection_uuid
        ]
        self._save_tokens()
        logger.info("token_removed", connection_uuid=connection_uuid)

    def cleanup_expired_tokens(self) -> int:
        """
        清理过期的令牌

        Returns:
            清理的数量
        """
        original_count = len(self.tokens)
        self.tokens = [
            item
            for item in self.tokens
            if self.verify_token(item["token"])
        ]
        removed = original_count - len(self.tokens)

        if removed > 0:
            self._save_tokens()
            logger.info("tokens_cleaned", removed=removed)

        return removed
