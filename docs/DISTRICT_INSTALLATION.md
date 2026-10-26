# District Management Server Setup and updater

Download **School-CSM-District-Management-Server-Setup.exe** from the
[latest release](https://github.com/kylebriancasoy-eng/School-CSM-Control-Center/releases/latest).
This is separate from School CSM Control Center Setup. Neither requires Python.

## First installation or upgrade from an older ZIP

1. In the existing district app, create a fresh **Encrypted Recovery Backup**.
   Keep its private password and backup file safely; do not share the password.
2. Exit the district application from its notification-area menu. Stopping the
   service or closing its window alone is not enough. Leave the school app alone.
3. Run District Setup and approve its administrator prompt. If district data
   already exists, acknowledge the fresh backup before choosing **Install**.
4. Start **School CSM District Management Server** using the new desktop or Start
   menu shortcut, under the same Windows account used previously. Do not reopen
   the old ZIP copy. Existing configuration, roster, credentials and database
   are reused; do not reset tokens or delete ProgramData.
5. Open district status and confirm that the portal is available. For a school's
   queued registration, select **Send / check district approval** in the school
   app, then review the request in the district portal. Directory approval and
   gateway activation remain separate steps.

If Windows needs a different administrator account to install, close Setup and
launch the installed app from the usual operator's desktop session. Credential
Manager entries belong to that operator, not to a different administrator.

## Update, repair and uninstall

Reopen **School CSM District Management Server Setup** in the Start menu, or
download Setup again. The district app also has **Download District Setup /
updater**. Choose **Check for Update** to install the latest published version;
**Repair** restores verified files for the installed version. Both require a
fresh encrypted backup when district state exists and the district app to exit.

Setup checks HTTPS release metadata, product identity, entry point, size,
SHA-256 checksums and native application version before deployment. It refuses
to replace a running district program, including an older portable ZIP copy.
Failed file deployment attempts to restore the previous application files.

Binary-only rollback is unavailable because a newer server may migrate the
database schema. Recovery must use a matching program and encrypted backup.
**Uninstall always retains district records, configuration, recovery backups
and credentials.** It does not revoke Cloudflare resources or delete schools.
There is no district data-removal checkbox or unattended data-removal option.

## Locations and release assets

- Application: `C:\Program Files (x86)\MoSSLab\School CSM District Management Server`
- Protected Setup, cache and logs: `C:\Program Files (x86)\MoSSLab\School CSM District Management Server Maintenance`
- Existing runtime state: `%ProgramData%\MoSSLab\School CSM District Management Server`
- Secrets: the existing operator's Windows Credential Manager.

Custom runtime folders remain supported outside the two deployment folders.
Setup refuses runtime data inside its application/maintenance directories and
redirected deployment paths. It does not add an inbound firewall rule or change
the school application's firewall rules. An already-enabled current-user district
startup entry is redirected to the installed EXE; disabled startup stays disabled.

Each GitHub release contains the district ZIP, district Setup,
`district-release.json`, and `District-SHA256SUMS.txt`. The school application's
`release.json` is deliberately separate. Build with
`scripts/build_district_management_server.ps1 -Repository owner/repository`;
the script verifies the isolated build, native runtime self-test, package
contents, installer identity and release checksums before producing artifacts.
