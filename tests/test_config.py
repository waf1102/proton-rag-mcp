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
