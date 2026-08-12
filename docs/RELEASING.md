# Build and release guide

## Repository layout

- `school_csm_control_center/` contains the application package.
- `assets/` contains installed application and scanner resources.
- `tests/` contains regression tests.
- `packaging/pyinstaller/` defines the compiled application bundle.
- `packaging/installer/` contains the dependency-free .NET Framework online maintenance program.
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

`school_csm_control_center/version.py` is the single source of truth:

- application semantic version: `0.4.1`;
- Windows executable version: `0.4.1.0`;
- release tag: `v0.4.1`.

After changing the version, regenerate and check the PyInstaller version resource:

```powershell
python scripts/generate_version_info.py
```

The release workflow refuses a tag that does not exactly match `RELEASE_TAG`.

## Local build prerequisites

- 64-bit Python 3.11 through 3.14;
- dependencies in `requirements-lock.txt`;
- PyInstaller 6.x;
- the Windows .NET Framework 4.x C# compiler at `%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe`;
- optional Windows SDK `signtool.exe` and a code-signing certificate.

Build tools are never installed by the build scripts. Prepare the environment, then run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_release.ps1 `
  -Repository OWNER/REPOSITORY `
  -Clean
```

The final release assets are written to `release\artifacts`. Intermediate compiled files remain in `release\build`, which is excluded from source control.

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

`scripts/create_release.py` builds the ZIP in sorted order with normalized timestamps and refuses redirected source entries. `scripts/verify_release.py` cross-checks the central version, GitHub repository and tag URLs, asset sizes and checksums, and rejects unsafe or colliding ZIP paths, symbolic links, reparse points, source launch files, oversized archives, and missing or altered entry points.

The setup program always obtains the current manifest from:

```text
https://github.com/OWNER/REPOSITORY/releases/latest/download/release.json
```

Each manifest points its application package at an immutable tagged-release URL. This lets Repair redownload the exact installed version even after a newer release becomes latest.

## GitHub release process

1. Merge a tested version change into `main`.
2. Create and push the matching tag, for example `git tag -a v0.4.1 -m "School CSM Control Center 0.4.1"` followed by `git push origin v0.4.1`.
3. The release workflow runs the test suite, builds the compiled app and maintenance setup, verifies all release assets, and publishes them to the tag.
4. Download setup from the release and test install, update, repair, rollback, normal uninstall, and explicit data removal on a clean Windows machine.

A manual workflow run may publish an existing matching tag.

## Optional Authenticode signing

For production, configure these repository secrets:

- `WINDOWS_CERTIFICATE_BASE64` — base64-encoded PFX;
- `WINDOWS_CERTIFICATE_PASSWORD` — PFX password.

The release workflow imports the certificate into the temporary runner and passes its thumbprint to the build. Both the application EXE and setup EXE are signed and timestamped before checksums are calculated. The hosted runner is discarded after the job.

Without these secrets, release files are checksummed but unsigned. SHA-256 proves that the package matches its manifest; it does not establish publisher identity if an attacker controls the repository or release. Authenticode and protected GitHub release permissions are therefore strongly recommended.

## Recovery and preservation contract

Updates are staged and verified before the installed directory is swapped. The previous checksummed installation package is cached under ProgramData, never in the Documents data folder. A deployment failure restores the displaced installation automatically; a successful update exposes the previous version through Roll Back.

Normal install, update, repair, rollback, and uninstall preserve:

- `Documents\MoSSLab Data\School CSM Control Center`;
- Windows Credential Manager target `MoSSLab.SchoolCSMControlCenter.OpenAIApiKey`.

Only `--uninstall --remove-user-data` or the matching explicit UI checkbox removes the standard data folder and credential for the current Windows account.
