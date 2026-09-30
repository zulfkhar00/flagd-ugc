from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

from .assets import Demo
from .camera_style import (
    US_RESIDENTIAL_ENVIRONMENT_INSTRUCTION,
    with_handheld_motion,
    with_handheld_scene,
)
from .errors import ProviderError, QualityError, TransientError
from .hook_formats import HOOK_FORMATS, HookFormat, get_hook_format, visible_hook_text
from .hooks import validate_hook, validate_title


def _json_from_text(text: str) -> Any:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise QualityError(f"provider did not return valid JSON: {exc}") from exc


def _mime(path: Path) -> str:
    with path.open("rb") as handle:
        header = handle.read(16)
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    guessed = mimetypes.guess_type(path.name)[0]
    return guessed or "application/octet-stream"


def _inline_image(path: Path) -> dict[str, Any]:
    return {
        "inline_data": {
            "mime_type": _mime(path),
            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
        }
    }


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _exception_chain(exc: BaseException) -> str:
    """Keep actionable socket/TLS details without exposing request credentials."""
    details: list[str] = []
    current: BaseException | None = exc
    while current is not None:
        name = type(current).__name__
        message = str(current).strip()
        detail = f"{name}: {message}" if message else name
        if detail not in details:
            details.append(detail)
        current = current.__cause__ or current.__context__
    return " -> ".join(details)


class GeminiProvider:
    def __init__(
        self,
        api_key: str,
        *,
        text_model: str,
        image_model: str,
        timeout: float = 120.0,
        client: httpx.Client | None = None,
    ):
        self.api_key = api_key
        self.text_model = text_model
        self.image_model = image_model
        self.client = client or httpx.Client(timeout=timeout)

    def _generate(
        self, model: str, parts: list[dict[str, Any]], generation_config: dict[str, Any]
    ) -> dict[str, Any]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            response = self.client.post(
                url,
                headers={"x-goog-api-key": self.api_key},
                json={
                    "contents": [{"role": "user", "parts": parts}],
                    "generationConfig": generation_config,
                },
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise TransientError(
                f"Gemini request failed: {_exception_chain(exc)}"
            ) from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise TransientError(
                f"Gemini temporarily unavailable ({response.status_code})"
            )
        if response.is_error:
            raise ProviderError(
                f"Gemini rejected the request ({response.status_code}): {response.text[:1000]}"
            )
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise ProviderError("Gemini returned a non-JSON response") from exc

    def check_image_model(self) -> dict[str, Any]:
        """Check network, API-key access, and model availability without inference."""
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.image_model}"
        )
        try:
            response = self.client.get(
                url,
                headers={"x-goog-api-key": self.api_key},
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise TransientError(
                f"Gemini connectivity check failed: {_exception_chain(exc)}"
            ) from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise TransientError(
                f"Gemini connectivity check temporarily failed ({response.status_code})"
            )
        if response.is_error:
            raise ProviderError(
                "Gemini model check failed "
                f"({response.status_code}): {response.text[:1000]}"
            )
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderError("Gemini model check returned non-JSON") from exc
        if not isinstance(payload, dict):
            raise ProviderError("Gemini model check returned an invalid response")
        return payload

    @staticmethod
    def _parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            return list(payload["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, TypeError) as exc:
            feedback = payload.get("promptFeedback", {})
            raise QualityError(
                f"Gemini produced no usable candidate: {feedback}"
            ) from exc

    def _text_json(self, prompt: str) -> Any:
        payload = self._generate(
            self.text_model,
            [{"text": prompt}],
            {"temperature": 0.4, "responseMimeType": "application/json"},
        )
        text = "".join(str(part.get("text", "")) for part in self._parts(payload))
        return _json_from_text(text)

    def generate_hooks(
        self,
        demos: list[Demo],
        *,
        formats: list[HookFormat] | tuple[HookFormat, ...] | None = None,
    ) -> list[dict[str, str]]:
        """Generate grounded copy for pre-assigned, distinct caption formats."""
        assigned_formats = tuple(
            HOOK_FORMATS[: len(demos)] if formats is None else formats
        )
        if len(assigned_formats) != len(demos):
            raise ValueError("each hook demo needs exactly one assigned format")
        if len({item.key for item in assigned_formats}) != len(assigned_formats):
            raise QualityError("a hook batch may not repeat a caption format")
        demo_payload = [
            {
                "file": demo.path.name,
                "truthful_description": demo.description,
                "hook_format": format_definition.key,
                "format_rule": format_definition.prompt_rule,
                "example_shape_only": format_definition.example,
            }
            for demo, format_definition in zip(demos, assigned_formats, strict=True)
        ]
        prompt = f"""
You create truthful short opening hooks for flagd, an app whose exact behavior is described
only by the supplied approved demonstration descriptions. Write exactly one hook for each demo.
Each hook must:
- use the exact assigned hook_format and obey its format_rule; the on-camera woman is the
  narrator, but familiar TikTok shorthand such as POV or `when ...` need not use I/me/my;
- contain fewer than 12 words;
- be lower-case, conversational, distinct, and safe;
- make no guarantee, medical claim, invented result, or claim unsupported by its demo;
- contain no emoji, hashtag, CTA, stage direction, logo, or brand claim;
- never invent a score. A scorecard can use only the exact number stated in its demo description.

Also provide a separate upload title for every item. A title must:
- contain 3–8 lower-case words;
- be a factual, neutral label for a person manually uploading this exact demo;
- be distinct from the on-video hook and grounded only in the matching demo description;
- contain no emoji, hashtag, CTA, URL, quotation marks, invented score, or engagement claim.

Return only a JSON array of objects with exactly these keys: "demo_file", "hook_format", "hook", "title".
Use every supplied demo_file exactly once. Copy its filename and assigned hook_format exactly.
For quote-receipt, return only the quote body; the renderer adds the quotation marks and `— him`.

Approved demonstrations:
{json.dumps(demo_payload, ensure_ascii=False)}
""".strip()
        payload = self._text_json(prompt)
        if not isinstance(payload, list) or len(payload) != len(demos):
            raise QualityError(f"expected {len(demos)} hooks")
        allowed = {
            demo.path.name: (demo, format_definition)
            for demo, format_definition in zip(demos, assigned_formats, strict=True)
        }
        seen: set[str] = set()
        seen_titles: set[str] = set()
        results: list[dict[str, str]] = []
        for item in payload:
            if not isinstance(item, dict):
                raise QualityError("hook response contains a non-object")
            if set(item) != {"demo_file", "hook_format", "hook", "title"}:
                raise QualityError(
                    "hook response must contain only demo_file, hook_format, hook, title"
                )
            filename = str(item.get("demo_file", ""))
            if filename not in allowed or filename in seen:
                raise QualityError("hook response changed or repeated a demo filename")
            seen.add(filename)
            demo, format_definition = allowed[filename]
            format_key = str(item.get("hook_format", ""))
            if format_key != format_definition.key:
                raise QualityError(
                    f"hook response changed assigned format for {filename}: "
                    f"expected {format_definition.key!r}, got {format_key!r}"
                )
            hook = validate_hook(
                str(item.get("hook", "")),
                hook_format=format_key,
                demo_description=demo.description,
            )
            title = validate_title(
                str(item.get("title", "")),
                demo_description=demo.description,
            )
            if title.casefold() in {
                hook.casefold(),
                visible_hook_text(hook, format_key).casefold(),
            }:
                raise QualityError("upload title must be separate from the on-video hook")
            if title.casefold() in seen_titles:
                raise QualityError("hook response repeated an upload title")
            seen_titles.add(title.casefold())
            results.append(
                {
                    "demo_path": str(demo.path),
                    "hook": hook,
                    "hook_format": format_key,
                    "title": title,
                }
            )
        self.review_hooks(
            results, {filename: assignment[0] for filename, assignment in allowed.items()}
        )
        return results

    def review_hooks(self, hooks: list[dict[str, str]], demos: dict[str, Demo]) -> None:
        descriptions = {demo.path.name: demo.description for demo in demos.values()}
        review_items = [
            {
                "hook": item["hook"],
                "title": item["title"],
                "hook_format": str(item.get("hook_format", "direct-observation")),
                "format_rule": get_hook_format(
                    str(item.get("hook_format", "direct-observation"))
                ).prompt_rule,
                "demo_file": Path(item["demo_path"]).name,
                "demo_description": descriptions[Path(item["demo_path"]).name],
            }
            for item in hooks
        ]
        prompt = f"""
Act as a strict advertising safety reviewer. For each item, determine whether the hook and
separate manual-upload title are truthful and fully supported by the demonstration description,
whether the hook obeys its assigned caption format, and whether neither implies an unsupported
result, fabricated engagement, CTA, or unsafe claim. Titles must be neutral lower-case factual
labels of 3–8 words, rather than a rewrite of the opening hook. The content is suitable for a
general adult audience.
The on-camera adult woman is the narrator even when the short-form format omits I/me.
Return only JSON: {{"passed": boolean, "reason": string}}. Set passed false if any item fails.

Items: {json.dumps(review_items, ensure_ascii=False)}
""".strip()
        review = self._text_json(prompt)
        if not isinstance(review, dict) or review.get("passed") is not True:
            reason = (
                review.get("reason", "hook safety review failed")
                if isinstance(review, dict)
                else "invalid review"
            )
            raise QualityError(str(reason))

    def generate_scene(
        self,
        hook: str,
        demo_description: str,
        references: list[Path],
        output: Path,
        *,
        direction: str | None = None,
    ) -> None:
        direction_block = (
            f"\n\nApproved opening direction:\n{direction.strip()}"
            if direction
            else ""
        )
        prompt = with_handheld_scene(f"""
Create a photorealistic vertical 9:16 UGC opening frame featuring the exact same synthetic
woman shown in the supplied approved character references. The result is the live view from
the phone's front-facing camera while she holds that phone herself at arm's length; the phone
cannot appear because it is the camera producing the image. Match her facial structure, hair,
skin tone and defining features precisely. Use a realistic, lived-in setting, natural phone-
camera lighting and believable anatomy. No logos, text, captions, UI, extra people, extra
fingers, distorted hands, warped objects or sexualized styling. Leave the top safe area clear
for a caption that will be added later.

Hook context: {hook}
The following approved app demo shows: {demo_description}
{direction_block}

{US_RESIDENTIAL_ENVIRONMENT_INSTRUCTION}
""".strip())
        self.generate_image(
            prompt,
            references,
            output,
            aspect_ratio="9:16",
            image_size="2K",
        )

    def generate_image(
        self,
        prompt: str,
        references: list[Path],
        output: Path,
        *,
        aspect_ratio: str = "9:16",
        image_size: str = "2K",
    ) -> None:
        """Generate one image from an explicit prompt and reference set."""
        parts: list[dict[str, Any]] = [{"text": prompt}]
        parts.extend(_inline_image(path) for path in references)
        payload = self._generate(
            self.image_model,
            parts,
            {
                "temperature": 0.2,
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": {
                    "aspectRatio": aspect_ratio,
                    "imageSize": image_size,
                },
            },
        )
        image_data: str | None = None
        for part in self._parts(payload):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                image_data = str(inline["data"])
                break
        if not image_data:
            raise QualityError(f"{self.image_model} returned no image")
        try:
            decoded = base64.b64decode(image_data, validate=True)
        except ValueError as exc:
            raise QualityError(
                f"{self.image_model} returned invalid image data"
            ) from exc
        _atomic_bytes(output, decoded)

    def review_visual(
        self, images: list[Path], references: list[Path], *, kind: str
    ) -> dict[str, Any]:
        prompt = f"""
Review this synthetic {kind} for production use. The first supplied image(s) are generated
content; the final supplied images labeled by order are approved character references.
The generated woman must match the same synthetic character and the apparent maturity shown
in the reference. It must unmistakably be her own arm's-length front-camera image: exactly
one plausible camera-bearing arm, no visible phone because that phone is the camera, no
external photographer viewpoint, and a distinct free arm and hand when visible. Reject any
branched limb, extra arm, extra hand, deformed face, malformed fingers, extra digits, warping,
duplicate person, text, unsafe content, or other visible generation defect. For animation
frames, identity, anatomy and the self-shot camera relationship must hold across every frame.
Be strict.
Return only JSON with exactly: {{"passed": boolean, "consistent": boolean,
"defect_free": boolean, "self_shot": boolean, "reason": string}}.
""".strip()
        parts: list[dict[str, Any]] = [{"text": prompt}]
        parts.extend(_inline_image(path) for path in images)
        parts.append({"text": "The images after this marker are approved references."})
        parts.extend(_inline_image(path) for path in references[:4])
        payload = self._generate(
            self.text_model,
            parts,
            {"temperature": 0, "responseMimeType": "application/json"},
        )
        text = "".join(str(part.get("text", "")) for part in self._parts(payload))
        review = _json_from_text(text)
        required = ("passed", "consistent", "defect_free", "self_shot")
        if not isinstance(review, dict) or any(
            review.get(key) is not True for key in required
        ):
            reason = (
                review.get("reason", "visual quality review failed")
                if isinstance(review, dict)
                else "invalid review"
            )
            raise QualityError(str(reason))
        return review


class KlingProvider:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str,
        model: str,
        request_timeout: float = 120.0,
        poll_seconds: float = 15.0,
        job_timeout: float = 1800.0,
        client: httpx.Client | None = None,
        sleep: Any = time.sleep,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.poll_seconds = poll_seconds
        self.job_timeout = job_timeout
        self.client = client or httpx.Client(timeout=request_timeout)
        self.sleep = sleep

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _check(response: httpx.Response, context: str) -> dict[str, Any]:
        if response.status_code == 429 or response.status_code >= 500:
            raise TransientError(
                f"Kling {context} temporarily failed ({response.status_code})"
            )
        if response.is_error:
            raise ProviderError(
                f"Kling {context} failed ({response.status_code}): {response.text[:1000]}"
            )
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Kling {context} returned non-JSON") from exc

    def animate(
        self,
        scene: Path,
        output: Path,
        *,
        task_id: str | None = None,
        on_submitted: Any | None = None,
        motion_prompt: str | None = None,
    ) -> None:
        if task_id is None:
            image = base64.b64encode(scene.read_bytes()).decode("ascii")
            body = {
                "model_name": self.model,
                "image": image,
                "prompt": with_handheld_motion(
                    motion_prompt.strip()
                    if motion_prompt
                    else "Subtle natural self-shot UGC movement: she holds the front-camera "
                    "phone herself in one extended arm outside its own image while she "
                    "breathes and blinks. Preserve her exact face, free hand, framing and "
                    "identity. No camera cuts, morphing, text or speech."
                ),
                "negative_prompt": (
                    "child, teenager, deformed hands, extra fingers, face morph, identity change, "
                    "warping, duplicate person, text, logo, camera cut"
                ),
                "duration": "5",
                "mode": "pro",
                "sound": "off",
            }
            try:
                response = self.client.post(
                    f"{self.base_url}/videos/image2video",
                    headers=self.headers,
                    json=body,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise TransientError(
                    f"Kling submission failed: {type(exc).__name__}"
                ) from exc
            payload = self._check(response, "submission")
            data = payload.get("data", payload)
            task_id = (
                data.get("task_id") or data.get("taskId") or payload.get("task_id")
            )
            if not task_id:
                raise ProviderError(
                    f"Kling submission returned no task id: {str(payload)[:1000]}"
                )
            task_id = str(task_id)
            if on_submitted is not None:
                on_submitted(task_id)
        deadline = time.monotonic() + self.job_timeout
        video_url: str | None = None
        while time.monotonic() < deadline:
            self.sleep(self.poll_seconds)
            try:
                response = self.client.get(
                    f"{self.base_url}/videos/image2video/{task_id}",
                    headers=self.headers,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise TransientError(
                    f"Kling polling failed: {type(exc).__name__}"
                ) from exc
            status_payload = self._check(response, "poll")
            status_data = status_payload.get("data", status_payload)
            status = str(
                status_data.get("task_status")
                or status_data.get("status")
                or status_payload.get("status", "")
            ).lower()
            if status in {"failed", "error", "cancelled", "canceled"}:
                message = (
                    status_data.get("task_status_msg")
                    or status_data.get("message")
                    or status
                )
                raise ProviderError(f"Kling generation failed: {message}")
            if status in {"succeed", "succeeded", "complete", "completed"}:
                works = (
                    status_data.get("task_result", {}).get("videos")
                    or status_data.get("videos")
                    or status_data.get("output", {}).get("videos")
                    or []
                )
                if works:
                    video_url = works[0].get("url") or works[0].get("video_url")
                video_url = (
                    video_url or status_data.get("video_url") or status_data.get("url")
                )
                break
        if not video_url:
            raise TransientError(
                f"Kling task {task_id} did not complete before timeout"
            )
        try:
            with self.client.stream("GET", video_url) as download:
                if download.status_code == 429 or download.status_code >= 500:
                    raise TransientError(
                        f"Kling download temporarily failed ({download.status_code})"
                    )
                if download.is_error:
                    raise ProviderError(
                        f"Kling download failed ({download.status_code})"
                    )
                content = b"".join(download.iter_bytes())
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise TransientError(
                f"Kling download failed: {type(exc).__name__}"
            ) from exc
        if not content:
            raise QualityError("Kling returned an empty video")
        _atomic_bytes(output, content)
