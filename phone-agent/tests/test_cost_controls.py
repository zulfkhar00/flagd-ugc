import asyncio
import base64
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from flagd_phone.model import GeminiVision, ModelOutputError, SYSTEM
from flagd_phone.prompts import decision_system
from flagd_phone.runner import Journal, execute_action, run_agent
from flagd_phone.vision import Frame, validate_action
from flagd_phone.workflows import browse_task
from test_agent import FakePortal, decision, png64


def reply(action, *, model, tokens=100):
    return httpx.Response(200, json={
        'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps({'action': action})}]}}],
        'modelVersion': model,
        'usageMetadata': {'promptTokenCount': tokens, 'candidatesTokenCount': 10,
                          'totalTokenCount': tokens + 10,
                          'promptTokensDetails': [{'modality': 'IMAGE', 'tokenCount': 40},
                                                 {'modality': 'TEXT', 'tokenCount': tokens - 40}]},
    })


def test_posting_copy_is_withheld_from_requests_but_inserted_in_full(tmp_path):
    async def scenario():
        caption = 'Short title\n\n' + 'Exact long copy with punctuation. ' * 100
        task = {'goal': 'Enter the supplied Instagram caption', 'mode': 'publish', 'max_steps': 3,
                'text_fields': {'caption': caption},
                'authorization': {'app': 'Instagram', 'account': 'try.flagd', 'limits': {'publish': 0},
                                  'verification': {'account': False, 'preview': False}},
                'publication': {'source_id': 'test-caption', 'slide_count': 6, 'required_text_fields': ['caption']}}
        requests = []
        def handler(request):
            body = json.loads(request.content)
            context = json.loads(body['contents'][0]['parts'][0]['text'])
            requests.append(body)
            assert caption not in request.content.decode()
            assert context['text_fields']['caption']['characters'] == len(caption)
            action = {'kind': 'type_text', 'frame_id': context['frame_id'], 'reason': 'Enter exact caption',
                      'expected_change': 'Caption entered', 'confidence': .95, 'requires_review': False,
                      'effect': 'none', 'text_key': 'caption'} if len(requests) == 1 else {
                      'kind': 'done', 'frame_id': context['frame_id'], 'reason': 'Caption present',
                      'expected_change': '', 'confidence': .95, 'requires_review': False, 'effect': 'none'}
            assert 'gemini-3.8-flash' in str(request.url)  # Posting retains the main model.
            return reply(action, model='gemini-3.8-flash')
        model = GeminiVision('test', 'gemini-3.8-flash', browse_model='gemini-3.1-flash-lite',
                             client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        portal = FakePortal()
        try:
            result = await run_agent(portal, model, task, Journal(tmp_path/'caption'), execute=True)
            inputs = [c for c in portal.calls if c[0] == 'keyboard/input']
            assert len(inputs) == 1
            assert base64.b64decode(inputs[0][1]['base64_text']).decode() == caption
            assert inputs[0][1]['clear'] is True
            assert result['entered_text_fields'] == ['caption']
        finally:
            await model.close()
    asyncio.run(scenario())


def test_full_caption_remains_available_when_explicit_preview_check_requires_it():
    frame = Frame.from_portal(png64())
    task = {'goal': 'Inspect caption', 'mode': 'publish', 'text_fields': {'caption': 'word ' * 400}}
    assert GeminiVision._context(task, frame, [])['text_fields'] == task['text_fields']
    assert len(decision_system({'mode': 'browse'}, legacy=SYSTEM)) < len(SYSTEM) / 2
    assert 'confirm_media' not in decision_system({'mode': 'browse'}, legacy=SYSTEM)


def test_fast_navigation_keeps_publishing_on_main_model_and_escalates_public_input():
    async def scenario():
        requested = []
        def handler(request):
            context = json.loads(json.loads(request.content)['contents'][0]['parts'][0]['text'])
            model_name = str(request.url).split('/models/')[1].split(':')[0]
            requested.append(model_name)
            common = {'frame_id': context['frame_id'], 'reason': 'Visible control',
                      'expected_change': 'Screen changes', 'confidence': .95,
                      'requires_review': False, 'effect': 'none'}
            action = ({**common, 'kind': 'tap', 'x': 100, 'y': 100, 'effect': 'like'}
                      if len(requested) == 2 else
                      {**common, 'kind': 'tap', 'x': 100, 'y': 100})
            return reply(action, model=model_name)
        model = GeminiVision('test', 'gemini-3.8-flash',
                             navigation_model='gemini-3.1-flash-lite',
                             client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        frame = Frame.from_portal(png64())
        try:
            await model.decide({'goal': 'Open Settings', 'mode': 'standard'}, frame, [])
            action, metadata = await model.decide({'goal': 'Find a post', 'mode': 'standard'}, frame, [])
            assert action['effect'] == 'none'
            assert metadata['api_calls'] == 2
            await model.decide({'goal': 'Publish', 'mode': 'publish'}, frame, [])
            assert requested == ['gemini-3.1-flash-lite', 'gemini-3.1-flash-lite',
                                 'gemini-3.8-flash', 'gemini-3.8-flash']
        finally:
            await model.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['low_confidence', 'invalid_json', 'handoff', 'exact_copy',
                                      'loading_input', 'public_input', 'comment_input'])
def test_browse_fallback_precedes_any_input_and_counts_both_requests(failure, tmp_path):
    async def scenario():
        requested = []
        def handler(request):
            body = json.loads(request.content)
            context = json.loads(body['contents'][0]['parts'][0]['text'])
            requested.append(str(request.url))
            common = {'frame_id': context['frame_id'], 'reason': 'Observed screen', 'expected_change': '',
                      'confidence': .95, 'requires_review': False, 'effect': 'none'}
            if len(requested) == 1:
                return reply({**common, 'kind': 'confirm_account', 'account': 'try.flagd', 'app': 'Instagram',
                              'evidence': '@try.flagd visible'}, model='gemini-3.1-flash-lite')
            if len(requested) == 2:
                action = ({**common, 'kind': 'tap', 'x': 100, 'y': 100, 'confidence': .5}
                          if failure == 'low_confidence' else
                          {**common, 'kind': 'type_query', 'query': 'conversation'} if failure == 'exact_copy' else
                          {**common, 'kind': 'swipe', 'x': 500, 'y': 800, 'end_x': 500, 'end_y': 400,
                           'reason': 'Content is still loading; close the panel'} if failure == 'loading_input' else
                          {**common, 'kind': 'tap', 'x': 900, 'y': 500, 'effect': 'like'} if failure == 'public_input' else
                          {**common, 'kind': 'type_comment', 'comment': 'How would you reply?',
                           'visible_context': 'What if she says idk?'} if failure == 'comment_input' else
                          {**common, 'kind': 'handoff'} if failure == 'handoff' else {'kind': 'unknown'})
                return reply(action, model='gemini-3.1-flash-lite', tokens=200)
            return reply({**common, 'kind': 'watch', 'duration_ms': 15000}, model='gemini-3.8-flash', tokens=300)
        model = GeminiVision('test', 'gemini-3.8-flash', browse_model='gemini-3.1-flash-lite',
                             client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        task = browse_task(app='Instagram', account='try.flagd', keywords=['conversation'], seconds=1)
        portal = FakePortal()
        try:
            result = await run_agent(portal, model, task, Journal(tmp_path/failure), execute=True)
            assert len(requested) == result['model_calls'] == 3
            assert 'gemini-3.8-flash' in requested[-1]
            assert not any(c[2] for c in portal.calls)  # Uncertain cheap-model tap was never sent.
            assert result['status'] == 'duration_limit'
            assert result['reported_total_tokens'] == 630
            assert result['token_counts']['promptTokenCount'] == 600
            assert result['model_usage']['gemini-3.1-flash-lite']['calls'] == 2
            assert result['model_usage']['gemini-3.8-flash']['calls'] == 1
        finally:
            await model.close()
    asyncio.run(scenario())


def test_failed_fallback_keeps_both_costs_and_no_phone_inputs():
    async def scenario():
        requested = []
        def handler(request):
            requested.append(str(request.url))
            return reply({'kind': 'bad'}, model='model', tokens=100)
        model = GeminiVision('test', 'gemini-3.8-flash', browse_model='gemini-3.1-flash-lite',
                             client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            with pytest.raises(ModelOutputError) as caught:
                await model.decide(browse_task(app='Instagram', account='try.flagd', keywords=['conversation']),
                                   Frame.from_portal(png64()), [])
            assert len(requested) == 2
            assert caught.value.metadata['api_calls'] == 2
            assert caught.value.metadata['usage']['totalTokenCount'] == 220
        finally:
            await model.close()
    asyncio.run(scenario())


def test_controller_rejection_routes_directly_to_main_model():
    async def scenario():
        def handler(request):
            assert 'gemini-3.8-flash' in str(request.url)
            body = json.loads(request.content)
            context = json.loads(body['contents'][0]['parts'][0]['text'])
            action = {'kind': 'wait', 'frame_id': context['frame_id'], 'duration_ms': 3000,
                      'reason': 'Reconsider fresh screen', 'expected_change': '', 'confidence': .9,
                      'requires_review': False, 'effect': 'none'}
            return reply(action, model='gemini-3.8-flash')
        model = GeminiVision('test', 'gemini-3.8-flash', browse_model='gemini-3.1-flash-lite',
                             client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        task = browse_task(app='Instagram', account='try.flagd', keywords=['conversation'])
        task['execution_context'] = {'decision_rejection': 'Account must be confirmed first'}
        try:
            _, metadata = await model.decide(task, Frame.from_portal(png64()), [])
            assert metadata['api_calls'] == 1
        finally:
            await model.close()
    asyncio.run(scenario())


def test_loading_wait_can_be_longer_without_extra_inference(monkeypatch):
    async def scenario():
        sleep = AsyncMock()
        monkeypatch.setattr('flagd_phone.runner.asyncio.sleep', sleep)
        frame = Frame.from_portal(png64())
        action = decision(frame, 'wait', duration_ms=6000)
        validate_action(action, frame, {})
        portal = FakePortal()
        await execute_action(portal, action, frame, {})
        sleep.assert_awaited_once_with(6.0)
        assert not portal.calls
        with pytest.raises(ValueError):
            validate_action(decision(frame, 'wait', duration_ms=11000), frame, {})
    asyncio.run(scenario())
