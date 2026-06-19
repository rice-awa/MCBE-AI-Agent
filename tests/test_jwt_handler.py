"""JWT Handler 测试"""

import sys
from pathlib import Path

import jwt
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from config.settings import Settings
from services.auth.jwt_handler import JWTHandler


def test_jwt_algorithm_default() -> None:
    """默认 jwt_algorithm 应为 HS256"""
    settings = Settings()
    assert settings.jwt_algorithm == "HS256"


def test_generate_token_uses_configured_algorithm() -> None:
    """生成 token 时应使用配置的 jwt_algorithm"""
    settings = Settings()
    settings.jwt_algorithm = "HS256"
    handler = JWTHandler(settings)
    token = handler.generate_token()
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "HS256"


def test_verify_token_uses_configured_algorithm() -> None:
    """验证 token 时应使用配置的 jwt_algorithm"""
    settings = Settings()
    settings.jwt_algorithm = "HS256"
    handler = JWTHandler(settings)
    token = handler.generate_token()
    assert handler.verify_token(token) is True


def test_verify_token_rejects_invalid_token() -> None:
    """无效 token 应被拒绝"""
    settings = Settings()
    handler = JWTHandler(settings)
    assert handler.verify_token("invalid.token.here") is False


def test_custom_jwt_algorithm_via_settings() -> None:
    """通过 Settings 设置不同的 jwt_algorithm 可正常工作"""
    settings = Settings()
    settings.jwt_algorithm = "HS256"
    handler = JWTHandler(settings)
    token = handler.generate_token()
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "HS256"
    assert handler.verify_token(token) is True
