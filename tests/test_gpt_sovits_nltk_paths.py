import os

from scripts.run_gpt_sovits import _configure_local_nltk_data


def test_installed_corpora_precede_redirected_cache_and_preserve_custom_paths(tmp_path, monkeypatch):
    local = tmp_path / ".local-setup" / "nltk_data"
    local.mkdir(parents=True)
    previous = str(tmp_path / "other-corpora")
    monkeypatch.setenv("NLTK_DATA", previous)

    _configure_local_nltk_data(tmp_path)

    assert os.environ["NLTK_DATA"].split(os.pathsep) == [str(local), previous]


def test_repeated_setup_keeps_single_local_entry(tmp_path, monkeypatch):
    local = tmp_path / ".local-setup" / "nltk_data"
    local.mkdir(parents=True)
    monkeypatch.delenv("NLTK_DATA", raising=False)

    _configure_local_nltk_data(tmp_path)
    _configure_local_nltk_data(tmp_path)

    assert os.environ["NLTK_DATA"] == str(local)


def test_missing_local_data_does_not_change_existing_setup(tmp_path, monkeypatch):
    previous = str(tmp_path / "existing-data")
    monkeypatch.setenv("NLTK_DATA", previous)

    _configure_local_nltk_data(tmp_path)

    assert os.environ["NLTK_DATA"] == previous
