"""Codex settings → discovery → runtime/vision routes, with no network calls."""
import asyncio
import json

import pytest

from backend.core import codex_runtime, llm_emit, model_registry
from backend.services import ai_model_settings as ai


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOCLIP_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('AUTOCLIP_APP_DIR', str(tmp_path))
    monkeypatch.setenv('OPENAI_API_KEY', '')
    monkeypatch.setenv('DASHSCOPE_API_KEY', '')
    monkeypatch.setattr(ai, 'migrate_legacy', lambda: ai.ModelSettings())
    monkeypatch.setattr(llm_emit, '_post_event', lambda *a: None)


def settings(**kwargs):
    return ai.ModelSettings(connections=[ai.Connection(id='c', name='Codex', provider='codex')],
        analysis=ai.Assignment(connection_id='c', model='future-model', capability='multimodal'), **kwargs)


def test_save_reload_routes_text_and_vision_without_http_credentials(monkeypatch, tmp_path):
    from backend.core.llm_manager import LLMManager
    from backend.core.llm_providers import CodexProvider
    from backend.services.studio import intelligence, vision_settings
    monkeypatch.setattr(LLMManager, '_sync_config_if_needed', lambda self: None)
    calls = []
    def infer(model, prompt='', **kwargs):
        calls.append((model, prompt, kwargs))
        return {'content': '{"color":"red"}' if kwargs.get('content') else '{"clips":[]}',
                'usage': {'prompt_tokens': 2, 'completion_tokens': 3, 'total_tokens': 5},
                'model': model, 'finish_reason': 'stop'}
    monkeypatch.setattr(codex_runtime, 'infer', infer)
    ai.save(settings())
    manager = LLMManager(settings_file=tmp_path / 'settings.json')
    assert isinstance(manager.current_provider, CodexProvider)
    assert manager.get_current_provider_info()['provider'] == 'codex'
    assert manager.openai_compatible_endpoint() is None
    assert json.loads(manager.call('Find clips', 'subtitle data')) == {'clips': []}
    assert 'subtitle data' in calls[0][1]
    effective = vision_settings.effective()
    assert effective['provider'] == 'codex' and effective['base_url'] == ''
    assert vision_settings.public()['configured'] and intelligence.ready()
    assert intelligence.vision_call([{'type': 'text', 'text': 'frame'}]) == {'color': 'red'}
    assert calls[1][2]['content'][0]['text'] == 'frame'


def test_discovery_persists_trusted_capabilities_and_honors_refresh(monkeypatch):
    calls = []
    def catalog():
        calls.append(1)
        return [{'id': 'future-model', 'capability': 'multimodal', 'capability_source': 'provider',
                 'analysis': True, 'image': False, 'asr': False}]
    monkeypatch.setattr(codex_runtime, 'discover_models', catalog)
    config = settings()
    config.analysis.capability = 'auto'
    connection = config.connections[0]
    assert ai.vision_endpoint(config) is None
    assert asyncio.run(model_registry.discover(connection))['preview'] is False
    assert ai.vision_endpoint(config)['provider'] == 'codex'
    assert asyncio.run(model_registry.discover(connection))['source'] == 'cache'
    asyncio.run(model_registry.discover(connection, refresh=True))
    assert len(calls) == 2


def test_codex_never_stores_api_keys_or_accepts_chat_models_as_cover_asr():
    c = ai.Connection(id='c', name='Codex', provider='codex', api_key='unused-secret', base_url='https://other.example')
    assert c.api_key == c.base_url == ''
    with pytest.raises(ValueError, match='封面'):
        settings(cover=ai.Assignment(connection_id='c', model='future-model'))
    with pytest.raises(ValueError, match='ASR'):
        settings(transcription=ai.Transcription(provider='cloud', connection_id='c', model='future-model'))


def test_http_test_endpoint_routes_codex_text_and_image_and_preserves_actionable_errors(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.v1.settings import router
    app = FastAPI()
    app.include_router(router)
    payload = {'connection': settings().connections[0].model_dump(), 'model': 'future-model'}
    calls = []
    def infer(model, prompt='', **kwargs):
        calls.append(kwargs)
        return {'content': '{"color":"red"}' if kwargs.get('content') else 'OK',
                'usage': None, 'model': model, 'finish_reason': 'stop'}
    monkeypatch.setattr(codex_runtime, 'infer', infer)
    with TestClient(app) as client:
        assert client.post('/settings/ai-models/test', json=payload).json()['success']
        assert client.post('/settings/ai-models/test', json={**payload, 'vision': True}).json()['ok']
        assert calls[1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
        def unavailable(*a, **k):
            raise codex_runtime.CodexError('Please run codex login')
        monkeypatch.setattr(codex_runtime, 'infer', unavailable)
        for vision in (False, True):
            response = client.post('/settings/ai-models/test', json={**payload, 'vision': vision})
            assert response.status_code == 422
            assert 'codex login' in response.json()['detail']


def test_vision_invalid_json_and_event_lists(monkeypatch):
    from backend.services.studio.intelligence import vision_call, VisionRequestError
    response = {'content': '[]', 'usage': None, 'model': 'future-model', 'finish_reason': 'stop'}
    monkeypatch.setattr(codex_runtime, 'infer', lambda *a, **k: response)
    config = {'provider': 'codex', 'model': 'future-model'}
    assert vision_call([], config, allow_event_list=True) == {'events': []}
    for text in ('[]', '{partial'):
        response['content'] = text
        with pytest.raises(VisionRequestError) as error:
            vision_call([], config)
        assert error.value.code == 'invalid_response'


def test_telemetry_labels_codex_without_endpoint_or_private_data(monkeypatch):
    from backend.core.llm_providers import CodexProvider
    captured = []
    monkeypatch.setattr(llm_emit, '_post_event', lambda event, props: captured.append(props))
    monkeypatch.setattr(codex_runtime, 'infer', lambda *a, **k: {
        'content': 'private answer', 'usage': None, 'model': 'future-model', 'finish_reason': 'stop'})
    CodexProvider('', 'future-model').call('private prompt')
    assert captured[-1]['provider'] == 'codex'
    assert 'http_status' not in captured[-1] and 'base_url_host' not in captured[-1]
    assert 'private' not in json.dumps(captured)
    assert llm_emit._vision_connection_fields({'provider': 'codex', 'model': 'future-model'})['provider'] == 'codex'


def test_failed_codex_calls_do_not_trigger_model_manager_retries(monkeypatch):
    from backend.core.llm_manager import LLMManager
    monkeypatch.setattr(LLMManager, '_sync_config_if_needed', lambda self: None)
    ai.save(settings())
    calls = []
    def unavailable(*a, **k):
        calls.append(1)
        raise codex_runtime.CodexError('Codex unavailable')
    monkeypatch.setattr(codex_runtime, 'infer', unavailable)
    with pytest.raises(codex_runtime.CodexError):
        LLMManager().call_with_retry('prompt')
    assert len(calls) == 1
