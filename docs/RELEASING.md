# Build and release guide

## Repository layout

- `school_csm_control_center/` contains the application package.
- `assets/` contains installed application and scanner resources.
- `tests/` contains regression tests.
- `packaging/pyinstaller/` defines the compiled application bundle.
- `packaging/installer/` contains the dependency-free .NET Framework online maintenance program.
- `registration_service/` contains the separately deployed, metadata-only
  Internet Gateway registration and passkey service.
- `district_management_server/` contains the compiled Windows host/controller
  for the separate district service; it is not part of a school's app bundle.
- `district_gateway_worker/` contains the public `management` edge Worker and
  its contract tests. Numeric school Workers remain separate.
- `scripts/` contains repeatable build, manifest, checksum, and verification tools.
- `.github/workflows/` contains continuous integration and tagged-release automation.
- `docs/` contains operator and maintainer documentation.

Runtime data must not be added to the repository. The `.gitignore` excludes build output and common data directories.

## Connect the local repository

The GitHub owner and repository name are intentionally not hard-coded. Create an empty GitHub repository, then configure the local checkout:

```powershell
git remote add origin https://github.com/OWNER/REPOSITORY.git
git branch -M main
git push -u origin main
```

GitHub Actions automatically injects `${{ github.repository }}` when it compiles setup. A local installer build must receive the same value through `-Repository owner/repository` or the `GITHUB_REPOSITORY` environment variable. The build stops clearly if that value is absent or malformed.

The bootstrapper intentionally performs anonymous HTTPS downloads and stores no GitHub token. Publish the release assets from a public repository. A private repository needs a separate authenticated distribution design and is not supported by this bootstrapper.

## Versioning

`school_csm_control_center/version.py` is the single source of truth for this
release:

- application semantic version: `0.8.9`;
- Windows executable version: `0.8.9.0`;
- release tag: `v0.8.9`.

District releases also include a separate product-bound `district-release.json`,
`District-SHA256SUMS.txt`, and `School-CSM-District-Management-Server-Setup.exe`.
The district build script generates these after verifying the native runtime and
ZIP contents. Do not substitute the school `release.json` or school Setup.
See [district installation and upgrade safety](DISTRICT_INSTALLATION.md).

After changing the version, regenerate and check the PyInstaller version resource:

```powershell
python scripts/generate_version_info.py
python scripts/generate_version_info.py --output packaging/pyinstaller/district_version_info.txt --product-name "School CSM District Management Server" --original-filename "School CSM District Management Server.exe" --python-version-output district_management_server/version.py
```

The release workflow refuses a tag that does not exactly match `RELEASE_TAG`.

## Local build prerequisites

- 64-bit Python 3.11 through 3.14;
- dependencies in `requirements-lock.txt`;
- PyInstaller 6.x;
- the Windows .NET Framework 4.x C# compiler at `%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe`;
- optional Windows SDK `signtool.exe` and a code-signing certificate.

The app build fetches the official Windows AMD64 `cloudflared` 2026.5.2 asset
and accepts it only when its SHA-256 is exactly:

```text
20b9638f685333d623798e733effbad2487093f15ba592f6c7752360ff3b7ab7
```

The downloaded executable remains an ignored build input under
`vendor\cloudflared`; it is not checked into Git. Both PyInstaller packaging and
application startup verify the pinned component. A different version or hash is
a deliberate dependency update and requires code, test, and documentation review.

Build tools are never installed by the build scripts. Prepare the environment, then run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_release.ps1 `
  -Repository OWNER/REPOSITORY `
  -Clean
```

The final release assets are written to `release\artifacts`. Intermediate compiled files remain in `release\build`, which is excluded from source control.

The tagged Windows workflow builds the district host in an isolated Python 3.12
environment using its fully resolved dependency lock. Local district builds
should use the same environment. To build only that host:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_district_management_server.ps1 `
  -Python release/district-venv/Scripts/python.exe `
  -OutputRoot release/build-district `
  -Clean
```

Prepare `release/district-venv` with Python 3.12 and install
`district_management_server/requirements-lock.txt` before running that command.
The build verifies its pinned dependencies, bundles the reviewed Worker and
portal assets, and runs the compiled EXE's `--runtime-self-test`. That test starts
the real registration service on loopback with temporary credentials and a
temporary database, then checks the health endpoint and district portal assets.

To create its deterministic archive and checksum after compiling:

```powershell
python scripts/package_district_management_server.py `
  --app-dir "release/build-district/app/School CSM District Management Server" `
  --output release/artifacts
```

To compile only setup without downloading or packaging application dependencies:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_installer.ps1 `
  -Repository OWNER/REPOSITORY
```

## Release assets

Each tagged release contains:

- `School-CSM-Control-Center-Setup.exe` — stable online setup and maintenance entry point;
- `School-CSM-Control-Center-X.Y.Z-windows-x64.zip` — compiled application files only;
- `release.json` — immutable tag URLs, versions, sizes, entry point, and SHA-256 checksums;
- `SHA256SUMS.txt` — independent checksums for the package, setup program, and manifest.
- `School-CSM-District-Management-Server-vX.Y.Z-win-x64.zip` — the separate
  compiled district pilot host; and
- `School-CSM-District-Management-Server-vX.Y.Z-win-x64.zip.sha256` — its
  independent SHA-256 checksum.

The compiled ZIP contains the application runtime and the verified tunnel
component, so an operator does not need Python or a separate `cloudflared`
installation. It must not contain the mutable sidecar filename
`internet_gateway_provider.json`; release creation fails if that filename is
present. A deployment-specific school release may and, for the Motiong managed
rollout, must contain `internet_gateway_provider.default.json`. That bundled
default is public configuration only: HTTPS management origin, managed domain,
Ed25519 public signing key, and trusted proxy addresses. It must contain no
private key, Cloudflare token, connector credential, activation code, or school
data.

The district ZIP is not consumed by the school Setup program and must never be
installed on every school computer. Its package check rejects a copied runtime
database, `server.json`, SQLite sidecars, logs, top-level runtime directories,
source launch files, and redirected paths. The district database, keys, access
token, and Cloudflare token are deployment state and must never be GitHub
release assets.

`scripts/create_release.py` builds the ZIP in sorted order with normalized timestamps and refuses redirected source entries. `scripts/verify_release.py` cross-checks the central version, GitHub repository and tag URLs, asset sizes and checksums, and rejects unsafe or colliding ZIP paths, symbolic links, reparse points, source launch files, oversized archives, and missing or altered entry points.

The setup program always obtains the current manifest from:

```text
https://github.com/OWNER/REPOSITORY/releases/latest/download/release.json
```

Each manifest points its application package at an immutable tagged-release URL. This lets Repair redownload the exact installed version even after a newer release becomes latest.

## GitHub release process

1. Merge a tested version change into `main`.
2. Create and push the matching tag, for example `git tag -a v0.8.4 -m "School CSM Control Center 0.8.4"` followed by `git push origin v0.8.4`.
3. The release workflow runs the test suite, builds the compiled school app,
   maintenance setup, and separate district host, verifies their release
   assets, and publishes them to the tag.
4. Download setup from the release and test school install, update, repair,
   rollback, normal uninstall, and explicit data removal on a clean Windows
   machine. Separately extract the district ZIP on an isolated test host and
   verify that its checksum, loopback listener, runtime-state location, portal,
   and clean shutdown match the documented contract.

A manual workflow run may publish an existing matching tag.

The separate `registration-service.yml` workflow installs the service's pinned
production and test dependencies and runs its core, HTTP/WebAuthn, Cloudflare,
and shared desktop-provider contract tests. It validates the service but does not
deploy production infrastructure.

Gateway release validation must cover both desktop transfer formats. `.mossmig`
is a short-lived, destination-installation-bound live transfer package;
`.mossbak` is a portable, school-bound recovery backup for backup-assisted
replacement. Each format uses its own authenticated envelope and a newly
generated 256-bit key shown once. Neither key is stored in the package, provider
file, Credential Manager, registration service, or release asset. Tests must keep
strict format classification, inert staging, portable-data allowlists, complete
hash and record-count validation, atomic commit, and rollback behavior intact.

## Registration service and district host deployment

The public school Setup does not create Cloudflare infrastructure or host the
registration service. A deployment owner must separately configure either the
compiled Windows district pilot host or an approved hosted deployment. The free
Windows pilot depends on its designated district PC remaining powered on,
connected, and running; it is not independently hosted. A deployment owner must
provide:

- an HTTPS reverse proxy and exact WebAuthn origin/RP ID;
- a persistent infrastructure-only SQLite volume;
- an Ed25519 authorization signing key and a separate random 32-byte handoff key;
- a managed DNS zone and least-privilege Cloudflare account/zone token;
- a secret manager, monitoring, encrypted backups, and recovery procedures; and
- a reviewed non-secret provider profile bundled as
  `internet_gateway_provider.default.json` or installed on each managed Windows
  computer.

Use [`../registration_service/README.md`](../registration_service/README.md) for
the container, environment variables, activation codes, administrator passkeys,
and recovery commands. Use
[`DISTRICT_MANAGEMENT_SERVER.md`](DISTRICT_MANAGEMENT_SERVER.md) for the compiled
pilot's installation, onboarding, backup, security, update, and incident
sequence. Never add production secrets, runtime database files, or the mutable
`internet_gateway_provider.json` sidecar to GitHub release assets. The reviewed
public default profile is safe to publish under its distinct `.default.json`
filename.

## Optional Authenticode signing

For production, configure these repository secrets:

- `WINDOWS_CERTIFICATE_BASE64` — base64-encoded PFX;
- `WINDOWS_CERTIFICATE_PASSWORD` — PFX password.

The release workflow imports the certificate into the temporary runner and
passes its thumbprint to the build. The school application EXE, setup EXE, and
district-server EXE are signed and timestamped before checksums are calculated.
The hosted runner is discarded after the job.

Without these secrets, release files are checksummed but unsigned. SHA-256 proves that the package matches its manifest; it does not establish publisher identity if an attacker controls the repository or release. Authenticode and protected GitHub release permissions are therefore strongly recommended.

## Recovery and preservation contract

Updates are staged and verified before the installed directory is swapped. The previous checksummed installation package is cached under ProgramData, never in the Documents data folder. A deployment failure restores the displaced installation automatically; a successful update exposes the previous version through Roll Back.

Normal install, update, repair, rollback, and uninstall preserve:

- `Documents\MoSSLab Data\School CSM Control Center`;
- `C:\ProgramData\MoSSLab\School CSM Control Center\Configuration\internet_gateway_provider.json`;
- Windows Credential Manager target `MoSSLab.SchoolCSMControlCenter.OpenAIApiKey`;
- Windows Credential Manager target `MoSSLab.SchoolCSMControlCenter.InternetGateway.TunnelCredential`;
- Windows Credential Manager target `MoSSLab.SchoolCSMControlCenter.InternetGateway.InstallationSecret`;
- Windows Credential Manager target `MoSSLab.SchoolCSMControlCenter.InternetGateway.DevicePrivateKey`; and
- Windows Credential Manager target `MoSSLab.SchoolCSMControlCenter.InternetGateway.DeferredTransferReceipt`.

If a validated provider file is found beside the installed EXE, Setup copies it
to the durable ProgramData location before replacing or removing Program Files.
The sidecar has runtime precedence when both copies exist. Update, repair,
rollback, normal uninstall, and explicit current-account data removal retain the
ProgramData copy.

Only `--uninstall --remove-user-data` or the matching explicit UI checkbox removes
the standard data folder and all five named credentials for the current Windows
account. It does not remove the machine-wide provider configuration, data under a
different Windows account, or data in a deployment-managed custom location.
