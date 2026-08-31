# Install and maintain School CSM Control Center

School CSM Control Center 0.6.0 is distributed as one setup program. Operators
run only the compiled Setup EXE and application EXE. Python does not need to be
installed, and operators do not run command files or source files.

## Install

1. Open the [latest public release](https://github.com/kylebriancasoy-eng/School-CSM-Control-Center/releases/latest).
2. Download [`School-CSM-Control-Center-Setup.exe`](https://github.com/kylebriancasoy-eng/School-CSM-Control-Center/releases/latest/download/School-CSM-Control-Center-Setup.exe).
3. Open the setup file and approve the Windows administrator prompt.
4. Select **Install**.
5. When setup finishes, open **School CSM Control Center** from the desktop or Start menu shortcut.

The setup program downloads the application package over HTTPS with a strict size limit, checks its published size and SHA-256 checksum, rejects unsafe paths, links, reparse points, and Windows-ambiguous names, extracts it in a staging area, checks the compiled application again, and only then replaces the installed copy. Temporary connection closures are retried up to five times. An interrupted package download continues from its partial byte position when GitHub supports it, while all size and checksum checks remain mandatory.

An internet connection is needed for install, update, and repair. Survey
collection, scanning, analysis, printing, and narrative reports remain local and
can run without internet. The optional Internet Gateway also falls back to local
operation when its provider or tunnel is unavailable.

The online setup uses anonymous HTTPS downloads, so its GitHub release assets must be publicly downloadable. It never asks an operator for GitHub credentials.

## Locations and data safety

| Purpose | Location | Removed by a normal uninstall? |
|---|---|---:|
| Compiled application | `C:\Program Files (x86)\MoSSLab\School CSM Control Center` | Yes |
| Survey records, settings, exports, logs, and backups | `Documents\MoSSLab Data\School CSM Control Center` | No |
| Maintenance staging and rollback cache | `C:\ProgramData\MoSSLab\School CSM Control Center\Maintenance` | Yes; the small setup program and diagnostic log may remain when setup uninstalls itself |
| Deployment-owned Internet Gateway provider configuration | `C:\ProgramData\MoSSLab\School CSM Control Center\Configuration\internet_gateway_provider.json` | No |
| Optional OpenAI API key | Windows Credential Manager target `MoSSLab.SchoolCSMControlCenter.OpenAIApiKey` | No |
| Internet Gateway tunnel credential | Windows Credential Manager target `MoSSLab.SchoolCSMControlCenter.InternetGateway.TunnelCredential` | No |
| Internet Gateway installation secret | Windows Credential Manager target `MoSSLab.SchoolCSMControlCenter.InternetGateway.InstallationSecret` | No |
| Internet Gateway device private key | Windows Credential Manager target `MoSSLab.SchoolCSMControlCenter.InternetGateway.DevicePrivateKey` | No |

Install, update, repair, and rollback never intentionally modify the Documents data folder. Before replacing an older installation, setup also copies any legacy `data`, `exports`, `logs`, or `backups` folders found beside the old program into the stable Documents location without overwriting different files. Conflicts are retained under `migration\installer-conflicts`.

Setup also preserves a valid administrator-installed
`internet_gateway_provider.json` across install, update, repair, rollback, and
uninstall. If an older deployment placed the file beside the application EXE,
Setup copies the exact validated file into the durable ProgramData location
before changing Program Files. A sidecar file beside the EXE has deliberate
runtime precedence when both locations exist. The public GitHub release does not
contain a production provider file.

The current PySide6 runtime is 64-bit and requires 64-bit Windows 10 or Windows 11. The application is placed in the requested `Program Files (x86)` organizational path even though its bundled Python runtime is 64-bit.

## Optional Internet Gateway

The application starts in **Local Only** mode. Local Survey and Scanner access
does not require Internet Gateway registration. Internet Gateway setup offers
two routes described in [`INTERNET_GATEWAY.md`](INTERNET_GATEWAY.md):

- **Single-school Cloudflare pilot** uses a free `workers.dev` address, one
  narrow Workers VPC Service, and one named Tunnel. It needs no custom domain or
  central registration service. Workers VPC is currently beta and the route is
  intended as a monitored school pilot.
- **Managed multi-school provider** uses a deployment-owned domain, registration
  service, signed device authorization, and server-transfer controls. A
  deployment administrator must install its non-secret provider configuration;
  none of its production infrastructure or secrets is supplied by the public
  repository.

For the single-school pilot, set the preferred port to `8080`, create the Tunnel,
VPC Service, and School-ID Worker in Cloudflare, then enter the Worker hostname,
Tunnel ID, and connector token in the app's setup overlay. The token is saved
only in Windows Credential Manager. For managed mode, first registration uses an
administrator-issued one-time activation code and a passkey created in the
provider's HTTPS page. Its one-time completion code returns the device
credentials to the Control Center. In both modes, start the local Survey Server;
the outbound tunnel starts only after local health and the mode's authorization
check succeed.

For replacement computers, use one of these encrypted formats:

- `.mossmig` is a live-device **Server and Data** package bound to the destination
  installation ID. Create it on the active source computer while the Survey
  Server is stopped.
- `.mossbak` is a portable recovery backup used by **Server and Data from
  backup**. It is not destination-bound and is intended for backup-assisted
  recovery when the original server cannot perform a live transfer.

Both workflows generate a fresh 256-bit key that is shown only once and is not
saved by the Control Center. Record the key securely and deliver it through a
different trusted channel from the encrypted file. The destination validates
and commits the restored data before opening the passkey-authorized server
cutover. Do not rename another backup or ZIP file to either extension.

## Background server and notification-area controls

Open **Survey Server and Respondent Access** and enable **Start with Windows and
run Survey Server in background** to start the compiled Control Center at Windows
sign-in, keep it in the notification area, and start the saved Survey Server
automatically. This preference is off by default and applies only to the current
Windows account.

While enabled, closing the main window hides it without stopping the server. Use
the notification-area icon to open the Control Center, see server status, start or
stop the server, open the verified Survey Form address, change the startup
preference, or choose **Exit** to stop the server and close the application.

Setup configures an app-specific inbound Windows Firewall rule during its normal
administrator-approved install or update. The rule is limited to the installed
EXE, the Private profile, and the local subnet. Automatic background startup does
not bypass Windows security or silently elevate the application. If an
administrator or security policy removes the rule, use Setup's **Repair** action
before accepting respondent connections.

## Update, repair, and rollback

Open **Settings > Apps > Installed apps**, choose School CSM Control Center, and select **Modify**, or reopen the setup file.

- **Check for Update** downloads and installs the latest GitHub release.
- **Repair** redownloads the exact release recorded for the current installation and restores its compiled files. Saved data is untouched.
- **Roll Back** restores the previous verified application copy when one is available. The replaced version becomes the next rollback copy.
- **Uninstall** removes the compiled application, shortcuts, Windows startup and
  firewall entries, registration, and maintenance cache while preserving saved
  data, credentials, and the provider configuration.

If an invalid provider configuration is found beside the installed EXE, Setup
moves it to the ProgramData Configuration quarantine folder before uninstalling.
If that preservation step fails, uninstall stops before deleting application
files. An invalid configuration already in ProgramData remains outside the
application-removal boundary and is left for the deployment administrator.

The application must be closed before any maintenance operation.

## Remove saved data explicitly

Saved data is never selected for removal automatically. To remove it for the Windows account running setup:

1. Open setup and select the checkbox **When uninstalling, also remove my saved
   records and application credentials**.
2. Select **Uninstall**.
3. Read the warning and confirm.

This deletes only the standard Documents data folder for the current Windows
account and these four exact Credential Manager targets:

- `MoSSLab.SchoolCSMControlCenter.OpenAIApiKey`
- `MoSSLab.SchoolCSMControlCenter.InternetGateway.TunnelCredential`
- `MoSSLab.SchoolCSMControlCenter.InternetGateway.InstallationSecret`
- `MoSSLab.SchoolCSMControlCenter.InternetGateway.DevicePrivateKey`

Data stored in a managed custom location or under another Windows account is
left in place and must be handled by its owner or administrator. The deployment
provider configuration in ProgramData is machine-wide and is retained even by
explicit current-account data removal; only a deployment administrator should
replace or remove it.

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

- If setup says it is not connected to a repository, the file was a development compile check. Download setup from the [public releases page](https://github.com/kylebriancasoy-eng/School-CSM-Control-Center/releases).
- If setup says the application is running, close School CSM Control Center and retry.
- If all five GitHub download attempts fail, confirm that the internet connection, proxy, and security software permit `github.com`, then reopen the latest Setup and select **Install** again.
- If checksum verification fails, do not bypass it. Download setup again and confirm that the GitHub release is complete.
- If Internet Gateway remains **Local Only**, ask the deployment administrator
  to validate the ProgramData provider file. Do not copy secrets into it or use
  `internet_gateway_provider.example.json` as a production file.
- If the tunnel component fails its integrity check, use Setup's **Repair**
  action. Do not download or substitute a different `cloudflared.exe` manually.
- Maintenance details are recorded in `C:\ProgramData\MoSSLab\School CSM Control Center\Maintenance\maintenance.log`.
- Application startup details are recorded in `Documents\MoSSLab Data\School CSM Control Center\logs\control-center.log`.

Unsigned development builds may trigger Windows SmartScreen. Production releases should be Authenticode-signed by MoSSLab; the checksum protects package integrity but does not replace publisher signing.
