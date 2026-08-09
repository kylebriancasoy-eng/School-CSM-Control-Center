# Install and maintain School CSM Control Center

School CSM Control Center is distributed as one setup program. Operators do not need Python and do not run command files or source files.

## Install

1. Open the repository's **Releases** page.
2. Download `School-CSM-Control-Center-Setup.exe` from the latest release.
3. Open the setup file and approve the Windows administrator prompt.
4. Select **Install**.
5. When setup finishes, open **School CSM Control Center** from the desktop or Start menu shortcut.

The setup program downloads the application package over HTTPS with a strict size limit, checks its published size and SHA-256 checksum, rejects unsafe paths, links, reparse points, and Windows-ambiguous names, extracts it in a staging area, checks the compiled application again, and only then replaces the installed copy.

An internet connection is needed for install, update, and repair. Normal survey collection and analysis remain local and can run without internet.

The online setup uses anonymous HTTPS downloads, so its GitHub release assets must be publicly downloadable. It never asks an operator for GitHub credentials.

## Locations and data safety

| Purpose | Location | Removed by a normal uninstall? |
|---|---|---:|
| Compiled application | `C:\Program Files (x86)\MoSSLab\School CSM Control Center` | Yes |
| Survey records, settings, exports, logs, and backups | `Documents\MoSSLab Data\School CSM Control Center` | No |
| Maintenance staging and rollback cache | `C:\ProgramData\MoSSLab\School CSM Control Center\Maintenance` | Yes; the small setup program and diagnostic log may remain when setup uninstalls itself |
| Optional OpenAI API key | Windows Credential Manager target `MoSSLab.SchoolCSMControlCenter.OpenAIApiKey` | No |

Install, update, repair, and rollback never intentionally modify the Documents data folder. Before replacing an older installation, setup also copies any legacy `data`, `exports`, `logs`, or `backups` folders found beside the old program into the stable Documents location without overwriting different files. Conflicts are retained under `migration\installer-conflicts`.

The current PySide6 runtime is 64-bit and requires 64-bit Windows 10 or Windows 11. The application is placed in the requested `Program Files (x86)` organizational path even though its bundled Python runtime is 64-bit.

## Update, repair, and rollback

Open **Settings > Apps > Installed apps**, choose School CSM Control Center, and select **Modify**, or reopen the setup file.

- **Check for Update** downloads and installs the latest GitHub release.
- **Repair** redownloads the exact release recorded for the current installation and restores its compiled files. Saved data is untouched.
- **Roll Back** restores the previous verified application copy when one is available. The replaced version becomes the next rollback copy.
- **Uninstall** removes the compiled application, shortcuts, registration, and maintenance cache while preserving saved data and credentials.

The application must be closed before any maintenance operation.

## Remove saved data explicitly

Saved data is never selected for removal automatically. To remove it for the Windows account running setup:

1. Open setup and select the checkbox **When uninstalling, also remove my saved records and OpenAI API key**.
2. Select **Uninstall**.
3. Read the warning and confirm.

This deletes only the standard Documents data folder for the current Windows account and the named Credential Manager entry. Data stored in a managed custom location or under another Windows account is left in place and must be handled by its owner or administrator.

## Command-line maintenance

The setup program also supports unattended administration from an elevated prompt:

```powershell
School-CSM-Control-Center-Setup.exe --install --quiet
School-CSM-Control-Center-Setup.exe --update --quiet
School-CSM-Control-Center-Setup.exe --repair --quiet
School-CSM-Control-Center-Setup.exe --rollback --quiet
School-CSM-Control-Center-Setup.exe --uninstall --quiet
School-CSM-Control-Center-Setup.exe --uninstall --remove-user-data --quiet
```

Exit code `0` means success, `2` means invalid or unconfigured setup, `3` means the operation failed, and `4` means another maintenance operation is already running.

## Troubleshooting

- If setup says it is not connected to a repository, the file was a development compile check. Download the setup file from an actual GitHub release.
- If setup says the application is running, close School CSM Control Center and retry.
- If checksum verification fails, do not bypass it. Download setup again and confirm that the GitHub release is complete.
- Maintenance details are recorded in `C:\ProgramData\MoSSLab\School CSM Control Center\Maintenance\maintenance.log`.
- Application startup details are recorded in `Documents\MoSSLab Data\School CSM Control Center\logs\control-center.log`.

Unsigned development builds may trigger Windows SmartScreen. Production releases should be Authenticode-signed by MoSSLab; the checksum protects package integrity but does not replace publisher signing.
