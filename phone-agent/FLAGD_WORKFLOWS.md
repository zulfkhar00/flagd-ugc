# flagd account workflows

This specializes the general visual assistant for flagd's owned social accounts.
The current prototype remains a screenshot/input controller, not a validated
social publishing service. These are acceptance criteria for the next layers.

## Topic research

An instruction identifies the app, account, topic, duration and expected report.
Example:

> On TikTok account @abcde, research advice about starting a conversation with
> someone you like for 25 minutes. Search relevant terms, inspect posts and
> comments, and return recurring advice and useful source posts.

The assistant should:

1. Navigate to the requested app and account through its visible interface.
2. Verify the visible username before continuing. Stop if it cannot establish
   the requested account.
3. Formulate relevant searches and revise them based on results.
4. Inspect relevant posts, captions and comment discussions. Maintain a visited
   source index rather than repeatedly inspecting the same post.
5. Use selected frames and available captions/audio for video content. A cover
   screenshot alone is not evidence of a video's full meaning.
6. Record source-linked findings and stop at the wall-clock deadline.
7. Report the account used, coverage, findings, uncertainties, elapsed time and
   model usage. Do not label an account successfully "warmed" from this activity.

## Daily keyword browsing

The October 12 requirements distinguish browsing from research: search supplied
keywords, watch videos, open/scroll comments, expand reply threads and return to
the feed. No content summaries or research findings are needed. The `browse`
mode uses compact screen decisions and makes no model requests during playback
waits. The session timer starts after the requested account is visibly checked.

Likes and comments are optional, explicitly capped in each account's job. A
comment requires readable current context; the agent must not invent a personal
experience, endorsement or fact. No need to analyze every watched video.
Following/unfollowing and messaging are outside this scoped workflow.

The daily scheduler runs serially on this single phone, uses
`America/Los_Angeles` including daylight saving, and records job receipts. It
does not replay missed or interrupted sessions. It requires the local service,
Mac and phone connection to remain available. A disabled example is supplied;
it is not an active daily automation.

## Native TikTok slideshow publishing

A publishing job must provide:

- A unique job ID and intended account username.
- Six to eight ordered, ready-made slide images, with their text already laid out.
- A Volume phone share link and its exact caption: first paragraph is the
  catchy title, at most 59 characters including spaces/punctuation; all remaining
  paragraphs are the description. Overlength titles are rejected, not truncated.
  A controller task declares this with `text_limits: {"title": 59}`.
- The separate full description.
- Music instructions: inspect TikTok's recommendations and choose an available,
  relevant track, or select a specified track if supplied. Record what was used.
- A mode: prepare for review, or publish this explicitly authorized job.

The assistant should:

1. Check the inputs and make the ordered images available in the phone's media
   picker. Verify gallery availability on the real device; file transfer alone
   is insufficient evidence.
2. Open TikTok and verify the requested account.
3. Enter the creation flow and select the correct photos in their intended order.
4. Confirm the result is a native swipeable photo carousel, not a video made
   from still images. The first slide must be the source's first photo, and the
   thumbnail marked Cover must match it. Cancel TikTok's collage/layout tool
   instead of accepting its checkmark. Inspect the photo strip through its end
   and remove any added collage/extra image through visible controls. Changing
   only the cover does not fix an extra image. Stop if correction or verification
   is uncertain; never infer the carousel count from a collage layout option.
5. Inspect the available music recommendations, select a suitable track and
   verify the visible selection. If unavailable, record that rather than claiming
   the requested audio was selected.
6. Type the supplied title and complete description verbatim into the relevant
   fields. Check visible text and truncation; do not silently rewrite the copy.
7. Inspect the final preview: account, slide count/order, title, description,
   selected sound and visible posting settings. `confirm_preview` requires
   explicit checks of native photo mode, the first slide, cover, original order
   absence of extra images and exact title/description. Flagd jobs declare
   `required_text_fields: ["title", "description"]`; acknowledged entry of both
   fields is required before the preview can be confirmed. The runtime rejects missing or failed checks,
   and invalidates the checkpoint after any subsequent navigation or edit.
8. In review mode, retain the preview and stop. For an explicitly authorized
   publication, publish once and verify the post on the intended account.
9. Store a publication receipt with the job ID, account, timestamp, source
   image order, selected sound and obtainable post URL/ID. A dismissed composer
   or dispatched tap is not sufficient proof of publication.

If the connection fails during publication, mark the outcome uncertain and
inspect the account for the new post before any retry. Never blindly repeat
the publish action.

Task-scoped authorization and account/preview checkpoints are implemented.
Publication needs a source/account-specific job with `publish: 1`, not simply
`--execute`. The model labels public-effect taps; the runtime enforces their
limits. The checkpoints are model interpretations of captured screens, not an
independent UI classifier. Native carousel and music handling still require
live device testing. A model completion remains a candidate outcome.

The October 12 device test exposed a sharing failure: TikTok's collage tool
added a composite image ahead of the six originals, making that composite the
cover and shifting the intended first slide to position 2. The updated checks
have offline tests; successful correction on the phone has not been established.

The next attempt had the correct cover but posted without the supplied caption.
The trace shows repeated Next taps during a screen transition; Next and Post
occupy the same area. Publishing workflows now capture again before every input
and discard decisions when the screen materially changed during inference.
This is a pixel-change heuristic, not an independent public-button classifier.

## Instagram and additional apps

Reuse the visual controller, planner, research notebook and job lifecycle.
Implement and test app-specific composer, account selection and success checks.
Do not assume that a working TikTok workflow establishes Instagram support.

## First practical checks

1. Verify screenshot capture, coordinate alignment and ordinary navigation on
   one real Android phone.
2. Complete one bounded topic-research task and inspect its source-linked report.
3. Prepare one real TikTok carousel and check its images, sound and full text.
4. Publish one authorized job and validate the receipt and duplicate prevention.
5. Add the duration/schedule controller and further app workflows after those
   checks pass.

No warm-up effect, human indistinguishability, publication reliability or
per-account reach improvement has been established by the offline tests.
