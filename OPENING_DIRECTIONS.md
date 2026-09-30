# Daily opening directions

`opening_director.py` deterministically maps each approved caption format and batch date to a
self-shot mundane task. The direction is written to SQLite before Nano Banana Pro or Kling is
called, so a retry or restart cannot silently change the setting, outfit, or action. The four
videos in a batch always use four distinct tasks; assignments rotate across days.

| Task | Setting | Wardrobe |
| --- | --- | --- |
| straighten the duvet | American apartment bedroom | charcoal hoodie |
| tie a sneaker | American apartment entryway | heather lilac tee |
| fold a throw over a sofa | American apartment living room | forest sweatshirt |
| wipe the counter | American apartment kitchen | rust striped tee |
| look through a kitchen drawer | American apartment kitchen | navy tee |
| fold a towel | American apartment laundry area | cream knit |
| put groceries away | American apartment kitchen | burgundy long sleeve |
| settle the seatbelt | safely parked U.S. car | olive street layers |

All treatments are arm's-length phone-front-camera shots. They start with the task already in
progress and continue it only slightly for one silent three-second excerpt. Her gaze stays on
the task, her face remains neutral, and her head, shoulders and torso move with the task. The
clip ends mid-action rather than resolving into a pose. Prompts reserve clear top space for the
local caption overlay and forbid a visible phone or external camera.

Prior persisted reaction-direction keys remain readable so partially completed older batches
can resume with their original creative direction.
