(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const state = { config: null, step: 0, totalSteps: 6, submitting: false, submissionToken: null };
  const stepNames = ["Welcome", "Client information", "Citizen's Charter", "Service quality", "Comments", "Review"];

  document.addEventListener("DOMContentLoaded", initialize);

  async function initialize() {
    $("#backButton").addEventListener("click", () => moveStep(-1));
    $("#nextButton").addEventListener("click", () => moveStep(1));
    $("#surveyForm").addEventListener("submit", submitSurvey);
    try {
      const response = await fetch("/api/config", { cache: "no-store" });
      const config = await response.json();
      if (!response.ok || !config.ok) throw new Error(config.error || "Unable to load survey configuration.");
      state.config = config;
      $("#schoolName").textContent = config.school_name;
      $("#privacyNotice").textContent = config.privacy_notice;
      if (!config.accepting_responses) {
        showClosed("The survey is currently closed by the CSM control center.");
        return;
      }
      renderSurvey(config);
      $("#loadingPanel").classList.add("hidden");
      $("#surveyForm").classList.remove("hidden");
      updateStep();
    } catch (error) {
      showClosed(error.message || "The local survey server could not be reached.");
    }
  }

  function showClosed(message) {
    $("#loadingPanel").classList.add("hidden");
    $("#closedMessage").textContent = message;
    $("#closedPanel").classList.remove("hidden");
  }

  function renderSurvey(config) {
    const spec = config.questionnaire;
    renderMetaFields(spec.meta_fields, config.service_sections);
    renderCC(spec.cc_questions);
    renderRatings(spec.sqd_questions, spec.rating_options);
    $("#feedbackLabel").textContent = spec.feedback_label;
    $("#emailField").classList.toggle("hidden", config.mode !== "onsite");
  }

  function renderMetaFields(fields, serviceSections) {
    const root = $("#metaFields");
    root.innerHTML = "";
    fields.filter(field => !["control_number", "survey_date", "email"].includes(field.key)).forEach(field => {
      const group = document.createElement("div");
      group.className = "field-group";
      const label = document.createElement("label");
      label.className = "field-label";
      label.innerHTML = `${escapeHtml(field.label)} <span class="optional">Optional</span>`;
      group.appendChild(label);

      if (field.type === "choice") {
        group.appendChild(choiceGrid(`meta_${field.key}`, field.options.map(value => ({ value, label: value }))));
      } else if (field.type === "integer") {
        const input = document.createElement("input");
        input.type = "number"; input.inputMode = "numeric"; input.min = "0"; input.max = "130";
        input.id = `meta_${field.key}`; input.placeholder = "Not provided";
        group.appendChild(input);
      } else if (field.type === "multi_choice") {
        const serviceRoot = document.createElement("div");
        serviceRoot.id = "meta_service_availed";
        serviceSections.forEach(section => {
          const box = document.createElement("div"); box.className = "service-group";
          box.innerHTML = `<h3>${escapeHtml(section.label)}</h3>`;
          section.options.forEach(option => {
            const row = document.createElement("label"); row.className = "check-row";
            row.innerHTML = `<input type="checkbox" name="meta_service_availed" value="${escapeAttribute(option)}"><span>${escapeHtml(option)}</span>`;
            box.appendChild(row);
          });
          serviceRoot.appendChild(box);
        });
        const other = document.createElement("div"); other.className = "field-group"; other.style.marginTop = "10px";
        other.innerHTML = `<label class="field-label" for="meta_service_other">Other school transaction <span class="optional">Optional</span></label><input id="meta_service_other" type="text" maxlength="180" placeholder="Specify another service">`;
        serviceRoot.appendChild(other);
        group.appendChild(serviceRoot);
      } else {
        const input = document.createElement("input");
        input.type = field.type === "email" ? "email" : "text";
        input.id = `meta_${field.key}`; input.maxLength = 180;
        group.appendChild(input);
      }
      root.appendChild(group);
    });
  }

  function renderCC(questions) {
    const root = $("#ccQuestions"); root.innerHTML = "";
    Object.entries(questions).forEach(([key, question]) => {
      const card = document.createElement("div"); card.className = "question-card"; card.dataset.ccKey = key;
      card.innerHTML = `<span class="question-code">${escapeHtml(question.code)}</span><p class="question-title">${escapeHtml(question.prompt)}</p>`;
      card.appendChild(choiceGrid(`cc_${key}`, question.options));
      if (question.allows_reason) {
        const reason = document.createElement("div"); reason.className = "field-group hidden"; reason.id = "cc3ReasonGroup"; reason.style.marginTop = "12px";
        reason.innerHTML = `<label class="field-label" for="cc3Reason">Reason <span class="optional">Optional</span></label><textarea id="cc3Reason" rows="3" maxlength="500" placeholder="Briefly state why the Citizen's Charter was not used."></textarea>`;
        card.appendChild(reason);
      }
      root.appendChild(card);
    });
    $$('input[name^="cc_"]').forEach(input => input.addEventListener("change", applyCCBranching));
  }

  function renderRatings(questions, options) {
    const legend = $("#ratingLegend");
    legend.innerHTML = options.map(option => `<span class="legend-chip"><strong>${escapeHtml(option.short_label)}</strong> · ${escapeHtml(option.label)}</span>`).join("");
    const root = $("#sqdQuestions"); root.innerHTML = "";
    Object.entries(questions).forEach(([key, question]) => {
      const card = document.createElement("div"); card.className = "question-card";
      card.innerHTML = `<span class="question-code">${escapeHtml(question.code)} · ${escapeHtml(question.dimension)}</span><p class="question-title">${escapeHtml(question.prompt)}</p>`;
      const grid = document.createElement("div"); grid.className = "rating-grid";
      options.forEach(option => {
        const label = document.createElement("label"); label.className = "choice-card rating-choice";
        label.innerHTML = `<input type="radio" name="sqd_${key}" value="${option.value}"><span><span><b>${escapeHtml(option.short_label)}</b>${escapeHtml(option.label)}</span></span>`;
        grid.appendChild(label);
      });
      card.appendChild(grid); root.appendChild(card);
    });
  }

  function choiceGrid(name, options) {
    const grid = document.createElement("div"); grid.className = "choice-grid one";
    options.forEach(option => {
      const label = document.createElement("label"); label.className = "choice-card";
      label.innerHTML = `<input type="radio" name="${escapeAttribute(name)}" value="${escapeAttribute(option.value)}"><span>${escapeHtml(option.label)}</span>`;
      grid.appendChild(label);
    });
    return grid;
  }

  function applyCCBranching() {
    const mode = state.config.mode;
    const cc1 = selectedValue("cc_cc1");
    const cc2 = selectedValue("cc_cc2");
    const cc2Card = $('[data-cc-key="cc2"]');
    const cc3Card = $('[data-cc-key="cc3"]');
    let showCC2 = true, showCC3 = true;
    if (mode === "onsite" && cc1 === "4") { showCC2 = false; showCC3 = false; }
    if (mode === "online" && cc1 === "3") { showCC2 = false; showCC3 = false; }
    if (mode === "online" && cc2 === "3") showCC3 = false;
    if (cc2Card) cc2Card.classList.toggle("hidden", !showCC2);
    if (cc3Card) cc3Card.classList.toggle("hidden", !showCC3);
    const reason = $("#cc3ReasonGroup");
    if (reason) reason.classList.toggle("hidden", !(showCC3 && selectedValue("cc_cc3") === "2"));
  }

  function moveStep(delta) {
    clearError();
    if (delta > 0 && !validateStep(state.step)) return;
    state.step = Math.max(0, Math.min(state.totalSteps - 1, state.step + delta));
    if (state.step === state.totalSteps - 1) renderReview();
    updateStep();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function updateStep() {
    $$(".step").forEach((step, index) => step.classList.toggle("active", index === state.step));
    $("#stepLabel").textContent = stepNames[state.step];
    $("#progressText").textContent = `${state.step + 1} of ${state.totalSteps}`;
    $("#progressBar").style.width = `${((state.step + 1) / state.totalSteps) * 100}%`;
    $("#backButton").classList.toggle("hidden", state.step === 0);
    $("#nextButton").classList.toggle("hidden", state.step === state.totalSteps - 1);
    $("#submitButton").classList.toggle("hidden", state.step !== state.totalSteps - 1);
  }

  function validateStep(step) {
    if (step === 0 && !$("#consent").checked) return showError("Please confirm that you have read the privacy notice.");
    if (step === 1) {
      const age = $("#meta_age");
      if (age && age.value && (+age.value < 0 || +age.value > 130)) return showError("Age must be between 0 and 130.");
    }
    if (step === 2) {
      const mode = state.config.mode;
      const cc1 = selectedValue("cc_cc1");
      const cc2 = selectedValue("cc_cc2");
      if (!cc1) return showError("Please answer CC1.");
      if (mode === "onsite" && cc1 !== "4" && (!cc2 || !selectedValue("cc_cc3"))) return showError("Please complete the Citizen's Charter questions.");
      if (mode === "online" && cc1 !== "3" && !cc2) return showError("Please answer CC2.");
      if (mode === "online" && cc1 !== "3" && cc2 !== "3" && !selectedValue("cc_cc3")) return showError("Please answer CC3.");
    }
    if (step === 3) {
      const missing = Object.keys(state.config.questionnaire.sqd_questions).filter(key => !selectedValue(`sqd_${key}`));
      if (missing.length) return showError(`Please answer all service-quality items: ${missing.map(key => key.toUpperCase()).join(", ")}.`);
    }
    const email = $("#feedbackEmail");
    if (step === 4 && email && email.value && !email.checkValidity()) return showError("Please enter a valid email address or leave it blank.");
    return true;
  }

  function collectData() {
    const meta = {};
    state.config.questionnaire.meta_fields.forEach(field => {
      if (["control_number", "survey_date", "email"].includes(field.key)) return;
      if (field.type === "choice") {
        const value = selectedValue(`meta_${field.key}`); if (value) meta[field.key] = value;
      } else if (field.type === "integer") {
        const input = $(`#meta_${field.key}`); if (input && input.value !== "") meta[field.key] = Number(input.value);
      } else if (field.type === "multi_choice") {
        const values = $$('input[name="meta_service_availed"]:checked').map(input => input.value);
        const other = $("#meta_service_other")?.value.trim(); if (other) values.push(other);
        meta[field.key] = values;
      } else {
        const input = $(`#meta_${field.key}`); if (input && input.value.trim()) meta[field.key] = input.value.trim();
      }
    });
    const cc = {};
    ["cc1", "cc2", "cc3"].forEach(key => { const value = selectedValue(`cc_${key}`); if (value) cc[key] = Number(value); });
    if (state.config.mode === "onsite" && cc.cc1 === 4) { cc.cc2 = 5; cc.cc3 = 4; }
    if (state.config.mode === "online" && cc.cc1 === 3) { delete cc.cc2; delete cc.cc3; }
    if (state.config.mode === "online" && cc.cc2 === 3) { delete cc.cc3; }
    if ($("#cc3Reason")?.value.trim() && cc.cc3 === 2) cc.cc3_reason = $("#cc3Reason").value.trim();
    const sqd = {};
    Object.keys(state.config.questionnaire.sqd_questions).forEach(key => { const value = selectedValue(`sqd_${key}`); if (value !== null) sqd[key] = Number(value); });
    return {
      mode: state.config.mode,
      submission_token: state.submissionToken || (state.submissionToken = createToken()),
      meta,
      cc,
      sqd,
      feedback: { comments: $("#comments").value.trim(), email: $("#feedbackEmail")?.value.trim() || "" }
    };
  }

  function renderReview() {
    const data = collectData();
    const root = $("#reviewContent"); root.innerHTML = "";
    const clientRows = [];
    Object.entries(data.meta).forEach(([key, value]) => clientRows.push([titleCase(key.replaceAll("_", " ")), Array.isArray(value) ? (value.join("; ") || "Not provided") : value]));
    root.appendChild(reviewSection("Client and transaction", clientRows.length ? clientRows : [["Information", "Not provided"]]));
    const ccRows = ["cc1", "cc2", "cc3"].filter(key => data.cc[key] !== undefined).map(key => [key.toUpperCase(), optionLabel(state.config.questionnaire.cc_questions[key].options, data.cc[key])]);
    root.appendChild(reviewSection("Citizen's Charter", ccRows));
    const sqdRows = Object.entries(data.sqd).map(([key, value]) => [key.toUpperCase(), optionLabel(state.config.questionnaire.rating_options, value)]);
    root.appendChild(reviewSection("Service quality", sqdRows));
    root.appendChild(reviewSection("Comments", [["Feedback", data.feedback.comments || "None"], ["Email", data.feedback.email || "Not provided"]]));
  }

  function reviewSection(title, rows) {
    const box = document.createElement("section"); box.className = "review-section"; box.innerHTML = `<h3>${escapeHtml(title)}</h3>`;
    rows.forEach(([label, value]) => { const row = document.createElement("div"); row.className = "review-row"; row.innerHTML = `<span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value ?? ""))}</strong>`; box.appendChild(row); });
    return box;
  }

  async function submitSurvey(event) {
    event.preventDefault();
    clearError();
    if (state.submitting || !validateStep(4) || !validateStep(2) || !validateStep(3)) return;
    state.submitting = true;
    const button = $("#submitButton"); button.disabled = true; button.textContent = "Submitting…";
    const payload = collectData();
    try {
      const response = await fetch("/api/submit", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || "The response could not be submitted.");
      $("#surveyForm").classList.add("hidden");
      $("#successMessage").textContent = result.message || "Your response was submitted successfully.";
      if (result.control_number) { $("#referenceNumber").textContent = result.control_number; $("#referenceCard").classList.remove("hidden"); }
      $("#successPanel").classList.remove("hidden");
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (error) {
      showError(error.message || "The response could not be submitted. Check the Wi-Fi connection and try again.");
      button.disabled = false; button.textContent = "Submit response"; state.submitting = false;
    }
  }

  function selectedValue(name) { return document.querySelector(`input[name="${CSS.escape(name)}"]:checked`)?.value ?? null; }
  function optionLabel(options, value) { return options.find(option => String(option.value) === String(value))?.label || String(value); }
  function showError(message) { const banner = $("#errorBanner"); banner.textContent = message; banner.classList.remove("hidden"); banner.scrollIntoView({ behavior: "smooth", block: "center" }); return false; }
  function clearError() { $("#errorBanner").classList.add("hidden"); $("#errorBanner").textContent = ""; }
  function createToken() { return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`; }
  function titleCase(text) { return text.replace(/\b\w/g, char => char.toUpperCase()); }
  function escapeHtml(value) { return String(value ?? "").replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char])); }
  function escapeAttribute(value) { return escapeHtml(value); }
})();
