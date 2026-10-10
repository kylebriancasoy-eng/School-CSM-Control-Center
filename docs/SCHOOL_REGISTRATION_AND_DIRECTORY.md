# School Registration and District Schools Directory

## School operator

1. Complete all three Mandatory School Registration sections. The profile,
   personnel, contact details, and school seal are saved locally as before.
2. With a configured district provider, the Control Center automatically queues
   the School ID, name, district, and division for district review. The request
   also has a device identifier and a private review receipt; neither is gateway
   authorization. Without Internet, local registration is still complete.
3. In **Server Dashboard → Internet Gateway → District school registration**,
   see **Queued locally**, **Pending approval**, **Approved**, or **Not approved**.
   The clearly labeled **Send / check district request** button retries manually.
   Automatic retries happen every five minutes while the app is open and on
   restart. Existing complete profiles are queued on their first updated launch.
4. After approval, obtain the one-time activation code from the district operator
   and use Internet Gateway setup to enroll the school server and its passkey.
   Approval alone does not make a survey or scanner available over the Internet.

If rejected, contact the district administrator and correct the local profile.
Saving corrected basic identity or clicking **Submit corrected request** creates
a fresh request. Do not repeatedly resubmit unchanged requests without resolving
the reason for rejection. Changes after approval do not automatically rewrite the
official roster; ask the district administrator to verify those changes.

The queue and protected receipt are device-specific and excluded from portable
school backups and server-data transfers. Normal update/repair/uninstall preserve
them; explicit saved-data removal deletes the named receipt credential as well.
Unreadable queue files are preserved and reported, not silently overwritten.

## District operator

1. Open **Schools → Registration requests**. Search by school name or School ID;
   use the separate review-status filter to see pending or reviewed requests.
2. Verify the identity and requester through an approved district channel.
   Submitted names and device identifiers are untrusted, not proof of ownership.
3. Choose **Review & approve** or **Reject**, review the confirmation, and save.
   If the school changed its submitted identity while the confirmation was open,
   saving is refused. Close the confirmation, refresh, and verify it again.
4. Approval creates a missing official directory entry. If an entry already
   exists, all basic identity fields must match; it is never overwritten.
   Suspended schools cannot be approved through this workflow. Reject conflicting
   submissions and ask the school to correct them. Existing enrolled servers,
   routes, data, and credentials are untouched by either decision.
5. Issue an activation code separately for a verified school awaiting its first
   gateway enrollment. The existing code/passkey/one-active-server checks remain.

Pending requests appear separately from the official roster. A request's approval
does not automatically approve other requests claiming the same School ID.
Listings are bounded to 1,000 entries, with pending requests first so reviewed
history cannot hide an older pending submission. The portal shows when reviewed
history is truncated; audit records remain available through the activity log.

## Deployment and safeguards

Update both applications; deploy the district server first. Back up the district
server before starting its updated binary. Schema 3 adds the request table without
removing old records. An older district server cannot open schema 3; restore a
matching pre-update encrypted backup only as a deliberate recovery operation.
Existing Calapi pilot-to-managed migration remains separate and fail-closed.

An older/unreachable district service leaves school synchronization unconfirmed,
with a visible retry message; it never blocks local surveys or reports. A provider
change cannot send an old receipt to the new provider. Receipt secrets stay in
Windows Credential Manager; the server stores only their SHA-256 digest. Public
submissions cannot retrieve the roster, activations, or credentials. They are
limited to 8 KiB, strict identity fields, a bounded queue, and request-rate limits.
Listing and review require a district administrator session; review additionally
requires CSRF verification. Audit events omit the receipt secret.

## API and verification

- `POST /v1/directory/registration-requests`: submit/check the same private
  receipt. Returns only its request ID, School ID, and review status.
- `GET /v1/admin/registration-requests?status=pending|all|approved|rejected`:
  authenticated review listing with identity revisions, status counts, and the
  listing limit; secrets are omitted.
- `POST /v1/admin/registration-requests/{request_id}/review`:
  authenticated/CSRF-protected `{ "decision": "approve" | "reject",
  "expected_revision": "<identity revision from listing>" }`. A stale identity
  revision returns HTTP 409 without changing the roster or review status.

Regression coverage: `tests/test_school_registration_requests.py`,
`tests/test_directory_registration_client.py`, `tests/test_school_registration.py`,
and `registration_service/test/district.test.mjs`. Tests use synthetic identities,
fake credentials/provisioners, and temporary databases; never a production roster.
