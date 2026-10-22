import asyncio
import base64
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest

from flagd_phone.controller import ControlError, TaskController
from flagd_phone.model import GeminiVision, VisionRequestError, VisionTransportError
from flagd_phone.runner import Journal, run_agent
from flagd_phone.vision import Frame
from test_agent import FakePortal, decision, png64
from test_media_requirements import media, reel_task


def test_account_checker_rejects_feed_author_even_when_the_requested_name_is_visible():
    async def scenario():
        def handler(request):
            body = json.loads(request.content)
            assert 'feed post' in body['systemInstruction']['parts'][0]['text']
            assert body['contents'][0]['parts'][1]['inlineData']['mimeType'] == 'image/jpeg'
            value = {'valid': True, 'account': 'try.flagd', 'source': 'none',
                     'evidence': 'Only a feed author and shared logo are visible.'}
            return httpx.Response(200, json={
                'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(value)}]}}],
            })
        model = GeminiVision('test', 'gemini-test', client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            result, _ = await model.verify_account(reel_task(), Frame.from_portal(png64()))
            assert result['valid'] is False
        finally:
            await model.close()
    asyncio.run(scenario())


def test_false_account_claim_is_rejected_before_it_can_unlock_publication(tmp_path):
    async def scenario():
        class Model:
            calls = 0
            checks = 0

            async def verify_account(self, task, frame):
                self.checks += 1
                return {'valid': self.checks == 2, 'evidence': 'Feed author is not the active account.'}, {}

            async def decide(self, task, frame, history):
                self.calls += 1
                actions = [
                    decision(frame, 'confirm_account', app='Instagram', account='try.flagd', evidence='Feed author'),
                    decision(frame, x=50, y=60),
                    decision(frame, 'confirm_account', app='Instagram', account='try.flagd', evidence='Own profile header'),
                    decision(frame, 'type_text', text_key='caption'), media(frame),
                    decision(frame, x=30, y=40, effect='publish'),
                    decision(frame, 'done', published_account='try.flagd'),
                ]
                if self.calls == 2:
                    assert task['execution_context']['account_confirmed'] is False
                    assert 'feed post' in task['execution_context']['decision_rejection']
                return actions[self.calls - 1], {}

        task = reel_task()
        task['authorization']['verification']['account'] = True
        portal = FakePortal()
        result = await run_agent(portal, Model(), task, Journal(tmp_path / 'account'), execute=True)
        assert result['status'] == 'completion_candidate'
        assert result['public_action_attempts']['publish'] == 1
        assert result['publication_account'] == 'try.flagd'
        assert result['model_calls'] == 9  # Seven decisions and two account assessments.
    asyncio.run(scenario())


def test_misdirected_success_does_not_complete_the_requested_publication(tmp_path):
    async def scenario():
        class Model:
            calls = 0

            async def decide(self, task, frame, history):
                self.calls += 1
                return [decision(frame, 'type_text', text_key='caption'), media(frame),
                        decision(frame, x=30, y=40, effect='publish'),
                        decision(frame, 'done', published_account='nomoreawkwardness')][self.calls - 1], {}

        result = await run_agent(FakePortal(), Model(), reel_task(), Journal(tmp_path / 'wrong-account'), execute=True)
        assert result['status'] == 'misdirected_publication'
        assert result['publication_account'] == 'nomoreawkwardness'
        assert result['public_action_attempts']['publish'] == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('named_destination', [True, False])
def test_reconciliation_preserves_the_post_receipt_and_requires_named_success_pixels(tmp_path, named_destination):
    async def scenario():
        portal = FakePortal()
        portal.reader = SimpleNamespace(done=lambda: False)
        portal.uncertain = False

        class Model:
            async def publication_destination(self, task, frame):
                return {'posted': named_destination, 'account': 'nomoreawkwardness' if named_destination else '',
                        'evidence': 'Posted to nomoreawkwardness.' if named_destination else 'No destination visible'}, {}

        controller = TaskController(SimpleNamespace(current=portal, device_id='phone'), Model(), tmp_path / 'runs')
        job_id = uuid.uuid4().hex
        task = reel_task()
        directory = controller.runs_dir / ('task-' + job_id)
        directory.mkdir()
        (directory / '002.png').write_bytes(base64.b64decode(png64()))
        (directory / 'trace.jsonl').write_text('\n'.join(json.dumps(row) for row in (
            {'event': 'input_intent', 'step': 1, 'action': {'effect': 'publish'}},
            {'event': 'input_acknowledged', 'step': 1},
            {'event': 'observation', 'step': 2},
        )))
        controller.jobs[job_id] = {'id': job_id, 'request_id': str(uuid.uuid4()), 'task': task,
                                   'state': 'finished', 'execute': True, 'run_dir': str(directory),
                                   'result': {'status': 'completion_candidate', 'public_action_attempts': {'publish': 1}}}
        try:
            if not named_destination:
                with pytest.raises(ControlError, match='do not name'):
                    await controller.reconcile_publication(job_id)
                assert 'publication_reconciliation' not in controller.jobs[job_id]
            else:
                await controller.reconcile_publication(job_id)
                saved = controller.jobs[job_id]
                assert saved['result']['status'] == 'misdirected_publication'
                assert saved['result']['public_action_attempts']['publish'] == 1
                assert saved['task']['authorization']['account'] == 'try.flagd'
                wrong_account_task = json.loads(json.dumps(task))
                wrong_account_task['authorization']['account'] = 'nomoreawkwardness'
                with pytest.raises(ControlError, match='already has a publication'):
                    controller.submit(wrong_account_task, execute=True, request_id=str(uuid.uuid4()))
                # The intended account has no publication of this source yet.
                reply = controller.submit(task, execute=True, request_id=str(uuid.uuid4()))
                assert reply['job']['id'] != job_id
            assert portal.calls == []  # Reconciliation never reads or operates the live phone.
        finally:
            await controller.close()
    asyncio.run(scenario())


def test_temporary_model_service_failure_retries_only_inference_and_counts_every_request(monkeypatch):
    async def scenario():
        calls = []

        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(503, json={'error': {'status': 'UNAVAILABLE'}})
            context = json.loads(json.loads(request.content)['contents'][0]['parts'][0]['text'])
            action = {'kind': 'tap', 'frame_id': context['frame_id'], 'x': 10, 'y': 10,
                      'reason': 'Visible control', 'expected_change': '', 'confidence': .95, 'requires_review': False}
            return httpx.Response(200, json={
                'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps({'action': action})}]}}],
            })

        async def sleep(_):
            pass

        monkeypatch.setattr('flagd_phone.model.asyncio.sleep', sleep)
        model = GeminiVision('test', 'gemini-test', client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            _, metadata = await model.decide({'goal': 'Open the app'}, Frame.from_portal(png64()), [])
            assert metadata['api_calls'] == 2
            assert [r['http_status'] for r in metadata['requests']] == [503, 200]
        finally:
            await model.close()
    asyncio.run(scenario())


def test_nontransient_model_errors_are_recorded_without_retrying_or_exposing_the_response():
    async def scenario():
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(403, json={'error': {'message': 'private provider details'}})

        model = GeminiVision('test', 'gemini-test', client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            with pytest.raises(VisionRequestError) as caught:
                await model.decide({'goal': 'Open the app'}, Frame.from_portal(png64()), [])
            assert len(calls) == 1
            assert caught.value.metadata['http_status'] == 403
            assert 'private' not in str(caught.value)
        finally:
            await model.close()
    asyncio.run(scenario())


def test_read_only_vision_request_recovers_from_a_network_timeout(monkeypatch):
    async def scenario():
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ReadTimeout("temporary stall")
            return httpx.Response(200, json={
                'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
                    {'text': json.dumps({'ok': True})}
                ]}}],
                'usageMetadata': {'totalTokenCount': 2},
            })

        async def no_sleep(_):
            pass

        monkeypatch.setattr('flagd_phone.model.asyncio.sleep', no_sleep)
        model = GeminiVision('test', 'gemini-test', client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            value, metadata = await model._request('system', {'goal': 'test'},
                                                    {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}})
            assert value == {'ok': True}
            assert metadata['api_calls'] == 2
            assert metadata['requests'][0]['transport_error'] is True
        finally:
            await model.close()

        def always_timeout(request):
            raise httpx.ReadTimeout('provider did not answer')

        broken = GeminiVision('test', 'gemini-test',
                              client=httpx.AsyncClient(transport=httpx.MockTransport(always_timeout)))
        try:
            with pytest.raises(VisionTransportError) as caught:
                await broken._request('system', {'goal': 'test'},
                                      {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}})
            assert caught.value.metadata['api_calls'] == 3
            assert 'provider did not answer' not in str(caught.value)
        finally:
            await broken.close()

    asyncio.run(scenario())
