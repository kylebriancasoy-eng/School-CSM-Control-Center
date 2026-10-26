# In-app update notifications and bundled Setup

The School Control Center's title bar contains a **Software updates** bell.
An availability dot appears when a newer stable GitHub release is found. Click
the bell to see the installed version, release details, **Check now**, and
**Update…** / **Open Setup**. Release details are displayed as plain text, not
executable HTML or browser content.

Compiled school apps check about 30 seconds after startup and every six hours
while running. Checks happen off the interface thread with a bounded response
size and connection timeout. No survey data, credentials or API keys are sent.
No sign-in is required. An unavailable Internet connection or GitHub rate limit
does not block local operation. Source-development runs do not check automatically.

Updating is always explicit:

1. Save unfinished work and create a backup.
2. Choose **Update…**, confirm opening Setup and closing the app, then approve
   the ordinary Windows permission prompt.
3. In the wizard choose **Check for Update**. The app closes normally, stopping
   its local server and gateway so Setup can replace files. If Setup cannot open
   or its permission prompt is canceled, the app remains open.
4. Start the app again from its normal shortcut after Setup completes.

Both school and district release packages include their own matching Setup EXE.
The district app's **Open District Setup / updater** action opens that local
update panel, as does its pinned bell at the top-right of the district header.
The panel matches the district seal's colors, shows release details, and requires
a fresh encrypted recovery-backup acknowledgement before launching District Setup.
An ongoing backup, restore or credential operation also blocks the launch.
The district window retains its native Windows title bar; its bell remains visible
below that title bar while settings scroll. The school bell uses its custom title bar.

Both products check on the same startup/six-hour cadence and can be checked
manually. District checks require the district Setup and district manifest assets;
they never select the school installer. Only opening Setup and confirming closure
stops the district service. Reading update details does not stop any service.
Backup acknowledgement resets when the district panel is reopened.

Setup is copied to a unique temporary folder and copy-verified before launch, so
it is not running inside the program folder it will replace. Build-time release
checks enforce that the bundled wizard matches the separately published installer.
The app never runs an executable selected by release notes or arbitrary remote
URLs. The wizard independently verifies its product-bound manifest, package size
and checksums before installation. Normal maintenance preserves saved data.

Successful staging folders are retained for the current wizard run; Windows or
the operator may clear them as ordinary temporary files after Setup exits.
Do not delete them while the wizard is running.

Older installations need one normal update through their existing installer to
receive the bell and bundled Setup. No application is silently self-updated.
