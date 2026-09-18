import base64
import json

import httpx
import pytest

from flagd_ugc.assets import Demo
from flagd_ugc.errors import QualityError
from flagd_ugc.hook_formats import get_hook_format
from flagd_ugc.providers import GeminiProvider, KlingProvider


def test_gemini_generates_image_with_reference_and_requested_format(tmp_path):
    requests = []
    expected = b"generated-image"

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "done"},
                                {
                                    "inlineData": {
                                        "mimeType": "image/png",
                                        "data": base64.b64encode(expected).decode(),
                                    }
                                },
                            ]
                        }
                    }
                ]
            },
        )

    reference = tmp_path / "master.jpeg"
    output = tmp_path / "scene.png"
    reference.write_bytes(b"\xff\xd8\xffreference")
    provider = GeminiProvider(
        "secret",
        text_model="gemini-2.5-flash",
        image_model="gemini-3-pro-image",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    provider.generate_image(
        "frame zero",
        [reference],
        output,
        aspect_ratio="9:16",
        image_size="2K",
    )

    assert output.read_bytes() == expected
    body = requests[0]
    assert body["generationConfig"]["imageConfig"] == {
        "aspectRatio": "9:16",
        "imageSize": "2K",
    }
    assert body["contents"][0]["parts"][0] == {"text": "frame zero"}
    assert body["contents"][0]["parts"][1]["inline_data"]["mime_type"] == ("image/jpeg")


def test_gemini_model_check_is_read_only():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "name": "models/gemini-3-pro-image",
                "displayName": "Nano Banana Pro",
                "supportedGenerationMethods": ["generateContent"],
            },
        )

    provider = GeminiProvider(
        "secret",
        text_model="gemini-2.5-flash",
        image_model="gemini-3-pro-image",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = provider.check_image_model()

    assert result["name"] == "models/gemini-3-pro-image"
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path.endswith("/models/gemini-3-pro-image")


def test_hook_generation_requires_the_assigned_format_and_reviews_it(tmp_path):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": json.dumps(
                                            [
                                                {
                                                    "demo_file": "demo.mp4",
                                                    "hook_format": "when",
                                                    "hook": "when i get the feedback",
                                                    "title": "the practice date feedback",
                                                }
                                            ]
                                        )
                                    }
                                ]
                            }
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": '{"passed": true, "reason": "ok"}'}]
                        }
                    }
                ]
            },
        )

    provider = GeminiProvider(
        "secret",
        text_model="gemini-3.6-flash",
        image_model="gemini-3-pro-image",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = provider.generate_hooks(
        [Demo(tmp_path / "demo.mp4", "The app gives clear feedback after a practice date.")],
        formats=[get_hook_format("when")],
    )

    assert result == [
        {
            "demo_path": str(tmp_path / "demo.mp4"),
            "hook_format": "when",
            "hook": "when i get the feedback",
            "title": "the practice date feedback",
        }
    ]
    prompt = requests[0]["contents"][0]["parts"][0]["text"]
    assert '"hook_format": "when"' in prompt
    assert '"title"' in prompt
    assert "Start exactly with `when `" in prompt
    assert len(requests) == 2


def test_visual_review_requires_self_shot_and_rejects_branched_limbs(tmp_path):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "passed": False,
                                            "consistent": True,
                                            "defect_free": False,
                                            "self_shot": True,
                                            "reason": "camera arm branches into an extra hand",
                                        }
                                    )
                                }
                            ]
                        }
                    }
                ]
            },
        )

    generated = tmp_path / "generated.png"
    reference = tmp_path / "master.jpeg"
    generated.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    reference.write_bytes(b"\xff\xd8\xffreference")
    provider = GeminiProvider(
        "secret",
        text_model="gemini-2.5-flash",
        image_model="gemini-3-pro-image",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(QualityError, match="camera arm branches"):
        provider.review_visual(
            [generated],
            [reference],
            kind="self-shot front-camera opening scene",
        )

    prompt = requests[0]["contents"][0]["parts"][0]["text"]
    assert "plausible camera-bearing arm" in prompt
    assert "branched limb" in prompt
    assert "25+" not in prompt


def test_kling_persists_submitted_task_and_downloads_result(tmp_path):
    requests = []

    def handler(request):
        requests.append((request.method, str(request.url)))
        if request.method == "POST":
            return httpx.Response(200, json={"data": {"task_id": "job-1"}})
        if str(request.url).endswith("/videos/image2video/job-1"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "task_status": "succeed",
                        "task_result": {
                            "videos": [{"url": "https://cdn.test/video.mp4"}]
                        },
                    }
                },
            )
        return httpx.Response(200, content=b"mp4-data")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = KlingProvider(
        "secret",
        base_url="https://kling.test/v1",
        model="kling-v3",
        client=client,
        sleep=lambda _: None,
    )
    scene = tmp_path / "scene.png"
    output = tmp_path / "opening.mp4"
    scene.write_bytes(b"image")
    submitted = []

    provider.animate(scene, output, on_submitted=submitted.append)

    assert submitted == ["job-1"]
    assert output.read_bytes() == b"mp4-data"
    assert sum(method == "POST" for method, _ in requests) == 1


def test_kling_resume_does_not_submit_a_second_paid_job(tmp_path):
    methods = []

    def handler(request):
        methods.append(request.method)
        if "cdn.test" in str(request.url):
            return httpx.Response(200, content=b"mp4-data")
        return httpx.Response(
            200,
            json={
                "data": {
                    "task_status": "succeed",
                    "task_result": {"videos": [{"url": "https://cdn.test/video.mp4"}]},
                }
            },
        )

    provider = KlingProvider(
        "secret",
        base_url="https://kling.test/v1",
        model="kling-v3",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    scene = tmp_path / "scene.png"
    scene.write_bytes(b"image")

    provider.animate(scene, tmp_path / "opening.mp4", task_id="existing-job")

    assert "POST" not in methods


def test_kling_uses_the_supplied_direction_motion_prompt(tmp_path):
    submission = {}

    def handler(request):
        if request.method == "POST":
            submission.update(json.loads(request.content))
            return httpx.Response(200, json={"data": {"task_id": "job-1"}})
        if str(request.url).endswith("/videos/image2video/job-1"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "task_status": "succeed",
                        "task_result": {
                            "videos": [{"url": "https://cdn.test/video.mp4"}]
                        },
                    }
                },
            )
        return httpx.Response(200, content=b"mp4-data")

    provider = KlingProvider(
        "secret",
        base_url="https://kling.test/v1",
        model="kling-v3",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    scene = tmp_path / "scene.png"
    scene.write_bytes(b"image")

    provider.animate(
        scene,
        tmp_path / "opening.mp4",
        motion_prompt="perform one silent restrained facepalm",
    )

    assert "perform one silent restrained facepalm" in submission["prompt"]
