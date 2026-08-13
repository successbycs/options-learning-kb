import pytest

from options_learning_kb.config import ConfigurationError, Settings


def configured_environment(monkeypatch):
    monkeypatch.setenv("OPTIONS_KB_DATABASE_URL", "postgresql://user:password@postgres:5432/options_kb")
    monkeypatch.setenv("OPTIONS_KB_OLLAMA_BASE_URL", "http://ollama:11434")


def test_settings_validate_schema_dimension_and_urls(monkeypatch):
    configured_environment(monkeypatch)
    settings = Settings.from_env()

    settings.validate_runtime()


def test_settings_reject_invalid_or_incompatible_values(monkeypatch):
    configured_environment(monkeypatch)
    monkeypatch.setenv("OPTIONS_KB_EMBEDDING_DIMENSIONS", "768")
    with pytest.raises(ConfigurationError, match="requires .*1024"):
        Settings.from_env().validate_runtime()

    monkeypatch.setenv("OPTIONS_KB_SEARCH_LIMIT", "many")
    with pytest.raises(ConfigurationError, match="must be an integer"):
        Settings.from_env()
