# Deploy the phone controller on Dokploy

This is a **separate** service from the dormant AI UGC pipeline. Its only
public endpoint is Portal's outbound WebSocket at `/portal`. The instruction
socket and saved task state remain private to the container. No daily schedule
is enabled.

1. In DNS, point a phone-controller subdomain to the IONOS VPS. In Dokploy,
   create a **Compose** application from this repository's `main` branch,
   using `phone-agent.compose.yml` as the Compose file. Do not replace the
   root `docker-compose.yml` used by the AI UGC service.
2. In that Dokploy application, set `PHONE_DEVICE_TOKEN` to the **same**
   random token already entered in Mobilerun Portal. Set `PHONE_DEVICE_ID` to
   the paired ID shown by the existing local controller, and set
   `PHONE_GEMINI_API_KEY` and `PHONE_VISION_MODEL` from the local phone-agent
   configuration. Keep all values in Dokploy's secret environment settings,
   never in Git or the Compose file.
3. Add a Dokploy Domain for service `flagd-phone-agent`, container port
   **8787**, with HTTPS/Let's Encrypt. Point DNS to the VPS **before** adding
   the domain. The Compose file only uses `expose`, so port 8787 is not opened
   directly on the VPS. Redeploy after adding or changing the domain.
4. Verify the public route by opening `https://<DOMAIN>/portal` without a
   token. `401 invalid device token` is expected and proves the request
   reached the controller. A certificate error, timeout, 404 or 502 is a
   deployment/DNS/proxy problem; do not bypass certificate validation.
5. In Mobilerun Portal's **Custom Connection**, change only the WebSocket URL
   to `wss://<DOMAIN>/portal`. Keep the existing device token. Tap Connect.
   The Dokploy log should report `Phone connected` with the expected device
   ID. A URL using `ws://` would send the token and phone data without TLS.
6. In Dokploy's service terminal, run the read-only checks below. `capture`
   takes a screenshot but does not request AI or tap the phone.

   ```sh
   flagd-phone status --control-socket /data/control.sock
   flagd-phone capture --control-socket /data/control.sock
   ```

The `flagd-phone-data` volume persists controller jobs and screenshots across
container restarts. The existing Mac controller's nine waiting jobs and one
blocked job are **not** copied to the VPS automatically. Do not move its
`controller-jobs.json` or resume those publications until their status has
been reviewed; pointing the phone at the VPS does not run them.

The current private control path is the container's Unix socket. To submit
instructions from another machine, use a separately secured admin channel
(for example, the existing authenticated instruction gateway through an SSH
tunnel). Do not expose the Unix socket or a plain HTTP instruction API to the
internet. The public WSS endpoint accepts phone connections only; it cannot
submit agent instructions.
