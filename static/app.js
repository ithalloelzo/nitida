(function () {
  "use strict";
  var $ = function (s) { return document.querySelector(s); };
  var LABEL = {
    style: { art: "Desenho", photo: "Foto" },
    noise: { "-1": "Sem redução", "0": "Ruído baixo", "1": "Ruído médio", "2": "Ruído alto", "3": "Ruído máximo" },
    format: { "default": "Formato original", webp: "WebP", jpg: "JPG", png: "PNG" },
    status: { pending: "Aguardando você", uploading: "Enviando…", queued: "Na fila", processing: "Processando", done: "Pronto", error: "Falhou", cancelled: "Cancelado" }
  };
  var SETTINGS_KEY = "nitida.settings";
  var queue = [];          // itens desta sessão
  var limits = null;

  // ---------- utilidades
  function fmtBytes(n) {
    if (n == null) return "–";
    if (n < 1024) return n + " B";
    var u = ["KB", "MB", "GB"], i = -1;
    do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
    return n.toFixed(n < 10 ? 1 : 0) + " " + u[i];
  }
  function fmtTime(s) {
    if (s == null) return "";
    return s < 60 ? Math.round(s) + " s" : Math.floor(s / 60) + " min " + Math.round(s % 60) + " s";
  }
  function toast(msg) {
    var t = $("#toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(toast.timer); toast.timer = setTimeout(function () { t.hidden = true; }, 5000);
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function button(text, cls, onClick) {
    var b = el("button", "btn" + (cls ? " " + cls : ""), text);
    b.type = "button"; b.addEventListener("click", onClick); return b;
  }
  function api(path, opts) {
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (body) {
        if (!r.ok) throw new Error(body.error || ("Erro " + r.status));
        return body;
      });
    });
  }
  function download(url) {
    var a = document.createElement("a"); a.href = url; a.download = ""; document.body.appendChild(a); a.click(); a.remove();
  }

  // ---------- configuração (lembrada entre visitas)
  var form = $("#settings");
  function settings() {
    var d = new FormData(form);
    return { style: d.get("style"), scale: Number(d.get("scale")), noise: Number(d.get("noise")), format: d.get("format") };
  }
  try {
    var saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "null");
    if (saved) Object.keys(saved).forEach(function (k) {
      var input = form.querySelector('input[name="' + k + '"][value="' + saved[k] + '"]');
      if (input) input.checked = true;
    });
  } catch (e) {}
  form.addEventListener("change", function () {
    try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings())); } catch (e) {}
    renderQueue();
  });

  // ---------- adicionar arquivos
  function addFiles(files) {
    Array.prototype.forEach.call(files, function (file) {
      if (!/^image\/(jpeg|png|webp)$/.test(file.type)) { toast(file.name + ": use JPG, PNG ou WebP."); return; }
      if (limits && file.size > limits.max_bytes) { toast(file.name + ": passa de 50 MB."); return; }
      var item = { key: Math.random().toString(36).slice(2), file: file, preview: URL.createObjectURL(file), status: "pending", task: null, w: 0, h: 0, error: "" };
      var img = new Image();
      img.onload = function () { item.w = img.naturalWidth; item.h = img.naturalHeight; renderQueue(); };
      img.src = item.preview;
      queue.push(item);
    });
    renderQueue();
  }
  $("#files").addEventListener("change", function (e) { addFiles(e.target.files); e.target.value = ""; });
  var drop = $("#drop");
  ["dragenter", "dragover"].forEach(function (t) { document.addEventListener(t, function (e) { e.preventDefault(); drop.classList.add("over"); }); });
  ["dragleave", "drop"].forEach(function (t) { document.addEventListener(t, function (e) { e.preventDefault(); if (t === "drop" || e.target === document.documentElement) drop.classList.remove("over"); }); });
  document.addEventListener("drop", function (e) { if (e.dataTransfer && e.dataTransfer.files.length) addFiles(e.dataTransfer.files); });

  // ---------- enviar
  function outputCheck(item, cfg) {
    if (!limits || !item.w) return "";
    var ext = cfg.format === "default" ? ({ "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp" })[item.file.type] : cfg.format;
    var ow = item.w * cfg.scale, oh = item.h * cfg.scale;
    if (Math.max(ow, oh) > limits.max_output_side[ext]) return "Resultado de " + ow + "×" + oh + " px passa do limite de " + ext.toUpperCase() + ". Reduza a ampliação ou troque o formato.";
    if (ow * oh > limits.max_output_pixels) return "Resultado grande demais (" + Math.round(ow * oh / 1e6) + " MP). Reduza a ampliação.";
    return "";
  }
  function start(item) {
    if (item.status !== "pending" && item.status !== "error") return Promise.resolve();
    var cfg = settings();
    var problem = outputCheck(item, cfg);
    if (problem) { item.status = "error"; item.error = problem; renderQueue(); return Promise.resolve(); }
    if (item.task && item.status === "error") {
      item.status = "queued"; renderQueue();
      return api("/api/tasks/" + item.task.id + "/retry", { method: "POST" })
        .then(function (t) { item.task = t; item.error = ""; renderQueue(); poll(); })
        .catch(function (e) { item.status = "error"; item.error = e.message; renderQueue(); });
    }
    item.status = "uploading"; item.error = ""; renderQueue();
    var qs = "style=" + cfg.style + "&scale=" + cfg.scale + "&noise=" + cfg.noise + "&format=" + cfg.format;
    return api("/api/tasks?" + qs, { method: "POST", body: item.file, headers: { "Content-Type": item.file.type, "X-File-Name": encodeURIComponent(item.file.name) } })
      .then(function (t) { item.task = t; item.status = t.status; renderQueue(); poll(); })
      .catch(function (e) { item.status = "error"; item.task = null; item.error = e.message; renderQueue(); });
  }
  $("#start-all").addEventListener("click", function () {
    queue.filter(function (i) { return i.status === "pending"; }).reduce(function (p, i) { return p.then(function () { return start(i); }); }, Promise.resolve());
  });
  $("#clear-done").addEventListener("click", function () {
    queue = queue.filter(function (i) { if (i.status === "done") { URL.revokeObjectURL(i.preview); return false; } return true; });
    renderQueue();
  });
  $("#zip-session").addEventListener("click", function () {
    var ids = queue.filter(function (i) { return i.status === "done"; }).map(function (i) { return i.task.id; });
    if (!ids.length) return toast("Nenhuma imagem pronta ainda.");
    download("/api/zip?ids=" + ids.join(","));
  });

  // ---------- acompanhar progresso
  var polling = null;
  function poll() {
    if (polling) return;
    polling = setInterval(function () {
      var active = queue.filter(function (i) { return i.task && (i.status === "queued" || i.status === "processing"); });
      if (!active.length) { clearInterval(polling); polling = null; return; }
      api("/api/tasks?ids=" + active.map(function (i) { return i.task.id; }).join(",")).then(function (r) {
        r.tasks.forEach(function (t) {
          var item = queue.find(function (i) { return i.task && i.task.id === t.id; });
          if (item) { item.task = t; item.status = t.status; item.error = t.error; }
        });
        renderQueue();
      }).catch(function () {});
    }, 1000);
  }

  // ---------- desenhar itens
  function chips(cfg) {
    var c = el("div", "chips");
    [LABEL.style[cfg.style], cfg.scale + "x", LABEL.noise[String(cfg.noise)], LABEL.format[cfg.format]].forEach(function (t) { c.appendChild(el("span", "chip", t)); });
    return c;
  }
  function itemRow(opts) {
    var li = el("li", "item");
    if (opts.select) { li.classList.add("has-sel"); li.appendChild(opts.select); }
    var img = el("img", "thumb"); img.src = opts.thumb; img.alt = ""; img.loading = "lazy"; li.appendChild(img);
    var info = el("div", "info");
    info.appendChild(el("div", "name", opts.name));
    info.appendChild(el("div", "meta", opts.meta));
    info.appendChild(chips(opts.cfg));
    if (opts.progress != null) {
      var p = el("div", "progress"); var bar = el("i"); bar.style.width = opts.progress + "%"; p.appendChild(bar); info.appendChild(p);
    }
    info.appendChild(el("div", "status " + (opts.statusClass || ""), opts.statusText));
    li.appendChild(info);
    var acts = el("div", "acts"); opts.actions.forEach(function (a) { acts.appendChild(a); }); li.appendChild(acts);
    return li;
  }
  function taskActions(t, onRemove) {
    var a = [];
    if (t.status === "done") {
      a.push(button("Comparar", "", function () { openCompare(t); }));
      a.push(button("Baixar", "primary", function () { download("/api/tasks/" + t.id + "/result?download=1"); }));
    }
    a.push(button("Excluir", "danger", onRemove));
    return a;
  }
  function describe(t) {
    var out = t.out_width ? t.out_width + "×" + t.out_height : (t.width * t.scale) + "×" + (t.height * t.scale);
    var s = t.width + "×" + t.height + " → " + out + " px · " + fmtBytes(t.size);
    if (t.out_size) s += " → " + fmtBytes(t.out_size);
    if (t.finished && t.started) s += " · " + fmtTime(t.finished - t.started);
    return s;
  }
  function statusOf(t, status, error) {
    if (status === "error") return { text: "Falhou: " + (error || "erro desconhecido"), cls: "error" };
    if (status === "processing") return { text: (t.note ? t.note + " · " : "") + "Processando " + Math.round(t.progress || 0) + "%", cls: t.note ? "note" : "" };
    if (status === "done") return { text: "Pronto", cls: "done" };
    return { text: LABEL.status[status] || status, cls: "" };
  }

  function renderQueue() {
    var ul = $("#queue"); ul.textContent = "";
    var cfg = settings();
    $("#queue-actions").hidden = !queue.length;
    var pending = queue.filter(function (i) { return i.status === "pending"; }).length;
    var done = queue.filter(function (i) { return i.status === "done"; }).length;
    $("#queue-count").textContent = queue.length + " imagem(ns) · " + done + " pronta(s)" + (pending ? " · " + pending + " aguardando" : "");
    $("#start-all").disabled = !pending;
    queue.forEach(function (item) {
      var t = item.task;
      var useCfg = t ? t : cfg;
      var meta = t ? describe(t) : (item.w ? item.w + "×" + item.h + " → " + item.w * cfg.scale + "×" + item.h * cfg.scale + " px · " : "") + fmtBytes(item.file.size);
      var st = t ? statusOf(t, item.status, item.error) : (item.status === "error" ? { text: item.error, cls: "error" } : { text: LABEL.status[item.status], cls: "" });
      var actions = [];
      if (item.status === "pending") actions.push(button("Ampliar", "primary", function () { start(item); }));
      if (item.status === "error") actions.push(button("Tentar de novo", "", function () { start(item); }));
      var remove = function () {
        if (t) api("/api/tasks/" + t.id, { method: "DELETE" }).catch(function () {});
        URL.revokeObjectURL(item.preview);
        queue.splice(queue.indexOf(item), 1); renderQueue();
      };
      actions = actions.concat(t ? taskActions(t, remove) : [button("Remover", "danger", remove)]);
      ul.appendChild(itemRow({
        thumb: item.preview, name: item.file.name, meta: meta, cfg: useCfg,
        progress: (item.status === "processing" || item.status === "queued") ? (t && t.progress) || 0 : null,
        statusText: st.text, statusClass: st.cls, actions: actions
      }));
    });
  }

  // ---------- histórico
  var hist = { offset: 0, limit: 20, total: 0, tasks: [], selected: new Set() };
  function loadHistory() {
    api("/api/tasks?offset=" + hist.offset + "&limit=" + hist.limit).then(function (r) {
      hist.tasks = r.tasks; hist.total = r.total; renderHistory();
    }).catch(function (e) { toast(e.message); });
  }
  function renderHistory() {
    var ul = $("#history"); ul.textContent = "";
    if (!hist.tasks.length) ul.appendChild(el("li", "empty", "Nenhuma imagem ampliada ainda."));
    hist.tasks.forEach(function (t) {
      var cb = document.createElement("input"); cb.type = "checkbox"; cb.className = "sel";
      cb.checked = hist.selected.has(t.id); cb.setAttribute("aria-label", "Selecionar " + t.name);
      cb.addEventListener("change", function () { cb.checked ? hist.selected.add(t.id) : hist.selected.delete(t.id); syncHistButtons(); });
      var st = statusOf(t, t.status, t.error);
      var when = new Date(t.created * 1000).toLocaleString("pt-BR");
      ul.appendChild(itemRow({
        select: cb, thumb: "/api/tasks/" + t.id + "/input", name: t.name, meta: when + " · " + describe(t), cfg: t,
        statusText: st.text, statusClass: st.cls,
        actions: taskActions(t, function () {
          api("/api/tasks/" + t.id, { method: "DELETE" }).then(function () { hist.selected.delete(t.id); loadHistory(); });
        })
      }));
    });
    var pages = Math.max(1, Math.ceil(hist.total / hist.limit));
    $("#hist-page").textContent = "Página " + (hist.offset / hist.limit + 1) + " de " + pages + " · " + hist.total + " imagem(ns)";
    $("#hist-prev").disabled = hist.offset === 0;
    $("#hist-next").disabled = hist.offset + hist.limit >= hist.total;
    $("#hist-all").checked = hist.tasks.length > 0 && hist.tasks.every(function (t) { return hist.selected.has(t.id); });
    syncHistButtons();
  }
  function syncHistButtons() {
    $("#hist-zip").disabled = $("#hist-del").disabled = hist.selected.size === 0;
  }
  $("#hist-all").addEventListener("change", function (e) {
    hist.tasks.forEach(function (t) { e.target.checked ? hist.selected.add(t.id) : hist.selected.delete(t.id); });
    renderHistory();
  });
  $("#hist-prev").addEventListener("click", function () { hist.offset = Math.max(0, hist.offset - hist.limit); loadHistory(); });
  $("#hist-next").addEventListener("click", function () { hist.offset += hist.limit; loadHistory(); });
  $("#hist-zip").addEventListener("click", function () { download("/api/zip?ids=" + Array.from(hist.selected).join(",")); });
  var confirmPending = false;
  $("#hist-del").addEventListener("click", function () {
    var btn = $("#hist-del");
    if (!confirmPending) {
      confirmPending = true; btn.textContent = "Confirmar exclusão de " + hist.selected.size;
      setTimeout(function () { confirmPending = false; btn.textContent = "Excluir selecionadas"; }, 4000);
      return;
    }
    confirmPending = false; btn.textContent = "Excluir selecionadas";
    Promise.all(Array.from(hist.selected).map(function (id) { return api("/api/tasks/" + id, { method: "DELETE" }).catch(function () {}); }))
      .then(function () { hist.selected.clear(); loadHistory(); });
  });

  // ---------- abas
  function showTab(name) {
    var work = name === "work";
    $("#tab-work").setAttribute("aria-selected", String(work));
    $("#tab-hist").setAttribute("aria-selected", String(!work));
    $("#view-work").hidden = !work; $("#view-hist").hidden = work;
    if (!work) loadHistory();
  }
  $("#tab-work").addEventListener("click", function () { showTab("work"); });
  $("#tab-hist").addEventListener("click", function () { showTab("hist"); });

  // ---------- comparação antes/depois
  var dlg = $("#compare"), stage = $("#cmp-stage");
  function setPos(p) { stage.style.setProperty("--pos", p + "%"); $("#cmp-range").value = p; }
  function openCompare(t) {
    $("#cmp-after").src = "/api/tasks/" + t.id + "/result";
    $("#cmp-before").src = "/api/tasks/" + t.id + "/input";
    $("#cmp-title").textContent = t.name + " · " + t.scale + "x";
    setPos(50); dlg.showModal();
  }
  $("#cmp-range").addEventListener("input", function (e) { setPos(e.target.value); });
  function dragTo(e) { var r = stage.getBoundingClientRect(); setPos(Math.max(0, Math.min(100, (e.clientX - r.left) / r.width * 100))); }
  stage.addEventListener("pointerdown", function (e) { stage.setPointerCapture(e.pointerId); dragTo(e); });
  stage.addEventListener("pointermove", function (e) { if (stage.hasPointerCapture(e.pointerId)) dragTo(e); });
  $("#cmp-close").addEventListener("click", function () { dlg.close(); });

  // ---------- estado do motor
  api("/api/info").then(function (info) {
    limits = info.limits;
    var e = $("#engine");
    if (info.engine_found) { e.textContent = "motor pronto"; e.className = "engine ok"; }
    else { e.textContent = "motor não encontrado"; e.className = "engine bad"; toast("Coloque o waifu2x-ncnn-vulkan na pasta engine e reinicie."); }
  }).catch(function () { $("#engine").textContent = "servidor offline"; $("#engine").className = "engine bad"; });

  renderQueue();
})();
