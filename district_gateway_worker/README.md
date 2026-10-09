# District Management Gateway Worker

This Worker is the public HTTPS edge for the separate District Management
Server. It is deliberately separate from every numeric school Worker.

Required bindings:

- `PUBLIC_HOST`: the exact public hostname, for example
  `management.motiong-district-csm-survey.workers.dev`.
- `DISTRICT_MANAGEMENT_ORIGIN`: a Workers VPC Service targeting the district
  server's loopback port through its dedicated named Cloudflare Tunnel.

The Worker accepts only `GET`, `HEAD`, `POST`, and `OPTIONS`, replaces all
browser-supplied proxy identity headers, and fails closed when either binding is
missing. The District Management Server must listen only on loopback.

Run the contract tests with `node --test district_gateway_worker/test/proxy.test.mjs`.
