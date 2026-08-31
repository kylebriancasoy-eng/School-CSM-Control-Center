import assert from "node:assert/strict";
import test from "node:test";

import worker, { proxyRequest } from "../src/index.js";


const PUBLIC_HOST = "123456.example-account.workers.dev";

function environment(fetchImplementation) {
  return {
    PUBLIC_HOST,
    SCHOOL_CSM_ORIGIN: {
      fetch: fetchImplementation,
    },
  };
}

function publicRequest(path = "/", init = {}) {
  const headers = new Headers(init.headers);
  if (!headers.has("cf-connecting-ip")) {
    headers.set("cf-connecting-ip", "203.0.113.24");
  }
  return new Request(`https://${PUBLIC_HOST}${path}`, { ...init, headers });
}

test("default Worker export delegates to the proxy handler", async () => {
  const response = await worker.fetch(
    publicRequest("/healthz"),
    environment(async () => new Response("healthy")),
  );
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "healthy");
});

test("rejects methods outside the explicit survey and scanner allowlist", async () => {
  let called = false;
  const response = await proxyRequest(
    publicRequest("/api/example", { method: "PUT" }),
    environment(async () => {
      called = true;
      return new Response("unexpected");
    }),
  );
  assert.equal(response.status, 405);
  assert.equal(response.headers.get("allow"), "GET, HEAD, POST, OPTIONS");
  assert.equal(called, false);
});

test("pins the public hostname and HTTPS transport", async () => {
  const env = environment(async () => new Response("unexpected"));

  const wrongHost = new Request("https://999999.example-account.workers.dev/", {
    headers: { "cf-connecting-ip": "203.0.113.24" },
  });
  assert.equal((await proxyRequest(wrongHost, env)).status, 421);

  const plaintext = new Request(`http://${PUBLIC_HOST}/`, {
    headers: { "cf-connecting-ip": "203.0.113.24" },
  });
  assert.equal((await proxyRequest(plaintext, env)).status, 400);
});

test("sanitizes forwarding and hop-by-hop headers while preserving origin and cookies", async () => {
  let captured;
  const request = publicRequest("/api/example?mode=test", {
    method: "POST",
    headers: {
      "cf-connecting-ip": "2001:db8::24",
      connection: "keep-alive, x-remove-me",
      cookie: "school_csm_session=opaque",
      forwarded: "for=attacker;proto=http",
      origin: `https://${PUBLIC_HOST}`,
      "proxy-authorization": "Basic must-not-pass",
      "x-forwarded-for": "198.51.100.99, 192.0.2.9",
      "x-forwarded-host": "attacker.example",
      "x-forwarded-proto": "http",
      "x-remove-me": "must-not-pass",
    },
    body: "payload=kept",
  });
  const response = await proxyRequest(
    request,
    environment(async (originRequest) => {
      captured = originRequest;
      return new Response("accepted", {
        headers: {
          connection: "x-origin-remove",
          "set-cookie": "school_csm_session=new; Secure; HttpOnly; SameSite=Lax",
          "x-origin-remove": "must-not-pass",
        },
      });
    }),
  );

  assert.equal(response.status, 200);
  assert.equal(await response.text(), "accepted");
  assert.equal(captured.url, "http://127.0.0.1:8080/api/example?mode=test");
  assert.equal(captured.headers.get("origin"), `https://${PUBLIC_HOST}`);
  assert.equal(captured.headers.get("cookie"), "school_csm_session=opaque");
  assert.equal(captured.headers.get("cf-connecting-ip"), "2001:db8::24");
  assert.equal(captured.headers.get("x-forwarded-for"), "2001:db8::24");
  assert.equal(captured.headers.get("x-forwarded-host"), PUBLIC_HOST);
  assert.equal(captured.headers.get("x-forwarded-proto"), "https");
  assert.equal(captured.headers.get("forwarded"), null);
  assert.equal(captured.headers.get("proxy-authorization"), null);
  assert.equal(captured.headers.get("connection"), null);
  assert.equal(captured.headers.get("x-remove-me"), null);
  assert.equal(await captured.text(), "payload=kept");
  assert.match(response.headers.get("set-cookie"), /school_csm_session=new/);
  assert.equal(response.headers.get("connection"), null);
  assert.equal(response.headers.get("x-origin-remove"), null);
});

test("fails closed when the edge client address is absent or invalid", async () => {
  const env = environment(async () => new Response("unexpected"));
  const missing = new Request(`https://${PUBLIC_HOST}/`);
  assert.equal((await proxyRequest(missing, env)).status, 400);

  const ambiguous = new Request(`https://${PUBLIC_HOST}/`, {
    headers: { "cf-connecting-ip": "203.0.113.1, 198.51.100.2" },
  });
  assert.equal((await proxyRequest(ambiguous, env)).status, 400);
});

test("returns a generic no-store 503 when the private service is unavailable", async () => {
  const response = await proxyRequest(
    publicRequest("/survey"),
    environment(async () => {
      throw new Error("secret tunnel diagnostic");
    }),
  );
  assert.equal(response.status, 503);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(response.headers.get("retry-after"), "15");
  assert.equal((await response.text()).includes("secret tunnel diagnostic"), false);
});
