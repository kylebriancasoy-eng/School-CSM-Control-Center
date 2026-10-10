import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../static/district.js", import.meta.url), "utf8");

function harness() {
  const nodes = new Map();
  let generatedNode = 0;
  function node(id) {
    if (!nodes.has(id)) {
      const classes = new Set();
      nodes.set(id, {
        hidden: false, open: false, value: "", type: "password", disabled: false,
        textContent: "", children: [], dataset: {}, attributes: {},
        classList: {
          add: (value) => classes.add(value), remove: (value) => classes.delete(value),
          contains: (value) => classes.has(value),
          toggle(value, enabled) { if (enabled) classes.add(value); else classes.delete(value); },
        },
        setAttribute(name, value) { this.attributes[name] = value; },
        replaceChildren(...children) { this.children = children; },
        append(...children) { this.children.push(...children); },
        showModal() { this.open = true; },
        close() { this.open = false; }, reset() { this.value = ""; },
        addEventListener() {}, focus() {},
      });
    }
    return nodes.get(id);
  }
  const context = vm.createContext({
    document: { getElementById: node, createElement: () => node(`generated-${++generatedNode}`), querySelectorAll: () => [], addEventListener() {} },
    window: { setTimeout() {}, clearTimeout() {} },
    fetch: () => new Promise(() => {}), // Initial session restore remains pending.
  });
  vm.runInContext(source, context);
  const portal = vm.runInContext("({ state, api, showLogin, signOut, renderRegistrationRequests, openRequestReview, submitRequestReview })", context);
  portal.state.csrfToken = "session-csrf";
  node("app-shell").hidden = false;
  node("login-view").hidden = true;
  return { context, portal, node };
}

const requestExample = {
  request_id: "11111111-1111-4111-8111-111111111111", installation_id: "22222222-2222-4222-8222-222222222222",
  school_id: "123456", school_name: "<img src=x onerror=alert(1)> School",
  school_district: "Schools District of Motiong", school_division: "Schools Division of Samar",
  status: "pending", created_at: "2026-10-10T00:00:00+00:00",
  revision: "e".repeat(64),
};

test("school submissions render as unverified pending requests with text-only school identity", () => {
  const { portal, node } = harness();
  portal.state.registrationRequests = [requestExample];
  node("request-status").value = "pending";
  portal.renderRegistrationRequests();
  assert.match(node("request-count").textContent, /1 pending approval/);
  const row = node("registration-requests-body").children[0];
  assert.equal(row.children[3].textContent, "Pending approval");
  assert.equal(row.children[4].children[0].textContent, "Review & approve");
  assert.ok(JSON.stringify(row).includes(requestExample.school_name));
  assert.ok(!source.includes("innerHTML"));
  assert.equal(portal.state.schools.length, 0);
});

test("review confirmation shows identity and ignores forged receipt paths", () => {
  const { portal, node } = harness();
  portal.openRequestReview(requestExample, "approve");
  assert.equal(node("request-review-dialog").open, true);
  assert.match(node("confirm-request-review").textContent, /identity only/);
  assert.equal(node("request-review-identity").textContent, `${requestExample.school_name} · School ID 123456`);
  node("request-review-dialog").close();
  portal.openRequestReview({ ...requestExample, request_id: "../admin/schools" }, "approve");
  assert.equal(node("request-review-dialog").open, false);
});

test("approving a request uses authenticated CSRF review only and never issues activation", async () => {
  const { context, portal, node } = harness();
  const sent = [];
  context.refreshDashboard = async () => {};
  context.fetch = async (path, request) => {
    sent.push({ path, request });
    return { ok: true, status: 200, json: async () => ({ ...requestExample, status: "approved" }) };
  };
  portal.openRequestReview(requestExample, "approve");
  await portal.submitRequestReview({ preventDefault() {} });
  assert.equal(sent.length, 1);
  assert.equal(sent[0].path, `/v1/admin/registration-requests/${requestExample.request_id}/review`);
  assert.equal(sent[0].request.headers["X-CSM-CSRF-Token"], "session-csrf");
  assert.deepEqual(JSON.parse(sent[0].request.body), { decision: "approve", expected_revision: requestExample.revision });
  assert.equal(node("request-review-dialog").open, false);
  assert.match(node("toast").textContent, /activation code separately/);
});

test("stale review cannot silently approve changed identity and explains re-review", async () => {
  const { context, portal, node } = harness();
  portal.openRequestReview(requestExample, "approve");
  // A later refresh must not change the immutable modal snapshot.
  portal.state.registrationRequests = [{ ...requestExample, revision: "f".repeat(64), school_name: "Changed School" }];
  let posted;
  context.fetch = async (_path, request) => {
    posted = JSON.parse(request.body);
    return { ok: false, status: 409, json: async () => ({ code: "directory_request_stale", detail: "The school's details changed. Close this window, refresh, and verify again." }) };
  };
  await portal.submitRequestReview({ preventDefault() {} });
  assert.equal(posted.expected_revision, requestExample.revision);
  assert.equal(node("request-review-dialog").open, true);
  assert.match(node("request-review-error").textContent, /changed.*refresh/);
  assert.equal(node("confirm-request-review").disabled, false);
  assert.equal(node("cancel-request-review").disabled, false);
});

test("missing review revision disables approval and bounded history is explained", () => {
  const { portal, node } = harness();
  portal.openRequestReview({ ...requestExample, revision: undefined }, "approve");
  assert.equal(node("request-review-dialog").open, false);
  portal.state.registrationRequests = [requestExample];
  portal.state.registrationRequestCounts = { pending: 1, approved: 1000, rejected: 0 };
  portal.renderRegistrationRequests();
  assert.match(node("request-count").textContent, /all pending requests included/);
});

test("ended sessions clear pending identity and review overlays", () => {
  const { portal, node } = harness();
  portal.state.registrationRequests = [requestExample];
  portal.openRequestReview(requestExample, "reject");
  portal.showLogin();
  assert.equal(portal.state.registrationRequests.length, 0);
  assert.equal(portal.state.reviewRequest, null);
  assert.equal(node("request-review-dialog").open, false);
  assert.equal(node("request-review-identity").textContent, "");
});

test("expiration clears school details, closes overlays, and resets the token field", () => {
  const { portal, node } = harness();
  portal.state.schools = [{ school_id: "123456", school_name: "Private roster entry" }];
  node("school-drawer").classList.add("is-open");
  node("drawer-body").children = ["Private roster entry"];
  node("school-dialog").open = true;
  node("activation-dialog").open = true;
  node("admin-token").value = "sensitive-value";
  node("admin-token").type = "text";
  portal.showLogin();
  assert.equal(node("app-shell").hidden, true);
  assert.equal(node("login-view").hidden, false);
  assert.equal(node("school-drawer").classList.contains("is-open"), false);
  assert.equal(node("drawer-body").children.length, 0);
  assert.equal(node("school-dialog").open, false);
  assert.equal(node("activation-dialog").open, false);
  assert.equal(portal.state.schools.length, 0);
  assert.equal(portal.state.csrfToken, "");
  assert.equal(node("admin-token").value, "");
  assert.equal(node("admin-token").type, "password");
});

test("failed sign-out remains visibly signed in and explains retry", async () => {
  const { context, portal, node } = harness();
  context.fetch = async () => { throw new Error("offline"); };
  await portal.signOut();
  assert.equal(portal.state.csrfToken, "session-csrf");
  assert.equal(node("app-shell").hidden, false);
  assert.equal(node("login-view").hidden, true);
  assert.match(node("toast").textContent, /Sign-out could not be confirmed/);
  assert.equal(node("logout-button").disabled, false);
});

test("confirmed or expired sign-out clears the session", async () => {
  for (const status of [200, 401]) {
    const { context, portal, node } = harness();
    context.fetch = async () => ({ ok: status === 200, status, json: async () => ({}) });
    await portal.signOut();
    assert.equal(portal.state.csrfToken, "");
    assert.equal(node("login-view").hidden, false);
  }
});

test("late responses cannot repopulate an ended session", async () => {
  const { context, portal } = harness();
  let finish;
  context.fetch = () => new Promise((resolve) => { finish = resolve; });
  const request = portal.api("/v1/admin/schools");
  portal.showLogin();
  finish({ ok: true, status: 200, json: async () => ({ schools: [{ school_id: "123456" }] }) });
  await assert.rejects(request, /session changed/);
  assert.equal(portal.state.schools.length, 0);
});

test("mutations send session CSRF and keep credentials in same origin", async () => {
  const { context, portal } = harness();
  let sent;
  context.fetch = async (_path, request) => {
    sent = request;
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  await portal.api("/v1/admin/schools/123456", { method: "PATCH", body: { status: "suspended" } });
  assert.equal(sent.headers["X-CSM-CSRF-Token"], "session-csrf");
  assert.equal(sent.credentials, "same-origin");
  assert.equal(sent.body, '{"status":"suspended"}');
});
