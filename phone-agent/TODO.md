# Future work

## Automatically reconcile a post after phone disconnection

When a Share/Post tap was acknowledged but Portal disconnects before the final
profile check, the durable queue currently pauses. The post may already be live,
so restarting the publishing task could create a duplicate.

On reconnection, resume from a durable **verification checkpoint**, not by
replaying phone inputs. Capture a fresh screen, navigate read-only to the intended
account's profile if needed, and verify that its newest published item matches
the queued source's first-slide identity. Store the account, source identity,
evidence screenshot, and reconciliation receipt. If the match is proven, mark the
item finished and continue the queue automatically. If publication cannot be
proven, keep the item blocked for inspection; never tap Share/Post a second time
merely because the first attempt lost its connection.

Acceptance cases: disconnection during upload; disconnection after the post
appears but before profile confirmation; wrong account or wrong newest post;
reconnection after a service restart. Each case must preserve exactly-once
publication and durable queue order.
