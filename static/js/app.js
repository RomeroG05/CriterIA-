(() => {
  "use strict";
  const boot = JSON.parse(document.getElementById("bootstrap").textContent);
  const T = boot.i18n;
  const $ = (id) => document.getElementById(id);
  const icon = (name) => `<svg class="i"><use href="#i-${name}"/></svg>`;
  const LOGO = document.querySelector('link[rel="icon"]').href;
  const LANGUAGE_LABEL = { es: "Español", en: "English", pt: "Português" };
  const NOTICE_ICON = { ambiguous: "circle-help", out_of_domain: "ban", no_evidence: "file-x", error: "triangle-alert" };

  const state = {
    conversationId: boot.conversationId,
    mode: boot.mode,
    busy: false,
    abort: null,
    guestHistory: [],
    language: boot.uiLanguage,
  };

  const app = $("app"), thread = $("thread"), list = $("messages"), input = $("input"), send = $("send");

  // ---------- Utilidades ----------
  function csrf() {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }
  function post(url, body) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() }, body: JSON.stringify(body || {}) });
  }
  let toastTimer;
  function toast(text) {
    const el = $("toast");
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (el.hidden = true), 3200);
  }
  function el(tag, className, html) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (html !== undefined) node.innerHTML = html;
    return node;
  }
  const nearBottom = () => thread.scrollHeight - thread.scrollTop - thread.clientHeight < 140;
  const toBottom = () => { thread.scrollTop = thread.scrollHeight; };

  // ---------- Markdown con citas clicables ----------
  marked.setOptions({ breaks: false, gfm: true });
  function linkCitations(root, citations) {
    const byNumber = new Map(citations.map((c) => [c.number, c]));
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) if (/\[\d{1,2}\]/.test(walker.currentNode.nodeValue) && !walker.currentNode.parentElement.closest("code")) nodes.push(walker.currentNode);
    for (const node of nodes) {
      const fragment = document.createDocumentFragment();
      for (const part of node.nodeValue.split(/(\[\d{1,2}\])/)) {
        const match = part.match(/^\[(\d{1,2})\]$/);
        const citation = match && byNumber.get(Number(match[1]));
        if (citation && citation.chunk_id) {
          const button = el("button", "cite-ref", match[1]);
          button.type = "button";
          button.title = `${citation.title}, ${T.page} ${citation.page}`;
          button.addEventListener("click", () => openViewer(citation.chunk_id));
          fragment.append(button);
        } else if (!match || !citation) {
          fragment.append(part);
        } else {
          fragment.append(el("span", "cite-ref", match[1]));
        }
      }
      node.replaceWith(fragment);
    }
  }
  function renderMarkdown(target, text, citations) {
    target.innerHTML = DOMPurify.sanitize(marked.parse(text), { USE_PROFILES: { html: true } });
    linkCitations(target, citations || []);
  }

  // ---------- Mensajes ----------
  function addUser(text) {
    const article = el("article", "msg user");
    const bubble = el("div", "bubble");
    bubble.textContent = text;
    article.append(bubble);
    list.append(article);
  }

  function metaRow(view, language, mode) {
    view.meta.innerHTML = "<span>CriterIA</span>";
    if (language) {
      const tag = el("span", "tag", LANGUAGE_LABEL[language] || language);
      tag.title = T.languageDetected;
      view.meta.append(tag);
    }
    if (mode) view.meta.append(el("span", "tag mode", T["mode" + mode]));
  }

  function newBotView() {
    const article = el("article", "msg bot");
    const img = el("img");
    img.src = LOGO; img.alt = ""; img.width = 32; img.height = 32;
    const body = el("div", "msg-body");
    const meta = el("div", "msg-meta", "<span>CriterIA</span>");
    const content = el("div", "md");
    body.append(meta, content);
    article.append(img, body);
    list.append(article);
    return { article, body, meta, content };
  }

  function renderSources(view, citations) {
    view.body.querySelectorAll(".sources, .sources-h").forEach((n) => n.remove());
    if (!citations.length) return;
    const heading = el("p", "sources-h", T.sources);
    const grid = el("div", "sources");
    for (const c of citations) {
      const card = el("button", "src-card");
      card.type = "button";
      card.disabled = !c.chunk_id;
      const percent = Math.round(c.similarity * 100);
      const number = el("span", "n", String(c.number));
      const info = el("div");
      const title = el("b"); title.textContent = c.title;
      const detail = el("small"); detail.textContent = `${T.page} ${c.page} · ${percent}% ${T.similarity}`;
      info.append(title, detail);
      card.append(number, info);
      if (c.chunk_id) card.addEventListener("click", () => openViewer(c.chunk_id));
      grid.append(card);
    }
    view.body.append(heading, grid);
  }

  function renderAsset(view, asset) {
    let box = view.body.querySelector(".assets");
    if (!box) { box = el("div", "assets"); const actions = view.body.querySelector(".actions"); view.body.insertBefore(box, actions); }
    if (box.querySelector(asset.kind === "image" ? "img" : "audio")) return;
    if (asset.kind === "image") { const img = el("img"); img.src = asset.url; img.alt = ""; box.append(img); }
    else { const audio = el("audio"); audio.controls = true; audio.src = asset.url; box.append(audio); }
  }

  function actionButton(name, label, handler) {
    const button = el("button", "icon-btn", icon(name));
    button.type = "button"; button.title = label; button.setAttribute("aria-label", label);
    button.addEventListener("click", () => handler(button));
    return button;
  }

  function generateHandler(view, messageId, kind) {
    return async (button) => {
      if (!boot.authenticated) return toast(T.registerToGenerate);
      if (!boot.gemini) return toast(T.geminiMissing);
      if (button.classList.contains("spin")) return;
      button.classList.add("spin");
      const svg = button.innerHTML; button.innerHTML = icon("loader-circle");
      toast(T.generating);
      try {
        const res = await post(`/api/mensajes/${messageId}/generar/${kind}/`);
        const data = await res.json();
        if (res.ok) renderAsset(view, data);
        else toast(data.error === "quota" ? T.quota : data.error === "gemini_missing" ? T.geminiMissing : T.genError);
      } catch { toast(T.genError); }
      button.classList.remove("spin"); button.innerHTML = svg;
    };
  }

  function renderActions(view, data) {
    view.body.querySelectorAll(".actions, .suggest").forEach((n) => n.remove());
    const actions = el("div", "actions");
    actions.append(actionButton("copy", T.copy, (button) => {
      navigator.clipboard.writeText(data.content.replace(/\[\d{1,2}\]/g, "").trim());
      button.innerHTML = icon("check");
      setTimeout(() => (button.innerHTML = icon("copy")), 1400);
    }));
    if (data.id) {
        const vote = (value, name, label) => {
          const button = actionButton(name, label, async () => {
            const next = button.classList.contains("on") ? 0 : value;
            const res = await post(`/api/mensajes/${data.id}/valorar/`, { value: next });
            if (res.ok) { actions.querySelectorAll("[data-vote]").forEach((b) => b.classList.remove("on")); if (next) button.classList.add("on"); }
          });
          button.dataset.vote = value;
          if (data.feedback === value) button.classList.add("on");
          return button;
        };
        actions.append(vote(1, "thumbs-up", T.useful), vote(-1, "thumbs-down", T.notUseful));
    }
    actions.append(actionButton("volume-2", T.audio, generateHandler(view, data.id, "audio")));
    actions.append(actionButton("image", T.image, generateHandler(view, data.id, "image")));
    view.body.append(actions);
    (data.assets || []).forEach((asset) => renderAsset(view, asset));
    if (data.suggestions && data.suggestions.length) {
      const row = el("div", "suggest");
      for (const item of data.suggestions) {
        const chip = el("button", item.end ? "end" : "");
        chip.type = "button"; chip.textContent = item.label;
        chip.addEventListener("click", () => { setMode(item.mode); submit(item.prompt); });
        row.append(chip);
      }
      view.body.append(row);
    }
  }

  function renderFinal(view, data) {
    view.body.querySelector(".translating")?.remove();
    metaRow(view, data.language, data.status === "answer" ? data.mode : null);
    const notice = NOTICE_ICON[data.status];
    if (notice) {
      view.content.className = "";
      view.content.innerHTML = "";
      const box = el("div", `notice-box ${data.status}`, icon(notice));
      const text = el("div", "md");
      renderMarkdown(text, data.content, []);
      box.append(text);
      view.content.append(box);
    } else {
      view.content.className = "md";
      renderMarkdown(view.content, data.content, data.citations);
    }
    renderSources(view, data.citations || []);
    if (data.status === "answer") renderActions(view, data);
  }

  function clearSuggestions() { list.querySelectorAll(".suggest").forEach((n) => n.remove()); }

  // ---------- Envío y recepción por streaming ----------
  function setBusy(busy) {
    state.busy = busy;
    send.innerHTML = icon(busy ? "square" : "send");
    send.setAttribute("aria-label", busy ? "Stop" : "Send");
  }

  function showStage(view, stage) {
    view.content.innerHTML = `<span class="typing"><i></i>${T[stage]}</span>`;
  }

  function showError(view, content, retryText) {
    view.content.className = "";
    view.content.innerHTML = "";
    const box = el("div", "notice-box error", icon("triangle-alert"));
    const text = el("div", "md"); text.textContent = content;
    const retry = el("button", "btn-sm ghost", T.retry);
    retry.type = "button";
    retry.addEventListener("click", () => {
      view.article.previousElementSibling?.remove();
      view.article.remove();
      submit(retryText);
    });
    box.append(text, retry);
    view.content.append(box);
  }

  function addToSidebar(id, title, url) {
    const nav = $("history");
    if (!nav) return;
    let group = nav.querySelector(".hist-group[data-today]") || null;
    if (!group) {
      const first = nav.querySelector(".hist-group");
      if (first && first.querySelector("h3").textContent === T.today) group = first;
      else { group = el("section", "hist-group", ""); const h = el("h3"); h.textContent = T.today; group.append(h); nav.prepend(group); }
    }
    nav.querySelectorAll(".hist-item.on").forEach((n) => n.classList.remove("on"));
    const row = el("div", "hist-item on");
    row.dataset.id = id;
    const a = el("a"); a.href = url; a.textContent = title; a.title = title;
    row.append(a);
    row.insertAdjacentHTML("beforeend", `<button class="icon-btn on-dark" data-act="rename" aria-label="rename">${icon("pencil")}</button><button class="icon-btn on-dark" data-act="delete" aria-label="delete">${icon("trash-2")}</button>`);
    group.querySelector("h3").after(row);
  }

  async function submit(text) {
    text = (text ?? input.value).trim();
    if (!text || state.busy) return;
    $("empty").hidden = true;
    clearSuggestions();
    addUser(text);
    input.value = ""; resizeInput();
    const view = newBotView();
    showStage(view, "analyzing");
    toBottom();
    setBusy(true);
    state.abort = new AbortController();

    let streamed = "", sources = [], raf = 0, ended = false;
    const paint = () => {
      raf = 0;
      const stick = nearBottom();
      view.content.className = "md";
      renderMarkdown(view.content, streamed, sources);
      if (stick) toBottom();
    };

    const handle = (event) => {
      switch (event.type) {
        case "conversation":
          state.conversationId = event.id;
          if (event.new) {
            history.replaceState(null, "", event.url);
            addToSidebar(event.id, event.title, event.url);
            $("top-title").textContent = event.title;
            const exportLink = $("export");
            exportLink.href = `/c/${event.id}/exportar.pdf`;
            exportLink.hidden = false;
          }
          break;
        case "language":
          state.language = event.language;
          metaRow(view, event.language, state.mode);
          break;
        case "status":
          if (!streamed) showStage(view, event.stage);
          else if (event.stage === "translating") view.body.append(el("p", "typing translating", `<i></i>${T.translating}`));
          break;
        case "sources":
          sources = event.citations;
          break;
        case "token":
          streamed += event.text;
          if (!raf) raf = requestAnimationFrame(paint);
          break;
        case "final":
          ended = true;
          if (raf) cancelAnimationFrame(raf);
          renderFinal(view, { ...event, id: event.message_id });
          if (!boot.authenticated && event.status === "answer") {
            state.guestHistory.push({ role: "user", content: text }, { role: "assistant", content: event.content });
            state.guestHistory = state.guestHistory.slice(-4);
          }
          break;
        case "error":
          ended = true;
          if (raf) cancelAnimationFrame(raf);
          if (event.conversation_removed) {
            state.conversationId = null;
            history.replaceState(null, "", "/");
            $("export").hidden = true;
            document.querySelector(".hist-item.on")?.remove();
          }
          showError(view, event.content, text);
          break;
      }
    };

    try {
      const response = await fetch("/api/chat/", {
        method: "POST",
        signal: state.abort.signal,
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
        body: JSON.stringify({ message: text, mode: state.mode, conversation_id: state.conversationId, history: state.guestHistory, language: state.language }),
      });
      if (!response.ok) {
        let message = T.genError;
        try { message = (await response.json()).error || message; } catch {}
        showError(view, message, text);
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let newline;
        while ((newline = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, newline);
          buffer = buffer.slice(newline + 1);
          if (line.trim()) handle(JSON.parse(line));
        }
      }
      if (!ended) showError(view, T.genError, text);
    } catch (error) {
      if (error.name === "AbortError") {
        if (raf) cancelAnimationFrame(raf);
        if (streamed) { view.content.className = "md"; renderMarkdown(view.content, streamed, sources); view.body.append(el("p", "tag", T.stopped)); }
        else { view.article.previousElementSibling?.remove(); view.article.remove(); input.value = text; resizeInput(); }
      } else {
        showError(view, T.genError, text);
      }
    } finally {
      setBusy(false);
      state.abort = null;
      input.focus();
      if (nearBottom()) toBottom();
    }
  }

  // ---------- Modo ----------
  function setMode(mode) {
    state.mode = mode;
    document.querySelectorAll(".modes button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.mode === mode)));
    input.placeholder = T["placeholder" + mode];
  }
  document.querySelectorAll(".modes button").forEach((b) => b.addEventListener("click", () => { setMode(b.dataset.mode); input.focus(); }));
  document.querySelectorAll(".mode-card").forEach((b) => b.addEventListener("click", () => { setMode(b.dataset.mode); input.focus(); }));
  document.querySelectorAll("[data-principle]").forEach((b) => b.addEventListener("click", () => {
    const template = state.mode === "B" ? T.casePrinciple : state.mode === "C" ? T.dilemmaPrinciple : T.conceptPrinciple;
    input.value = template.replace("%(name)s", b.dataset.principle);
    resizeInput();
    closeDrawer();
    input.focus();
  }));

  // ---------- Compositor ----------
  function resizeInput() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 180) + "px";
  }
  input.addEventListener("input", resizeInput);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!state.busy) submit(); }
  });
  $("composer").addEventListener("submit", (e) => {
    e.preventDefault();
    if (state.busy) state.abort?.abort(); else submit();
  });

  // ---------- Voz local ----------
  const mic = $("mic");
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    mic.hidden = true; // requiere https o localhost
  } else {
    let recorder = null, stopTimer;
    mic.addEventListener("click", async () => {
      if (recorder) { recorder.stop(); return; }
      let stream;
      try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
      catch { return toast(T.micDenied); }
      const chunks = [];
      recorder = new MediaRecorder(stream);
      recorder.ondataavailable = (e) => chunks.push(e.data);
      recorder.onstop = async () => {
        clearTimeout(stopTimer);
        stream.getTracks().forEach((t) => t.stop());
        recorder = null;
        mic.classList.remove("rec");
        mic.disabled = true;
        const previous = input.placeholder;
        input.placeholder = T.transcribing;
        const form = new FormData();
        form.append("audio", new Blob(chunks, { type: "audio/webm" }), "voz.webm");
        try {
          const res = await fetch("/api/voz/", { method: "POST", headers: { "X-CSRFToken": csrf() }, body: form });
          const data = await res.json();
          if (res.ok && data.text) { input.value = (input.value + " " + data.text).trim(); resizeInput(); input.focus(); }
          else toast(T.voiceError);
        } catch { toast(T.voiceError); }
        input.placeholder = previous;
        mic.disabled = false;
      };
      recorder.start();
      mic.classList.add("rec");
      toast(T.recording);
      stopTimer = setTimeout(() => recorder?.stop(), 60000);
    });
  }

  // ---------- Visor de fuentes y citas ----------
  const viewer = { source: null, page: 1, zoom: 100 };

  function showPage() {
    const s = viewer.source;
    $("v-img").src = `/corpus/${s.document_id}/pagina/${viewer.page}.jpg`;
    $("v-page").textContent = `${viewer.page} / ${s.pages}`;
    $("v-prev").disabled = viewer.page <= 1;
    $("v-next").disabled = viewer.page >= s.pages;
    $("v-open").href = `/corpus/${s.document_id}/archivo/#page=${viewer.page}`;
    const marks = $("v-marks");
    marks.innerHTML = "";
    if (viewer.page === s.page) {
      for (const [x0, y0, x1, y1] of s.bboxes) {
        const mark = el("div", "v-mark");
        Object.assign(mark.style, { left: x0 * 100 + "%", top: y0 * 100 + "%", width: (x1 - x0) * 100 + "%", height: (y1 - y0) * 100 + "%" });
        marks.append(mark);
      }
    }
  }

  async function openViewer(chunkId) {
    let res;
    try { res = await fetch(`/corpus/fuente/${chunkId}/`); } catch { return toast(T.genError); }
    if (!res.ok) return toast(T.genError);
    const s = await res.json();
    viewer.source = s; viewer.page = s.page; viewer.zoom = 100;
    $("v-sheet").style.width = "100%";
    $("v-title").textContent = s.title;
    $("v-quote").textContent = `«${s.text}»`;
    $("v-meta").textContent = [s.authors, s.year, `SHA-256 ${s.sha256.slice(0, 16)}…`].filter(Boolean).join(" · ");
    $("viewer").hidden = false;
    showPage();
    const first = s.bboxes[0];
    $("v-img").onload = () => { if (first && viewer.page === s.page) { const doc = $("v-doc"); doc.scrollTop = first[1] * $("v-sheet").offsetHeight - 40; } };
  }
  $("v-close").addEventListener("click", () => { $("viewer").hidden = true; });
  $("v-prev").addEventListener("click", () => { viewer.page--; showPage(); });
  $("v-next").addEventListener("click", () => { viewer.page++; showPage(); });
  function zoom(delta) { viewer.zoom = Math.max(70, Math.min(220, viewer.zoom + delta)); $("v-sheet").style.width = viewer.zoom + "%"; }
  $("v-in").addEventListener("click", () => zoom(20));
  $("v-out").addEventListener("click", () => zoom(-20));

  const modal = $("cite-modal");
  let currentStyle = null;
  function showStyle(style) {
    currentStyle = style;
    const box = $("cite-box");
    box.className = "cite-box" + (style.code ? " code" : "");
    box.innerHTML = style.html; // el servidor escapa todos los metadatos
    $("cite-tabs").querySelectorAll("button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.id === style.id)));
  }
  $("v-cite").addEventListener("click", () => {
    const citation = viewer.source.citation;
    const tabs = $("cite-tabs");
    tabs.innerHTML = "";
    for (const style of citation.styles) {
      const tab = el("button"); tab.type = "button"; tab.role = "tab"; tab.dataset.id = style.id; tab.textContent = style.label;
      tab.addEventListener("click", () => showStyle(style));
      tabs.append(tab);
    }
    showStyle(citation.styles[0]);
    $("cite-intext").textContent = `${T.inText}: ${citation.in_text}`;
    $("cite-warn").textContent = T.incompleteCitation;
    $("cite-warn").hidden = !citation.incomplete;
    modal.showModal();
  });
  $("cite-close").addEventListener("click", () => modal.close());
  modal.addEventListener("click", (e) => { if (e.target === modal) modal.close(); });
  $("cite-copy").addEventListener("click", async () => {
    await navigator.clipboard.writeText($("cite-box").textContent);
    const label = $("cite-copy").querySelector("span");
    const previous = label.textContent;
    label.textContent = T.copied;
    setTimeout(() => (label.textContent = previous), 1400);
  });

  // ---------- Barra lateral ----------
  const scrim = $("scrim");
  const isMobile = () => matchMedia("(max-width: 860px)").matches;
  function closeDrawer() { app.classList.remove("drawer"); scrim.hidden = true; }
  $("sb-open").addEventListener("click", () => {
    if (isMobile()) { app.classList.add("drawer"); scrim.hidden = false; }
    else app.classList.remove("collapsed");
  });
  $("sb-close").addEventListener("click", () => { if (isMobile()) closeDrawer(); else app.classList.add("collapsed"); });
  scrim.addEventListener("click", closeDrawer);

  $("hist-search")?.addEventListener("input", (e) => {
    const query = e.target.value.toLowerCase();
    document.querySelectorAll(".hist-group").forEach((group) => {
      let visible = 0;
      group.querySelectorAll(".hist-item").forEach((item) => {
        const match = item.textContent.toLowerCase().includes(query);
        item.hidden = !match;
        visible += match;
      });
      group.hidden = !visible;
    });
  });
  $("history")?.addEventListener("click", async (e) => {
    const button = e.target.closest("[data-act]");
    if (!button) return;
    const item = button.closest(".hist-item");
    const id = item.dataset.id;
    if (button.dataset.act === "delete") {
      if (!confirm(T.confirmDelete)) return;
      const res = await post(`/c/${id}/eliminar/`);
      if (res.ok) {
        if (id === state.conversationId) { location.href = "/"; return; }
        const group = item.closest(".hist-group");
        item.remove();
        if (!group.querySelector(".hist-item")) group.remove();
      }
    } else {
      const link = item.querySelector("a");
      const title = prompt(T.rename, link.textContent);
      if (!title || !title.trim()) return;
      const res = await post(`/c/${id}/renombrar/`, { title });
      if (res.ok) {
        link.textContent = (await res.json()).title;
        if (id === state.conversationId) $("top-title").textContent = link.textContent;
      }
    }
  });

  // ---------- Tema ----------
  const root = document.documentElement;
  const darkNow = () => root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  function paintTheme() { $("theme").innerHTML = icon(darkNow() ? "sun" : "moon"); }
  $("theme").addEventListener("click", () => {
    root.dataset.theme = darkNow() ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch {}
    paintTheme();
  });
  paintTheme();

  // ---------- Inicio ----------
  async function loadStatus() {
    const pill = $("status");
    try {
      const data = await (await fetch("/api/estado/")).json();
      pill.className = "status " + (data.online ? "ok" : "bad");
      pill.querySelector("span").textContent = (data.online ? T.aiOnline : T.aiOffline) + ` · ${data.documents} ${T.documents}`;
    } catch { pill.className = "status bad"; pill.querySelector("span").textContent = T.aiOffline; }
  }

  setMode(state.mode);
  setBusy(false);
  loadStatus();
  document.querySelectorAll("[data-flash]").forEach((n) => toast(n.textContent));
  for (const message of boot.messages) {
    if (message.role === "user") addUser(message.content);
    else {
      const view = newBotView();
      if (message.status === "stopped") {
        view.content.className = "md";
        renderMarkdown(view.content, message.content, []);
        metaRow(view, message.language, null);
        view.body.append(el("p", "tag", T.stopped));
      } else renderFinal(view, message);
    }
  }
  if (boot.messages.length) toBottom();
  if (!isMobile()) input.focus();
})();
