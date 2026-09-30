import pytest

import show_PnL as monitor


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, '__file__', str(tmp_path / 'show_PnL.py'))
    monkeypatch.delenv('FREQTRADE__API_SERVER__USERNAME', raising=False)
    monkeypatch.delenv('FREQTRADE__API_SERVER__PASSWORD', raising=False)
    return tmp_path / 'hyperliquid.env'


def test_auth_reads_file_beside_script_with_literal_values(env_file, tmp_path, monkeypatch):
    env_file.write_text('\ufeff# API login\nFREQTRADE__API_SERVER__USERNAME="paper"\n'
                        "FREQTRADE__API_SERVER__PASSWORD='test=a#b'\n", encoding='utf-8')
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    auth = monitor.load_api_auth()
    assert (auth.username, auth.password) == ('paper', 'test=a#b')


def test_process_environment_overrides_file(env_file, monkeypatch):
    env_file.write_text('FREQTRADE__API_SERVER__USERNAME=file-user\n'
                        'FREQTRADE__API_SERVER__PASSWORD=file-password\n')
    monkeypatch.setenv('FREQTRADE__API_SERVER__PASSWORD', 'override-password')
    auth = monitor.load_api_auth()
    assert (auth.username, auth.password) == ('file-user', 'override-password')


def test_process_environment_works_without_file(env_file, monkeypatch):
    monkeypatch.setenv('FREQTRADE__API_SERVER__USERNAME', 'env-user')
    monkeypatch.setenv('FREQTRADE__API_SERVER__PASSWORD', 'env-password')
    auth = monitor.load_api_auth()
    assert (auth.username, auth.password) == ('env-user', 'env-password')


def test_missing_credentials_fail_without_disclosing_values(env_file):
    env_file.write_text('FREQTRADE__API_SERVER__PASSWORD=private-test-value\n')
    with pytest.raises(ValueError, match='hyperliquid.env') as error:
        monitor.load_api_auth()
    assert 'private-test-value' not in str(error.value)


def test_explicit_empty_environment_does_not_fall_back(env_file, monkeypatch):
    env_file.write_text('FREQTRADE__API_SERVER__USERNAME=file-user\n'
                        'FREQTRADE__API_SERVER__PASSWORD=file-password\n')
    monkeypatch.setenv('FREQTRADE__API_SERVER__PASSWORD', '')
    with pytest.raises(ValueError):
        monitor.load_api_auth()
