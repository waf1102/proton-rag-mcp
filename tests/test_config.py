import pytest
from proton_rag.config import Settings


def test_defaults_and_folder_configuration():
    cfg = Settings.from_env({})
    assert cfg.folders == () and cfg.excluded_folders == ()
    assert cfg.workspace == "proton-mail"
    assert cfg.search_default == 10 and cfg.search_max == 50
    cfg = Settings.from_env({"RAG_FOLDERS": '["INBOX", "Folders/旅行"]', "RAG_POLL_SECONDS": "60"})
    assert cfg.folders == ("INBOX", "Folders/旅行") and cfg.poll_seconds == 60


@pytest.mark.parametrize(
    "env",
    [
        {"RAG_POLL_SECONDS": "0"},
        {"RAG_FOLDERS": '"INBOX"'},
        {"RAG_SEARCH_DEFAULT": "51"},
        {"IMAP_PORT": "0"},
    ],
)
def test_invalid_configuration_fails(env):
    with pytest.raises(ValueError):
        Settings.from_env(env)


def test_qdrant_and_ollama_settings_are_shared_and_loopback_only():
    settings = Settings.from_env(
        {
            "QDRANT_URL": "http://127.0.0.1:6333",
            "OLLAMA_URL": "http://127.0.0.1:11434",
            "QDRANT_COLLECTION": "mail-copy",
        }
    )
    assert settings.qdrant_url == "http://127.0.0.1:6333"
    assert settings.workspace == "mail-copy"
    with pytest.raises(ValueError):
        Settings.from_env({"QDRANT_URL": "https://remote.example"})


@pytest.mark.parametrize("ranking", ["legacy", "ensemble"])
def test_search_ranking_can_be_selected_without_changing_index_profile(ranking):
    settings = Settings.from_env({"RAG_SEARCH_RANKING": ranking})
    assert settings.search_ranking == ranking
    assert settings.embedding_model == Settings().embedding_model


def test_unknown_search_ranking_is_rejected():
    with pytest.raises(ValueError, match="RAG_SEARCH_RANKING"):
        Settings.from_env({"RAG_SEARCH_RANKING": "typo"})


@pytest.mark.parametrize("result_format", ["both", "text"])
def test_mcp_result_format_is_selectable_without_changing_retrieval(result_format):
    settings = Settings.from_env({"RAG_MCP_RESULT_FORMAT": result_format})
    assert settings.mcp_result_format == result_format
    assert settings.search_ranking == Settings().search_ranking


def test_unknown_mcp_result_format_is_rejected():
    with pytest.raises(ValueError, match="RAG_MCP_RESULT_FORMAT"):
        Settings.from_env({"RAG_MCP_RESULT_FORMAT": "typo"})
