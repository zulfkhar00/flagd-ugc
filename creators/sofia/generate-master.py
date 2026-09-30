"""Generate one pending-review Sofia master with the existing Gemini provider."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from datetime import datetime, timezone
from pathlib import Path

from flagd_ugc.config import Settings
from flagd_ugc.providers import GeminiProvider


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--confirm-paid', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    output = root / 'master-v1.png'
    record = root / 'master-v1-generation.json'
    if output.exists():
        print('Master already exists; no new generation submitted.', flush=True)
        return
    if record.exists():
        raise RuntimeError('A previous submission exists; inspect before repeating a paid request.')
    if not args.confirm_paid:
        raise ValueError('Use --confirm-paid to submit the single requested master generation.')
    reference = root / 'reference.jpg'
    prompt = (root / 'master-v1-prompt.txt').read_text()
    settings = Settings.from_env()
    if not settings.gemini_api_key:
        raise ValueError('GEMINI_API_KEY is not configured')
    metadata = {
        'creator': 'sofia',
        'model': 'gemini-3-pro-image',
        'image_size': '2K',
        'aspect_ratio': '3:4',
        'reference_sha256': hashlib.sha256(reference.read_bytes()).hexdigest(),
        'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
        'submitted_at': datetime.now(timezone.utc).isoformat(),
        'status': 'submitted',
        'production_approved': False,
    }
    with record.open('x') as handle:
        json.dump(metadata, handle, indent=2)
    provider = GeminiProvider(
        settings.gemini_api_key,
        text_model=settings.gemini_text_model,
        image_model='gemini-3-pro-image',
        timeout=240.0,
    )
    print('Submitting one Nano Banana Pro 2K identity master.', flush=True)
    try:
        provider.generate_image(prompt, [reference], output, aspect_ratio='3:4', image_size='2K')
    except Exception as exc:
        metadata.update(status='request_failed_or_uncertain', error_type=type(exc).__name__)
        record.write_text(json.dumps(metadata, indent=2) + '\n')
        print(json.dumps({'status': metadata['status'], 'error_type': type(exc).__name__}), flush=True)
        raise SystemExit(1) from None
    data = output.read_bytes()
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        metadata['status'] = 'output_requires_format_check'
        record.write_text(json.dumps(metadata, indent=2) + '\n')
        raise ValueError('Generated output is not PNG; inspect its actual format')
    width, height = struct.unpack_from('>II', data, 16)
    metadata.update(
        status='generated_pending_review',
        output_sha256=hashlib.sha256(data).hexdigest(),
        width=width,
        height=height,
        completed_at=datetime.now(timezone.utc).isoformat(),
    )
    record.write_text(json.dumps(metadata, indent=2) + '\n')
    print(json.dumps({'status': metadata['status'], 'width': width, 'height': height}), flush=True)


if __name__ == '__main__':
    main()
