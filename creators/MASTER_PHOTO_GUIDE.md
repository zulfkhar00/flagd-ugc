# Master-photo guide for the UGC pipeline

Researched September 30, 2026. This guide concerns identity-reference images used by
Nano Banana Pro to make scene stills. Kling animates those scene stills; it does not
receive this gray-background portrait as its starting frame in the current pipeline.

## Findings and their limits

- Google's [Nano Banana Pro prompt guide](https://blog.google/products-and-platforms/products/gemini/prompting-tips-nano-banana-pro/)
  recommends identifying each reference image's role and giving explicit composition,
  lighting and editing instructions. It also acknowledges that character consistency
  can vary across edits.
- Google's [Imagen person-customization guide](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/image/subject-customization)
  recommends a centered, large, frontal face without obstructions. This is guidance for
  Imagen, not a documented requirement or measured optimum for Gemini.
- Runway's [character-reference guide](https://help.runwayml.com/hc/en-us/articles/40042718905875-Creating-with-Gen-4-Image-References)
  recommends natural, even lighting and a neutral expression. This supports the general
  reference-photo approach; its model-specific results are not Gemini guarantees.
- The reviewed sources do not establish that gray outperforms white or every simple
  background. A plain light-gray background is our practical choice to keep unrelated
  scenery and color casts out of the identity reference and give dark hair visible contrast.
  That benefit is an inference, not a measured finding for this pipeline.

## Initial master specification

- One adult, one portrait, frontal head orientation, eyes toward the camera.
- Head and shoulders / upper chest, full crown and chin inside the frame; face clearly
  readable at normal viewing size. No hands, props or another person.
- Eye-level camera with ordinary perspective and no obvious wide-angle facial stretching.
- Relaxed closed mouth, no smile, pout, emotional reaction or fashion pose.
- Soft, even, neutral-colored illumination with natural facial shading and visible skin
  detail. Avoid direct flash hotspots, dramatic side light, face smoothing and beauty retouching.
- Hairline and facial contours visible; preserve reference hair color and defining features.
- Preserve the intended creator's visible makeup and recognizable styling from the source.
  A neutral expression does not mean a bare face. Do not strip lashes, blush, lip color or
  normal cosmetic gloss. Do not add or strengthen freckles to manufacture realism.
- An ordinary outfit without lettering. Existing small accessories can remain when they
  do not obscure facial features; remove them only when they create a specific problem.
- Plain matte light-gray background with no furnishings or pattern.
- One 3:4 portrait at Nano Banana Pro 2K. This size and crop are our starting choices,
  not proven optimum settings. Do not generate a collage or multi-angle sheet as the master.

## Acceptance

Compare against the supplied reference: eye spacing, eyebrows, nose, lip shape, jawline,
hairline, skin tone and distinguishing marks. Check for altered age presentation, beautified
face proportions, incorrect eye directions, plastic skin, edge halos, cropped crown and
clothing artifacts. Keep the portrait pending user review until accepted.

A generated frontal face is an interpretation of the source, not ground truth for unseen
angles. Preserve the original reference separately so drift can be checked. Additional
angles can be added later if a production need emerges; do not generate them automatically.

Sofia's first user-generated candidate lost the source makeup and made freckles more
pronounced. The user rejected that appearance change. Her revised prompt is
`sofia/master-v2-prompt.txt`; use the original `sofia/reference.jpg` for identity and styling.
Do not submit the v1 generation runner for this revision; the user is generating manually.

## Production use

Label the approved portrait as the reference for identity and facial features. The scene
prompt controls the outfit, setting, lighting, camera height, task and posture. Do not copy
the gray background or frontal portrait pose into hook scenes.

Keep each creator's references isolated. The current production character folder represents
one woman; placing several different people there would incorrectly combine identities.
Draft master images live under `creators/<name>/` locally and `/data/creator-drafts/<name>/`
on the VPS, outside the currently approved production reference directory.
