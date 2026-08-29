"use strict";

const $ = (id) => document.getElementById(id);
const statusBox = $("status");
const completion = $("completion");
const completionCode = $("completion-code");
let transferIntentId = "";

function base64urlToBytes(value) {
  const padding = "=".repeat((4 - value.length % 4) % 4);
  const binary = atob((value + padding).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(binary, character => character.charCodeAt(0));
}

function bytesToBase64url(value) {
  const bytes = new Uint8Array(value);
  let binary = "";
  bytes.forEach(byte => { binary += String.fromCharCode(byte); });
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function publicKeyOptions(options) {
  const result = structuredClone(options);
  result.challenge = base64urlToBytes(result.challenge);
  if (result.user && typeof result.user.id === "string") result.user.id = base64urlToBytes(result.user.id);
  for (const key of ["excludeCredentials", "allowCredentials"]) {
    if (Array.isArray(result[key])) result[key] = result[key].map(item => ({...item, id: base64urlToBytes(item.id)}));
  }
  return result;
}

function credentialJSON(credential) {
  const response = {};
  for (const key of ["clientDataJSON", "attestationObject", "authenticatorData", "signature", "userHandle"]) {
    if (credential.response[key] !== undefined && credential.response[key] !== null) response[key] = credential.response[key];
  }
  return {
    id: credential.id,
    rawId: bytesToBase64url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment,
    clientExtensionResults: credential.getClientExtensionResults(),
    response,
  };
}

function normalizeCredential(credential) {
  const output = credentialJSON(credential);
  for (const key of ["clientDataJSON", "attestationObject", "authenticatorData", "signature", "userHandle"]) {
    if (output.response[key] instanceof ArrayBuffer) output.response[key] = bytesToBase64url(output.response[key]);
    if (output.response[key] === null) delete output.response[key];
  }
  const transports = credential.response.getTransports?.();
  if (transports) output.response.transports = transports;
  return output;
}

async function request(path, payload) {
  const response = await fetch(path, {method: "POST", credentials: "omit", cache: "no-store", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
  const document = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(document.detail || document.error || "The authorization service could not complete this request.");
  return document;
}

function showStatus(message, error = false) {
  statusBox.hidden = !message;
  statusBox.textContent = message;
  statusBox.classList.toggle("error", error);
}

function showCompletion(document) {
  completionCode.textContent = document.completion_code || "";
  completion.hidden = !completionCode.textContent;
  showStatus("Authorization completed. Use the one-time code below in the Control Center.");
}

function switchMode(mode) {
  const registration = mode === "registration";
  const transfer = mode === "transfer";
  $("registration").hidden = !registration;
  $("transfer").hidden = !transfer;
  $("passkeys").hidden = mode !== "passkeys";
  $("registration-tab").classList.toggle("secondary", !registration);
  $("transfer-tab").classList.toggle("secondary", !transfer);
  $("passkeys-tab").classList.toggle("secondary", mode !== "passkeys");
  completion.hidden = true;
  showStatus("");
}

$("registration-tab").addEventListener("click", () => switchMode("registration"));
$("transfer-tab").addEventListener("click", () => switchMode("transfer"));
$("passkeys-tab").addEventListener("click", () => switchMode("passkeys"));

$("registration").addEventListener("submit", async event => {
  event.preventDefault();
  const button = $("register-button"); button.disabled = true;
  const payload = {school_id: $("registration-school-id").value.trim(), school_name: $("school-name").value.trim(), installation_id: $("registration-installation-id").value.trim(), activation_code: $("activation-code").value.trim()};
  try {
    showStatus("Preparing Windows passkey registration…");
    const begin = await request("/v1/registrations/begin", payload);
    const credential = await navigator.credentials.create({publicKey: publicKeyOptions(begin.public_key)});
    const complete = await request("/v1/registrations/complete", {...payload, ceremony_id: begin.ceremony_id, passkey_response: normalizeCredential(credential)});
    $("activation-code").value = "";
    showCompletion(complete);
  } catch (error) { showStatus(error.message || String(error), true); }
  finally { button.disabled = false; }
});

$("transfer").addEventListener("submit", async event => {
  event.preventDefault();
  const button = $("transfer-button"); button.disabled = true;
  const mode = $("transfer-mode").value;
  const payload = {intent_id: transferIntentId};
  try {
    if (!transferIntentId) throw new Error("Open this transfer from the destination Control Center.");
    if (mode === "server_only" && !confirm("Server Only will transfer authority without recovering historical data. Continue?")) return;
    showStatus("Requesting the registered administrator passkey…");
    const begin = await request("/v1/transfers/begin", payload);
    const credential = await navigator.credentials.get({publicKey: publicKeyOptions(begin.public_key)});
    const complete = await request("/v1/transfers/complete", {...payload, ceremony_id: begin.ceremony_id, passkey_response: normalizeCredential(credential)});
    showCompletion(complete);
  } catch (error) { showStatus(error.message || String(error), true); }
  finally { button.disabled = false; }
});

$("add-passkey").addEventListener("submit", async event => {
  event.preventDefault();
  const button = $("add-passkey-button"); button.disabled = true;
  const school = $("passkey-school-id").value.trim();
  try {
    showStatus("Requesting an existing administrator passkey…");
    const begin = await request("/v1/passkeys/add/begin", {school_id: school});
    const existing = await navigator.credentials.get({publicKey: publicKeyOptions(begin.public_key)});
    const authorized = await request("/v1/passkeys/add/authorize", {
      school_id: school,
      ceremony_id: begin.ceremony_id,
      passkey_response: normalizeCredential(existing),
    });
    showStatus("Create the additional administrator passkey…");
    const replacement = await navigator.credentials.create({publicKey: publicKeyOptions(authorized.public_key)});
    await request("/v1/passkeys/add/complete", {
      school_id: school,
      ceremony_id: authorized.ceremony_id,
      passkey_response: normalizeCredential(replacement),
      passkey_label: $("passkey-label").value.trim(),
    });
    showStatus("The additional administrator passkey was added.");
  } catch (error) { showStatus(error.message || String(error), true); }
  finally { button.disabled = false; }
});

$("recover-passkeys").addEventListener("submit", async event => {
  event.preventDefault();
  const button = $("recover-passkeys-button"); button.disabled = true;
  const payload = {
    school_id: $("recovery-school-id").value.trim(),
    reset_code: $("recovery-code").value.trim(),
  };
  try {
    if (!confirm("Recovery permanently revokes every previously registered administrator passkey. Continue?")) return;
    showStatus("Preparing administrator passkey recovery…");
    const begin = await request("/v1/passkeys/reset/begin", payload);
    const credential = await navigator.credentials.create({publicKey: publicKeyOptions(begin.public_key)});
    await request("/v1/passkeys/reset/complete", {
      ...payload,
      ceremony_id: begin.ceremony_id,
      passkey_response: normalizeCredential(credential),
    });
    $("recovery-code").value = "";
    showStatus("Passkey recovery completed. All previous administrator passkeys were revoked.");
  } catch (error) { showStatus(error.message || String(error), true); }
  finally { button.disabled = false; }
});

const query = new URLSearchParams(location.search);
$("registration-school-id").value = query.get("school_id") || "";
$("registration-installation-id").value = query.get("installation_id") || "";
$("passkey-school-id").value = query.get("school_id") || "";
$("recovery-school-id").value = query.get("school_id") || "";
$("school-name").value = query.get("school_name") || "";
const fragment = new URLSearchParams(location.hash.replace(/^#/, ""));
transferIntentId = fragment.get("intent") || "";

async function loadTransferIntent() {
  if (!transferIntentId) return;
  switchMode("transfer");
  try {
    const intent = await request("/v1/transfers/intents/inspect", {intent_id: transferIntentId});
    $("transfer-school-id").value = intent.school_id || "";
    $("transfer-installation-id").value = intent.destination_installation_id || "";
    $("transfer-mode").value = intent.transfer_mode || "server_only";
    $("transfer-mode").disabled = true;
    $("transfer-validation").textContent = intent.data_verified
      ? "The destination Control Center verified and activated the staged data before creating this one-time authorization request."
      : "Server Only was explicitly selected in the destination Control Center. No data recovery is attached to this authorization request.";
  } catch (error) {
    transferIntentId = "";
    $("transfer-button").disabled = true;
    showStatus(error.message || String(error), true);
  }
}

if (query.get("mode") === "passkeys") switchMode("passkeys");
else if (query.get("mode") === "transfer" || transferIntentId) switchMode("transfer");
loadTransferIntent();
