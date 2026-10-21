"""Offline CLI contract: stdin, final answer, attachments, failures and cancellation."""
import base64
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core import codex_runtime as codex

pytestmark = pytest.mark.stdlib_only


def result(stdout='', stderr='', code=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=code)


def test_login_requires_chatgpt_without_exposing_cli_output(monkeypatch):
    monkeypatch.setattr(codex, '_run', lambda *a, **k: result(stderr='Logged in using an API key: secret'))
    with pytest.raises(codex.CodexError, match='codex login') as error:
        codex.check_login()
    assert 'secret' not in str(error.value)
    monkeypatch.setattr(codex, '_run', lambda *a, **k: result(stderr='Logged in using ChatGPT'))
    codex.check_login()


def test_model_catalog_uses_modality_metadata_not_names(monkeypatch):
    from backend.core import codex_images
    monkeypatch.setattr(codex_images, 'catalog_entry', lambda: None)
    monkeypatch.setattr(codex, 'check_login', lambda: None)
    items = [{'slug': 'future-vision', 'visibility': 'list', 'input_modalities': ['text', 'image']},
             {'slug': 'future-text', 'visibility': 'list', 'input_modalities': ['text']},
             {'slug': 'hidden', 'visibility': 'hidden'}, {'slug': '--bad model', 'visibility': 'list'}]
    monkeypatch.setattr(codex, '_run', lambda *a, **k: result(json.dumps({'models': items})))
    models = codex.discover_models()
    assert [(m['id'], m['capability']) for m in models] == [('future-vision', 'multimodal'), ('future-text', 'text')]
    assert all(not m['image'] and not m['asr'] for m in models)


def test_inference_stdin_images_final_output_and_cleanup(monkeypatch):
    monkeypatch.setattr(codex, 'check_login', lambda: None)
    seen = []
    def run(args, **kwargs):
        folder = Path(kwargs['cwd'])
        seen.append(folder)
        assert '--ignore-user-config' in args and '--ignore-rules' in args
        assert '--ephemeral' in args and 'read-only' in args
        assert 'features.shell_tool=false' in args and 'features.hooks=false' in args
        assert 'features.image_generation=false' in args
        assert args[-1] == '-'
        prompt = kwargs['stdin'].read().decode('utf-8')
        assert 'secret subtitle' in prompt and 'secret subtitle' not in args
        image = Path(args[args.index('--image') + 1])
        assert image.read_bytes() == b'image-fixture'
        Path(args[args.index('--output-last-message') + 1]).write_text('{"color":"red"}', encoding='utf-8')
        return result('\n'.join([json.dumps({'type': 'item.completed', 'item': {'text': 'not the answer'}}),
                                  json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 8, 'output_tokens': 3}})]))
    monkeypatch.setattr(codex, '_run', run)
    response = codex.infer('test-model', content=[{'type': 'text', 'text': 'secret subtitle'},
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(b'image-fixture').decode()}}])
    assert response['content'] == '{"color":"red"}'
    assert response['usage']['total_tokens'] == 11
    assert not seen[0].exists()


@pytest.mark.parametrize('stdout,code', [('', 0), ('{"type":"turn.failed"}', 1), ('{"type":"turn.completed"}', 2)])
def test_partial_output_never_becomes_a_success(monkeypatch, stdout, code):
    monkeypatch.setattr(codex, 'check_login', lambda: None)
    def run(args, **kwargs):
        Path(args[args.index('--output-last-message') + 1]).write_text('partial private answer', encoding='utf-8')
        return result(stdout, 'private upstream detail', code)
    monkeypatch.setattr(codex, '_run', run)
    with pytest.raises(codex.CodexError) as error:
        codex.infer('test-model', 'private prompt')
    assert 'private' not in str(error.value)


def test_reject_remote_images_and_invalid_models_before_inference(monkeypatch):
    monkeypatch.setattr(codex, 'check_login', lambda: None)
    monkeypatch.setattr(codex, '_run', lambda *a, **k: pytest.fail('must not launch inference'))
    with pytest.raises(codex.CodexError):
        codex.infer('--model injected', 'prompt')
    with pytest.raises(codex.CodexError):
        codex.infer('test-model', content=[{'type': 'image_url', 'image_url': {'url': 'https://private/image'}}])


def test_subprocess_strips_api_keys_and_translates_timeout(monkeypatch, tmp_path):
    from backend.core import project_cancellation
    monkeypatch.setenv('OPENAI_API_KEY', 'secret')
    monkeypatch.setenv('CODEX_API_KEY', 'secret')
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    monkeypatch.setattr(codex, 'command', lambda: ['codex'])
    monkeypatch.setattr(project_cancellation, 'current', lambda: 'project')
    def run(args, **kwargs):
        assert kwargs['shell'] is False
        assert 'OPENAI_API_KEY' not in kwargs['env'] and 'CODEX_API_KEY' not in kwargs['env']
        assert kwargs['env']['CODEX_HOME'] == str(tmp_path)
        raise subprocess.TimeoutExpired(args, 1, stderr='secret')
    monkeypatch.setattr(project_cancellation, 'run', run)
    with pytest.raises(codex.CodexError, match='超时'):
        codex._run(['exec'], cwd=tmp_path, timeout=1)


@pytest.mark.parametrize('kind', ['JobStopped', 'ProjectDeleted', 'ServiceRestarted'])
def test_project_stop_propagates_instead_of_becoming_provider_error(monkeypatch, tmp_path, kind):
    from backend.core import project_cancellation
    monkeypatch.setattr(codex, 'command', lambda: ['codex'])
    monkeypatch.setattr(project_cancellation, 'current', lambda: 'project')
    def run(*args, **kwargs):
        raise getattr(project_cancellation, kind)(*(() if kind == 'ServiceRestarted' else ('stopped',)))
    monkeypatch.setattr(project_cancellation, 'run', run)
    with pytest.raises(getattr(project_cancellation, kind)):
        codex._run(['exec'], cwd=tmp_path, timeout=1)


def test_connection_test_subprocess_is_reaped_on_timeout(tmp_path):
    import sys
    import time
    marker = tmp_path / 'should-not-finish'
    child = 'import time,pathlib;time.sleep(1);pathlib.Path(' + repr(str(marker)) + ').touch()'
    code = 'import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",' + repr(child) + ']);time.sleep(60)'
    with pytest.raises(subprocess.TimeoutExpired):
        codex._unbound_run([sys.executable, '-c', code], timeout=.15, capture_output=True)
    time.sleep(1)
    assert not marker.exists()


def test_missing_executable_is_actionable(monkeypatch, tmp_path):
    monkeypatch.setenv('AUTOCLIP_CODEX_PATH', str(tmp_path / 'missing'))
    with pytest.raises(codex.CodexError, match='codex login'):
        codex.command()
