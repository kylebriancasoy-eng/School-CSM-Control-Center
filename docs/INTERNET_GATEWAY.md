# Internet Gateway and multi-school deployment

School CSM Control Center 0.6.0 is offline-first. The Internet Gateway is an
optional deployment feature, not a requirement for survey collection. With no
saved gateway configuration, the application stays **Local Only**, starts no
tunnel, and makes no registration-service request.

## Architecture and data ownership

- A registered school receives one stable HTTPS hostname derived from its
  official School ID, such as `123456.csm.example.gov.ph`.
- The bundled tunnel connector makes an outbound connection. No router port
  forwarding or public inbound Windows Firewall rule is required.
- The school computer remains the HTTP origin and authoritative data store.
  Survey answers, scanner photographs, reports, narrative text, print evidence,
  and portable backups are not copied into the registration service.
- The registration service stores infrastructure metadata only: school and
  installation identities, public hostname, passkey public credentials, tunnel
  identifiers, short-lived ceremonies and handoffs, transfer history, and audit
  events.
- The service enforces one active Internet Server per School ID. Transfer changes
  authority; it does not create a second active public server.

Local Survey and Scanner access continues to use the verified LAN addresses.
Internet Survey and Scanner links are separate and appear only for a configured,
registered, authorized installation.

## Single-school Cloudflare pilot without a custom domain

A school that does not operate the managed multi-school provider can use the
**Single-school Cloudflare pilot** setup route. It combines one remotely managed
Cloudflare Tunnel, one narrow HTTP VPC Service fixed to `127.0.0.1:8080`, and one
Worker named with the official School ID. The public address has this form:

```text
https://<SCHOOL_ID>.<ACCOUNT_SUBDOMAIN>.workers.dev
```

Workers VPC is currently a Cloudflare beta service, and Cloudflare describes
`workers.dev` as intended for non-business-critical use. Treat this route as a
school pilot, monitor it, and keep Local-Only access available. A future managed
domain can replace the public route without moving the local CSM records.

The public Worker implementation and deployment template are in
[`cloudflare_worker`](../cloudflare_worker/README.md). They contain no live
account ID, Tunnel ID, service ID, API token, or connector token. The Worker
accepts only the methods used by the Survey and Scanner service, replaces all
forwarding identity headers, rejects the wrong host or non-HTTPS request, and
can reach only the fixed VPC Service. The Control Center independently trusts
only the loopback connector and exact School-ID hostname.

### Pilot setup

1. Save the official 4-to-12-digit School ID and set the preferred Survey Server
   port to `8080`.
2. In Cloudflare, create one remotely managed named Tunnel for the school. Do
   not add a public hostname or a broad private-network route.
3. In **Workers VPC**, create an HTTP VPC Service for that Tunnel with IPv4 host
   `127.0.0.1`, HTTP port `8080`, and no HTTPS port.
4. Create a Worker whose name is the exact School ID, bind the VPC Service as
   `SCHOOL_CSM_ORIGIN`, set `PUBLIC_HOST` to the exact lowercase
   `workers.dev` hostname, disable preview URLs, deploy
   `cloudflare_worker/src/index.js`, and keep the production `workers.dev` route
   enabled.
5. In **Survey Server and Respondent Access > Internet Gateway > Set Up Internet
   Gateway**, choose **Single-school Cloudflare pilot**. Enter the public Worker
   hostname, Tunnel ID, and complete connector token, acknowledge the beta pilot,
   and save.
6. The Control Center stores the connector token only in Windows Credential
   Manager. Start the local Survey Server, choose **Connect Internet Gateway**,
   and verify both public Survey and Scanner links from a device that is not on
   the school Wi-Fi.

The connector must use Cloudflare Tunnel transport `auto` or `quic`; Workers VPC
does not use an ingress rule. Outbound connectivity to Cloudflare is required,
but no inbound router port and no public Windows Firewall rule is opened.

## Managed multi-school operator registration

1. Enter and save the official School ID in **School Information**.
2. Open **Survey Server and Respondent Access > Internet Gateway**.
3. Choose **Set Up Internet Gateway**. The Control Center checks the provider and
   opens its HTTPS management page in the default browser.
4. For first registration, enter the one-time activation code issued by the
   gateway administrator and create a Windows Hello or security-key passkey.
5. Copy the short-lived completion code back to the Control Center. The returned
   device credentials are written to Windows Credential Manager, not to the data
   folder or provider file. Delivery is acknowledged only after both the
   credentials and local registration state are durable. If the acknowledgement
   response is interrupted, the same unexpired code can be entered again for the
   same installation; delivery attempts are strictly bounded.
6. Start the local Survey Server. The gateway connects only after the local
   loopback health check and a fresh signed provider authorization check succeed.

Passkeys authorize registration, administrator-passkey management, and Internet
Server transfer. They are separate from Scanner Operator accounts and from the
optional OpenAI API key. An existing passkey must approve an additional passkey.
If all passkeys are lost, a deployment administrator can issue a short-lived
recovery code; successful recovery replaces all old passkeys.

## Moving the Internet Server

The replacement Control Center creates a short-lived transfer intent before it
opens the browser. The intent binds the School ID, destination installation ID,
transfer mode, and committed data-validation receipt. The browser receives only
an opaque intent identifier and cannot change those facts or self-assert that an
import succeeded.

### Live-device Server and Data transfer (`.mossmig`)

Use this mode when the current active computer is available.

1. On the replacement computer, copy its installation ID from **Server
   Transfer**.
2. On the active source computer, stop the local Survey Server and choose
   **Prepare Server + Data Package**.
3. Enter the replacement installation ID and save the encrypted `.mossmig` file.
4. Record the newly generated 256-bit key. It is shown once and is never stored
   by the Control Center.
5. Send the package and key through separate trusted channels.
6. On the replacement computer, select **Server and Data**, choose the package,
   enter its key, and continue.

A `.mossmig` package is bound to one destination installation. The destination
decrypts into an inert staging area, permits only the portable data allowlist,
verifies identity, version, paths, file hashes, record counts, and the complete
active-tree hash, then commits atomically with rollback evidence. A successfully
consumed `.mossmig` is deleted when local policy permits; if deletion is blocked,
the cleanup is recorded for follow-up. Portable scanner-preview files are
included, counted, verified, committed, and restored by rollback with the other
school-owned scanner records.

### Backup-assisted recovery (`.mossbak`)

Use **Server and Data from backup** when a separately prepared portable School
CSM recovery backup is available. A `.mossbak` is encrypted and school-bound but,
unlike `.mossmig`, is not bound to one destination computer. This makes it usable
when the original active server is unavailable.

Create the portable recovery backup while the source Survey Server is stopped.
Its separate 256-bit backup key is also shown once and is not persisted. At the
replacement computer, select the `.mossbak`, enter its key, and allow the same
strict staged validation and atomic commit boundary to complete before the
passkey cutover opens.

Only the School CSM Portable Recovery Backup 1.0 envelope is accepted. Installer
rollback copies, store `.last-good.bak` files, ordinary ZIP files, renamed files,
and `.mossmig` packages are not treated as `.mossbak` backups. Keep a portable
backup and its key in separately protected storage and test the recovery process.

### Server Only

**Server Only** moves the public address without restoring historical school
records. It requires an explicit data-loss warning confirmation. Use it only
when no valid live-transfer package or portable recovery backup exists and the
deployment owner accepts that the replacement starts without the old data.

### Authority cutover and old-computer retirement

After the data boundary succeeds, the operator approves the transfer with a
registered passkey. The registration service routes the permanent hostname to a
replacement tunnel, commits the new active installation, and then revokes the
old connector. A failed database cutover restores the old route and removes the
replacement tunnel.

The old installation receives a provider-signed `transferred` or `revoked`
state, verifies and persists that signed evidence, stops its local and Internet
servers, and starts only in the forced-retirement window. That window exposes
only **Uninstall Application** and **Exit**. A signed `transfer_pending` state is
only a warning; it does not retire the old installation.

## Provider configuration

The public repository and release intentionally include only
`internet_gateway_provider.example.json`. The example can never enable the
gateway. A deployment administrator installs the real, non-secret file at:

```text
C:\ProgramData\MoSSLab\School CSM Control Center\Configuration\internet_gateway_provider.json
```

For deliberate migration or emergency override, an exact
`internet_gateway_provider.json` beside the application EXE takes precedence.
Setup validates and preserves that sidecar into the durable ProgramData location
before install, update, repair, rollback, or uninstall changes Program Files.
The ProgramData copy survives all those operations, including explicit removal
of the current operator's records and credentials.

During uninstall, an invalid sidecar is moved to a timestamped ProgramData
quarantine file. If Setup cannot preserve an invalid sidecar outside the folder
it is about to remove, uninstall stops before deleting application files. An
invalid ProgramData copy is already outside that removal boundary and is left in
place for a deployment administrator to correct.

The file uses schema `1.0` and must contain:

- the exact HTTPS registration-service origin, without embedded credentials;
- the deployment-managed public domain;
- at least one key ID and raw 32-byte Ed25519 authorization public key encoded as
  Base64; and
- an explicit nonempty list of the exact proxy IP addresses allowed to provide
  forwarded HTTPS context.

Do not use broad proxy networks or rely on an assumed loopback default. List only
the addresses used by the local tunnel/reverse-proxy boundary. Private signing
keys, Cloudflare API tokens, tunnel credentials, handoff keys, activation codes,
passkey private material, and OpenAI keys must never enter this file, a Git
commit, or a support log.

## Device credentials and maintenance

Gateway device secrets use these exact current-account Windows Credential
Manager targets:

- `MoSSLab.SchoolCSMControlCenter.InternetGateway.TunnelCredential`
- `MoSSLab.SchoolCSMControlCenter.InternetGateway.InstallationSecret`
- `MoSSLab.SchoolCSMControlCenter.InternetGateway.DevicePrivateKey`

Normal install, update, repair, rollback, and uninstall preserve them. The Setup
checkbox for explicit saved-record and application-credential removal deletes
all three gateway targets and the separate OpenAI target for the Windows account
running Setup. It does not delete the machine-wide provider configuration.

The application bundle includes `cloudflared` 2026.5.2. Both the build and
runtime require SHA-256
`20b9638f685333d623798e733effbad2487093f15ba592f6c7752360ff3b7ab7`.
Operators must use Setup **Repair** after an integrity failure instead of
substituting another executable.

## Public request boundary

Internet requests are accepted only when they arrive from an explicitly trusted
proxy address, report forwarded HTTPS, and use the exact School-ID public host.
Public traffic cannot use the local captive-portal shortcut. Public cookies are
Secure, host-only, HttpOnly, and SameSite. Browser-supplied School IDs and
transport labels are ignored in favor of server-owned provenance. Scanner and
respondent routes enforce request-size, session, origin, and rate limits.
Public survey sessions and rate-limit identity stores have fixed memory bounds,
and client identities are stored only as process-keyed digests. Installation
authorization endpoints are throttled before repeated password-hash work. Local
operation retains its existing LAN behavior.

A cached `ACTIVE` status is display context, not permission to expose a tunnel
origin. In managed multi-school mode, every Control Center process must obtain a
fresh signed `active` response. In the single-school pilot, each process must
verify that the locally protected connector credential is present and validly
formed. Only then may the local server use its Gateway binding. If a periodic
managed authorization check fails, expires, or reports retirement, the app
stops the tunnel and restarts the Survey Server on its selected Local-Only
interface before continuing.

## Failure behavior

- Provider, DNS, or tunnel failure affects only Internet status; local collection,
  scanning, history, analysis, printing, and backups remain available.
- A missing or invalid managed-provider file disables managed registration. It
  leaves the app **Local Only** unless a valid single-school pilot configuration
  has been saved explicitly by the operator.
- A mismatch between saved School Information and signed registration stops the
  gateway; the app never silently rewrites either identity.
- Missing or altered `cloudflared.exe` fails closed and directs the operator to
  Setup **Repair**.
- The registration service cannot retrieve school response content because that
  content is not stored there.

## Production deployment checklist

1. Deploy the separately tested registration service using
   [`registration_service/README.md`](../registration_service/README.md).
2. Put it behind an HTTPS reverse proxy and set the exact WebAuthn origin and RP
   ID.
3. Protect and back up its infrastructure-only SQLite database together with the
   matching signing and handoff keys.
4. Configure the managed DNS zone and a least-privilege Cloudflare tunnel token.
5. Generate and protect an Ed25519 signing key and an independent random 32-byte
   credential-handoff key.
6. Install the matching public provider configuration in ProgramData with exact
   trusted proxy IPs.
7. Issue a one-time activation code for the exact School ID.
8. Test Local Only, public Survey, public Scanner, disconnection recovery,
   `.mossmig` live transfer, `.mossbak` backup recovery, Server Only warnings,
   source retirement, update/repair/rollback, normal uninstall, and explicit
   current-account data removal on clean Windows computers.

The public repository does not supply a production managed domain, registration
service, authorization keys, Cloudflare account/zone/token, DNS ownership,
reverse proxy, TLS certificates, secret manager, monitoring, or backup service.
Those are deployment-owner responsibilities and must be provisioned before the
optional gateway can be enabled.
