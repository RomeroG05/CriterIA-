(() => {
  "use strict";
  const boot = JSON.parse(document.getElementById("bootstrap").textContent);
  const T = boot.i18n;
  const $ = (id) => document.getElementById(id);
  const icon = (name) => `<svg class="i"><use href="#i-${name}"/></svg>`;
  const AVATAR = document.getElementById("app").dataset.avatar;
  const LANGUAGE_LABEL = { es: "Español", en: "English", pt: "Português" };
  const NOTICE_ICON = { ambiguous: "circle-help", out_of_domain: "ban", no_evidence: "file-x", error: "triangle-alert" };

  const state = {
    conversationId: boot.conversationId,
    mode: boot.mode,
    busy: false,
    abort: null,
    guestHistory: [],
    language: boot.uiLanguage,
    booted: false,
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
          button.dataset.n = match[1];
          button.title = `${citation.title}, ${T.page} ${citation.page}`;
          button.addEventListener("click", () => openViewer(citation.chunk_id));
          fragment.append(button);
        } else if (!match || !citation) {
          fragment.append(part);
        } else {
          const span = el("span", "cite-ref", match[1]);
          span.dataset.n = match[1];
          fragment.append(span);
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
    if (state.booted) article.classList.add("is-new");
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
    if (state.booted) article.classList.add("is-new");
    const img = el("img");
    img.src = AVATAR; img.alt = ""; img.width = 32; img.height = 32;
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
      card.dataset.n = String(c.number);
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

  // Al señalar una cita, se ilumina su fuente (y al revés), solo dentro de la misma respuesta.
  function linkSource(event, on) {
    if (event.pointerType === "touch") return;
    const target = event.target.closest?.(".cite-ref[data-n], .src-card[data-n]");
    const body = target?.closest(".msg-body");
    if (!body) return;
    body.querySelectorAll(`.cite-ref[data-n="${target.dataset.n}"], .src-card[data-n="${target.dataset.n}"]`)
      .forEach((node) => node.classList.toggle("is-linked", on));
  }
  list.addEventListener("pointerover", (e) => linkSource(e, true));
  list.addEventListener("pointerout", (e) => linkSource(e, false));
  list.addEventListener("focusin", (e) => linkSource(e, true));
  list.addEventListener("focusout", (e) => linkSource(e, false));

  function renderAsset(view, asset) {
    let box = view.body.querySelector(".assets");
    if (!box) { box = el("div", "assets"); const actions = view.body.querySelector(".actions"); view.body.insertBefore(box, actions); }
    if (box.querySelector(`.asset-item[data-kind="${asset.kind}"]`)) return;
    const isAudio = asset.kind === "audio";
    const item = el("div", `asset-item ${isAudio ? "audio" : "image"}`);
    item.dataset.kind = asset.kind;
    if (isAudio) { const audio = el("audio"); audio.controls = true; audio.src = asset.url; item.append(audio); }
    else { const img = el("img"); img.src = asset.url; img.alt = ""; item.append(img); }
    if (asset.delete_url) {
      const remove = el("button", "asset-del", icon("trash-2"));
      remove.type = "button";
      remove.title = T.removeAsset;
      remove.setAttribute("aria-label", T.removeAsset);
      remove.addEventListener("click", async () => {
        remove.disabled = true;
        const res = await post(asset.delete_url);
        if (res.ok) { item.remove(); if (!box.children.length) box.remove(); }
        else { remove.disabled = false; toast(T.genError); }
      });
      item.append(remove);
    }
    box.append(item);
  }

  function actionButton(name, label, handler) {
    const button = el("button", "icon-btn", icon(name));
    button.type = "button"; button.title = label; button.setAttribute("aria-label", label);
    button.addEventListener("click", () => handler(button));
    return button;
  }

  const VISUALS = [["image", "visualInfographic"], ["concept_map", "visualConceptMap"], ["ishikawa", "visualIshikawa"], ["comparison", "visualComparison"]];

  function visualMenuButton(view, messageId) {
    const wrap = el("div", "visual-wrap");
    const button = actionButton("image", T.image, () => {
      if (!boot.authenticated) return toast(T.registerToGenerate);
      document.querySelectorAll(".visual-menu").forEach((m) => { if (m !== menu) m.hidden = true; });
      menu.hidden = !menu.hidden;
    });
    button.setAttribute("aria-haspopup", "menu");
    const menu = el("div", "visual-menu");
    menu.hidden = true;
    menu.setAttribute("role", "menu");
    menu.append(el("p", "visual-menu-title", T.visualPrompt));
    for (const [kind, label] of VISUALS) {
      const item = el("button");
      item.type = "button";
      item.setAttribute("role", "menuitem");
      item.textContent = T[label];
      item.addEventListener("click", () => { menu.hidden = true; generateHandler(view, messageId, kind)(button); });
      menu.append(item);
    }
    wrap.append(button, menu);
    return wrap;
  }
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".visual-wrap")) document.querySelectorAll(".visual-menu").forEach((m) => (m.hidden = true));
  });

  function generateHandler(view, messageId, kind) {
    return async (button) => {
      if (!boot.authenticated) return toast(T.registerToGenerate);
      if (button.classList.contains("spin")) return;
      button.classList.add("spin");
      const svg = button.innerHTML; button.innerHTML = icon("loader-circle");
      toast(T.generating);
      try {
        const res = await post(`/api/mensajes/${messageId}/generar/${kind}/`);
        const data = await res.json();
        if (res.ok) renderAsset(view, data);
        else toast(data.error === "voice_missing" ? T.voiceMissing : data.error === "not_enough" ? T.notEnough : T.genError);
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
    actions.append(visualMenuButton(view, data.id));
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

  // options.voice: consulta hecha en modo voz (respuesta breve); options.onEvent recibe cada evento del stream.
  // Devuelve cómo terminó: "final", "error" o "aborted".
  async function submit(text, options = {}) {
    text = (text ?? input.value).trim();
    if (!text || state.busy) return "busy";
    $("empty").hidden = true;
    clearSuggestions();
    addUser(text);
    input.value = ""; resizeInput();
    const view = newBotView();
    showStage(view, "analyzing");
    toBottom();
    setBusy(true);
    state.abort = new AbortController();

    let streamed = "", sources = [], raf = 0, ended = false, outcome = "error";
    const paint = () => {
      raf = 0;
      const stick = nearBottom();
      view.content.className = "md";
      renderMarkdown(view.content, streamed, sources);
      if (stick) toBottom();
    };

    const handle = (event) => {
      options.onEvent?.(event);
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
          outcome = "final";
          if (raf) cancelAnimationFrame(raf);
          renderFinal(view, { ...event, id: event.message_id });
          if (!boot.authenticated && event.status === "answer") {
            state.guestHistory.push({ role: "user", content: text }, { role: "assistant", content: event.content, citations: event.citations });
            state.guestHistory = state.guestHistory.slice(-20); // el servidor busca ahí la pregunta original
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
        body: JSON.stringify({ message: text, mode: state.mode, conversation_id: state.conversationId, history: state.guestHistory, language: state.language, voice: !!options.voice }),
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
        outcome = "aborted";
        if (raf) cancelAnimationFrame(raf);
        if (streamed) { view.content.className = "md"; renderMarkdown(view.content, streamed, sources); view.body.append(el("p", "tag", T.stopped)); }
        else { view.article.previousElementSibling?.remove(); view.article.remove(); if (!options.voice) { input.value = text; resizeInput(); } }
      } else {
        showError(view, T.genError, text);
      }
    } finally {
      setBusy(false);
      state.abort = null;
      if (!voice.on) input.focus(); // con el modo voz abierto, el foco se queda en sus controles
      if (nearBottom()) toBottom();
    }
    return outcome;
  }

  // ---------- Modo ----------
  function setMode(mode) {
    state.mode = mode;
    document.querySelectorAll(".modes button, .voice-modes button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.mode === mode)));
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
      const mime = recorder.mimeType || "audio/webm";
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
        form.append("audio", new Blob(chunks, { type: mime }), mime.includes("ogg") ? "voz.ogg" : "voz.webm");
        try {
          const res = await fetch("/api/voz/", { method: "POST", headers: { "X-CSRFToken": csrf() }, body: form });
          const data = await res.json();
          if (res.ok && data.text) { input.value = (input.value + " " + data.text).trim(); resizeInput(); input.focus(); }
          else toast({ silent: T.voiceSilent, too_short: T.voiceShort, no_speech: T.voiceNoSpeech }[data.error] || T.voiceError);
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

  // ---------- Modo voz (solo usuarios registrados) ----------
  // Escucha -> Whisper transcribe -> consulta con respuesta breve -> Piper lee cada frase al completarse -> escucha otra vez.
  // Todo queda escrito en la conversación, con sus fuentes, como cualquier otra consulta.
  const voiceRoot = $("voice-mode"), voiceButton = $("voice"), orb = $("voice-orb");
  const voice = {
    on: false, paused: false, phase: "idle", turn: 0, gen: 0, turns: 0,
    stream: null, ctx: null, micAnalyser: null, outAnalyser: null, micData: null, outData: null, raf: 0, last: 0, level: 0,
    recorder: null, recStart: 0, heard: false, speechMs: 0, silenceMs: 0, bargeMs: 0, echo: 0, floor: 0.008,
    raw: "", consumed: 0, language: "es", queue: [], playing: false, source: null, answerDone: false, endAfter: false,
    themeColors: [],
  };
  const VOICE_STATUS = { listening: T.voiceListening, transcribing: T.transcribing, thinking: T.analyzing, speaking: T.voiceSpeaking, paused: T.voicePaused };
  const VOICE_STAGE = { analyzing: T.analyzing, searching: T.searching, writing: T.writing, translating: T.translating };
  // Lo que se lee en voz alta: sin números de cita ni símbolos de Markdown.
  const speakable = (text) => text
    .replace(/\[\d{1,2}\]/g, "")
    .replace(/^\s*(?:[-*•]|\d+[.)])\s+/gm, "")
    .replace(/[#*_`>|]+/g, "")
    .replace(/\s+/g, " ")
    .replace(/\s+([.,;:!?…])/g, "$1")
    .trim();

  function setPhase(phase, status) {
    voice.phase = phase;
    voiceRoot.dataset.state = phase;
    $("voice-status").textContent = status ?? VOICE_STATUS[phase] ?? "";
    $("voice-hint").textContent = phase === "speaking" ? T.voiceInterrupt : phase === "listening" && !voice.turns ? T.voiceHint : "";
  }

  // Frases completas recibidas hasta ahora (un punto seguido de espacio cierra una frase; «25.326» no).
  // Las muy cortas se juntan con la siguiente para que la entonación suene natural.
  function takeSentences(flush) {
    const rest = voice.raw.slice(voice.consumed);
    const pieces = [];
    const boundary = /[.!?…]+(?=\s)|\n+/g;
    let start = 0, match;
    while ((match = boundary.exec(rest))) {
      const end = match.index + match[0].length;
      if (speakable(rest.slice(start, end)).length >= 18 || match[0][0] === "\n") { pieces.push(rest.slice(start, end)); start = end; }
    }
    if (flush) { pieces.push(rest.slice(start)); start = rest.length; }
    voice.consumed += start;
    return pieces;
  }

  function enqueue(text) {
    const clean = speakable(text);
    if (!/[\p{L}\p{N}]/u.test(clean)) return;
    const audio = fetch("/api/voz/hablar/", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify({ text: clean, language: voice.language }),
    }).then((res) => { if (!res.ok) throw res; return res.arrayBuffer(); })
      .then((data) => voice.ctx.decodeAudioData(data));
    audio.catch(() => {}); // el error se atiende al reproducir
    voice.queue.push({ clean, audio, gen: voice.gen });
    if (!voice.playing) playNext();
  }

  async function playNext() {
    const item = voice.queue.shift();
    if (!item) { voice.playing = false; if (voice.answerDone) answerSpoken(); return; }
    voice.playing = true;
    let buffer;
    try { buffer = await item.audio; }
    catch (error) {
      if (item.gen !== voice.gen) return;
      if (error?.status === 503) $("voice-hint").textContent = T.voiceMissing;
      return playNext();
    }
    if (item.gen !== voice.gen || !voice.on) return;
    if (voice.phase !== "speaking") setPhase("speaking");
    $("voice-said").textContent = item.clean;
    const source = voice.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(voice.outAnalyser);
    source.onended = () => { if (voice.source === source) { voice.source = null; playNext(); } };
    voice.source = source;
    source.start();
  }

  // Corta la voz en curso y descarta las frases pendientes.
  function stopSpeech() {
    voice.gen++;
    voice.queue = [];
    voice.playing = false;
    const source = voice.source;
    voice.source = null;
    try { source?.stop(); } catch {}
  }

  function answerSpoken() {
    if (!voice.on) return;
    if (voice.endAfter) return closeVoice(); // «adiós»: se despide y cierra
    if (voice.paused) setPhase("paused"); else startListening();
  }

  function startRecorder() {
    stopRecorder(true);
    const recorder = new MediaRecorder(voice.stream);
    const chunks = [];
    recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    recorder.onstop = () => { if (!recorder.discard) transcribeVoice(new Blob(chunks, { type: recorder.mimeType || "audio/webm" })); };
    recorder.start();
    Object.assign(voice, { recorder, recStart: performance.now(), heard: false, speechMs: 0, silenceMs: 0 });
  }

  function stopRecorder(discard) {
    const recorder = voice.recorder;
    voice.recorder = null;
    if (recorder && recorder.state !== "inactive") { recorder.discard = discard; recorder.stop(); }
  }

  function startListening() {
    if (!voice.on) return;
    voice.bargeMs = 0;
    voice.echo = 0;
    setPhase("listening");
    startRecorder();
  }

  // Detección de voz por volumen: aprende el ruido de fondo y da la frase por terminada tras 0,9 s de silencio.
  function listenStep(level, dt, now) {
    if (!voice.heard && level < voice.floor * 3) voice.floor = voice.floor * 0.98 + level * 0.02;
    const threshold = Math.max(0.012, voice.floor * 3);
    if (level > threshold) { voice.speechMs += dt; voice.silenceMs = 0; if (voice.speechMs > 200) voice.heard = true; }
    else if (voice.heard) voice.silenceMs += dt;
    else voice.speechMs = Math.max(0, voice.speechMs - dt);
    const length = now - voice.recStart;
    if (voice.heard && (voice.silenceMs > 900 || length > 30000)) { setPhase("transcribing"); stopRecorder(false); }
    else if (!voice.heard && length > 15000) startRecorder(); // sin voz: se reinicia para no acumular silencio
  }

  // Mientras CriterIA habla, una voz clara y sostenida la interrumpe (el navegador cancela el eco de los altavoces).
  function bargeStep(level, dt) {
    if (voice.bargeMs === 0) voice.echo = voice.echo * 0.97 + level * 0.03; // eco residual que se cuela de los altavoces
    const threshold = Math.max(0.05, voice.floor * 6, voice.echo * 2.5);
    voice.bargeMs = level > threshold ? voice.bargeMs + dt : Math.max(0, voice.bargeMs - dt * 2);
    if (voice.bargeMs > 380) interrupt();
  }

  function rms(analyser, data) {
    analyser.getFloatTimeDomainData(data);
    let sum = 0;
    for (const value of data) sum += value * value;
    return Math.sqrt(sum / data.length);
  }

  function voiceLoop(now) {
    if (!voice.on) return;
    const dt = Math.min(100, now - (voice.last || now));
    voice.last = now;
    const mic = rms(voice.micAnalyser, voice.micData), out = rms(voice.outAnalyser, voice.outData);
    if (voice.phase === "listening") listenStep(mic, dt, now);
    else if (voice.phase === "speaking" && !voice.paused) bargeStep(mic, dt);
    const target = voice.phase === "speaking" ? out * 4 : voice.phase === "listening" ? mic * 6 : 0;
    voice.level += (Math.min(1, target) - voice.level) * 0.35;
    orb.style.setProperty("--level", voice.level.toFixed(3));
    voice.raf = requestAnimationFrame(voiceLoop);
  }

  async function transcribeVoice(blob) {
    if (!voice.on) return;
    const turn = ++voice.turn;
    setPhase("transcribing");
    const form = new FormData();
    form.append("audio", blob, blob.type.includes("ogg") ? "voz.ogg" : "voz.webm");
    let data;
    try {
      const res = await fetch("/api/voz/", { method: "POST", headers: { "X-CSRFToken": csrf() }, body: form });
      data = await res.json();
    } catch { data = { error: "transcribe" }; }
    if (turn !== voice.turn || !voice.on) return;
    if (!data.text) {
      // Ruido, tos o una grabación muy corta: se sigue escuchando sin molestar.
      if (!["no_speech", "too_short", "silent"].includes(data.error)) $("voice-said").textContent = T.voiceRetry;
      return voice.paused ? setPhase("paused") : startListening();
    }
    askVoice(data.text, turn);
  }

  async function askVoice(text, turn) {
    $("voice-heard").textContent = `«${text}»`;
    $("voice-said").textContent = "";
    Object.assign(voice, { raw: "", consumed: 0, answerDone: false, endAfter: false, language: state.language });
    voice.turns++;
    setPhase("thinking");
    for (let waited = 0; state.busy && waited < 3000; waited += 50) await new Promise((r) => setTimeout(r, 50));
    if (turn !== voice.turn || !voice.on) return;
    const outcome = await submit(text, { voice: true, onEvent: (event) => onVoiceEvent(event, turn) });
    if (turn !== voice.turn || !voice.on || voice.answerDone) return;
    voice.answerDone = true; // la consulta falló antes de responder
    if (outcome !== "aborted") enqueue(T.voiceFailed);
    if (!voice.playing && !voice.queue.length) answerSpoken();
  }

  function speakAll(content) {
    stopSpeech();
    voice.raw = content;
    voice.consumed = 0;
    takeSentences(true).forEach(enqueue);
  }

  function onVoiceEvent(event, turn) {
    if (turn !== voice.turn || !voice.on) return;
    switch (event.type) {
      case "language":
        voice.language = event.language;
        break;
      case "status":
        if (voice.phase === "thinking") setPhase("thinking", VOICE_STAGE[event.stage]);
        break;
      case "token":
        voice.raw += event.text;
        takeSentences(false).forEach(enqueue);
        break;
      case "final":
        voice.answerDone = true;
        voice.endAfter = event.status === "farewell";
        voice.language = event.language || voice.language;
        // Si la respuesta final no es la que se fue leyendo (otro idioma o sin evidencia), se lee la final.
        if (event.status === "answer" && speakable(voice.raw) === speakable(event.content)) takeSentences(true).forEach(enqueue);
        else speakAll(event.content);
        if (!voice.playing && !voice.queue.length) answerSpoken();
        break;
      case "error":
        voice.answerDone = true;
        speakAll(event.content);
        if (!voice.playing && !voice.queue.length) answerSpoken();
        break;
    }
  }

  // Interrumpir: calla, detiene la respuesta si aún se escribe y vuelve a escuchar.
  function interrupt() {
    voice.turn++;
    stopSpeech();
    if (state.busy) state.abort?.abort();
    if (voice.paused) setPhase("paused"); else startListening();
  }

  function togglePause() {
    voice.paused = !voice.paused;
    const button = $("voice-mic");
    const label = voice.paused ? T.voiceResume : T.voicePause;
    button.setAttribute("aria-pressed", String(voice.paused));
    button.setAttribute("aria-label", label);
    button.title = label;
    button.innerHTML = icon(voice.paused ? "mic-off" : "mic");
    voice.stream.getAudioTracks().forEach((track) => (track.enabled = !voice.paused));
    if (voice.paused && voice.phase === "listening") { stopRecorder(true); setPhase("paused"); }
    else if (!voice.paused && voice.phase === "paused") startListening();
  }

  function setThemeColor(color) {
    document.querySelectorAll('meta[name="theme-color"]').forEach((meta, i) => {
      if (color) { voice.themeColors[i] = meta.content; meta.content = color; }
      else if (voice.themeColors[i]) meta.content = voice.themeColors[i];
    });
  }

  async function openVoice() {
    if (voice.on) return;
    const Context = window.AudioContext || window.webkitAudioContext;
    const ctx = new Context(); // se crea dentro del clic para que el navegador permita reproducir audio
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } }); }
    catch { ctx.close(); return toast(T.micDenied); }
    if (ctx.state === "suspended") await ctx.resume().catch(() => {});
    const micAnalyser = ctx.createAnalyser(), outAnalyser = ctx.createAnalyser(), mute = ctx.createGain();
    micAnalyser.fftSize = outAnalyser.fftSize = 1024;
    mute.gain.value = 0; // el micrófono se analiza pero no se oye
    ctx.createMediaStreamSource(stream).connect(micAnalyser);
    micAnalyser.connect(mute).connect(ctx.destination);
    outAnalyser.connect(ctx.destination);
    Object.assign(voice, {
      on: true, paused: false, turns: 0, stream, ctx, micAnalyser, outAnalyser, level: 0, last: 0, floor: 0.008,
      micData: new Float32Array(1024), outData: new Float32Array(1024), queue: [], playing: false, source: null,
    });
    $("voice-heard").textContent = "";
    $("voice-said").textContent = "";
    const micButton = $("voice-mic");
    micButton.setAttribute("aria-pressed", "false");
    micButton.setAttribute("aria-label", T.voicePause);
    micButton.title = T.voicePause;
    micButton.innerHTML = icon("mic");
    setMode(state.mode);
    closeDrawer();
    app.inert = true;
    document.body.classList.add("voice-open");
    voiceRoot.hidden = false;
    setThemeColor(darkNow() ? "#08121e" : "#0e2742");
    $("voice-end").focus();
    startListening();
    voice.raf = requestAnimationFrame(voiceLoop);
  }

  function closeVoice() {
    if (!voice.on) return;
    voice.on = false;
    voice.turn++;
    stopSpeech();
    stopRecorder(true);
    cancelAnimationFrame(voice.raf);
    voice.stream?.getTracks().forEach((track) => track.stop());
    voice.ctx?.close().catch(() => {});
    voiceRoot.hidden = true;
    voiceRoot.dataset.state = "idle";
    app.inert = false;
    document.body.classList.remove("voice-open");
    setThemeColor(null);
    voiceButton.focus();
  }

  const canTalk = navigator.mediaDevices?.getUserMedia && window.MediaRecorder && (window.AudioContext || window.webkitAudioContext);
  if (!canTalk) voiceButton.hidden = true; // requiere https o localhost
  else voiceButton.addEventListener("click", () => {
    if (!boot.authenticated || !voiceRoot) return toast(T.voiceGuest);
    if (!state.busy) openVoice();
  });
  if (voiceRoot) {
    $("voice-end").addEventListener("click", closeVoice);
    $("voice-mic").addEventListener("click", togglePause);
    orb.addEventListener("click", () => {
      if (voice.phase === "speaking" || voice.phase === "thinking") interrupt();
      else if (voice.phase === "paused") togglePause();
      else if (voice.phase === "listening" && voice.heard) { setPhase("transcribing"); stopRecorder(false); }
    });
    voiceRoot.querySelectorAll(".voice-modes button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
    voiceRoot.addEventListener("keydown", (e) => { if (e.key === "Escape") closeVoice(); });
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
      s.bboxes.forEach(([x0, y0, x1, y1], index) => {
        const mark = el("div", "v-mark");
        Object.assign(mark.style, { left: x0 * 100 + "%", top: y0 * 100 + "%", width: (x1 - x0) * 100 + "%", height: (y1 - y0) * 100 + "%" });
        mark.style.setProperty("--i", Math.min(index, 12));
        marks.append(mark);
      });
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
  const isMobile = () => matchMedia("(max-width: 1023px)").matches;
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
  function applyTheme(next) {
    root.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch {}
    document.querySelectorAll('meta[name="theme-color"]').forEach((m) => (m.content = m.dataset[next] || m.content));
    paintTheme();
  }
  // El tema nuevo se revela en un círculo que nace del botón; sin animación si se pidió menos movimiento.
  $("theme").addEventListener("click", () => {
    const next = darkNow() ? "light" : "dark";
    if (!document.startViewTransition || matchMedia("(prefers-reduced-motion: reduce)").matches) return applyTheme(next);
    const r = $("theme").getBoundingClientRect();
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    const transition = document.startViewTransition(() => applyTheme(next));
    transition.ready.then(() => {
      root.animate(
        { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
        { duration: 380, easing: "cubic-bezier(0.23, 1, 0.32, 1)", pseudoElement: "::view-transition-new(root)" },
      );
    }).catch(() => {});
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
  state.booted = true;
  if (!isMobile()) input.focus();
})();
