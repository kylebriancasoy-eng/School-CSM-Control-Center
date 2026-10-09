# School CSM District Management Server

The **School CSM District Management Server** is the separate Windows host for
the district roster, activation approvals, Internet Gateway provisioning, and
infrastructure-status portal. It is not another School CSM Control Center and it
does not receive or consolidate survey responses.

The compiled district program runs without a separate Python installation. It
hosts the registration service on the district computer and uses an outbound
Cloudflare connection for HTTPS access. Each registered school still owns its
own Control Center computer, local response database, School-ID hostname,
tunnel, VPC Service, Worker, and connector credential.

## Free-pilot availability boundary

The no-custom-domain pilot keeps the district service on the designated Windows
computer. For the public portal, activation, provisioning, and check-ins to be
available, that computer must remain:

- powered on and signed in or configured for approved background startup;
- connected to the internet;
- running the District Management Server and its outbound connector; and
- permitted by local firewall, endpoint-security, and Cloudflare policies.

If that computer or its internet connection is unavailable, already installed
school Control Centers retain their local data and Local-Only Survey and Scanner
functions. Their public Internet Gateway routes, the district portal, first-time
activation, reconnect authorization, and district check-ins may be unavailable
until the district host returns. This is a monitored pilot design, not an
always-on cloud-hosting guarantee.

Use a dedicated, managed district computer with reliable power and connectivity.
For an availability requirement that must not depend on that computer, move the
same registration-service responsibilities to approved always-on hosting before
treating the service as production-critical.

## Public address and school isolation

The Motiong pilot uses the Cloudflare account's `workers.dev` subdomain as an
address family:

```text
https://management.motiong-district-csm-survey.workers.dev/district
https://<OFFICIAL_SCHOOL_ID>.motiong-district-csm-survey.workers.dev
```

The `management` Worker reaches only the district service. A School-ID Worker
reaches only that school's fixed local Survey Server origin through its own VPC
Service and tunnel. The School ID in a QR address selects the school before any
form is shown; locked School ID and school-name fields on the form are
confirmation labels, not editable routing instructions.

Taking one school's form **Offline** or **Under Maintenance** affects only that
school. A district-host outage does not merge school traffic, move responses to
another school, or change another school's form status.

## Installation and runtime locations

The tagged release publishes a separate Windows x64 archive named like:

```text
School-CSM-District-Management-Server-vX.Y.Z-win-x64.zip
```

Extract it to a district-administrator-controlled application location and run
only:

```text
School CSM District Management Server.exe
```

Do not run the source entry point on an operator computer. The compiled archive
contains its Python runtime; Python does not need to be installed.

Machine-wide runtime state is stored outside the program files under:

```text
C:\ProgramData\MoSSLab\School CSM District Management Server\
  Configuration\server.json
  Data\registration.sqlite3
  Logs\district-management-server.log
  Locks\district-management-server.lock
```

`server.json` contains non-secret settings only. Private signing material,
handoff encryption material, the district administrator credential, and the
Cloudflare API token belong in the documented
`MoSSLab.SchoolCSMDistrictManagementServer.*` Windows Credential Manager
targets. Never copy those secrets into JSON, a command file, a Git commit, a QR
code, a support ticket, or a screenshot.

Only one District Management Server process may use a runtime directory at a
time. Do not place the SQLite database on a network share or synchronize its
live files with consumer file-sync software.

## Initial district setup

Before onboarding schools, the district operator must complete these steps on
the designated host:

1. Verify the official district and division labels and reserve the account's
   `workers.dev` subdomain used for this deployment.
2. Create a least-privilege Cloudflare API token for the district server. Limit
   it to the intended account and grant only **Cloudflare Tunnel: Write**,
   **Connectivity Directory: Admin**, and **Workers Scripts: Write** (or the
   equivalent Edit labels shown by Cloudflare's current token screen).
3. Save the token through the district program so it is written to Windows
   Credential Manager, not the configuration file.
4. Generate the district authorization signing key, handoff key, and high-entropy
   district administrator access token through the program's protected setup.
   Save the one-time administrator token in the district's approved password
   manager before choosing **Finish**. The setup window cannot be dismissed
   while provisioning is active or while that token awaits acknowledgement.
5. Start the local service and outbound connector, then verify `/healthz` and
   the exact HTTPS district portal address.
6. Sign in to `/district`, test logout and a fresh login, and use **Create
   Encrypted Recovery Backup** before issuing the first activation code.

Workers VPC uses Cloudflare Tunnel transport `quic`. The host firewall must
allow outbound UDP port `7844`; no inbound Internet port should be opened. If a
Worker named `management` already exists, setup stops before creating any
resource. Review and migrate that Worker deliberately instead of overwriting it.

Cloudflare account changes and API-token creation are privileged operations.
Perform them while signed in as the authorized Cloudflare account owner, review
the exact account scope, and revoke any temporary or broader token after the
least-privilege district token is verified.

## School onboarding

The district operator and school operator have separate duties.

### District operator

1. Verify the request through an approved district channel.
2. Add the official School ID and school name to the district roster. Confirm
   the district and division values before saving.
3. Issue one short-lived activation code. Deliver it to the verified school
   coordinator through the approved channel; do not place it in a public QR code
   or ordinary support log.
4. Keep the portal open only long enough to confirm that the correct School ID
   becomes registered and begins a healthy check-in.

Issuing a replacement activation code invalidates the earlier unused code.
Suspending a roster entry prevents new enrollment; it does not delete the
school's local data or silently turn off an already authorized local server.

### School operator

1. Install School CSM Control Center on the computer that will be that school's
   active server.
2. Complete every section of **Mandatory School Registration** using the same
   official School ID recorded by the district.
3. Open **Survey Server and Respondent Access** and go to **Internet Gateway**.
4. Select **Connect or reconnect the Internet Gateway**.
5. Enter the district-issued activation code when the HTTPS registration page
   requests it, verify the school identity shown, and create the school's
   administrator passkey.
6. Return to the Control Center and wait for the signed authorization and
   connector credential to be saved. Confirm **Connected**, then test the
   school's own Survey and Scanner addresses from an external connection.

After first enrollment, reconnects use the installation authorization protected
by Windows Credential Manager. A different computer does not become active just
because it uses the same School ID; follow the Server-and-Data transfer or
recovery workflow so the one-active-server rule and local records move safely.

For this four-step school experience to work without a manual file-copy step,
the school release must contain the non-secret generated profile as
`internet_gateway_provider.default.json`. The district host creates the source
profile under `School Deployment`; maintainers review it and bundle that public
default in the school release. It contains only the management HTTPS origin,
managed address family, public signing key, and proxy boundary—never a token or
private key.

An existing numeric Worker from an earlier single-school pilot is not replaced
automatically. Initial managed enrollment stops before making changes and asks
for the documented migration process. This protects the school's working route
from accidental overwrite or rollback deletion.

## What the district can and cannot see

The district database may contain the official roster, installation identity,
public hostname, passkey public credential, tunnel/VPC/Worker identifiers,
short-lived provisioning ceremonies, safe audit events, and bounded health
states.

It must not contain:

- respondent names or identifying information;
- survey answers, remarks, or service selections;
- scanner images or recognition results;
- Dashboard or Narrative Report contents;
- school `.mossbak` or `.mossmig` files; or
- a school's OpenAI API key.

The health status is an infrastructure signal. **Connected** means a recent
check-in reported a running local server and gateway without a bounded error; it
does not prove that a response was submitted or validate any survey result.

## Backup and recovery

Choose **Create Encrypted Recovery Backup** to create one `.mossdistrict` file
containing a matched, authenticated, AES-256-GCM-encrypted set of:

- a consistent `Data\registration.sqlite3` snapshot, including committed WAL
  content through SQLite's online backup operation;
- `Configuration\server.json`;
- the authorization private key and its key ID;
- the handoff encryption key;
- the stored district administrator token digest (not the raw token);
- the scoped Cloudflare API token;
- the management connector token; and
- a record of the active public `workers.dev` subdomain and Cloudflare account.

The program uses SQLite's online backup operation, validates the copied database,
and encrypts the complete set before it replaces the destination file. Do not
make a live backup by copying only the main SQLite file while the service is
writing. Store the backup file and its 12-or-more-character recovery password in
separate approved locations and keep at least one offline copy.

Use **Restore Encrypted Recovery Backup** only on an empty district host. Restore
fails closed if configuration, database files, the deployment profile, or any
district Credential Manager entry already exists; it never overwrites a running
deployment. The raw district administrator token is deliberately not in the
backup because the service never stores it. Keep that token in the approved
password manager. If it is lost, use **Reset District Administrator Token** on
the authorized district host, save the replacement shown once, and immediately
create a new encrypted recovery backup.

The database and cryptographic keys are a matched set. Restoring the database
without the corresponding signing and handoff keys can invalidate authorization
or outstanding completion flows. Restoring secrets without the matching
database can also make audit and ownership state inconsistent. Back up before
updates, schema changes, Cloudflare provisioning changes, and server-host
migration.

A district backup is not a school `.mossbak` file. Restoring the district server
does not restore a school's responses; each school remains responsible for its
own encrypted Control Center backups.

## District portal token reset

The district portal login token and the Cloudflare API token are different
credentials. Keep the portal token in the approved password manager under a
distinct label such as **CSM District Portal Login**; never send it in a support
message or screenshot.

To replace a lost portal token, open the district app's status window and choose
**Reset District Administrator Token**. Save the replacement before ticking the
saved confirmation and choosing **Finish**. Finish clears the clipboard, so
retrieve the saved value from the password manager when signing in. Wait for
the service to show **Running**, refresh the portal, and sign in with the latest
token. Older tokens and browser sessions must no longer work. Create a new
encrypted recovery backup after confirming the replacement.

The original v0.8.0 district build reused its first application instance during
an in-app restart. After resetting a token on that build, fully **Exit** the
district app from its notification-area menu and reopen its EXE once to load the
replacement. Closing the status window only hides it and **Start / Restart** is
not enough. Do not repeatedly reset the token, restore an old backup, or remove
Credential Manager entries to work around this problem. The corrected source
creates fresh application and login-session state on every service start.

## Security checklist

- Keep the Windows host patched, encrypted, and limited to authorized district
  administrators.
- Require HTTPS; never expose the local Uvicorn port directly to the internet.
- Keep the local service bound to loopback and
  expose it only through the narrow outbound tunnel and VPC Service.
- Protect the district portal with its high-entropy administrator token and, if
  available, an additional Cloudflare identity/access policy.
- Do not share the district administrator token with school operators. School
  passkeys and one-time activation codes are the school enrollment boundary.
- Scope the Cloudflare token to the one account and required resources. Rotate
  it after suspected disclosure, staff turnover, or a scope reduction.
- Review audit events for activation, suspension, provisioning, transfer,
  credential rotation, failed authentication, backup, and restore operations.
- Do not record raw access tokens, activation codes, connector credentials,
  private keys, response data, or raw client IP addresses in logs.
- Keep Local-Only operation tested at each school so a district or Cloudflare
  outage does not stop onsite collection.

## Update and incident sequence

Before updating the district program:

1. Stop new onboarding and transfer work.
2. Create and verify an encrypted matched backup.
3. Record current `/healthz`, portal, and school check-in status.
4. Stop the district program cleanly, replace only the compiled application
   files, and preserve the ProgramData runtime directory and Credential Manager
   entries.
5. Start the new version and verify schema startup, portal authentication,
   Cloudflare connectivity, and a known school check-in before resuming
   activation.

For suspected credential disclosure, stop provisioning, revoke or rotate the
affected Cloudflare or district credential, preserve logs and audit evidence,
and verify every School-ID route before reconnecting schools. Do not delete the
registration database as a troubleshooting shortcut.

## Source deployment and validation

Maintainers can still deploy `registration_service/` behind an approved HTTPS
host instead of using the Windows pilot host. The same data-isolation,
credential, backup, and one-active-server rules apply. See
[`../registration_service/README.md`](../registration_service/README.md) for
service configuration and protected administrative recovery commands, and
[`INTERNET_GATEWAY.md`](INTERNET_GATEWAY.md) for the school-side lifecycle.

The source-service configuration for the free Workers VPC route uses:

```text
SCHOOL_CSM_CLOUDFLARE_MODE=workers_vpc
SCHOOL_CSM_MANAGED_DOMAIN=motiong-district-csm-survey.workers.dev
CLOUDFLARE_ACCOUNT_ID=<district account ID>
CLOUDFLARE_API_TOKEN=<secret; inject from the secret store>
SCHOOL_CSM_TUNNEL_ORIGIN=http://127.0.0.1:8080
```

`CLOUDFLARE_ZONE_ID` is required by the custom-domain `dns_tunnel` mode, not by
`workers_vpc`. `SCHOOL_CSM_WORKER_SOURCE` is a controlled deployment override;
do not point it at unreviewed code. Provisioning creates the named tunnel,
narrow VPC Service, ordinary School-ID Worker, and connector credential in that
order. A failed replacement restores the prior Worker binding before removing
the replacement resources.
