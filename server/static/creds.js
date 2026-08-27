"use strict";

// ---------------------------------------------------------------------------
// Webin credentials — this browser tab only. sessionStorage, never localStorage:
// closing the tab should forget them.
// ---------------------------------------------------------------------------
const CREDS_KEY = "ena-browser-ui.creds";

function credsConfigured() { return !!(CREDS.username && CREDS.password); }

function reflectCredStatus() {
  const el = $("credStatus");
  el.textContent = "credentials: " + (credsConfigured() ? "set" : "not set");
  el.className = "creds-status " + (credsConfigured() ? "on" : "");
}

function restoreCreds() {
  try {
    const raw = sessionStorage.getItem(CREDS_KEY);
    if (raw) CREDS = JSON.parse(raw);
  } catch { CREDS = { username: "", password: "" }; }
  $("username").value = CREDS.username || "";
  reflectCredStatus();
}

$("credSave").onclick = () => {
  const username = $("username").value.trim();
  const password = $("password").value;
  if (!username || !password) { banner("bad", "Enter a Webin username and password."); return; }
  CREDS = { username, password };
  sessionStorage.setItem(CREDS_KEY, JSON.stringify(CREDS));
  $("password").value = "";
  reflectCredStatus();
  banner("ok", `Credentials saved for this browser tab. Loading ${ENTITY} from ${envLabel()}…`);
  loadEntity();
};

$("credClear").onclick = () => {
  CREDS = { username: "", password: "" };
  sessionStorage.removeItem(CREDS_KEY);
  $("password").value = "";
  reflectCredStatus();
  banner("ok", "Credentials cleared.");
};

$("credValidate").onclick = async () => {
  if (!credsConfigured()) { banner("bad", "Save a username and password first."); return; }
  try {
    await api("/api/credentials/validate", { method: "POST", body: "{}" });
    banner("ok", `Credentials are valid for ${envLabel()}.`);
  } catch (error) {
    banner("bad", `Credentials rejected by ${envLabel()}: ${error.message}`);
  }
};
