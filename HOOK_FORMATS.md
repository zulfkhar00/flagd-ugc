# Approved opening-hook formats

The generated woman is the narrator during the first three seconds. The app demo immediately
afterward must substantiate every product or score claim; no hook may promise an outcome, invent
a score, or use a CTA. Quote/receipt copy is a clearly editorial reaction format, not a claim that
the words appear in flagd's UI.

New four-video batches rotate among `pov`, `when`, `reaction-meme`, `inner-thought` and
`direct-observation`, using four different formats. Each overlay explains a relatable silent
strong, immediately readable reaction, not just the name of an app screen. Inner thoughts require a feedback/result demo;
plain reaction captions provide a fallback for browsing/practice-only banks. The format is
stable for its run date and never changes if the batch resumes. Quote/receipt and scorecard
remain available for old batches and local rendering, but are not selected for new daily plans.

Each item also receives a separate manual-upload title. It is a 3–8 word lower-case factual
label, never on-screen copy: no emoji, hashtags, URLs, CTA, quotation marks, fabricated
engagement, or unsupported numbers. The title is safety-reviewed with its hook, stored before
rendering, and used as the Telegram document caption.

| Format | Copy shape | Example | Caption treatment |
| --- | --- | --- | --- |
| `pov` | `pov: …` | `pov: practicing the reply before i send it` | TikTok Classic |
| `quote-receipt` | short quote body | `i'm good at texting` | quote plus small `— him` attribution |
| `when` | `when …` | `when the feedback calls out my opener` | TikTok Classic |
| `direct-observation` | plain reaction caption | `that feedback humbled me` | TikTok Classic |
| `reaction-meme` | `my face when …` | `my face when i got the feedback` | TikTok Classic |
| `scorecard` | `rating the chat: N/100` | `rating the chat: 43/100` | small label plus large score |
| `inner-thought` | `i …` | `i did not expect that feedback` | TikTok Classic |

TikTok Classic means lower-case white bold sans text centred in the top safe area, with a black
outline and subtle shadow. It has no card, banner, logo, or persistent text after the demo starts.
The quote and scorecard variants use the same typography but two intentional text sizes.

## Guardrails

- Copy is under 12 words and lower-case.
- Format grammar is checked locally before image or video generation.
- The model receives only approved demo descriptions and its assigned format. Its response must
  echo the exact demo filename and assigned format.
- The second Gemini pass rejects copy that does not fit the demo, makes an unsupported claim, or
  breaks the assigned format. It reviews the separate upload title and the chosen reaction at
  the same time; a win/failure cannot contradict the actual demo outcome.
- New hooks use only shock, disbelief, laughter, cringe, flustered pressure or excited surprise. Casual reactions
  are archived and rejected by the new-plan boundary.
- Time pressure uses flustered, not disbelief. Reactions may repeat; emotional fit comes before
  variety. A rejected plan's review feedback is passed to the next bounded attempt and retained
  on resume. The semantic reviewer remains mandatory before media generation.
- Copy and reaction are planned together. The planner returns an approved `reaction_key`;
  the pipeline persists it inside the versioned `opening_direction` before paid media generation.
- Scorecards are permitted only when the reviewed demo description contains the exact overall
  score. The number must match exactly.
- The database stores `hook_format`, `title`, and `opening_direction` before image generation. A
  resumed batch uses the same stored caption format, setting, wardrobe, and silent reaction.
