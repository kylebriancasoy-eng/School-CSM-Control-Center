import assert from "node:assert/strict";
import test from "node:test";

import {
  ALLOWED_METHODS,
  configuredPublicHost,
  proxyRequest,
  requestHeadersForOrigin,
} from "../src/index.js";

const PUBLIC_HOST = "management.motiong-district-csm-survey.workers.dev";

test("accepts the exact configured district workers.dev hostname", () => {
  assert.equal(configuredPublicHost({ PUBLIC_HOST }), PUBLIC_HOST);
  assert.throws(() => configuredPublicHost({ PUBLIC_HOST: "example.com" }));
  assert.throws(() => configuredPublicHost({ PUBLIC_HOST: "*.workers.dev" }));
});

test("replaces browser-supplied forwarding identity", () => {
  const request = new Request(`https://${PUBLIC_HOST}/district`, {
    headers: {
      "x-forwarded-for": "attacker.invalid",
      "x-forwarded-host": "attacker.invalid",
      "x-forwarded-proto": "http",
    },
  });
  const headers = requestHeadersForOrigin(request, PUBLIC_HOST, "203.0.113.10");
  assert.equal(headers.get("x-forwarded-for"), "203.0.113.10");
  assert.equal(headers.get("x-forwarded-host"), PUBLIC_HOST);
  assert.equal(headers.get("x-forwarded-proto"), "https");
});

test("proxies only exact HTTPS host through the fixed VPC binding", async () => {
  let received;
  const env = {
    PUBLIC_HOST,
    DISTRICT_MANAGEMENT_ORIGIN: {
      async fetch(request) {
        received = request;
        return new Response("ok", { status: 200 });
      },
    },
  };
  const response = await proxyRequest(
    new Request(`https://${PUBLIC_HOST}/district?view=schools`, {
      headers: { "cf-connecting-ip": "203.0.113.10" },
    }),
    env,
  );
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "ok");
  assert.equal(received.url, "http://127.0.0.1:8090/district?view=schools");

  const wrongHost = await proxyRequest(
    new Request("https://other.motiong-district-csm-survey.workers.dev/district", {
      headers: { "cf-connecting-ip": "203.0.113.10" },
    }),
    env,
  );
  assert.equal(wrongHost.status, 421);
});

test("forwards PATCH and DELETE requests required by the district portal", async () => {
  const received = [];
  const env = {
    PUBLIC_HOST,
    DISTRICT_MANAGEMENT_ORIGIN: {
      async fetch(request) {
        received.push({
          method: request.method,
          url: request.url,
          body: await request.text(),
        });
        return new Response(null, { status: 204 });
      },
    },
  };

  const patchResponse = await proxyRequest(
    new Request(`https://${PUBLIC_HOST}/api/schools/123627`, {
      method: "PATCH",
      headers: {
        "cf-connecting-ip": "203.0.113.10",
        "content-type": "application/json",
      },
      body: JSON.stringify({ status: "suspended" }),
      duplex: "half",
    }),
    env,
  );
  const deleteResponse = await proxyRequest(
    new Request(`https://${PUBLIC_HOST}/api/schools/123627`, {
      method: "DELETE",
      headers: { "cf-connecting-ip": "203.0.113.10" },
    }),
    env,
  );

  assert.equal(patchResponse.status, 204);
  assert.equal(deleteResponse.status, 204);
  assert.deepEqual(received, [
    {
      method: "PATCH",
      url: "http://127.0.0.1:8090/api/schools/123627",
      body: '{"status":"suspended"}',
    },
    {
      method: "DELETE",
      url: "http://127.0.0.1:8090/api/schools/123627",
      body: "",
    },
  ]);
});

test("rejects methods outside the explicit portal allowlist", async () => {
  let originCalls = 0;
  const env = {
    PUBLIC_HOST,
    DISTRICT_MANAGEMENT_ORIGIN: {
      async fetch() {
        originCalls += 1;
        return new Response("unexpected");
      },
    },
  };

  for (const method of ["PUT", "PROPFIND"]) {
    const response = await proxyRequest(
      new Request(`https://${PUBLIC_HOST}/district`, {
        method,
        headers: { "cf-connecting-ip": "203.0.113.10" },
      }),
      env,
    );
    assert.equal(response.status, 405);
    assert.equal(response.headers.get("allow"), ALLOWED_METHODS.join(", "));
  }
  assert.equal(originCalls, 0);
});

test("fails closed when the VPC binding or Cloudflare client address is absent", async () => {
  const missingIp = await proxyRequest(
    new Request(`https://${PUBLIC_HOST}/district`),
    { PUBLIC_HOST },
  );
  assert.equal(missingIp.status, 400);

  const missingBinding = await proxyRequest(
    new Request(`https://${PUBLIC_HOST}/district`, {
      headers: { "cf-connecting-ip": "2001:db8::1" },
    }),
    { PUBLIC_HOST },
  );
  assert.equal(missingBinding.status, 503);
});
