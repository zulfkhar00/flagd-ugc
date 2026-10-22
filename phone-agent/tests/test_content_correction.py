import asyncio
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest

from flagd_phone.authorization import AuthorizationState
from flagd_phone.controller import ControlError, TaskController
from flagd_phone.model import GeminiVision
from flagd_phone.protocol import UncertainAction
from flagd_phone.runner import Journal, run_agent, validate_task
from flagd_phone.vision import Frame
from test_agent import FakePortal, decision, png64
from test_media_requirements import media, reel_task


def deletion_task():
    return validate_task({
        'goal': 'Delete the specific mismatched published Reel only', 'max_steps': 12,
        'authorization': {'app': 'Instagram', 'account': 'try.flagd', 'limits': {'delete': 1}},
        'deletion': {'target_id': 'bad-original', 'identity': {
            'required_text': ['You flirted. Then panicked and added “lol.”'],
            'any_text': ['YOU’RE REALLY QUIET.', 'Quiet doesn’t mean uninterested.'],
        }},
    })


def test_delete_requires_exact_target_scope_and_consumes_the_single_attempt():
    task = deletion_task()
    frame = Frame.from_portal(png64())
    state = AuthorizationState(task)
    deletion = decision(frame, x=20, y=30, effect='delete')
    with pytest.raises(ValueError, match='authorization'):
        state.check(deletion)
    state.record_before_input(decision(frame, 'confirm_account', app='Instagram', account='try.flagd', evidence='Own profile'))
    with pytest.raises(ValueError, match='exact published deletion target'):
        state.check(deletion)
    state.record_before_input(decision(frame, 'confirm_target', evidence='Matching caption and wrong media'))
    state.check(deletion)
    state.record_before_input(deletion)
    with pytest.raises(ValueError, match='authorization'):
        state.check(deletion)
    task['authorization']['limits']['delete'] = 2
    with pytest.raises(ValueError):
        validate_task(task)


@pytest.mark.parametrize('surface,caption,valid', [
    ('published_post', 'You flirted. Then panicked and added “lol.” Quiet doesn’t mean uninterested.', True),
    ('published_post', 'You’re quiet. Quiet doesn’t mean uninterested.', False),
    ('none', 'You flirted. Then panicked and added “lol.” Quiet doesn’t mean uninterested.', False),
    ('published_post', 'You flirted. Then panicked and added “lol.” FOR LEGAL PROTECTION.', False),
])
def test_visual_target_checker_requires_both_caption_and_wrong_media_on_published_item(surface, caption, valid):
    async def scenario():
        def handler(request):
            body = json.loads(request.content)
            assert body['contents'][0]['parts'][1]['inlineData']['mimeType'] == 'image/jpeg'
            value = {'valid': True, 'visible_text': caption, 'surface': surface, 'evidence': 'Current pixels'}
            return httpx.Response(200, json={'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(value)}]}}]})
        model = GeminiVision('test', 'gemini-test', client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            observed, _ = await model.verify_visual_identity(deletion_task(), Frame.from_portal(png64()), 'confirm_target')
            assert observed['valid'] is valid
        finally:
            await model.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('uncertain', [False, True])
def test_rejected_target_is_not_deleted_and_uncertain_delete_is_never_repeated(tmp_path, uncertain):
    async def scenario():
        class Portal(FakePortal):
            async def call(self, method, params=None, *, mutation=False):
                if method == 'tap' and uncertain:
                    self.calls.append((method, params, mutation))
                    raise UncertainAction('outcome unknown')
                return await super().call(method, params, mutation=mutation)

        class Model:
            calls = checks = 0
            async def verify_account(self, task, frame):
                return {'valid': True, 'evidence': 'Own profile'}, {}
            async def verify_visual_identity(self, task, frame, kind):
                self.checks += 1
                return {'valid': self.checks == 2, 'evidence': 'Wrong caption first; matching item second'}, {}
            async def decide(self, task, frame, history):
                self.calls += 1
                return [
                    decision(frame, 'confirm_account', app='Instagram', account='try.flagd', evidence='Own profile'),
                    decision(frame, 'confirm_target', evidence='Wrong post'),
                    decision(frame, 'confirm_target', evidence='Matching post'),
                    decision(frame, x=20, y=30, effect='delete'),
                    decision(frame, 'done', reason='Visible deletion success'),
                ][self.calls - 1], {}
        portal = Portal()
        result = await run_agent(portal, Model(), deletion_task(), Journal(tmp_path / 'deletion'), execute=True)
        assert result['status'] == ('uncertain_input' if uncertain else 'completion_candidate')
        assert result['public_action_attempts']['delete'] == 1
        assert result['public_action_attempts']['publish'] == 0
        assert [method for method, _, _ in portal.calls].count('tap') == 1
    asyncio.run(scenario())


def test_corrective_reel_cannot_publish_without_confirmed_selected_content():
    task = reel_task()
    task['authorization']['verification']['account'] = True
    task['publication']['content_identity'] = {'required_text': ['YOU FLIRTED. THEN ADDED “LOL” FOR LEGAL PROTECTION.'], 'any_text': []}
    frame = Frame.from_portal(png64())
    state = AuthorizationState(validate_task(task))
    state.record_before_input(decision(frame, 'confirm_account', app='Instagram', account='try.flagd', evidence='Own profile'))
    state.record_after_input(decision(frame, 'type_text', text_key='caption'))
    state.record_before_input(media(frame))
    with pytest.raises(ValueError, match='selected source content'):
        state.check(decision(frame, x=20, y=30, effect='publish'))


def test_replacement_needs_completed_matching_deletion_receipt_and_deletion_is_deduplicated(tmp_path):
    async def scenario():
        portal = FakePortal()
        portal.reader = SimpleNamespace(done=lambda: False)
        portal.uncertain = False
        controller = TaskController(SimpleNamespace(current=portal, device_id='phone'), object(), tmp_path / 'runs')
        original_id, removal_id = uuid.uuid4().hex, uuid.uuid4().hex
        task = reel_task()
        task['authorization']['verification']['account'] = True
        task['publication']['source_id'] = 'corrected-revision'
        task['publication']['content_identity'] = {'required_text': ['Correct cover'], 'any_text': []}
        task['replacement'] = {'previous_job_id': original_id, 'deletion_job_id': removal_id}
        original = reel_task()
        removal = deletion_task()
        removal['deletion']['target_id'] = original_id
        controller.jobs[original_id] = {'id': original_id, 'request_id': str(uuid.uuid4()), 'task': original,
            'state': 'finished', 'execute': True, 'result': {'status': 'completion_candidate', 'public_action_attempts': {'publish': 1}}}
        controller.jobs[removal_id] = {'id': removal_id, 'request_id': str(uuid.uuid4()), 'task': removal,
            'state': 'finished', 'execute': True, 'result': {'status': 'uncertain_input', 'public_action_attempts': {'delete': 1}, 'deletion_target_id': original_id}}
        try:
            with pytest.raises(ControlError, match='matching completed deletion'):
                controller.submit(task, execute=True, request_id=str(uuid.uuid4()))
            with pytest.raises(ControlError, match='already has a deletion'):
                controller.submit(removal, execute=True, request_id=str(uuid.uuid4()))
            controller.jobs[removal_id]['result']['status'] = 'completion_candidate'
            controller.recovery_required = True
            with pytest.raises(ControlError) as caught:
                controller.submit(task, execute=True, request_id=str(uuid.uuid4()))
            assert caught.value.code != 'replacement_not_ready'
        finally:
            await controller.close()
    asyncio.run(scenario())
