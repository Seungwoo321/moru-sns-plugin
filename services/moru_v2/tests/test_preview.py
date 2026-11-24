import importlib.util
import json
import logging

import pytest
from starlette.testclient import TestClient
from services.moru_v2.preview.prepare import prepare


@pytest.fixture
def probe(tmp_path, monkeypatch):
    bundle = tmp_path/'bundle'
    manifest = prepare(bundle)
    assert set(manifest) == {'api/index.py','privacy.py','vercel.json','requirements.txt'}
    config = json.loads((bundle/'vercel.json').read_text())
    assert config['git']['deploymentEnabled'] is False
    monkeypatch.syspath_prepend(str(bundle))
    spec = importlib.util.spec_from_file_location('preview_probe', bundle/'api/index.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv('VERCEL_ENV', 'preview')
    # The upload runs in its own process, without the service's separately named
    # privacy module. Isolate its LogRecordFactory just as the Vercel worker does.
    previous = logging.getLogRecordFactory()
    logging.setLogRecordFactory(logging.LogRecord)
    try:
        yield TestClient(module.app, base_url='https://fixture.test')
    finally:
        logging.setLogRecordFactory(previous)


@pytest.mark.parametrize('environment', ['production','development',''])
def test_probe_refuses_non_preview(probe, monkeypatch, environment):
    monkeypatch.setenv('VERCEL_ENV', environment)
    assert probe.get('/health').status_code == 503


@pytest.mark.parametrize('path', ['/auth/callback','/connections/threads/callback','/connections/instagram/callback'])
@pytest.mark.parametrize('mode,status', [('ok',200),('error',502)])
def test_bundle_callbacks_scrub_synthetic_queries(probe, caplog, path, mode, status):
    caplog.set_level(logging.DEBUG)
    # The external test-client INFO request log is outside the app, like a proxy.
    # Limit that logger while retaining descendant outbound httpcore warnings.
    caplog.set_level(logging.WARNING, logger='httpx')
    response = probe.get(path, params={'code':'MORU_CANARY_CODE','state':'MORU_CANARY_STATE','mode':mode})
    assert response.status_code == status
    assert 'MORU_CANARY' not in response.text
    assert 'MORU_CANARY' not in caplog.text
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert 'synthetic_callback_' in caplog.text  # Evidence the application ran.


def test_probe_has_no_live_login_or_account_routes(probe):
    assert probe.get('/health').json()['live_accounts'] is False
    assert probe.get('/login').status_code == 404
    assert probe.get('/connections').status_code == 404
    assert probe.get('/auth/callback?code=real&state=real&mode=ok').status_code == 400
    assert probe.get('/auth/callback?code=MORU_CANARY_C&state=MORU_CANARY_S&mode=ok&code=duplicate').status_code == 400
