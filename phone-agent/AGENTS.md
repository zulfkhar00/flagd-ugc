# General phone assistant scope

- The product is a general visual phone assistant controlled by natural-language
  instructions. TikTok, Instagram and flagd publishing are individual workflows.
- Keep the controller, task format, memory and verification reusable across apps.
  Do not assume that every task is social-media browsing or publishing.
- Use current screen evidence to navigate. App-specific knowledge may supply
  hints, but must not replace observing and checking the current interface.
- Give the agent the desired outcome, source content and constraints, not a
  fixed sequence of screens. The vision agent chooses the route and every
  ordinary UI action. Unfamiliar screens and popups should trigger reasoning
  and recovery, not automatic handoff. Return missing-checkpoint feedback to
  the agent so it can correct its next action without weakening authorization.
- Keep workflow-specific rules out of generic task validation. For flagd TikTok
  slideshows, declare the confirmed 59-character posting-title limit explicitly
  with `text_limits: {"title": 59}`. Other apps do not inherit that limit.
- Record task scope and authorization once; ordinary gesture execution should
  not introduce repeated approval prompts for actions already authorized.
- Executed tasks should recover ordinary popups, overlays, unfamiliar screens
  and missing checkpoints autonomously. Return review flags to the agent for
  reconsideration rather than immediately stopping for operator review. Do not
  silently execute a flagged input or loosen the authorized action limits.
- Account evidence must identify the active profile, selected account or explicit
  posting destination. A feed author, commenter, content watermark or shared
  avatar/logo is not proof of the logged-in account. Record any visible mismatch
  in the publication destination honestly and halt that batch for correction.
- Distinguish implemented/tested capabilities from planned capabilities, and
  offline simulation results from successful execution on a real phone.
- Phone operation is on demand: the user supplies an instruction and Codex
  executes it through the visual phone agent. Keep daily schedules disabled
  unless the user explicitly asks to activate scheduling again.
- For user-requested multi-post batches, enqueue each prepared task with a
  stable request ID through the service's durable queue. Check queue receipts
  and resolve blocked items before reporting the batch complete. Do not use an
  ephemeral shell loop as the only record of pending posts.
- After successful executed tasks, leave the foreground app, disable the screen
  wake watchdog and lock the display. Enable PHONE_PARK_ON_FINISH for the local
  service. The next task should wake the phone automatically. Do not navigate
  away from a failed task, review screen or uncertain input during cleanup.
- For Instagram, publish these prepared slides as a Reel with selected, enabled
  music, not as a swipeable feed carousel. Declare media_format=reel and
  require_music=true in the task. Keep the full title-plus-description in the
  single caption editor. The user corrected this on October 13, 2026.
