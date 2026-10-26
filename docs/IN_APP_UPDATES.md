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
wizard after confirmation; create a fresh encrypted district backup first.
The district window retains its native Windows title bar; the new notification
bell is in the School Control Center's custom title bar.

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
