import importlib.util
from pathlib import Path
import wave

import numpy as np
from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dragon_avatar_test', ROOT/'scripts/dragon_avatar_server.py')
avatar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(avatar)


def test_silence_never_opens_mouth_and_sound_drives_envelope(tmp_path):
    path = tmp_path/'voice.wav'
    samples = np.concatenate([np.zeros(8000), np.sin(np.arange(8000)*.2)*8000]).astype('<i2')
    with wave.open(str(path),'wb') as wav:
        wav.setparams((1,2,16000,len(samples),'NONE','not compressed'))
        wav.writeframes(samples.tobytes())
    duration, levels = avatar.envelope(path)
    assert duration == 1 and len(levels) == 50
    assert max(levels[:25]) == 0
    assert max(levels[25:]) > .8


def test_origin_and_host_protect_control_from_other_websites():
    client = TestClient(avatar.app)
    assert client.get('/api/status').json()['service'] == 'dragon-maid-2d-v1'
    assert client.post('/api/speak', json={'text':'test'}).status_code == 403
    assert client.get('/api/status',headers={'host':'evil.example'}).status_code == 403
    assert client.post('/api/speak',json={'text':''},headers={'origin':'http://127.0.0.1:6187'}).status_code==422


def test_websocket_state_clamps_values_and_rejects_unknown_expression():
    client=TestClient(avatar.app)
    with client.websocket_connect('/ws',headers={'host':'127.0.0.1:6187','origin':'http://127.0.0.1:6187'}) as ws:
        assert ws.receive_json()['type']=='state'
        ws.send_json({'type':'expression','value':'cry'})
        assert ws.receive_json()['expression']=='cry'
        ws.send_json({'type':'zoom','value':999})
        assert ws.receive_json()['zoom']==1.7
        ws.send_json({'type':'expression','value':'invalid'})
        ws.send_json({'type':'mouth','value':.8})
        changed=ws.receive_json()
        assert changed['expression']=='cry' and changed['mouth']==.8 and changed['mouth_at']>0
    avatar.state.update(expression='neutral',zoom=1,mouth=0,mouth_at=0)


def test_expired_voice_is_not_replayed_to_new_obs_source():
    avatar.state['voice']={'start':1,'duration':1}
    assert avatar.snapshot()['voice'] is None


async def test_audio_has_one_output_even_with_desktop_and_obs(tmp_path):
    p=tmp_path/'test.wav'
    with wave.open(str(p),'wb') as w:
        w.setparams((1,2,16000,1600,'NONE','not compressed'));w.writeframes(b'\0\0'*1600)
    avatar.client_roles['test-desktop']='desktop'
    avatar.client_roles['test-obs']='obs'
    await avatar.publish_audio(p,preview=True)
    assert avatar.state['voice']['output']=='browser'
    await avatar.publish_audio(p)
    assert avatar.state['voice']['output']=='desktop'
    avatar.client_roles.clear();avatar.state['voice']=None


def test_live_room_rejects_unconfigured_room_without_speech(monkeypatch):
    monkeypatch.setattr(avatar,'configured_live_room',lambda:0)
    client=TestClient(avatar.app)
    r=client.post('/api/live-reply',json={'event_id':'a','room_id':123,'text':'test'},
                  headers={'origin':'http://127.0.0.1:6187'})
    assert r.status_code==409
