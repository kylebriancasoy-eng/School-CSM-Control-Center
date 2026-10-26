# Link an existing direct Cloudflare pilot to district monitoring

The original single-school pilot can run a working tunnel without being a managed
district installation. In older versions it never sent district check-ins, so the
directory showed **Awaiting setup** despite the connected school-side tunnel.

After updating both district and school applications:

1. In the district portal, select the approved school and issue an activation code.
2. In the school app, open **Server Dashboard → Internet Gateway → Connection →
   Link district monitoring**. Enter the district-issued activation code privately.
   Never enter a Cloudflare token here or send the code in chat.
3. Refresh the district portal after the first status report. Reports continue on
   connection changes and the existing five-minute authorization-check cadence.

This is **monitoring only**. It preserves the current public hostname, tunnel,
connector credentials, survey records and existing local gateway registration.
No new tunnel, managed installation, passkey or transfer authority is created.
The district detail view labels the school as a linked existing pilot.

The link requires an approved directory entry and a school-bound, unused,
unexpired activation code. A separate random reporting secret is stored in the
school operator's Windows Credential Manager; the district stores only its hash.
Reports carry operational state and app version, never responses or personnel.
A linked pilot cannot use normal initial registration to provision a replacement
tunnel. Moving it to fully managed operation needs a deliberate migration.

**Reset monitoring link** in the authenticated district school detail revokes only
reporting and invalidates outstanding activation codes. It does not stop or
reconfigure the tunnel. Issue a fresh code to relink; this rotates the reporting
secret. Suspending the school also blocks monitoring, not its independent tunnel.
No other school's status is changed.

The district database migrates additively to schema 4. Create a fresh encrypted
district recovery backup before updating. Old district binaries reject the newer
schema; do not perform a binary-only downgrade.
