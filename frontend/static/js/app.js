"use strict";
// zerde/deploy — progressive enhancement for server-rendered pages. No dependencies.

const S = JSON.parse(document.getElementById("zd-strings")?.textContent || "{}");
const $ = (selector, root = document) => root.querySelector(selector);
const fmt = (text, params) => String(text).replace(/\{(\w+)\}/g, (match, key) => (key in params ? params[key] : match));
const SKIP = new Set([".git", "node_modules", "__MACOSX", ".DS_Store", "Thumbs.db"]);

function humanSize(bytes) {
  const units = S.units || ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  const text = index === 0 || value >= 10 ? String(Math.round(value)) : value.toFixed(1);
  return `${text.replace(".", S.dec || ".")} ${units[index]}`;
}

/* Screen readers hear `text` through the page's one polite live region (#zd-live in base.html).
   Emptied first and filled a moment later, so the same message twice is read twice. */
function announce(text) {
  const live = $("#zd-live");
  if (!live) return;
  live.textContent = "";
  setTimeout(() => { live.textContent = text; }, 100);
}

/* Copy buttons: <button data-copy="text"><svg class="i-copy"><svg class="i-done"><span>Label</span></button>.
   For a moment the label says "Copied" and the icon turns into a check mark, which phones (label hidden) show. */
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  try {
    await navigator.clipboard.writeText(button.dataset.copy);
  } catch {
    return;
  }
  const label = $("span", button);
  label.dataset.label ||= label.textContent;
  label.textContent = S.copied;
  button.classList.add("is-copied");
  announce(S.copied);
  clearTimeout(button.copyTimer);
  button.copyTimer = setTimeout(() => {
    label.textContent = label.dataset.label;
    button.classList.remove("is-copied");
  }, 1500);
});

/* Account menu (<details class="menu">): Escape or a click outside closes it */
{
  const menu = $(".menu");
  if (menu) {
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !menu.open) return;
      const inside = menu.contains(document.activeElement);
      menu.open = false;
      if (inside) $("summary", menu).focus();
    });
    document.addEventListener("click", (event) => {
      if (menu.open && !menu.contains(event.target)) menu.open = false;
    });
  }
}

/* Type-the-name confirmation: <input data-confirm="name"> enables its form's submit button */
for (const input of document.querySelectorAll("[data-confirm]")) {
  const submit = input.form.querySelector("[type=submit]");
  const sync = () => { submit.disabled = input.value.trim().toLowerCase() !== input.dataset.confirm; };
  input.addEventListener("input", sync);
  sync();
}

/* Rate-limit countdown: <button data-wait="seconds" data-label="…" data-wait-label="… {wait} …">.
   Seconds under a minute, else whole minutes rounded up, as i18n.format_wait does on the server.
   Each tick reads the time left off a fixed deadline: background tabs run timers late, not the clock. */
{
  const button = $("[data-wait]");
  const seconds = Number(button?.dataset.wait || 0);
  const deadline = Date.now() + seconds * 1000;
  const wait = (s) => (s < 60 ? fmt(S.waitSec, { n: s }) : fmt(S.waitMin, { n: Math.ceil(s / 60) }));
  const tick = () => {
    const left = deadline - Date.now();
    if (left <= 0) {
      button.disabled = false;
      button.textContent = button.dataset.label;
      return;
    }
    button.disabled = true;
    button.textContent = fmt(button.dataset.waitLabel, { wait: wait(Math.ceil(left / 1000)) });
    setTimeout(tick, left % 1000 || 1000); // next whole second
  };
  if (button && seconds > 0) tick();
}

/* Code field: submit as soon as six digits are typed or pasted */
{
  const field = $(".code-input");
  field?.addEventListener("input", () => {
    if (field.value.replace(/\D/g, "").length === 6) field.form.requestSubmit();
  });
}

/* Sign in with Google (Google Identity Services, popup mode) */
{
  const slot = $("#g-btn");
  const onCredential = async ({ credential }) => {
    try {
      const response = await fetch("/auth/google", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ credential, next: slot.dataset.next }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error);
      location.assign(data.redirect || "/");
    } catch {
      $(".g-error").hidden = false;
    }
  };
  const giveUp = Date.now() + 10_000;
  const start = () => {
    const gis = window.google?.accounts?.id;
    if (!gis) {
      if (Date.now() < giveUp) {
        setTimeout(start, 100);
      } else { // blocked by an extension or the network: offer the email form alone
        slot.hidden = true;
        $(".or").hidden = true;
      }
      return;
    }
    gis.initialize({ client_id: slot.dataset.clientId, callback: onCredential, ux_mode: "popup", itp_support: true });
    gis.renderButton(slot, {
      type: "standard",
      size: "large",
      text: "continue_with",
      shape: "rectangular",
      logo_alignment: "left",
      theme: matchMedia("(prefers-color-scheme: dark)").matches ? "filled_black" : "outline",
      width: Math.min(slot.clientWidth || 400, 400),
      locale: slot.dataset.locale,
    });
  };
  if (slot) start();
}

/* Drop zone: drag a folder, files or a .zip — or pick them — then upload with progress.
   One upload at a time: drops and picks are ignored while one runs. */
{
  const form = $("#drop");
  if (form) {
    const idle = $(".drop-idle", form);
    const busy = $(".drop-busy", form);
    const status = $(".drop-status", form);
    const bar = $(".bar > span", form);
    const error = $(".drop-error", form);
    const cancel = $(".drop-cancel", form);
    const firstPick = $("input[type=file]", form);
    let request = null;
    let round = 0; // Cancel and each new pick bump it, so a folder still being read is not uploaded
    let spoken = 0; // the last quarter of the upload read out to screen readers

    // Switching panels hides what had the focus: Cancel takes it while busy, the first pick button after.
    const show = (isBusy) => {
      const switching = busy.hidden === isBusy;
      idle.hidden = isBusy;
      busy.hidden = !isBusy;
      cancel.hidden = false;
      if (switching) (isBusy ? cancel : firstPick).focus();
    };
    const report = (text) => {
      status.textContent = text;
      announce(text);
    };
    const fail = (message) => {
      show(false);
      error.textContent = message;
      error.hidden = false;
    };
    // The whole body is sent and the server is publishing it: Cancel could no longer stop that.
    const publishing = () => {
      if (busy.hidden) return; // the server already answered (an early refusal)
      if (document.activeElement === cancel) status.focus();
      cancel.hidden = true;
      bar.style.width = "100%";
      report(S.processing);
    };

    const readEntry = (entry, prefix, out) => new Promise((resolve, reject) => {
      if (SKIP.has(entry.name)) return resolve();
      if (entry.isFile) {
        entry.file((file) => { out.push([prefix + file.name, file]); resolve(); }, reject);
        return;
      }
      const reader = entry.createReader();
      const nextBatch = () => reader.readEntries(async (entries) => {
        if (!entries.length) return resolve();
        try {
          for (const child of entries) await readEntry(child, `${prefix}${entry.name}/`, out);
          nextBatch();
        } catch (err) {
          reject(err);
        }
      }, reject);
      nextBatch();
    });

    const collect = async (transfer) => {
      const entries = [...transfer.items].map((item) => item.webkitGetAsEntry?.()).filter(Boolean);
      if (!entries.length) return [...transfer.files].map((file) => [file.name, file]);
      const out = [];
      for (const entry of entries) await readEntry(entry, "", out);
      return out;
    };

    const upload = (files) => {
      error.hidden = true;
      if (!files.length) return fail(S.empty);
      const total = files.reduce((sum, [, file]) => sum + file.size, 0);
      if (files.length > S.maxFiles) return fail(S.tooMany);
      if (total > S.maxBytes) return fail(S.tooBig);
      const data = new FormData();
      for (const [path, file] of files) {
        data.append("files", file, file.name);
        data.append("paths", path);
      }
      show(true);
      bar.style.width = "0%";
      spoken = 0;
      report(fmt(S.uploading, { done: humanSize(0), total: humanSize(total) }));
      request = new XMLHttpRequest();
      request.open("POST", form.action);
      request.setRequestHeader("Accept", "application/json");
      request.upload.onprogress = (event) => {
        if (!event.lengthComputable || event.loaded >= event.total) return;
        const percent = Math.round((event.loaded / Math.max(event.total, 1)) * 100);
        bar.style.width = `${percent}%`;
        status.textContent = fmt(S.uploading, { done: humanSize(event.loaded), total: humanSize(event.total) });
        if (Math.floor(percent / 25) > spoken) { // read out at 25, 50 and 75 %, not on every event
          spoken = Math.floor(percent / 25);
          announce(status.textContent);
        }
      };
      request.upload.onload = publishing;
      request.onload = () => {
        let body = {};
        try { body = JSON.parse(request.responseText); } catch { /* not JSON */ }
        if (request.status === 201) location.assign(body.manage_url);
        else fail(body.error || S.failed);
      };
      request.onerror = () => fail(S.failed);
      request.send(data);
    };

    window.addEventListener("dragover", (event) => event.preventDefault());
    window.addEventListener("drop", (event) => event.preventDefault());
    form.addEventListener("dragover", (event) => {
      event.preventDefault();
      if (busy.hidden) form.classList.add("is-over");
    });
    form.addEventListener("dragleave", (event) => {
      if (!form.contains(event.relatedTarget)) form.classList.remove("is-over");
    });
    form.addEventListener("drop", async (event) => {
      event.preventDefault();
      form.classList.remove("is-over");
      if (!busy.hidden) return;
      const mine = ++round;
      const pending = collect(event.dataTransfer);
      show(true);
      bar.style.width = "0%";
      report(S.reading);
      try {
        const files = await pending;
        if (mine === round) upload(files);
      } catch {
        if (mine === round) fail(S.failed);
      }
    });
    for (const input of form.querySelectorAll("input[type=file]")) {
      input.addEventListener("change", () => {
        const files = [...input.files]
          .map((file) => [file.webkitRelativePath || file.name, file])
          .filter(([path]) => !path.split("/").some((part) => SKIP.has(part)));
        input.value = "";
        if (!busy.hidden) return;
        round += 1;
        upload(files);
      });
    }
    cancel.addEventListener("click", () => {
      round += 1;
      request?.abort();
      show(false);
    });
    // The whole idle zone is a click target: a click on its empty area opens the file picker.
    form.addEventListener("click", (event) => {
      if (!busy.hidden || event.target.closest("label, button, a, input")) return;
      form.querySelector("input[type=file]").click();
    });
  }
}
