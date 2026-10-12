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

/* Copy buttons: <button data-copy="text"><span>Label</span></button> */
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  try {
    await navigator.clipboard.writeText(button.dataset.copy);
  } catch {
    return;
  }
  const label = button.querySelector("span") || button;
  const original = label.textContent;
  label.textContent = S.copied;
  setTimeout(() => { label.textContent = original; }, 1500);
});

/* Type-the-name confirmation: <input data-confirm="name"> enables its form's submit button */
for (const input of document.querySelectorAll("[data-confirm]")) {
  const submit = input.form.querySelector("[type=submit]");
  const sync = () => { submit.disabled = input.value.trim().toLowerCase() !== input.dataset.confirm; };
  input.addEventListener("input", sync);
  sync();
}

/* Rate-limit countdown: <button data-wait="seconds" data-label="…" data-wait-label="… {wait} …">.
   Seconds under a minute, else whole minutes rounded up, as i18n.format_wait does on the server. */
{
  const button = $("[data-wait]");
  let left = Number(button?.dataset.wait || 0);
  const wait = (s) => (s < 60 ? fmt(S.waitSec, { n: s }) : fmt(S.waitMin, { n: Math.ceil(s / 60) }));
  const tick = () => {
    if (left <= 0) {
      button.disabled = false;
      button.textContent = button.dataset.label;
      return;
    }
    button.disabled = true;
    button.textContent = fmt(button.dataset.waitLabel, { wait: wait(left) });
    left -= 1;
    setTimeout(tick, 1000);
  };
  if (button && left > 0) tick();
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
  let tries = 0;
  const start = () => {
    const gis = window.google?.accounts?.id;
    if (!gis) {
      if (++tries < 100) setTimeout(start, 100);
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

/* Drop zone: drag a folder, files or a .zip — or pick them — then upload with progress */
{
  const form = $("#drop");
  if (form) {
    const idle = $(".drop-idle", form);
    const busy = $(".drop-busy", form);
    const status = $(".drop-status", form);
    const bar = $(".bar > span", form);
    const error = $(".drop-error", form);
    let request = null;

    const show = (isBusy) => { idle.hidden = isBusy; busy.hidden = !isBusy; };
    const fail = (message) => {
      show(false);
      error.textContent = message;
      error.hidden = false;
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
      request = new XMLHttpRequest();
      request.open("POST", form.action);
      request.setRequestHeader("Accept", "application/json");
      request.upload.onprogress = (event) => {
        const all = event.lengthComputable ? event.total : total;
        const done = event.lengthComputable ? event.loaded : 0;
        bar.style.width = `${Math.round((done / Math.max(all, 1)) * 100)}%`;
        status.textContent = done >= all ? S.processing : fmt(S.uploading, { done: humanSize(done), total: humanSize(all) });
      };
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
      form.classList.add("is-over");
    });
    form.addEventListener("dragleave", (event) => {
      if (!form.contains(event.relatedTarget)) form.classList.remove("is-over");
    });
    form.addEventListener("drop", async (event) => {
      event.preventDefault();
      form.classList.remove("is-over");
      const pending = collect(event.dataTransfer);
      show(true);
      status.textContent = S.reading;
      bar.style.width = "0%";
      try {
        upload(await pending);
      } catch {
        fail(S.failed);
      }
    });
    for (const input of form.querySelectorAll("input[type=file]")) {
      input.addEventListener("change", () => {
        const files = [...input.files]
          .map((file) => [file.webkitRelativePath || file.name, file])
          .filter(([path]) => !path.split("/").some((part) => SKIP.has(part)));
        input.value = "";
        upload(files);
      });
    }
    $(".drop-cancel", form).addEventListener("click", () => {
      request?.abort();
      show(false);
    });
  }
}
