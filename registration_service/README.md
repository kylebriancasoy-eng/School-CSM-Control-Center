# School CSM Internet Gateway registration service

This is the separately deployed infrastructure-control service for the optional
Internet Gateway in School CSM Control Center 0.5.1. It does not host the survey
application and its schema contains no survey responses, scanner images,
narrative content, portable backup content, or print records.

## Responsibilities

- issue and consume one-time School activation codes;
- register a School-ID hostname and the first active installation;
- enroll and verify WebAuthn passkeys;
- enforce one active Internet Server per School ID;
- provision and rotate remotely managed Cloudflare tunnels and DNS routes;
- sign short-lived authorization/retirement envelopes with Ed25519;
- deliver new installation credentials through an encrypted, installation-bound,
  short-lived completion code with bounded redelivery until the destination
  acknowledges durable local storage;
- add administrator passkeys only after an existing passkey authorizes enrollment;
  and
- provide protected, one-time lost-passkey recovery and last-passkey-safe
  revocation administration.

## Required production infrastructure

The service requires values supplied by the deployment owner. This repository
does not contain production values:

- `SCHOOL_CSM_MANAGED_DOMAIN`
- `SCHOOL_CSM_WEBAUTHN_ORIGIN` and `SCHOOL_CSM_WEBAUTHN_RP_ID`
- `SCHOOL_CSM_AUTHORIZATION_KEY_ID`
- `SCHOOL_CSM_AUTHORIZATION_PRIVATE_KEY_BASE64` (raw 32-byte Ed25519 private key)
- `SCHOOL_CSM_HANDOFF_ENCRYPTION_KEY_BASE64` (32 random bytes)
- `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_ZONE_ID`, and a least-privilege
  `CLOUDFLARE_API_TOKEN`
- `SCHOOL_CSM_TUNNEL_ORIGIN`, the service-owned origin passed to newly provisioned
  remotely managed tunnels
- a persistent `SCHOOL_CSM_REGISTRATION_DATABASE` location

Use the hosting platform's secret manager. Do not place secret values in `.env`,
container images, Git commits, desktop provider configuration, or support logs.
`.env.example` lists variable names only.

`SCHOOL_CSM_ALLOW_INSECURE_TEST_CLIENT=1` exists only for the in-process test
client. Never set it in a deployed service.

## Run for development

Use a dedicated Python 3.12 environment and install the pinned service
dependencies. `requirements-test.txt` is needed only for the in-process API test
client.

```powershell
python -m pip install -r registration_service/requirements.txt
python -m pip install -r registration_service/requirements-test.txt
python -m unittest tests.test_registration_service_core `
  tests.test_registration_service_app `
  tests.test_registration_service_cloudflare `
  tests.test_internet_gateway_provider -v
```

For production, run `registration_service.app:app` behind an HTTPS reverse proxy.
The supplied Dockerfile starts a non-root Uvicorn process on port 8000 and trusts
forwarded headers only from loopback. Publish the health check at `/healthz` and
the passkey/registration interface at `/manage`. Set the public WebAuthn origin
to the exact HTTPS registration origin and the RP ID to its correct registrable
host scope; WebAuthn fails closed for a different origin or RP ID.

Mount the SQLite parent directory on persistent storage and inject secrets at
runtime through the hosting platform. The container image and public repository
are not secret stores. The reverse proxy must terminate TLS, prevent direct
public access to Uvicorn, and preserve the exact public origin needed by
WebAuthn.

## Initial activation

Issue an activation code from a protected administrative shell on the service
host:

```powershell
python -m registration_service.admin issue-activation --school-id 123456
```

The code is shown once, stored only as a digest, and expires. Deliver it through
an approved channel to the school operator. Never issue activation codes from a
public anonymous endpoint.

Additional administrator passkeys are enrolled from the HTTPS **Passkeys** page
only after an existing passkey authorizes the operation. Protected service-host
administration provides the recovery and revocation boundary:

```powershell
python -m registration_service.admin issue-passkey-reset --school-id 123456
python -m registration_service.admin list-passkeys --school-id 123456
python -m registration_service.admin revoke-passkey --school-id 123456 `
  --credential-id BASE64URL_ID --confirm
```

A recovery code is short-lived and shown once. Completing recovery atomically
revokes every old passkey and installs the newly verified passkey. Ordinary
revocation refuses to remove a school's final passkey.

Run the commands against the same persistent database used by the service, either
through `SCHOOL_CSM_REGISTRATION_DATABASE` or the global `--database` argument.
Treat activation codes, recovery codes, and the credential IDs printed by the
listing command as administrative material; do not put them in tickets or public
logs.

## Persistence and backup

The SQLite database is infrastructure metadata. Back it up together with the
matching signing and handoff keys using the hosting platform's encrypted backup
facility. Losing the handoff key invalidates outstanding completion codes;
rotating the signing key requires shipping the new public key in desktop provider
configuration before using it to sign authoritative status.

The handoff table stores AES-GCM ciphertext, not plaintext tunnel or installation
secrets. Registration or transfer and its encrypted handoff row commit in the
same SQLite transaction. A completion response lost in transit recovers the
exact same code for the exact same request instead of provisioning or cutting
over again. Redemption may return the same payload only a small bounded number
of times to its one bound destination; the desktop acknowledges only after
Windows Credential Manager and local registration state are durable. That
acknowledgement clears the ciphertext. Codes expire after ten minutes.

The database backup is not a School CSM `.mossbak` file. Service database backups
contain infrastructure metadata, while `.mossbak` files contain encrypted
school-owned desktop data and remain outside this service.

## Security and operations

- Publish only through HTTPS and restrict direct Uvicorn access to the proxy.
- Keep the Cloudflare token scoped to the required account tunnel and DNS zone
  operations.
- Monitor rate-limit, activation, passkey, authorization, transfer, and tunnel
  audit events without recording raw client IPs or school response content.
- Keep the service on one controlled worker unless the deployment supplies a
  shared rate-limit backend; the reference in-memory limiter is process-local,
  bounded, and applies per-client and per-installation throttles before repeated
  installation-secret verification.
- Back up before schema or hosting changes and test restore procedures.
- During transfer, the replacement tunnel receives the permanent DNS route before
  the database cutover. A failed database cutover restores the source route and
  deletes the replacement; the source connector is revoked only after the new
  active installation is durably committed.
- Do not weaken the transfer-intent boundary: the Server-and-Data API accepts a
  strict committed-migration receipt, while the browser receives only its opaque
  short-lived identifier and cannot edit or self-assert validation fields.

## Desktop provider configuration

After the service, domain, DNS, and signing key are provisioned, create the
non-secret desktop `internet_gateway_provider.json` with schema `1.0`, the exact
HTTPS service URL, the managed domain, the signing key ID and raw Ed25519 public
key in Base64, and an explicit nonempty list of exact trusted proxy IP addresses.
Install it at:

```text
C:\ProgramData\MoSSLab\School CSM Control Center\Configuration\internet_gateway_provider.json
```

The public provider file must not contain the private signing key, handoff key,
Cloudflare token, tunnel credentials, activation/recovery codes, or passkey
private material. Do not publish the real file in the public GitHub release.
Setup preserves the ProgramData copy across update, repair, rollback, and
uninstall.

## Deployment validation

Before issuing production activation codes, test exact-origin WebAuthn, public
hostname routing, one-active-server enforcement, database-backout route rollback,
old-tunnel revocation, lost-passkey recovery, additional-passkey enrollment,
encrypted handoff expiry/redemption, signed desktop retirement, service backup
restore, and Local Only behavior when the service is unavailable.

See [`../docs/INTERNET_GATEWAY.md`](../docs/INTERNET_GATEWAY.md) for the desktop
and operator lifecycle.
