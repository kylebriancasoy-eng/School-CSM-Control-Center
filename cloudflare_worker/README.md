# Single-school Cloudflare Workers VPC gateway

This directory contains the public edge proxy for a no-custom-domain,
single-school School CSM pilot. It gives responders a stable address in this
form:

```text
https://<SCHOOL_ID>.<ACCOUNT_SUBDOMAIN>.workers.dev
```

The Worker sends each accepted request through a **VPC Service** bound to one
named Cloudflare Tunnel. The VPC Service, not the URL supplied by a respondent,
fixes the private target to `HTTP 127.0.0.1:8080` on the school computer. The
School CSM response database, scanner files, reports, and operator credentials
remain on that computer.

This is a pilot deployment path. Workers VPC is currently a beta service, and
Cloudflare describes `workers.dev` as suitable for non-business-critical use.
Do not represent this free address as a guaranteed production service. Review
the current [Workers VPC documentation](https://developers.cloudflare.com/workers-vpc/),
[VPC Service configuration](https://developers.cloudflare.com/workers-vpc/configuration/vpc-services/),
and [`workers.dev` limitations](https://developers.cloudflare.com/workers/configuration/routing/workers-dev/)
before each deployment.

## Security boundary

`src/index.js` deliberately:

- accepts only `GET`, `HEAD`, `POST`, and `OPTIONS`;
- requires HTTPS and the exact configured School-ID `workers.dev` hostname;
- rejects requests without one valid Cloudflare edge client IP;
- removes browser-supplied `Forwarded`, `X-Forwarded-*`, client-IP aliases,
  hop-by-hop headers, and every header named by `Connection`;
- creates its own `CF-Connecting-IP`, `X-Forwarded-For`,
  `X-Forwarded-Host`, and `X-Forwarded-Proto` assertions;
- preserves `Origin`, `Cookie`, request bodies, response bodies, and
  `Set-Cookie` so the Control Center's existing session and same-origin checks
  continue to work; and
- returns a generic, non-cacheable `503` without tunnel or exception details
  when the VPC Service cannot reach the school computer.

The local application must trust only the loopback peer used by `cloudflared`
and must independently require the same exact public host and forwarded HTTPS
context. The Worker is an additional boundary, not a replacement for the
Control Center's request checks.

## Prerequisites

- A saved official School ID containing 4–12 digits.
- A Cloudflare Zero Trust account and a configured Workers account subdomain.
- A remotely managed named Tunnel for this school. Its connector must run
  `cloudflared` 2025.7.0 or later with transport `auto` or `quic`. Outbound UDP
  port 7844 must be permitted for QUIC.
- The School CSM local server fixed to port `8080` for this deployment mode.
- Permission to create a VPC Service and bind it to a Worker. Cloudflare
  currently calls these roles **Connectivity Directory Admin** and
  **Connectivity Directory Bind**.

The tunnel token is a persistent credential. Enter it only through the Control
Center setup flow so it can be placed in Windows Credential Manager. Never put
it in this directory, a Wrangler file, a Git commit, a screenshot, or a support
message.

## Cloudflare dashboard setup

1. Create the remotely managed Tunnel and its connector. Do **not** add a public
   hostname or a broad private-network route. Start the connector from the
   Control Center using its securely saved token.
2. In Workers VPC, create one HTTP VPC Service with these values:

   | Field | Required value |
   | --- | --- |
   | Tunnel | This school's named Tunnel |
   | Host or IP | `127.0.0.1` |
   | HTTP port | `8080` |
   | HTTPS port | Not configured |
   | TLS verification | Not applicable; the local hop is HTTP |

   Use a VPC **Service**, not a broader VPC Network binding. The fixed service
   limits the Worker to this one loopback origin and port.
3. Create a Worker whose name is the exact School ID. Its production URL will
   be `<SCHOOL_ID>.<ACCOUNT_SUBDOMAIN>.workers.dev`. Keep preview URLs disabled.
4. Add the VPC Service binding using the exact binding name
   `SCHOOL_CSM_ORIGIN`.
5. Add a plain-text Worker variable named `PUBLIC_HOST` containing only the
   exact lowercase hostname, without `https://`, a port, path, or trailing dot.
6. Deploy `src/index.js`, then enter that same public hostname and the Tunnel ID
   in the Control Center's single-school Internet Gateway setup.

No Cloudflare account ID, tunnel ID, service ID, API token, or connector token
belongs in the public repository.

## Optional Wrangler deployment

Wrangler is not required on an end-user computer; this option is for the person
administering the Cloudflare account.

1. Copy `wrangler.example.jsonc` to the ignored file `wrangler.jsonc`.
2. Replace all three `REPLACE-...` placeholders. `name` must be the exact School
   ID, `PUBLIC_HOST` must be its complete production `workers.dev` hostname, and
   `service_id` must be the VPC Service UUID (not a tunnel token).
3. Authenticate interactively with Cloudflare and deploy:

   ```powershell
   npx wrangler@latest deploy --config wrangler.jsonc
   ```

The template intentionally has no `account_id` and no secret. Do not use a
broad API token merely to avoid the interactive sign-in.

## Verification and rollback

After the local server and connector are running:

1. Open `https://<PUBLIC_HOST>/healthz` from a device that is not using the
   school Wi-Fi. It must return the School CSM healthy response over HTTPS.
2. Open `/survey`, start an access-code session, and submit a disposable test
   response according to the school's testing procedure.
3. Open `/scanner`, verify that anonymous access cannot reach operator actions,
   then sign in with a test Scanner Operator account and complete a disposable
   scan workflow.
4. Stop the connector. The public address must return only the generic `503`;
   local survey/scanner use must continue.
5. Confirm the Control Center logs contain no tunnel token, response content,
   respondent IP address, session cookie, or operator password.

To take the pilot offline immediately, disable the Worker `workers.dev` route
and stop the connector. Removing the Worker, its binding, or the VPC Service
does not delete School CSM data because that data is stored locally. Revoke and
replace the Tunnel token if it may have been exposed.

## Local contract tests

The Worker tests use only Node's built-in test runner and make no network calls:

```powershell
cd cloudflare_worker
npm test
```
