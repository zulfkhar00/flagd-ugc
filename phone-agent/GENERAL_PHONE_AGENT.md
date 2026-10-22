# General visual phone assistant: agreed direction

The target is a general owned-phone assistant controlled by natural-language
requests across Settings, stores, browsers, Maps, email and other installed apps.
TikTok and Instagram are individual workflows. The assistant should
understand a goal, inspect the visible screen, choose its own UI interactions,
adapt to unfamiliar screens, collect evidence and return useful results.

The concrete account research and native slideshow requirements are recorded in
[flagd account workflows](FLAGD_WORKFLOWS.md).

"Uses a phone like a human" describes visual navigation through ordinary app
interfaces. It does not establish human-level reliability or undetectability.
No success rate is measured yet; the controller, planner integration and research
notebook have offline tests using simulated screens and scripted model responses.

## Example tasks and acceptance criteria

| Request | Expected behavior | Result |
| --- | --- | --- |
| Find a setting on the phone | Open Settings, inspect its navigation, locate the requested control | The visible control and current value; change it only when the task requests that |
| Find nearby places using Maps | Open Maps, search the requested category/location, inspect results | A shortlist with observed names, locations and relevant visible details |
| Research a question using the browser | Formulate searches, inspect results and relevant pages | Source-linked findings and uncertainty |
| Find a particular email | Open the authenticated mail app, search and read matching messages | The requested information with message/date evidence |
| Find Thailand trip ideas on TikTok or Instagram | Open the app, generate several relevant searches, inspect distinct posts, read captions and relevant comments, avoid duplicates | A shortlist with places, why they were recommended, post/account references and uncertainty |
| Check friends' latest Instagram posts for tagged locations | Establish which followed accounts count as friends, visit those accessible accounts, inspect each latest post and its visible location tag | A table of account, post/date if visible, explicit tag or "no visible location tag", plus inaccessible or unchecked accounts |
| Install a named free app and figure out a specific task | Navigate the store UI, choose the correct app/publisher, install, explore onboarding and learn its navigation | Evidence of installation and a completed or clearly blocked task; credentials/payment/ambiguous permissions may need the operator |
| Browse a topic on an owned TikTok account for 25 minutes | Switch account and verify the visible username, search relevant terms, watch/read relevant material, keep useful notes, stop at the deadline | A research summary, checked account, coverage and elapsed time |

"All my friends" is ambiguous unless the operator provides a list or explicitly
chooses following/mutual-following accounts. Access must stay within what that
logged-in account can ordinarily view. A missing location tag must not be
replaced with an inferred or guessed location.

Topic research and passive keyword browsing are separate modes. Public likes,
comments, follows and messages act on the operator's behalf and need specified
authorization. The owned-account browsing workflow supports capped likes and
comments; comments need readable current context and must not invent experiences.
Viewing/searching a topic does not establish that an account is "warmed" or
guarantee improved reach.

## Components and implementation direction

1. **Task interpretation.** Accept an ordinary instruction directly. Extract the
   account, goal, time/cost limits, permitted actions and required output. Ask
   only for missing details that materially change execution.
2. **Planning and revision.** Break the goal into subgoals. Generate queries and
   reconsider the plan after failed searches, popups or changed app screens.
   The current prototype stores an initial plan and updates progress and a
   session summary on every screen assessment. Navigation does not depend on
   hardcoded screen coordinates. Structural plan editing is still future work.
3. **Visual execution.** Choose a visible gesture against the latest screenshot,
   convert coordinates, execute through Portal, and inspect the result. Existing
   controller code provides the first version of this component.
4. **Session memory.** Keep verified account identity, visited posts, findings,
   unfinished subgoals and the current app position. Preserve compact summaries
   rather than repeatedly sending an unlimited screenshot history. A SQLite
   notebook now persists plans, visited source labels, findings and observations;
   an independent account identity verifier and reusable app knowledge are not built.
5. **Content understanding.** Read captions, comments and explicit metadata.
   Videos need selected frames over time and available audio/captions, not a
   conclusion based only on the cover image. Audio availability and protected
   capture behavior must be tested on the real phone.
6. **Evidence and reporting.** Attach every finding to the relevant post/account,
   a captured observation and a timestamp. Return a readable report. Distinguish
   observed claims, inferences and missing information. Markdown/JSON reports
   now include recorded finding IDs, screenshot references, transcribed text and
   uncertainty. URLs are optional and must be transcribed from the current screen.
7. **Recovery and verification.** Handle understood dismissible popups, loading
   and navigation mistakes. Authentication challenges, unclear sensitive
   requests and uncertain mutations require operator recovery. Independently
   inspect task-specific success conditions; a model saying DONE is insufficient.
8. **Long sessions.** Use wall-clock deadlines, cancellation, a per-phone task
   lock and durable checkpoints. Time spent watching or waiting should not cause
   continuous inference; call the model when a decision or content analysis is
   needed. Measure the resulting cost on actual tasks.

App knowledge can be learned and saved as navigation hints: which screens were
useful, how a successful task was completed and common recoverable errors.
Hints must be checked against current screen evidence. This is workflow memory,
not retraining the underlying model or a guarantee about every unfamiliar app.

## Current implementation limits

- It has authenticated phone transport and a bounded screenshot/input loop.
- A natural-language goal can be supplied with `run --goal`, through task JSON,
  or with `submit --goal` to a persistent `serve` process. Local status, cancellation
  and screenshot commands share that connection. One task owns the phone at a
  time; receipts prevent repeated submission IDs from replaying a task. Interrupted
  or uncertain tasks are not resumed. There is no conversational UI or scheduler.
- Exact form text is supplied separately. Search queries can be generated and
  refined in visibly focused search fields; verification-code entry is not implemented.
- A compact summary, recent findings and visited sources are fed into screen
  assessments. All findings remain in SQLite for reports and offline reopening.
  This is per-task memory, not a cross-task app knowledge store or automatic resumption.
- Research answers cite recorded findings, but the vision model's readings and
  conclusions still need checking against the captured evidence.
- There is no audio/video comprehension, download/install workflow, 25-minute
  session controller, durable task resumption or social-account verifier yet.
- Completion candidates are logged, not independently verified as task success.
- Initial owned-phone checks validated connection, screenshots, the screen-awake
  watchdog and navigation to Android's Display settings. A read-only Gemini run
  recorded the visible brightness level. The first navigation attempts also
  exposed missed targets, a step limit and a model output limit; broader app
  navigation and research still need live testing. See the README's dated results.

## Benchmark sequence

First check general navigation in Settings and the browser, including correct
text entry and visible outcomes. Then run a research request in a chosen app:

Use the real phone to run a bounded research request:

> Find five places to visit in Thailand. Use the requested browser or social app
> and include the place, recommendation and source. Search and read only.

This tests language interpretation, query generation, visual navigation,
content reading, memory and evidence-backed reporting in one workflow. Retain
failures, elapsed time, model usage and the resulting report. Test unfamiliar-app
installation and longer research sessions after this loop is reliable.

This file describes requirements and implementation order. It is not a claim
that these capabilities are already built.
