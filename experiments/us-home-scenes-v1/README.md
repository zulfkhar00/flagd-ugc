# U.S. home stills, round one

Eight 9:16 starting-frame images for comparing ordinary American apartment and house
settings. This pass uses Gemini 3.1 Flash Image (1K), the same synthetic character
reference for every frame, and no animation calls. Estimated image output cost is $0.067
per frame ($0.536 for eight), excluding minor input-token charges.

Run `python run.py` inside the app container. Each image has a submission marker so a
rerun skips completed images instead of buying duplicates. The approved animation shortlist
is laundry, kitchen, bedroom, and living room; `animate.py` submits only those four, using
their existing stills and one four-second standard Kling v3 clip each.

`animate-v2.py` runs the shorter three-second, low-narrative test on the existing
kitchen and bedroom stills only. It does not regenerate either image; estimated cost is
$0.252 per clip.

`animate-v3.py` tests three restrained variants (two bedroom, one kitchen) using the same
approved stills. Each three-second clip is estimated at $0.252; no image generation is used.
