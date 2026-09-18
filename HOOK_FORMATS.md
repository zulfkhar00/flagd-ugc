# Approved opening-hook formats

The generated woman is the narrator during the first three seconds. The app demo immediately
afterward must substantiate every product or score claim; no hook may promise an outcome, invent
a score, or use a CTA. Quote/receipt copy is a clearly editorial reaction format, not a claim that
the words appear in flagd's UI.

Every four-video batch uses four different formats. The choice is stable for its run date, is
persisted with the video, and never changes if the batch resumes.

Each item also receives a separate manual-upload title. It is a 3–8 word lower-case factual
label, never on-screen copy: no emoji, hashtags, URLs, CTA, quotation marks, fabricated
engagement, or unsupported numbers. The title is safety-reviewed with its hook, stored before
rendering, and used as the Telegram document caption.

| Format | Copy shape | Example | Caption treatment |
| --- | --- | --- | --- |
| `pov` | `pov: …` | `pov: he thought he had game until this` | TikTok Classic |
| `quote-receipt` | short quote body | `i'm good at texting` | quote plus small `— him` attribution |
| `when` | `when …` | `when he says he's good at texting` | TikTok Classic |
| `direct-observation` | plain observation | `the 43/100 explained a lot` | TikTok Classic |
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
  breaks the assigned format. It reviews the separate upload title at the same time.
- Scorecards are permitted only when the reviewed demo description contains the exact overall
  score. The number must match exactly.
- The database stores `hook_format`, `title`, and `opening_direction` before image generation. A
  resumed batch uses the same stored caption format, setting, wardrobe, and silent reaction.
