"use strict";

const state = {
  csrfToken: "",
  schools: [],
  overview: null,
  activeView: "overview",
  selectedSchoolId: "",
  refreshInProgress: false,
  sessionEpoch: 0,
  drawerRequest: 0,
};

const viewTitles = {
  overview: "Overview",
  schools: "Schools",
  enrollment: "Add & enroll school",
  activity: "Activity log",
};

const statusLabels = {
  connected: "Connected",
  disconnected: "Disconnected",
  attention: "Needs attention",
  stale: "Check-in overdue",
  unseen: "No check-in yet",
  pending: "Awaiting setup",
  suspended: "Enrollment suspended",
  approved: "Approved",
  online: "Online",
  offline: "Offline",
  maintenance: "Under maintenance",
  running: "Running",
  stopped: "Stopped",
  error: "Error",
  unknown: "Not reported",
  registered: "Registered",
  not_registered: "Not registered",
};

const eventLabels = {
  activation_code_issued: "Activation code issued",
  initial_registration_completed: "Initial Internet Server registered",
  district_school_saved: "School directory entry saved",
  district_school_status_changed: "School enrollment status changed",
  transfer_intent_created: "Server transfer started",
  authorization_transferred: "Internet Server transferred",
  administrator_passkey_added: "Administrator passkey added",
  administrator_passkey_revoked: "Administrator passkey revoked",
  administrator_passkeys_recovered: "Administrator passkeys recovered",
  passkey_reset_code_issued: "Passkey recovery issued",
  source_tunnel_revocation_pending: "Old tunnel cleanup pending",
};

const byId = (id) => document.getElementById(id);

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== "") node.textContent = String(text);
  return node;
}

function button(text, className, action) {
  const node = element("button", className, text);
  node.type = "button";
  node.addEventListener("click", action);
  return node;
}

class ApiError extends Error {
  constructor(message, status, code = "") {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function api(path, options = {}) {
  const sessionEpoch = state.sessionEpoch;
  const method = String(options.method || "GET").toUpperCase();
  const headers = { Accept: "application/json" };
  const request = { method, headers, credentials: "same-origin" };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    request.body = JSON.stringify(options.body);
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && options.csrf !== false) {
    headers["X-CSM-CSRF-Token"] = state.csrfToken;
  }
  let response;
  try {
    response = await fetch(path, request);
  } catch (_error) {
    throw new ApiError("The district management service could not be reached.", 0);
  }
  let documentBody = {};
  try {
    documentBody = await response.json();
  } catch (_error) {
    documentBody = {};
  }
  if (sessionEpoch !== state.sessionEpoch) {
    throw new ApiError("The district session changed. Please try again.", 401);
  }
  if (!response.ok) {
    if (response.status === 401 && path !== "/v1/admin/session") showLogin();
    throw new ApiError(
      String(documentBody.detail || "The request could not be completed."),
      response.status,
      String(documentBody.code || "")
    );
  }
  return documentBody;
}

function showLogin(message = "") {
  state.sessionEpoch += 1;
  state.csrfToken = "";
  state.schools = [];
  state.overview = null;
  state.selectedSchoolId = "";
  closeDrawer();
  closeNavigation();
  byId("school-dialog").close();
  byId("activation-dialog").close();
  byId("school-form").reset();
  ["drawer-body", "school-roster-body", "recent-schools-body", "audit-body",
    "health-list", "attention-list", "activation-school", "audit-school"].forEach((id) => {
    byId(id).replaceChildren();
  });
  byId("drawer-title").textContent = "School";
  byId("drawer-subtitle").textContent = "";
  byId("activation-dialog-school").textContent = "";
  byId("toast").hidden = true;
  byId("admin-token").value = "";
  byId("admin-token").type = "password";
  byId("token-visibility").textContent = "Show";
  byId("token-visibility").setAttribute("aria-pressed", "false");
  byId("session-check").hidden = true;
  byId("app-shell").hidden = true;
  byId("login-view").hidden = false;
  const error = byId("login-error");
  error.textContent = message;
  error.hidden = !message;
  window.setTimeout(() => byId("admin-token").focus(), 0);
}

function showApp() {
  byId("session-check").hidden = true;
  byId("login-view").hidden = true;
  byId("app-shell").hidden = false;
}

function setBusy(control, busy, busyText = "Working…") {
  if (!control) return;
  if (busy) {
    control.dataset.previousText = control.textContent;
    control.textContent = busyText;
    control.disabled = true;
  } else {
    control.textContent = control.dataset.previousText || control.textContent;
    control.disabled = false;
    delete control.dataset.previousText;
  }
}

function safeStatus(value) {
  const normalized = String(value || "unknown").toLowerCase();
  return Object.prototype.hasOwnProperty.call(statusLabels, normalized)
    ? normalized
    : "unknown";
}

function statusChip(value, override = "") {
  const normalized = safeStatus(value);
  return element(
    "span",
    `status-chip status-chip--${normalized}`,
    override || statusLabels[normalized]
  );
}

function initials(name) {
  const parts = String(name || "School").trim().split(/\s+/).filter(Boolean);
  return parts.slice(0, 2).map((part) => part[0].toUpperCase()).join("") || "SC";
}

function formatTime(value) {
  if (!value) return "Not yet";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Not yet";
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(parsed);
}

function relativeTime(value) {
  if (!value) return "No check-in yet";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "No check-in yet";
  const seconds = Math.round((parsed.getTime() - Date.now()) / 1000);
  const absolute = Math.abs(seconds);
  let divisor = 1;
  let unit = "second";
  if (absolute >= 86400) {
    divisor = 86400;
    unit = "day";
  } else if (absolute >= 3600) {
    divisor = 3600;
    unit = "hour";
  } else if (absolute >= 60) {
    divisor = 60;
    unit = "minute";
  }
  return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(
    Math.round(seconds / divisor),
    unit
  );
}

function schoolCell(school) {
  const wrapper = element("div", "school-cell");
  wrapper.append(element("span", "school-avatar", initials(school.school_name)));
  const labels = element("div");
  labels.append(
    element("strong", "", school.school_name || "Unnamed school"),
    element("small", "", `School ID ${school.school_id || "—"}`)
  );
  wrapper.append(labels);
  return wrapper;
}

function detailField(term, description) {
  const wrapper = element("div", "detail-field");
  const dt = element("dt", "", term);
  const dd = element("dd", "", description || "—");
  wrapper.append(dt, dd);
  return wrapper;
}

function showToast(message, error = false) {
  const toast = byId("toast");
  toast.textContent = message;
  toast.classList.toggle("is-error", error);
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 5200);
}

function showLoadError(error) {
  byId("load-error-detail").textContent = error.message || "Check the service and try again.";
  byId("load-error").hidden = false;
  byId("service-dot").classList.remove("is-online");
  byId("service-dot").classList.add("is-error");
  byId("service-label").textContent = "Service unavailable";
  byId("service-detail").textContent = "Local school services are unaffected";
}

function clearLoadError() {
  byId("load-error").hidden = true;
  byId("service-dot").classList.remove("is-error");
  byId("service-dot").classList.add("is-online");
  byId("service-label").textContent = "Management service online";
  byId("service-detail").textContent = "Infrastructure metadata only";
}

async function restoreSession() {
  try {
    const session = await api("/v1/admin/session", { csrf: false });
    state.csrfToken = String(session.csrf_token || "");
    if (!state.csrfToken) throw new ApiError("The session is incomplete.", 401);
    showApp();
    await refreshDashboard();
  } catch (error) {
    showLogin(error.status && error.status !== 401 ? error.message : "");
  }
}

async function signIn(event) {
  event.preventDefault();
  const input = byId("admin-token");
  const error = byId("login-error");
  const submit = byId("login-button");
  error.hidden = true;
  if (!input.checkValidity()) {
    error.textContent = "Enter the complete administrator access token.";
    error.hidden = false;
    input.focus();
    return;
  }
  setBusy(submit, true, "Signing in…");
  try {
    const session = await api("/v1/admin/session", {
      method: "POST",
      csrf: false,
      body: { access_token: input.value },
    });
    state.csrfToken = String(session.csrf_token || "");
    input.value = "";
    showApp();
    await refreshDashboard();
  } catch (apiError) {
    error.textContent = apiError.message;
    error.hidden = false;
    input.select();
  } finally {
    setBusy(submit, false);
  }
}

async function signOut() {
  const control = byId("logout-button");
  control.disabled = true;
  try {
    await api("/v1/admin/session", { method: "DELETE" });
    showLogin();
  } catch (error) {
    if (error.status === 401) showLogin();
    else showToast("Sign-out could not be confirmed. Check your connection and try again.", true);
  } finally {
    control.disabled = false;
  }
}

async function refreshDashboard() {
  if (state.refreshInProgress || !state.csrfToken) return;
  state.refreshInProgress = true;
  const refresh = byId("refresh-button");
  setBusy(refresh, true, "Refreshing…");
  try {
    const [overview, roster] = await Promise.all([
      api("/v1/admin/overview"),
      api("/v1/admin/schools?status=all&limit=500"),
    ]);
    state.overview = overview;
    state.schools = Array.isArray(roster.schools) ? roster.schools : [];
    clearLoadError();
    renderOverview();
    renderRoster();
    renderSchoolChoices();
    byId("last-updated").textContent = `Updated ${relativeTime(overview.generated_at)}`;
    if (state.activeView === "activity") await loadAudit();
  } catch (error) {
    if (error.status !== 401) showLoadError(error);
  } finally {
    state.refreshInProgress = false;
    setBusy(refresh, false);
  }
}

function renderOverview() {
  const counts = state.overview && state.overview.counts ? state.overview.counts : {};
  byId("metric-registered").textContent = String(counts.registered || 0);
  byId("metric-registered-note").textContent = `${counts.total || 0} schools in the directory`;
  byId("metric-connected").textContent = String(counts.connected || 0);
  byId("metric-attention").textContent = String(counts.attention || 0);
  byId("metric-pending").textContent = String(counts.pending || 0);
  byId("nav-school-count").textContent = String(counts.total || state.schools.length);

  const districtHealth = byId("district-health");
  const hasAttention = Number(counts.attention || 0) > 0;
  const health = hasAttention ? "attention" : (counts.registered ? "connected" : "pending");
  districtHealth.className = `status-chip status-chip--${health}`;
  districtHealth.textContent = hasAttention ? "Review needed" : (counts.registered ? "Operating normally" : "Awaiting first connection");

  const healthList = byId("health-list");
  healthList.replaceChildren();
  [
    ["Connected Internet Gateways", counts.connected || 0, "Fresh check-in and tunnel connected", "connected"],
    ["Awaiting first setup", counts.pending || 0, "Approved school not yet registered", "pending"],
    ["Requires follow-up", counts.attention || 0, "Offline, overdue, unseen, or suspended", "attention"],
  ].forEach(([title, count, note, status]) => {
    const row = element("div", "health-item");
    const icon = element("span", `health-item__icon ${status === "connected" ? "is-good" : (status === "attention" ? "is-warn" : "is-neutral")}`, status === "connected" ? "✓" : (status === "attention" ? "!" : "→"));
    icon.setAttribute("aria-hidden", "true");
    row.append(icon);
    const copy = element("div");
    copy.append(element("strong", "", title), element("small", "", note));
    row.append(copy, element("span", "mini-count", count));
    healthList.append(row);
  });

  const attention = state.schools.filter((school) =>
    ["attention", "disconnected", "stale", "unseen", "suspended"].includes(school.health_status)
  );
  const attentionList = byId("attention-list");
  attentionList.replaceChildren();
  if (!attention.length) {
    const row = element("div", "attention-item");
    const icon = element("span", "health-item__icon is-good", "✓");
    icon.setAttribute("aria-hidden", "true");
    row.append(icon);
    const copy = element("div");
    copy.append(element("strong", "", "No schools currently need follow-up"), element("small", "", "Statuses are based on recent infrastructure check-ins."));
    row.append(copy);
    attentionList.append(row);
  } else {
    attention.slice(0, 5).forEach((school) => {
      const row = element("div", "attention-item");
      row.append(element("span", "school-avatar", initials(school.school_name)));
      const copy = element("div");
      copy.append(
        element("strong", "", school.school_name),
        element("small", "", `${statusLabels[safeStatus(school.health_status)]} · ${relativeTime(school.last_seen_at)}`)
      );
      row.append(copy, button("View", "text-button", () => openSchool(school.school_id)));
      attentionList.append(row);
    });
  }

  const recentBody = byId("recent-schools-body");
  recentBody.replaceChildren();
  const recent = [...state.schools].sort((left, right) =>
    String(right.last_seen_at || "").localeCompare(String(left.last_seen_at || ""))
  ).slice(0, 6);
  if (!recent.length) {
    const row = element("tr");
    const cell = element("td", "", "No district schools have been added yet.");
    cell.colSpan = 5;
    row.append(cell);
    recentBody.append(row);
    return;
  }
  recent.forEach((school) => {
    const row = element("tr");
    const identity = element("td");
    identity.append(schoolCell(school));
    const survey = element("td");
    survey.append(statusChip(school.survey_status));
    const gateway = element("td");
    gateway.append(statusChip(school.health_status));
    const seen = element("td", "", relativeTime(school.last_seen_at));
    seen.title = formatTime(school.last_seen_at);
    const action = element("td");
    action.append(button("View", "text-button", () => openSchool(school.school_id)));
    row.append(identity, survey, gateway, seen, action);
    recentBody.append(row);
  });
}

function filteredSchools() {
  const query = byId("school-search").value.trim().toLowerCase();
  const filter = byId("school-status").value;
  return state.schools.filter((school) => {
    const matchesQuery = !query || String(school.school_id).includes(query) ||
      String(school.school_name).toLowerCase().includes(query);
    let matchesStatus = true;
    if (filter === "registered") matchesStatus = school.registration_state === "registered";
    else if (["approved", "suspended"].includes(filter)) matchesStatus = school.directory_status === filter;
    else if (filter === "attention") matchesStatus = ["attention", "disconnected", "stale", "unseen", "suspended"].includes(school.health_status);
    else if (filter !== "all") matchesStatus = school.health_status === filter;
    return matchesQuery && matchesStatus;
  });
}

function renderRoster() {
  const schools = filteredSchools();
  const body = byId("school-roster-body");
  body.replaceChildren();
  byId("roster-result-count").textContent = `${schools.length} school${schools.length === 1 ? "" : "s"}`;
  byId("roster-empty").hidden = schools.length !== 0;
  if (!schools.length) return;
  schools.forEach((school) => {
    const row = element("tr");
    const identity = element("td");
    identity.append(schoolCell(school));
    const gateway = element("td");
    gateway.append(statusChip(school.health_status));
    const survey = element("td");
    survey.append(statusChip(school.survey_status));
    const version = element("td", "", school.application_version || "Not reported");
    const seen = element("td", "", relativeTime(school.last_seen_at));
    seen.title = formatTime(school.last_seen_at);
    const action = element("td");
    action.append(button("View", "text-button", () => openSchool(school.school_id)));
    row.append(identity, gateway, survey, version, seen, action);
    body.append(row);
  });
}

function renderSchoolChoices() {
  const activation = byId("activation-school");
  const audit = byId("audit-school");
  const selectedActivation = activation.value || state.selectedSchoolId;
  const selectedAudit = audit.value;
  activation.replaceChildren(new Option("Choose a school…", ""));
  audit.replaceChildren(new Option("All schools", ""));
  state.schools.forEach((school) => {
    audit.append(new Option(`${school.school_name} · ${school.school_id}`, school.school_id));
    if (school.directory_status === "approved" && school.registration_state !== "registered") {
      activation.append(new Option(`${school.school_name} · ${school.school_id}`, school.school_id));
    }
  });
  if ([...activation.options].some((item) => item.value === selectedActivation)) {
    activation.value = selectedActivation;
  }
  if ([...audit.options].some((item) => item.value === selectedAudit)) audit.value = selectedAudit;
  updateSelectedSchool();
}

function updateSelectedSchool() {
  const schoolId = byId("activation-school").value;
  state.selectedSchoolId = schoolId;
  const school = state.schools.find((item) => item.school_id === schoolId);
  const card = byId("selected-school");
  card.hidden = !school;
  if (!school) return;
  byId("selected-school-avatar").textContent = initials(school.school_name);
  byId("selected-school-name").textContent = school.school_name;
  byId("selected-school-detail").textContent = `School ID ${school.school_id}`;
  const status = byId("selected-school-status");
  status.replaceChildren(statusChip(school.directory_status));
}

async function openSchool(schoolId) {
  const request = ++state.drawerRequest;
  byId("drawer-title").textContent = "Loading school…";
  byId("drawer-subtitle").textContent = `School ID ${schoolId}`;
  byId("drawer-body").replaceChildren(element("div", "skeleton skeleton--table"));
  byId("drawer-backdrop").hidden = false;
  byId("school-drawer").classList.add("is-open");
  byId("school-drawer").setAttribute("aria-hidden", "false");
  try {
    const response = await api(`/v1/admin/schools/${encodeURIComponent(schoolId)}`);
    if (request !== state.drawerRequest) return;
    renderSchoolDetail(response.school);
  } catch (error) {
    if (request !== state.drawerRequest) return;
    byId("drawer-body").replaceChildren(element("p", "form-error", error.message));
  }
}

function renderSchoolDetail(school) {
  byId("drawer-title").textContent = school.school_name;
  byId("drawer-subtitle").textContent = `School ID ${school.school_id}`;
  const body = byId("drawer-body");
  body.replaceChildren();

  const hero = element("div", "detail-hero");
  hero.append(element("span", "school-avatar", initials(school.school_name)));
  const heroCopy = element("div");
  heroCopy.append(element("strong", "", school.school_name), element("small", "", `${school.school_district} · ${school.school_division}`));
  hero.append(heroCopy, statusChip(school.health_status));
  body.append(hero);

  const infrastructure = element("section", "detail-section");
  infrastructure.append(element("h3", "", "Infrastructure status"));
  const grid = element("dl", "detail-grid");
  grid.append(
    detailField("Directory", statusLabels[safeStatus(school.directory_status)]),
    detailField("Registration", statusLabels[safeStatus(school.registration_state)]),
    detailField("Gateway", statusLabels[safeStatus(school.gateway_state)]),
    detailField("Survey form", statusLabels[safeStatus(school.survey_status)]),
    detailField("Application", school.application_version || "Not reported"),
    detailField("Last check-in", formatTime(school.last_seen_at)),
    detailField("Public host", school.public_host || "Not assigned"),
    detailField("Active server", school.active_installation_id || "Not assigned")
  );
  infrastructure.append(grid);
  body.append(infrastructure);

  const installations = element("section", "detail-section");
  installations.append(element("h3", "", "Registered installations"));
  const installationList = element("ul", "detail-list");
  const installationRows = Array.isArray(school.installations) ? school.installations : [];
  if (!installationRows.length) {
    const item = element("li");
    item.append(element("span", "", "No Internet Server registered"));
    installationList.append(item);
  } else {
    installationRows.forEach((installation) => {
      const item = element("li");
      item.append(
        element("span", "", installation.installation_id),
        element("small", "", `${installation.state} · ${formatTime(installation.registered_at)}`)
      );
      installationList.append(item);
    });
  }
  installations.append(installationList);
  body.append(installations);

  const passkeys = element("section", "detail-section");
  passkeys.append(element("h3", "", "Administrator passkeys"));
  const passkeyList = element("ul", "detail-list");
  const passkeyRows = Array.isArray(school.passkeys) ? school.passkeys : [];
  if (!passkeyRows.length) {
    const item = element("li");
    item.append(element("span", "", "No passkey registered yet"));
    passkeyList.append(item);
  } else {
    passkeyRows.forEach((passkey) => {
      const item = element("li");
      item.append(
        element("span", "", passkey.label || "Administrator passkey"),
        element("small", "", passkey.last_used_at ? `Used ${relativeTime(passkey.last_used_at)}` : "Not used yet")
      );
      passkeyList.append(item);
    });
  }
  passkeys.append(passkeyList);
  body.append(passkeys);

  const boundary = element("p", "field-help", "Suspending enrollment blocks new activation codes. It does not stop this school's active server, change its Survey Form status, or affect any other school.");
  body.append(boundary);
  const actions = element("div", "detail-actions");
  if (school.directory_status === "approved" && school.registration_state !== "registered") {
    actions.append(button("Issue activation", "button button--primary", () => {
      closeDrawer();
      navigate("enrollment");
      byId("activation-school").value = school.school_id;
      updateSelectedSchool();
    }));
  }
  const nextStatus = school.directory_status === "suspended" ? "approved" : "suspended";
  const statusText = nextStatus === "approved" ? "Reactivate enrollment" : "Suspend new enrollment";
  actions.append(button(statusText, "button button--secondary", (event) => updateSchoolStatus(school, nextStatus, event.currentTarget)));
  body.append(actions);
}

async function updateSchoolStatus(school, status, control) {
  const action = status === "suspended" ? "suspend new enrollment for" : "reactivate enrollment for";
  if (!window.confirm(`Do you want to ${action} ${school.school_name}?\n\nThis does not stop an existing school server or affect other schools.`)) return;
  setBusy(control, true);
  try {
    const response = await api(`/v1/admin/schools/${encodeURIComponent(school.school_id)}`, {
      method: "PATCH",
      body: { status },
    });
    showToast(status === "suspended" ? "New enrollment is suspended for this school." : "Enrollment is active for this school.");
    await refreshDashboard();
    renderSchoolDetail(response.school);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(control, false);
  }
}

function closeDrawer() {
  state.drawerRequest += 1;
  byId("school-drawer").classList.remove("is-open");
  byId("school-drawer").setAttribute("aria-hidden", "true");
  byId("drawer-backdrop").hidden = true;
}

function openSchoolDialog() {
  const form = byId("school-form");
  form.reset();
  byId("new-school-district").value = "Schools District of Motiong";
  byId("new-school-division").value = "Schools Division of Samar";
  byId("school-form-error").hidden = true;
  byId("school-dialog").showModal();
  byId("new-school-id").focus();
}

function closeSchoolDialog() {
  byId("school-dialog").close();
}

async function saveSchool(event) {
  event.preventDefault();
  const form = byId("school-form");
  const error = byId("school-form-error");
  const control = byId("save-school-button");
  error.hidden = true;
  if (!form.checkValidity()) {
    form.reportValidity();
    return;
  }
  const payload = {
    school_id: byId("new-school-id").value.trim(),
    school_name: byId("new-school-name").value.trim(),
    school_district: byId("new-school-district").value.trim(),
    school_division: byId("new-school-division").value.trim(),
  };
  setBusy(control, true, "Saving…");
  try {
    const response = await api("/v1/admin/schools", { method: "POST", body: payload });
    closeSchoolDialog();
    state.selectedSchoolId = response.school.school_id;
    showToast(`${response.school.school_name} was added to the official directory.`);
    await refreshDashboard();
    navigate("enrollment");
    byId("activation-school").value = response.school.school_id;
    updateSelectedSchool();
  } catch (apiError) {
    error.textContent = apiError.message;
    error.hidden = false;
  } finally {
    setBusy(control, false);
  }
}

function reviewActivation(event) {
  event.preventDefault();
  const schoolId = byId("activation-school").value;
  const school = state.schools.find((item) => item.school_id === schoolId);
  if (!school) {
    showToast("Choose an approved school that is awaiting setup.", true);
    byId("activation-school").focus();
    return;
  }
  byId("activation-dialog-school").textContent = `${school.school_name} · School ID ${school.school_id}`;
  byId("activation-dialog").showModal();
}

async function submitActivation(event) {
  event.preventDefault();
  if (!event.submitter || event.submitter.value !== "confirm") {
    byId("activation-dialog").close();
    return;
  }
  const schoolId = byId("activation-school").value;
  const school = state.schools.find((item) => item.school_id === schoolId);
  if (!school) {
    byId("activation-dialog").close();
    showToast("The selected school is no longer available for activation.", true);
    return;
  }
  const control = byId("confirm-activation-button");
  setBusy(control, true, "Issuing…");
  try {
    const response = await api(`/v1/admin/schools/${encodeURIComponent(schoolId)}/activation-codes`, {
      method: "POST",
      body: { valid_days: 14 },
    });
    const copied = await copyText(String(response.activation_code || ""));
    byId("activation-dialog").close();
    byId("activation-school").value = "";
    updateSelectedSchool();
    showToast(copied
      ? `Activation code copied. Deliver it securely to ${school.school_name}; it expires in 14 days.`
      : "Clipboard access was blocked. For safety, issue a new code after allowing clipboard access.",
      !copied
    );
    await refreshDashboard();
  } catch (apiError) {
    byId("activation-dialog").close();
    showToast(apiError.message, true);
  } finally {
    setBusy(control, false);
  }
}

async function copyText(value) {
  if (!value) return false;
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch (_error) {
    const temporary = document.createElement("textarea");
    temporary.value = value;
    temporary.setAttribute("readonly", "");
    temporary.className = "clipboard-buffer";
    document.body.append(temporary);
    temporary.select();
    let copied = false;
    try {
      copied = document.execCommand("copy");
    } catch (_copyError) {
      copied = false;
    }
    temporary.remove();
    return copied;
  }
}

async function loadAudit() {
  const schoolId = byId("audit-school").value;
  const eventType = byId("audit-type").value;
  const query = new URLSearchParams({ limit: "200" });
  if (schoolId) query.set("school_id", schoolId);
  if (eventType) query.set("event_type", eventType);
  const body = byId("audit-body");
  try {
    const response = await api(`/v1/admin/audit?${query.toString()}`);
    const events = Array.isArray(response.events) ? response.events : [];
    body.replaceChildren();
    byId("audit-empty").hidden = events.length !== 0;
    events.forEach((event) => {
      const row = element("tr");
      const school = state.schools.find((item) => item.school_id === event.school_id);
      row.append(
        element("td", "", formatTime(event.occurred_at)),
        element("td", "", school ? `${school.school_name} · ${event.school_id}` : (event.school_id || "District service")),
        element("td", "", eventLabels[event.event_type] || String(event.event_type || "Recorded event").replaceAll("_", " ")),
        element("td", "", "Recorded")
      );
      body.append(row);
    });
  } catch (error) {
    if (error.status !== 401) showToast(error.message, true);
  }
}

function navigate(view, options = {}) {
  if (!viewTitles[view]) return;
  state.activeView = view;
  document.querySelectorAll("[data-view-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.viewPanel !== view;
  });
  document.querySelectorAll("[data-view]").forEach((control) => {
    const active = control.dataset.view === view;
    control.classList.toggle("is-active", active);
    if (active) control.setAttribute("aria-current", "page");
    else control.removeAttribute("aria-current");
  });
  byId("page-title").textContent = viewTitles[view];
  closeNavigation();
  if (options.statusFilter) byId("school-status").value = options.statusFilter;
  if (view === "schools") renderRoster();
  if (view === "activity") loadAudit();
  byId("main-content").focus();
}

function openNavigation() {
  byId("sidebar").classList.add("is-open");
  byId("nav-backdrop").hidden = false;
  byId("menu-button").setAttribute("aria-expanded", "true");
}

function closeNavigation() {
  byId("sidebar").classList.remove("is-open");
  byId("nav-backdrop").hidden = true;
  byId("menu-button").setAttribute("aria-expanded", "false");
}

function bindEvents() {
  byId("login-form").addEventListener("submit", signIn);
  byId("token-visibility").addEventListener("click", () => {
    const input = byId("admin-token");
    const revealing = input.type === "password";
    input.type = revealing ? "text" : "password";
    byId("token-visibility").textContent = revealing ? "Hide" : "Show";
    byId("token-visibility").setAttribute("aria-pressed", String(revealing));
  });
  byId("logout-button").addEventListener("click", signOut);
  byId("refresh-button").addEventListener("click", refreshDashboard);
  byId("retry-button").addEventListener("click", refreshDashboard);
  byId("school-search").addEventListener("input", renderRoster);
  byId("school-status").addEventListener("change", renderRoster);
  byId("activation-school").addEventListener("change", updateSelectedSchool);
  byId("activation-form").addEventListener("submit", reviewActivation);
  byId("activation-dialog-form").addEventListener("submit", submitActivation);
  byId("audit-school").addEventListener("change", loadAudit);
  byId("audit-type").addEventListener("change", loadAudit);
  byId("audit-refresh-button").addEventListener("click", loadAudit);
  byId("add-school-button").addEventListener("click", openSchoolDialog);
  document.querySelectorAll("[data-open-school-dialog]").forEach((control) => control.addEventListener("click", openSchoolDialog));
  byId("school-form").addEventListener("submit", saveSchool);
  byId("close-school-dialog").addEventListener("click", closeSchoolDialog);
  byId("cancel-school-dialog").addEventListener("click", closeSchoolDialog);
  byId("close-drawer").addEventListener("click", closeDrawer);
  byId("drawer-backdrop").addEventListener("click", closeDrawer);
  byId("menu-button").addEventListener("click", () => {
    if (byId("sidebar").classList.contains("is-open")) closeNavigation();
    else openNavigation();
  });
  byId("nav-backdrop").addEventListener("click", closeNavigation);
  document.querySelectorAll("[data-view]").forEach((control) => control.addEventListener("click", () => navigate(control.dataset.view)));
  document.querySelectorAll("[data-go-view]").forEach((control) => control.addEventListener("click", () => navigate(control.dataset.goView, { statusFilter: control.dataset.statusFilter || "" })));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeDrawer();
      closeNavigation();
    }
  });
}

bindEvents();
restoreSession();
