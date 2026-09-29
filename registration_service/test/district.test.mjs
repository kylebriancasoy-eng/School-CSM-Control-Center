import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../static/district.js", import.meta.url), "utf8");

function harness() {
  const nodes = new Map();
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
        close() { this.open = false; }, reset() { this.value = ""; },
        addEventListener() {}, focus() {},
      });
    }
    return nodes.get(id);
  }
  const context = vm.createContext({
    document: { getElementById: node, querySelectorAll: () => [], addEventListener() {} },
    window: { setTimeout() {}, clearTimeout() {} },
    fetch: () => new Promise(() => {}), // Initial session restore remains pending.
  });
  vm.runInContext(source, context);
  const portal = vm.runInContext("({ state, api, showLogin, signOut })", context);
  portal.state.csrfToken = "session-csrf";
  node("app-shell").hidden = false;
  node("login-view").hidden = true;
  return { context, portal, node };
}

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
