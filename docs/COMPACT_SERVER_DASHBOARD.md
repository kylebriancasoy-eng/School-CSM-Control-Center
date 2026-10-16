# Compact Server Dashboard (0.8.4)

The navy-and-teal visual theme is retained with tighter spacing, smaller status
metrics, and readable icon-and-text actions. Status, Start server, Stop server,
and Save settings stay above the scrolling sections.

- **Survey settings:** questionnaire, survey date/status, port, access address,
  session duration, public message, and the Windows background-startup switch.
- **Internet Gateway → Connection:** registration, device authorization,
  connectivity, and Connect / reconnect, Check connection, Setup, and Details.
- **Internet Gateway → Public QR links:** survey/scanner links and QR export.
- **Internet Gateway → Registration and backup:** district request status,
  request retry, encrypted recovery backup, transfer package, and server transfer.
- **Wi-Fi and QR:** hotspot/network settings, respondent access QRs, access-key
  renewal, and the printable access sheet. QRs reflow to fewer columns in smaller
  windows; addresses are unchanged.
- **Scanner accounts:** scanner intake/processing controls and operator accounts.

Switching sections does not save edited values, start a server, change network
settings, or connect a gateway. Use Save settings to apply the existing form.
Scanner accounts still use their separate Save account action. Background startup
retains its existing safe synchronization and user-confirmed setup behavior.

Initial managed activation remains in School Information, as introduced in 0.8.3.
No authorization, server-transfer, data-retention, or backup rules are changed.
Existing district services on 0.8.2 or newer support the redesigned school UI;
there is no district database migration in this update.

Regression coverage includes compact-dashboard rendering at 780×570 and
1280×720, persistent action visibility, unsaved field preservation, unchanged
background switch signals, and gateway authorization/retirement restrictions.
