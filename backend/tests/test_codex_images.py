"""Official CLI image artifacts → saved cover, with all model calls mocked."""
import io
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from backend.core import codex_images as images, codex_runtime as codex, image_providers, project_cancellation
from backend.services import ai_model_settings as ai, cover

THREAD = '00000000-0000-0000-0000-000000000001'


def png():
    stream = io.BytesIO()
    Image.new('RGB', (64, 36), '#334455').save(stream, 'PNG')
    return stream.getvalue()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOCLIP_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('AUTOCLIP_APP_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    monkeypatch.setattr(codex, 'check_login', lambda: None)


def done(code=0):
    return subprocess.CompletedProcess([], code, json.dumps({'type': 'thread.started', 'thread_id': THREAD}) + '\n{"type":"turn.completed"}', 'private upstream detail')


@pytest.mark.parametrize('state,expected', [('stable true', True), ('stable false', False), ('experimental true', False), ('', False)])
def test_feature_discovery_is_a_tool_not_an_analysis_model(monkeypatch, state, expected):
    def run(args, **kw):
        assert args == ['features', 'list']
        return subprocess.CompletedProcess([], 0, 'image_generation ' + state, '')
    monkeypatch.setattr(codex, '_run', run)
    entry = images.catalog_entry()
    assert bool(entry) == expected
    if entry:
        assert entry['id'] == images.MODEL and entry['image'] and not entry['analysis'] and not entry['asr']


def test_generate_uses_official_cli_stdin_reference_and_fresh_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr(images, 'catalog_entry', lambda: {'id': images.MODEL})
    folders = []
    def run(args, **kw):
        folder = Path(kw['cwd']); folders.append(folder)
        assert args[0] == 'exec' and args[-1] == '-'
        assert '--ignore-user-config' in args and 'read-only' in args
        assert '--model' not in args  # codex-image is a tool, never a reasoning model ID
        assert 'features.image_generation=true' in args
        assert 'features.apps=false' in args and 'features.shell_tool=false' in args
        assert Path(args[args.index('--image') + 1]).read_bytes() == png()
        assert 'private design' in kw['stdin'].read().decode('utf-8')
        artifact = tmp_path / 'codex/generated_images' / THREAD / 'result.png'
        artifact.parent.mkdir(parents=True); artifact.write_bytes(png())
        os.utime(artifact, (time.time() + 1, time.time() + 1))
        return done()
    monkeypatch.setattr(codex, '_run', run)
    assert images.generate(model=images.MODEL, prompt='private design', width=1280, height=720, reference=png()) == png()
    assert not folders[0].exists()
    assert (tmp_path / 'codex/generated_images' / THREAD / 'result.png').exists()


@pytest.mark.parametrize('case', ['outside', 'stale', 'invalid', 'missing', 'relative', 'large'])
def test_agent_paths_cannot_read_unrelated_or_invalid_files(tmp_path, case):
    started = time.time() - 5
    path = tmp_path / ('secret.png' if case == 'outside' else 'codex/generated_images/turn/out.png')
    path.parent.mkdir(parents=True, exist_ok=True)
    if case != 'missing':
        path.write_bytes(b'private-invalid-data' if case == 'invalid' else png())
    if case == 'stale':
        os.utime(path, (started - 10, started - 10))
    if case == 'large':
        with path.open('wb') as stream:
            stream.truncate(images.MAX_BYTES + 1)
    with pytest.raises(codex.CodexError) as error:
        images._read_generated('relative.png' if case == 'relative' else str(path), started)
    assert 'private' not in str(error.value) and str(tmp_path) not in str(error.value)


@pytest.mark.parametrize('response', ['empty', 'malformed', 'partial', 'failed'])
def test_failed_generation_never_falls_back_to_http(monkeypatch, response):
    monkeypatch.setattr(images, 'catalog_entry', lambda: {'id': images.MODEL})
    monkeypatch.setattr(image_providers, 'generate_openai', lambda **kw: pytest.fail('API fallback forbidden'))
    calls = []
    def run(args, **kw):
        calls.append(args)
        r = done(1 if response == 'failed' else 0)
        if response == 'partial': r.stdout = ''
        if response == 'malformed': r.stdout = '{private malformed response}'
        return r
    monkeypatch.setattr(codex, '_run', run)
    with pytest.raises(image_providers.ImageError) as error:
        image_providers.generate_image(provider='codex', api_key='', base_url='',
            request=image_providers.ImageRequest('prompt', 1280, 720, model=images.MODEL))
    assert len(calls) == 1 and 'private' not in str(error.value)


def test_artifact_lookup_uses_cli_session_not_agent_prose(tmp_path):
    folder = tmp_path / 'codex/generated_images' / THREAD
    folder.mkdir(parents=True)
    (folder / 'one.png').write_bytes(png())
    assert images._session_image(THREAD, 0) == png()
    (folder / 'two.png').write_bytes(png())
    with pytest.raises(codex.CodexError): images._session_image(THREAD, 0)
    with pytest.raises(codex.CodexError): images._session_image('../private', 0)
    with pytest.raises(codex.CodexError): images._session_image(None, 0)


def test_cancel_reaps_the_cli(monkeypatch, tmp_path):
    import sys
    marker = tmp_path / 'should-not-finish'
    code = f'import time,pathlib;time.sleep(2);pathlib.Path({str(marker)!r}).touch()'
    cancel = threading.Event()
    timer = threading.Timer(.15, cancel.set); timer.start()
    try:
        with pytest.raises(project_cancellation.JobStopped):
            codex._unbound_run([sys.executable, '-c', code], timeout=5, capture_output=True, cancel=cancel)
    finally:
        timer.cancel()
    assert not marker.exists()


def config(reference=False):
    return ai.ModelSettings(connections=[ai.Connection(id='c', name='Codex', provider='codex')],
        analysis=ai.Assignment(connection_id='c', model='test-model', capability='multimodal'),
        cover=ai.Assignment(connection_id='c', model=images.MODEL), cover_enabled=True, allow_send_frame=reference)


@pytest.mark.parametrize('reference', [False, True])
def test_saved_codex_cover_routes_generation_ocr_and_output(monkeypatch, tmp_path, reference):
    ai.save(config(reference))
    cfg = cover.load_config()
    assert cfg.configured and cfg.provider == 'codex' and cfg.api_key == cfg.base_url == ''
    assert cover.verify_endpoint(cfg)['provider'] == 'codex'
    monkeypatch.setattr(cover, 'load_clip_meta', lambda *a: {'title': 'Demo'})
    monkeypatch.setattr(cover, 'resolve_clip_frame', lambda *a: (tmp_path / 'video.mp4', 0))
    monkeypatch.setattr(cover, 'extract_frame_jpeg', lambda *a, **kw: png())
    calls = []
    def generate(**kw):
        calls.append(kw)
        assert kw['reference'] == (png() if reference else None)
        return png()
    monkeypatch.setattr(images, 'generate', generate)
    monkeypatch.setattr(codex, 'infer', lambda *a, **kw: {'content': 'Demo'})
    output = cover.generate_cover(project_id='p', clip_id='1', title='Demo')
    assert len(calls) == 1 and output['method'] == ('model' if reference else 'model_bg')
    with Image.open(output['path']) as image:
        assert image.size == (1146, 717) and image.format == 'JPEG'


def test_cover_job_cancellation_is_not_reported_as_failure(monkeypatch, tmp_path):
    ai.save(config())
    monkeypatch.setattr(cover, 'load_clip_meta', lambda *a: {'title': 'Demo'})
    monkeypatch.setattr(cover, 'resolve_clip_frame', lambda *a: (tmp_path / 'video.mp4', 0))
    monkeypatch.setattr(cover, 'extract_frame_jpeg', lambda *a, **kw: png())
    cancel = threading.Event()
    def generate(**kw):
        cancel.set()
        raise project_cancellation.JobStopped()
    monkeypatch.setattr(images, 'generate', generate)
    with pytest.raises(cover.CoverError, match='已取消'):
        cover.generate_cover(project_id='p', clip_id='1', cancel=cancel)
