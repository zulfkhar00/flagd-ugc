# Daily opening directions

Every new daily video opens with a three-second silent reaction and a text overlay, then cuts
to the matching approved app demo. Gemini plans the caption and one approved reaction together;
the copy review checks that the emotion fits the overlay and the actual demo outcome.

`opening_director.py` combines that reaction with a date-rotated setting and wardrobe, and
writes the versioned direction to SQLite before Nano Banana Pro or Kling is called. Four
distinct caption formats give four distinct settings in a daily batch. A restart reuses the
stored direction, frame and animation request rather than selecting a new expression.

| Reaction | Treatment |
| --- | --- |
| shocked | wide eyes, clear eyebrow rise, silent jaw-drop and small recoil |
| disbelief | strong incredulous expression and slight chin recoil |
| laughing | broad amused grin and one natural shoulder movement |
| cringing | visible wince, awkward grimace and coupled upper-body recoil |
| flustered | tense grimace, urgent concentration and small forward lean under pressure |
| excited | bright wide eyes, delighted grin and spontaneous slight lean forward |

| Scene | Setting | Wardrobe |
| --- | --- | --- |
| bed edge | American apartment bedroom | charcoal hoodie |
| kitchen counter | contemporary American apartment kitchen | navy tee |
| sofa | American apartment living room | forest sweatshirt |
| entry | American apartment entryway | burgundy long sleeve |
| bedroom chair | American apartment bedroom | heather lilac tee |
| parked car | safely parked U.S. car | olive street layers |
| laundry area | American apartment laundry closet | cream knit |

All new treatments are expressive self-shot front-camera clips. The starting frame captures the onset,
readable upper shoulders, asymmetric phone framing and top space for the locally added caption.
Preserve identity and everyday makeup, ordinary American interiors, contemporary clothing and
practical room lighting. Do not make every belonging worn or every room messy.

Animate one strong emotion that is clearly readable within the first second. Head, shoulders
and upper torso should respond naturally to that emotion. Never select a casual glance, faint
smirk or tiny eyebrow twitch for a new hook. Keep natural blinking and irregular handheld
wrist drift. No unrelated emotional sequence, cartoon distortion, hand-to-face gesture,
household task, speech, lip-sync or silent mouthing. A single silent jaw-drop, visible teeth
or a broad smile is allowed when it matches the chosen reaction. Cut while the reaction is
still alive, before a neutral reset or posed ending. Kling audio generation is disabled, and
FFmpeg strips all audio and shows the overlay only on the opening.

Animation quality review samples six chronological frames at 2 FPS. It checks identity, anatomy,
self-shot framing and a strong expression consistent with the chosen reaction. It rejects
flat casual motion, repeated speech-like mouth shapes and generation defects; it accepts
a believable silent gasp or broad grin. Quality retries keep their existing limits.
Caption-planning retries receive the latest rejection reason, including when a failed batch
resumes. Timer pressure uses the flustered reaction; disbelief requires incredulity or a
contradiction. Reactions may repeat when that best fits the approved demos.
Scene retries also receive the latest generation or visual-review rejection, including on
resume. The opening prompt treats the hook and app demo as context for later editing rather
than a reason to show a device. Keep the camera-bearing hand and handset outside the image.
Scene review retains each rejection reason, and an exhausted scene reports its last defect.

New expressive recipes use v2 direction keys. Older casual v1, task and reaction direction keys remain readable so already planned batches resume with
their original creative direction. Completed videos are not regenerated. This change needs
the normal app redeploy; no new environment variables or database migration are required.
