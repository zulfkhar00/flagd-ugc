import json
import ssl

import httpx
import pytest

from flagd_ugc.errors import ProviderError, ProviderValidationError
from flagd_ugc.fal_provider import FalKlingProvider


def test_fal_kling_uses_three_seconds_audio_off_and_downloads(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "request_id": "fal-job-1",
                    "status_url": "https://queue.test/status",
                    "response_url": "https://queue.test/response",
                    "cancel_url": "https://queue.test/cancel",
                },
            )
        if request.url.path == "/status":
            return httpx.Response(
                200, json={"status": "COMPLETED", "request_id": "fal-job-1"}
            )
        if request.url.path == "/response":
            return httpx.Response(
                200, json={"video": {"url": "https://cdn.test/opening.mp4"}}
            )
        return httpx.Response(200, content=b"mp4-data")

    provider = FalKlingProvider(
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    scene = tmp_path / "scene.png"
    scene.write_bytes(b"\xff\xd8\xffimage")
    output = tmp_path / "opening.mp4"

    job = provider.submit(scene, "small natural reaction", duration=3)
    provider.wait_and_download(job, output)

    body = json.loads(requests[0].content)
    assert requests[0].headers["authorization"] == "Key secret"
    assert body["start_image_url"].startswith("data:image/jpeg;base64,")
    assert body["duration"] == "3"
    assert body["generate_audio"] is False
    assert body["shot_type"] == "customize"
    assert "silent mouthing" in body["negative_prompt"]
    assert "hand entering frame" in body["negative_prompt"]
    assert all(request.headers["connection"] == "close" for request in requests)
    assert output.read_bytes() == b"mp4-data"
    assert sum(request.method == "POST" for request in requests) == 1


def test_fal_kling_can_allow_prompted_hand_entry(tmp_path):
    submitted = []

    def handler(request):
        submitted.append(json.loads(request.content))
        return httpx.Response(200, json={"request_id": "fal-job-hand"})

    provider = FalKlingProvider(
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    scene = tmp_path / "scene.png"
    scene.write_bytes(b"\xff\xd8\xffimage")

    provider.submit(
        scene,
        "one hand rises naturally to cover her mouth",
        duration=3,
        allow_hand_entry=True,
    )

    assert "hand entering frame" not in submitted[0]["negative_prompt"]
    assert "deformed hands" in submitted[0]["negative_prompt"]


def test_fal_rejects_overlong_prompt_before_submission(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"request_id": "must-not-submit"})

    provider = FalKlingProvider(
        "secret", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    scene = tmp_path / "scene.png"
    scene.write_bytes(b"\xff\xd8\xffimage")

    with pytest.raises(ProviderValidationError, match="2,500-character limit"):
        provider.submit(scene, "x" * 2501, duration=3)

    assert requests == []


def test_fal_resume_path_has_no_second_submission(tmp_path):
    methods = []

    def handler(request):
        methods.append(request.method)
        if request.url.path == "/status":
            return httpx.Response(200, json={"status": "COMPLETED"})
        if request.url.path == "/response":
            return httpx.Response(
                200, json={"video": {"url": "https://cdn.test/opening.mp4"}}
            )
        return httpx.Response(200, content=b"mp4-data")

    from flagd_ugc.fal_provider import FalJob

    provider = FalKlingProvider(
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    job = FalJob(
        "existing",
        "https://queue.test/status",
        "https://queue.test/response",
        "https://queue.test/cancel",
    )

    provider.wait_and_download(job, tmp_path / "opening.mp4")

    assert "POST" not in methods


def test_fal_polling_retries_a_temporary_connection_failure(tmp_path):
    status_calls = 0

    def handler(request):
        nonlocal status_calls
        if request.url.path == "/status":
            status_calls += 1
            if status_calls == 1:
                raise httpx.ConnectError("temporary", request=request)
            return httpx.Response(200, json={"status": "COMPLETED"})
        if request.url.path == "/response":
            return httpx.Response(
                200, json={"video": {"url": "https://cdn.test/opening.mp4"}}
            )
        return httpx.Response(200, content=b"mp4-data")

    from flagd_ugc.fal_provider import FalJob

    provider = FalKlingProvider(
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    job = FalJob(
        "existing",
        "https://queue.test/status",
        "https://queue.test/response",
        "https://queue.test/cancel",
    )

    provider.wait_and_download(job, tmp_path / "opening.mp4")

    assert status_calls == 2


def test_fal_polling_retries_html_proxy_403_with_backoff(tmp_path):
    status_calls = 0
    sleeps = []

    def handler(request):
        nonlocal status_calls
        if request.url.path == "/status":
            status_calls += 1
            if status_calls == 1:
                return httpx.Response(
                    403,
                    headers={"content-type": "text/html; charset=utf-8"},
                    text="<html><div class='ns-template-wrap'>Blocked</div></html>",
                )
            return httpx.Response(200, json={"status": "COMPLETED"})
        if request.url.path == "/response":
            return httpx.Response(
                200, json={"video": {"url": "https://cdn.test/opening.mp4"}}
            )
        return httpx.Response(200, content=b"mp4-data")

    from flagd_ugc.fal_provider import FalJob

    provider = FalKlingProvider(
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
    )
    job = FalJob(
        "existing",
        "https://queue.test/status",
        "https://queue.test/response",
        "https://queue.test/cancel",
    )

    provider.wait_and_download(job, tmp_path / "opening.mp4")

    assert status_calls == 2
    assert sleeps == [180.0]


def test_bloomberg_policy_block_is_not_retried(tmp_path):
    status_calls = 0
    sleeps = []

    def handler(request):
        nonlocal status_calls
        status_calls += 1
        return httpx.Response(
            403,
            headers={"content-type": "text/html; charset=utf-8"},
            text=(
                "<html><title>Bloomberg Proxy Exception Page - Access Denied</title>"
                "<!--X-BBG-BLOCK--><div>Access denied: cust_policy_denied</div>"
                "<!--Blocked Category: Suspicious --></html>"
            ),
        )

    from flagd_ugc.fal_provider import FalJob

    provider = FalKlingProvider(
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
    )
    job = FalJob(
        "existing",
        "https://queue.test/status",
        "https://queue.test/response",
        "https://queue.test/cancel",
    )

    with pytest.raises(ProviderError, match="denied by Bloomberg web policy"):
        provider.wait_and_download(job, tmp_path / "opening.mp4")

    assert status_calls == 1
    assert sleeps == []


def test_new_fal_submission_waits_before_first_status_poll(tmp_path):
    sleeps = []

    def handler(request):
        if request.url.path == "/status":
            return httpx.Response(200, json={"status": "COMPLETED"})
        if request.url.path == "/response":
            return httpx.Response(
                200, json={"video": {"url": "https://cdn.test/opening.mp4"}}
            )
        return httpx.Response(200, content=b"mp4-data")

    from flagd_ugc.fal_provider import FalJob

    provider = FalKlingProvider(
        "secret",
        initial_poll_delay=180,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
    )
    job = FalJob(
        "new",
        "https://queue.test/status",
        "https://queue.test/response",
        "https://queue.test/cancel",
    )

    provider.wait_and_download(
        job,
        tmp_path / "opening.mp4",
        new_submission=True,
    )

    assert sleeps == [180]


def test_fal_can_retrieve_completed_result_without_status_request(tmp_path):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/response":
            return httpx.Response(
                200, json={"video": {"url": "https://cdn.test/opening.mp4"}}
            )
        if request.url.path == "/status":
            raise AssertionError("status endpoint should not be called")
        return httpx.Response(200, content=b"mp4-data")

    from flagd_ugc.fal_provider import FalJob

    provider = FalKlingProvider(
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    job = FalJob(
        "completed",
        "https://queue.test/status",
        "https://queue.test/response",
        "https://queue.test/cancel",
    )

    provider.wait_and_download(
        job,
        tmp_path / "opening.mp4",
        prefer_result_endpoint=True,
    )

    assert paths == ["/response", "/opening.mp4"]


def test_proxy_blocked_result_falls_back_to_fresh_status_request(tmp_path):
    paths = []
    response_calls = 0

    def handler(request):
        nonlocal response_calls
        paths.append(request.url.path)
        assert request.headers["connection"] == "close"
        if request.url.path == "/response":
            response_calls += 1
            if response_calls == 1:
                return httpx.Response(
                    403,
                    headers={"content-type": "text/html"},
                    text="<html><div class='ns-template-wrap'>Blocked</div></html>",
                )
            return httpx.Response(
                200, json={"video": {"url": "https://cdn.test/opening.mp4"}}
            )
        if request.url.path == "/status":
            return httpx.Response(200, json={"status": "COMPLETED"})
        return httpx.Response(200, content=b"mp4-data")

    from flagd_ugc.fal_provider import FalJob

    provider = FalKlingProvider(
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    job = FalJob(
        "existing",
        "https://queue.test/status",
        "https://queue.test/response",
        "https://queue.test/cancel",
    )

    provider.wait_and_download(
        job,
        tmp_path / "opening.mp4",
        prefer_result_endpoint=True,
    )

    assert paths == ["/response", "/status", "/response", "/opening.mp4"]


def test_fal_polling_outlives_more_than_configured_read_retries(tmp_path):
    status_calls = 0

    def handler(request):
        nonlocal status_calls
        if request.url.path == "/status":
            status_calls += 1
            if status_calls <= 5:
                raise httpx.ConnectError("temporary", request=request)
            return httpx.Response(200, json={"status": "COMPLETED"})
        if request.url.path == "/response":
            return httpx.Response(
                200, json={"video": {"url": "https://cdn.test/opening.mp4"}}
            )
        return httpx.Response(200, content=b"mp4-data")

    from flagd_ugc.fal_provider import FalJob

    provider = FalKlingProvider(
        "secret",
        transient_retries=1,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    job = FalJob(
        "existing",
        "https://queue.test/status",
        "https://queue.test/response",
        "https://queue.test/cancel",
    )

    provider.wait_and_download(job, tmp_path / "opening.mp4")

    assert status_calls == 6


def test_fal_connection_check_forces_fresh_unpaid_requests():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(404)

    provider = FalKlingProvider(
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = provider.check_connection(3)

    assert result["status"] == "ok"
    assert result["fresh_tls_connections"] == 3
    assert len(requests) == 3
    assert all(request.method == "GET" for request in requests)
    assert all("authorization" not in request.headers for request in requests)
    assert all(request.headers["connection"] == "close" for request in requests)


def test_fal_queue_access_check_accepts_unpaid_not_found_probe():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(404, json={"detail": "request not found"})

    provider = FalKlingProvider(
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = provider.check_queue_access()

    assert result == {"status": "ok", "http_status": 404}
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path.endswith(
        "/requests/00000000-0000-0000-0000-000000000000/status"
    )


def test_fal_queue_access_check_accepts_reachable_method_not_allowed_probe():
    provider = FalKlingProvider(
        "secret",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(405))
        ),
    )

    assert provider.check_queue_access() == {
        "status": "ok",
        "http_status": 405,
    }


def test_fal_queue_access_check_rejects_bloomberg_policy_page():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            403,
            headers={"content-type": "text/html"},
            text=(
                "<html><title>Bloomberg Proxy Exception Page</title>"
                "<!--X-BBG-BLOCK--><div>cust_policy_denied</div></html>"
            ),
        )

    provider = FalKlingProvider(
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ProviderError, match=r"queue.fal.run and \*.fal.media"):
        provider.check_queue_access()

    assert calls == 1


def test_certificate_failure_is_not_retried_forever(tmp_path):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        certificate_error = ssl.SSLCertVerificationError(
            1, "certificate verify failed: self-signed certificate in certificate chain"
        )
        raise httpx.ConnectError("TLS failed", request=request) from certificate_error

    from flagd_ugc.fal_provider import FalJob

    provider = FalKlingProvider(
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    job = FalJob(
        "existing",
        "https://queue.test/status",
        "https://queue.test/response",
        "https://queue.test/cancel",
    )

    with pytest.raises(ProviderError, match="do not disable TLS verification"):
        provider.wait_and_download(job, tmp_path / "opening.mp4")

    assert calls == 1
