from noc_cli import config


def test_data_dir_respects_noc_home(monkeypatch, tmp_path):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    assert config.data_dir() == tmp_path


def test_load_config_reads_env_file(monkeypatch, tmp_path):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    monkeypatch.delenv("ZENDESK_SUBDOMAIN", raising=False)
    monkeypatch.delenv("NOC_OWNER", raising=False)
    (tmp_path / ".env").write_text("ZENDESK_SUBDOMAIN=carbyne\nNOC_OWNER=alice\n")
    cfg = config.load_config()
    assert cfg.zendesk_subdomain == "carbyne"
    assert cfg.owner == "alice"
    assert cfg.zendesk_base_url == "https://carbyne.zendesk.com/api/v2"


def test_process_env_overrides_file(monkeypatch, tmp_path):
    monkeypatch.setenv("NOC_HOME", str(tmp_path))
    (tmp_path / ".env").write_text("ZENDESK_SUBDOMAIN=fromfile\n")
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "fromenv")
    cfg = config.load_config()
    assert cfg.zendesk_subdomain == "fromenv"
