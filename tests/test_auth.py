import pytest
from fastapi import HTTPException

from app.config import settings
from app.security.auth import require_api_key


def test_no_key_configured_allows_any_request():
    settings.internal_api_key = ""
    require_api_key(None)  # should not raise
    require_api_key("anything")  # should not raise


def test_key_configured_rejects_missing_header():
    settings.internal_api_key = "secret123"
    try:
        with pytest.raises(HTTPException) as exc_info:
            require_api_key(None)
        assert exc_info.value.status_code == 401
    finally:
        settings.internal_api_key = ""


def test_key_configured_rejects_wrong_key():
    settings.internal_api_key = "secret123"
    try:
        with pytest.raises(HTTPException) as exc_info:
            require_api_key("wrong-key")
        assert exc_info.value.status_code == 401
    finally:
        settings.internal_api_key = ""


def test_key_configured_accepts_correct_key():
    settings.internal_api_key = "secret123"
    try:
        require_api_key("secret123")  # should not raise
    finally:
        settings.internal_api_key = ""
