/**
 * BRFlow Produtividade — painel 100% JS (sem Python).
 * Le CSV da pasta Downloads (File System Access API), trata e salva XLSX detalhado.
 *
 * Pre-requisito: logado no BRFlow, tela Produtividade.
 * Uso: DevTools -> Sources -> Snippets -> executar.
 */
(() => {
  if (window.__BRFLOW_PROD_PANEL__) {
    window.__BRFLOW_PROD_PANEL__.toggle();
    return;
  }

  const PANEL_VERSION = "3.3-manual-csv";

  const DEFAULTS = {
    diasAtras: [0, 1, 2],
    intervaloMs: 8000,
    pausaCsvMs: 500,
    prefixo: "relatorio_produtividade",
    xlsxCdn: "https://cdn.sheetjs.com/xlsx-0.20.3/package/dist/xlsx.full.min.js",
  };

  const COL_MAP = {
    matricula: "Matrícula",
    "matrícula": "Matrícula",
    "matrícula do colaborador": "Matrícula",
    "matricula do colaborador": "Matrícula",
    "data de analise": "Data de Análise",
    "data de análise": "Data de Análise",
    workflow: "Workflow",
    etapa: "Etapa",
    "tempo total": "Tempo Total",
    "total de analise": "Total de Análise",
    "total de análise": "Total de Análise",
  };

  const OUTPUT_COLS = [
    "Matrícula",
    "Data de Análise",
    "Hora",
    "Workflow",
    "Etapa",
    "Tempo Total",
    "Total de Análise",
    "Media_Tempo_por_Analise",
    "Meta",
    "Percentual",
  ];

  const state = {
    running: false,
    abort: false,
    phase: "idle",
    dias: [],
    logs: [],
    minimized: false,
    csvCapture: null,
    downloadsDir: null,
    seenCsvNames: new Set(),
    captureMode: "manual",
    pendingCsvPick: null,
  };

  /* ── util ── */
  const sleep = (ms) =>
    new Promise((resolve) => {
      if (state.abort) return resolve("aborted");
      const id = setTimeout(() => {
        clearInterval(poll);
        resolve(state.abort ? "aborted" : "done");
      }, ms);
      const poll = setInterval(() => {
        if (state.abort) {
          clearTimeout(id);
          clearInterval(poll);
          resolve("aborted");
        }
      }, 200);
    });

  const log = (msg, level = "info") => {
    state.logs.unshift({ t: new Date().toLocaleTimeString("pt-BR"), msg, level });
    state.logs = state.logs.slice(0, 40);
    render();
    console.log(`[ProdPanel] ${msg}`);
  };

  const numeroRobusto = (valor) => {
    if (valor == null) return 0;
    let t = String(valor).trim().replace(/\s/g, "");
    if (!t) return 0;
    try {
      if (t.includes(".") && t.includes(",")) {
        return parseFloat(t.replace(/\./g, "").replace(",", "."));
      }
      if (t.includes(",")) return parseFloat(t.replace(",", "."));
      if (t.includes(".")) {
        const p = t.split(".");
        if (p.length === 2 && p[1].length === 3 && /^\d+$/.test(p[0]) && /^\d+$/.test(p[1])) {
          return parseFloat(t.replace(/\./g, ""));
        }
        return parseFloat(t);
      }
      return parseFloat(t);
    } catch {
      return 0;
    }
  };

  const tempoParaSegundos = (valor) => {
    if (valor == null) return 0;
    const t = String(valor).trim();
    if (!t) return 0;
    if (t.includes(":")) {
      const p = t.split(":").map(Number);
      if (p.length === 3) return p[0] * 3600 + p[1] * 60 + p[2];
      if (p.length === 2) return p[0] * 60 + p[1];
    }
    return numeroRobusto(t);
  };

  const parseDateBR = (str) => {
    if (!str) return null;
    const s = String(str).trim();
    const m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?/);
    if (!m) {
      const d = new Date(s);
      return isNaN(d) ? null : d;
    }
    return new Date(
      +m[3],
      +m[2] - 1,
      +m[1],
      +(m[4] || 0),
      +(m[5] || 0),
      +(m[6] || 0)
    );
  };

  const dateToIso = (d) => {
    if (!d) return "";
    const dd = String(d.getDate()).padStart(2, "0");
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    return `${d.getFullYear()}-${mm}-${dd}`;
  };

  const extrairHora = (dataStr) => {
    const dt = parseDateBR(dataStr);
    return dt ? dt.getHours() : -1;
  };

  const parseCsv = (text) => {
    text = text.replace(/^\uFEFF/, "");
    const sep = text.includes(";") ? ";" : ",";
    const lines = text.split(/\r?\n/).filter((l) => l.trim());
    if (lines.length < 2) return [];

    const splitLine = (line) => {
      const out = [];
      let cur = "";
      let q = false;
      for (let i = 0; i < line.length; i++) {
        const c = line[i];
        if (c === '"') {
          q = !q;
          continue;
        }
        if (c === sep && !q) {
          out.push(cur.trim());
          cur = "";
        } else {
          cur += c;
        }
      }
      out.push(cur.trim());
      return out;
    };

    const headers = splitLine(lines[0]).map((h) => h.replace(/^"|"$/g, "").trim());
    return lines.slice(1).map((line) => {
      const vals = splitLine(line);
      const row = {};
      headers.forEach((h, i) => {
        row[h] = (vals[i] || "").replace(/^"|"$/g, "").trim();
      });
      return row;
    });
  };

  const looksLikeProdCsv = (text) => {
    if (!text || text.length < 30) return false;
    const head = text.slice(0, 3000).toLowerCase();
    if (head.trimStart().startsWith("<!") || head.includes("<html") || head.includes("<body")) {
      return false;
    }
    return (
      head.includes("matr") &&
      (head.includes("analise") ||
        head.includes("análise") ||
        head.includes("workflow") ||
        head.includes("etapa"))
    );
  };

  const pushCsv = async (payload) => {
    if (!state.csvCapture) return;
    let text = payload;
    if (payload instanceof Blob) {
      try {
        text = await payload.text();
      } catch {
        return;
      }
    }
    if (typeof text !== "string" || !looksLikeProdCsv(text)) return;
    if (state.csvCapture.waiter) {
      const resolve = state.csvCapture.waiter;
      state.csvCapture.waiter = null;
      resolve(text);
    } else {
      state.csvCapture.queue.push(text);
    }
  };

  const normalizarBrflow = (rows) => {
    const want = ["Matrícula", "Data de Análise", "Workflow", "Etapa", "Tempo Total", "Total de Análise"];
    return rows.map((row) => {
      const n = {};
      for (const [k, v] of Object.entries(row)) {
        const key = COL_MAP[k.trim().toLowerCase()] || k.trim();
        n[key] = v;
      }
      const out = {};
      want.forEach((c) => {
        if (n[c] != null) out[c] = n[c];
      });
      return out;
    }).filter((r) => Object.keys(r).length > 0);
  };

  const processarDataframe = (rows, targetDateIso) => {
    const target = targetDateIso.split("-").map(Number);
    const targetD = new Date(target[0], target[1] - 1, target[2]);

    const norm = normalizarBrflow(rows).filter((r) => {
      if (!r["Matrícula"]) return false;
      if (!/^[A-Za-z]\d{5}[A-Za-z]$/.test(String(r["Matrícula"]).trim())) return false;
      const dt = parseDateBR(r["Data de Análise"]);
      if (!dt) return false;
      return (
        dt.getFullYear() === targetD.getFullYear() &&
        dt.getMonth() === targetD.getMonth() &&
        dt.getDate() === targetD.getDate()
      );
    });

    const enriched = norm.map((r) => ({
      ...r,
      _hora: extrairHora(r["Data de Análise"]),
      _dataIso: dateToIso(parseDateBR(r["Data de Análise"])),
      _tempo: tempoParaSegundos(r["Tempo Total"]),
      _total: numeroRobusto(r["Total de Análise"]),
    }));

    const groups = new Map();
    for (const r of enriched) {
      const key = [r._dataIso, r["Matrícula"], r._hora, r["Workflow"], r["Etapa"]].join("|");
      if (!groups.has(key)) {
        groups.set(key, {
          "Matrícula": r["Matrícula"],
          "Data de Análise": r._dataIso,
          Hora: r._hora,
          Workflow: r["Workflow"] || "",
          Etapa: r["Etapa"] || "",
          _tempo: 0,
          _total: 0,
        });
      }
      const g = groups.get(key);
      g._tempo += r._tempo;
      g._total += r._total;
    }

    return Array.from(groups.values()).map((g) => {
      const media =
        g._total === 0 ? 0 : Math.round(g._tempo / g._total);
      return {
        "Matrícula": g["Matrícula"],
        "Data de Análise": g["Data de Análise"],
        Hora: g.Hora,
        Workflow: g.Workflow,
        Etapa: g.Etapa,
        "Tempo Total": g._tempo,
        "Total de Análise": g._total,
        Media_Tempo_por_Analise: media,
        Meta: "",
        Percentual: 0,
      };
    });
  };

  async function ensureXlsx() {
    if (window.XLSX) return;
    await new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = DEFAULTS.xlsxCdn;
      s.onload = resolve;
      s.onerror = () => reject(new Error("Falha ao carregar SheetJS (CDN bloqueado?)"));
      document.head.appendChild(s);
    });
  }

  function downloadXlsx(rows, filename) {
    const ws = XLSX.utils.json_to_sheet(rows, { header: OUTPUT_COLS });
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "Detalhado");
    XLSX.writeFile(wb, filename);
  }

  /* ── captura CSV ── */
  function installCsvInterceptors() {
    if (state.csvCapture?.installed) return;
    state.csvCapture = { installed: true, queue: [], waiter: null };

    const tryCaptureResponse = async (res) => {
      try {
        const cd = (res.headers?.get?.("content-disposition") || "").toLowerCase();
        const ct = (res.headers?.get?.("content-type") || "").toLowerCase();
        const maybeFile =
          cd.includes("attachment") ||
          cd.includes(".csv") ||
          ct.includes("csv") ||
          ct.includes("octet-stream") ||
          ct.includes("text/plain");
        if (!maybeFile && !ct.includes("text")) return;
        const clone = res.clone();
        await pushCsv(await clone.text());
      } catch (_) {}
    };

    const origFetch = window.fetch.bind(window);
    window.fetch = async (...args) => {
      const res = await origFetch(...args);
      tryCaptureResponse(res);
      return res;
    };

    const XO = XMLHttpRequest.prototype.open;
    const XS = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (method, url, ...rest) {
      this._ppUrl = String(url || "");
      return XO.call(this, method, url, ...rest);
    };
    XMLHttpRequest.prototype.send = function (...args) {
      this.addEventListener("load", function () {
        try {
          if (this.response instanceof Blob) {
            pushCsv(this.response);
            return;
          }
          if (this.responseType === "arraybuffer" && this.response) {
            pushCsv(new Blob([this.response]));
            return;
          }
          const text = typeof this.responseText === "string" ? this.responseText : "";
          if (text) pushCsv(text);
        } catch (_) {}
      });
      return XS.apply(this, args);
    };

    const origCreateObjectURL = URL.createObjectURL.bind(URL);
    URL.createObjectURL = function (obj) {
      const url = origCreateObjectURL(obj);
      if (obj instanceof Blob) pushCsv(obj);
      return url;
    };

    const hookIframe = (iframe) => {
      if (!iframe || iframe._ppHooked) return;
      iframe._ppHooked = true;
      iframe.addEventListener("load", () => {
        try {
          const doc = iframe.contentDocument;
          const text = doc?.body?.innerText || doc?.documentElement?.innerText || "";
          if (text) pushCsv(text);
        } catch (_) {}
      });
    };

    document.querySelectorAll("iframe").forEach(hookIframe);
    new MutationObserver((muts) => {
      muts.forEach((m) =>
        m.addedNodes.forEach((n) => {
          if (n.tagName === "IFRAME") hookIframe(n);
          n.querySelectorAll?.("iframe").forEach(hookIframe);
        })
      );
    }).observe(document.documentElement, { childList: true, subtree: true });

    const origAnchorClick = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function () {
      const href = this.href || "";
      const dl = (this.download || "").toLowerCase();
      if (href.startsWith("blob:") || dl.includes(".csv") || href.toLowerCase().includes(".csv")) {
        fetch(href, { credentials: "include" })
          .then((r) => r.blob())
          .then((b) => pushCsv(b))
          .catch(() => {});
      }
      return origAnchorClick.call(this);
    };
  }

  async function authorizeFolderFromClick() {
    if (typeof window.showDirectoryPicker !== "function") {
      throw new Error("Pasta auto exige Chrome ou Edge recente");
    }
    log("Abrindo seletor de pasta…", "warn");
    render();
    let handle;
    try {
      handle = await window.showDirectoryPicker({
        mode: "readwrite",
        startIn: "downloads",
      });
    } catch (err) {
      if (err?.name === "AbortError") throw new Error("Selecao de pasta cancelada");
      try {
        handle = await window.showDirectoryPicker({
          mode: "read",
          startIn: "downloads",
        });
        log("Pasta em modo leitura (CSV bruto nao sera removido)", "warn");
      } catch (err2) {
        if (err2?.name === "AbortError") throw new Error("Selecao de pasta cancelada");
        throw new Error(
          err2?.message ||
            "Nao foi possivel abrir a pasta. Use modo Manual ou verifique politica do Chrome."
        );
      }
    }
    state.downloadsDir = handle;
    const n = (await listCsvInDownloads()).length;
    log(`Pasta autorizada: ${handle.name} (${n} CSV(s) visiveis)`, "ok");
    return handle;
  }

  async function ensureDownloadsAccess() {
    if (state.downloadsDir) return state.downloadsDir;
    throw new Error('Clique em "Autorizar pasta" antes de iniciar no modo Pasta auto');
  }

  async function listCsvInDownloads() {
    const dir = state.downloadsDir;
    if (!dir) return [];
    const out = [];
    for await (const entry of dir.values()) {
      if (entry.kind !== "file" || !entry.name.toLowerCase().endsWith(".csv")) continue;
      const file = await entry.getFile();
      out.push({
        name: entry.name,
        handle: entry,
        lastModified: file.lastModified,
        size: file.size,
        file,
      });
    }
    return out;
  }

  async function snapshotCsvState() {
    const csvs = await listCsvInDownloads();
    return {
      names: new Set(csvs.map((c) => c.name)),
      mtime: new Map(csvs.map((c) => [c.name, c.lastModified])),
    };
  }

  async function readCsvText(file) {
    let text = await file.text();
    if (looksLikeProdCsv(text)) return text;
    try {
      const buf = await file.arrayBuffer();
      const latin = new TextDecoder("latin1").decode(buf);
      if (looksLikeProdCsv(latin)) return latin;
    } catch (_) {}
    return text;
  }

  function rankCsvCandidate(item) {
    const lower = item.name.toLowerCase();
    let score = 0;
    if (lower.startsWith(DEFAULTS.prefixo)) score += 100;
    if (lower.includes("produtiv")) score += 50;
    if (lower.includes("relatorio")) score += 25;
    return score;
  }

  async function waitForNewCsvInDownloads(baseline, sinceMs) {
    const deadline = Date.now() + 120000;
    const rejectReasons = [];
    let lastDiag = "";

    while (Date.now() < deadline) {
      if (state.abort) throw new Error("Abortado");
      const csvs = await listCsvInDownloads();
      const candidates = [];

      for (const item of csvs) {
        const prevMtime = baseline.mtime.get(item.name);
        const isNewName = !baseline.names.has(item.name);
        const isUpdated = prevMtime != null && item.lastModified > prevMtime + 800;
        const isRecent = item.lastModified >= sinceMs - 15000;

        if (!isNewName && !isUpdated && !isRecent) continue;
        if (state.seenCsvNames.has(item.name) && !isUpdated && !isRecent) continue;
        candidates.push(item);
      }

      candidates.sort((a, b) => {
        const scoreDiff = rankCsvCandidate(b) - rankCsvCandidate(a);
        if (scoreDiff) return scoreDiff;
        return b.lastModified - a.lastModified;
      });

      for (const item of candidates) {
        if (item.size === 0) {
          rejectReasons.push(`${item.name}: vazio (download em andamento?)`);
          continue;
        }
        const text = await readCsvText(item.file);
        if (!looksLikeProdCsv(text)) {
          rejectReasons.push(`${item.name}: nao parece CSV de produtividade`);
          continue;
        }
        state.seenCsvNames.add(item.name);
        log(`CSV detectado: ${item.name}`, "ok");
        return { text, name: item.name };
      }

      const folder = state.downloadsDir?.name || "?";
      lastDiag = `${csvs.length} CSV(s) em "${folder}"`;
      if (candidates.length) {
        lastDiag += `; ${candidates.length} candidato(s): ${candidates.map((c) => c.name).join(", ")}`;
      } else if (csvs.length) {
        lastDiag += `; nenhum novo desde Pesquisar (${csvs.slice(0, 3).map((c) => c.name).join(", ")}${csvs.length > 3 ? "…" : ""})`;
      }

      await sleep(700);
    }

    const folder = state.downloadsDir?.name || "?";
    const hint =
      "O Chrome salvou em outra pasta? Abra o Explorer no arquivo baixado, copie o caminho e use Trocar pasta Downloads.";
    const detail = rejectReasons.length
      ? ` Rejeitados: ${rejectReasons.slice(-3).join("; ")}.`
      : lastDiag
        ? ` ${lastDiag}.`
        : "";
    throw new Error(`CSV nao apareceu em "${folder}" (120s).${detail} ${hint}`);
  }

  async function removerCsvDownloads(nome) {
    if (!state.downloadsDir || !nome) return;
    try {
      await state.downloadsDir.removeEntry(nome);
      log(`CSV removido: ${nome}`, "ok");
    } catch (_) {
      log(`Nao foi possivel remover ${nome}`, "warn");
    }
  }

  function waitForCsvNetwork(timeoutMs = 8000) {
    return new Promise((resolve, reject) => {
      if (state.csvCapture?.queue?.length) {
        return resolve(state.csvCapture.queue.shift());
      }
      state.csvCapture.waiter = resolve;
      setTimeout(() => {
        if (state.csvCapture.waiter === resolve) {
          state.csvCapture.waiter = null;
          reject(new Error("Timeout rede CSV"));
        }
      }, timeoutMs);
    });
  }

  function garantirFormatoCsv() {
    const radio = document.getElementById("formato-csv");
    if (!radio) throw new Error("Opcao CSV (#formato-csv) nao encontrada");
    radio.checked = true;
    radio.dispatchEvent(new Event("change", { bubbles: true }));
    radio.click();
    const label = document.querySelector('label[for="formato-csv"]');
    if (label) label.click();
  }

  function waitForManualCsv(diaLabel) {
    return new Promise((resolve, reject) => {
      state.pendingCsvPick = {
        diaLabel,
        resolve: (file) => {
          state.pendingCsvPick = null;
          render();
          resolve(file);
        },
        reject: (err) => {
          state.pendingCsvPick = null;
          render();
          reject(err);
        },
      };
      log(`Dia ${diaLabel}: clique "Selecionar CSV" apos o download`, "warn");
      render();
    });
  }

  async function obterCsvDiaManual(diaLabel) {
    garantirFormatoCsv();
    await sleep(400);
    state.csvCapture.queue = [];
    log("Pesquisar -> aguardando download…");
    render();
    clicarPesquisar();

    const fromNet = await waitForCsvNetwork(8000).catch(() => null);
    if (fromNet) return { text: fromNet, csvName: null };

    const file = await waitForManualCsv(diaLabel);
    const text = await readCsvText(file);
    if (!looksLikeProdCsv(text)) {
      throw new Error(`${file.name} nao parece CSV de produtividade`);
    }
    log(`CSV selecionado: ${file.name}`, "ok");
    return { text, csvName: null };
  }

  async function obterCsvDiaFolder() {
    await ensureDownloadsAccess();
    garantirFormatoCsv();
    await sleep(400);

    const baseline = await snapshotCsvState();
    const sinceMs = Date.now();
    state.csvCapture.queue = [];
    log(`Monitorando "${state.downloadsDir.name}" (${baseline.names.size} CSV(s) antes)`);
    log("Pesquisar -> aguardando CSV…");
    render();
    clicarPesquisar();

    const fromNet = await waitForCsvNetwork(6000).catch(() => null);
    if (fromNet) return { text: fromNet, csvName: null };

    const fromFolder = await waitForNewCsvInDownloads(baseline, sinceMs);
    return { text: fromFolder.text, csvName: fromFolder.name };
  }

  async function obterCsvDia(diaLabel) {
    if (state.captureMode === "folder") return obterCsvDiaFolder();
    return obterCsvDiaManual(diaLabel);
  }

  /* ── UI helpers BRFlow ── */
  const setField = (name, value) => {
    const el = document.querySelector(`[name="${name}"]`);
    if (!el) throw new Error(`Campo nao encontrado: ${name}`);
    el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
  };

  const clickEl = (el, desc) => {
    if (!el) throw new Error(`Elemento nao encontrado: ${desc}`);
    el.click();
  };

  const configurarMeta = () => {
    const meta = document.querySelector(
      "div.form-group[data-campo='2'] select[name='indMetaConfigurada']"
    );
    if (!meta) return;
    meta.value = "1";
    meta.dispatchEvent(new Event("change", { bubbles: true }));
    if (window.jQuery) {
      try {
        window.jQuery(meta).val("1").trigger("change");
      } catch (_) {}
    }
  };

  const clicarPesquisar = () => {
    const btn =
      document.querySelector("#layout_layout2_panel_main button.btn-pesquisar") ||
      document.querySelector("#layout_layout2_panel_main button.btn-primary.btn-sutmit-pesquisar") ||
      document.evaluate(
        '//*[@id="layout_layout2_panel_main"]/div[4]/div/fieldset/form/div/div/div[2]/button',
        document,
        null,
        XPathResult.FIRST_ORDERED_NODE_TYPE,
        null
      ).singleNodeValue;
    clickEl(btn, "Pesquisar");
  };

  const formatarData = (diasAtras) => {
    const d = new Date();
    d.setDate(d.getDate() - diasAtras);
    const dd = String(d.getDate()).padStart(2, "0");
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const yyyy = d.getFullYear();
    return {
      label: `${dd}/${mm}/${yyyy}`,
      iso: `${yyyy}-${mm}-${dd}`,
      ini: `${dd}/${mm}/${yyyy} 00:00`,
      fim: `${dd}/${mm}/${yyyy} 23:59`,
    };
  };

  const initDias = () => {
    state.dias = DEFAULTS.diasAtras.map((offset, i) => {
      const { label } = formatarData(offset);
      return { index: i, label, status: "pending" };
    });
  };

  const getConfig = () => ({
    intervaloMs: Number(root.querySelector("#pp-intervalo")?.value || DEFAULTS.intervaloMs),
    pausaCsvMs: DEFAULTS.pausaCsvMs,
  });

  /* ── fluxo principal ── */
  async function runPipeline() {
    if (state.running) return;
    state.running = true;
    state.abort = false;
    state.phase = "running";
    initDias();
    installCsvInterceptors();
    log("Carregando biblioteca XLSX…");
    render();

    try {
      await ensureXlsx();
    } catch (err) {
      log(String(err.message || err), "err");
      state.running = false;
      state.phase = "idle";
      render();
      return;
    }

    if (state.captureMode === "folder" && !state.downloadsDir) {
      log('Modo Pasta auto: clique "Autorizar pasta" e tente de novo', "err");
      state.running = false;
      state.phase = "idle";
      render();
      return;
    }

    log(`Iniciando (3 dias, modo ${state.captureMode === "folder" ? "pasta" : "manual"})…`);
    configurarMeta();
    const cfg = getConfig();
    let okCount = 0;

    try {
      for (let i = 0; i < DEFAULTS.diasAtras.length; i++) {
        if (state.abort) break;
        const diasAtras = DEFAULTS.diasAtras[i];
        const { label, iso, ini, fim } = formatarData(diasAtras);
        state.dias[i].status = "active";
        log(`Dia ${i + 1}: ${label}`);
        render();

        setField("datAnaliseInicial", ini);
        setField("datAnaliseFinal", fim);
        if ((await sleep(800)) === "aborted") break;

        const { text: csvText, csvName } = await obterCsvDia(label);

        const rows = parseCsv(csvText);
        const tratado = processarDataframe(rows, iso);
        if (!tratado.length) {
          log(`Dia ${label}: sem registros validos`, "warn");
          state.dias[i].status = "pending";
        } else {
          const nome = `${DEFAULTS.prefixo}_detalhado_${iso}.xlsx`;
          downloadXlsx(tratado, nome);
          log(`Salvo: ${nome} (${tratado.length} linhas)`, "ok");
          if (csvName) await removerCsvDownloads(csvName);
          state.dias[i].status = "done";
          okCount++;
        }
        render();

        if (i < DEFAULTS.diasAtras.length - 1 && !state.abort) {
          if ((await sleep(cfg.intervaloMs)) === "aborted") break;
        }
      }

      if (state.abort) {
        log("Interrompido", "warn");
      } else if (okCount) {
        log(`${okCount} XLSX detalhado(s) gerados`, "ok");
        state.phase = "done";
      } else {
        log("Nenhum arquivo gerado", "warn");
        state.phase = "idle";
      }
    } catch (err) {
      log(String(err.message || err), "err");
      state.phase = "idle";
    }

    state.running = false;
    render();
  }

  function stopPipeline() {
    if (!state.running) return;
    state.abort = true;
    if (state.csvCapture?.waiter) {
      state.csvCapture.waiter = null;
    }
    if (state.pendingCsvPick) {
      state.pendingCsvPick.reject(new Error("Abortado"));
    }
    log("Parando…", "warn");
    render();
  }

  /* ── UI ── */
  const root = document.createElement("div");
  root.id = "brflow-prod-panel-root";
  document.body.appendChild(root);

  const shadow = root.attachShadow({ mode: "open" });
  shadow.innerHTML = `
<style>
  :host, * { box-sizing: border-box; font-family: "Segoe UI", system-ui, sans-serif; }
  .panel {
    position: fixed; top: 72px; right: 20px; z-index: 2147483646;
    width: 292px; border-radius: 16px;
    background: linear-gradient(145deg, #1e2433 0%, #151922 100%);
    border: 1px solid rgba(255,255,255,.08);
    box-shadow: 0 20px 50px rgba(0,0,0,.45), 0 0 0 1px rgba(45,212,191,.06);
    color: #e8ecf4; overflow: hidden;
  }
  .panel.minimized { width: 52px; height: 52px; border-radius: 14px; cursor: pointer; }
  .panel.minimized .body, .panel.minimized .footer { display: none; }
  .panel.minimized .head { padding: 0; justify-content: center; height: 52px; border: none; }
  .panel.minimized .head span, .panel.minimized .head-actions { display: none; }
  .panel.minimized .head::after { content: "P"; font-size: 18px; font-weight: 700; color: #2dd4bf; }
  .head {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 14px 10px; cursor: grab; user-select: none;
    border-bottom: 1px solid rgba(255,255,255,.06);
  }
  .head span { font-size: 13px; font-weight: 600; }
  .head small { display: block; font-size: 10px; color: #8b95a8; margin-top: 2px; }
  .icon-btn {
    width: 26px; height: 26px; border: none; border-radius: 8px;
    background: rgba(255,255,255,.06); color: #aab4c4; cursor: pointer;
  }
  .body { padding: 12px 14px 10px; }
  .badge {
    font-size: 10px; font-weight: 600; text-transform: uppercase;
    padding: 4px 8px; border-radius: 999px;
    background: rgba(139,149,168,.15); color: #8b95a8;
  }
  .badge.running { background: rgba(45,212,191,.15); color: #2dd4bf; }
  .badge.done { background: rgba(74,222,128,.15); color: #4ade80; }
  .badge.warn { background: rgba(251,191,36,.15); color: #fbbf24; }
  .days { display: flex; flex-direction: column; gap: 6px; margin: 10px 0; }
  .day {
    display: flex; align-items: center; gap: 8px; padding: 7px 10px; border-radius: 10px;
    background: rgba(255,255,255,.03); border: 1px solid rgba(255,255,255,.05); font-size: 12px;
  }
  .day.active { border-color: rgba(45,212,191,.35); background: rgba(45,212,191,.08); }
  .day.done { border-color: rgba(74,222,128,.25); }
  .day-dot { width: 7px; height: 7px; border-radius: 50%; background: #4b5563; }
  .day.active .day-dot { background: #2dd4bf; animation: pulse 1s infinite; }
  .day.done .day-dot { background: #4ade80; }
  @keyframes pulse { 50% { opacity: .4; } }
  .hint { font-size: 9px; color: #5c6578; margin-bottom: 8px; }
  .settings { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; font-size: 11px; color: #8b95a8; }
  .settings input {
    width: 64px; padding: 4px 6px; border-radius: 6px; border: 1px solid rgba(255,255,255,.1);
    background: rgba(0,0,0,.25); color: #e8ecf4; font-size: 11px;
  }
  .controls { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-bottom: 8px; }
  button.btn {
    border: none; border-radius: 10px; padding: 9px 0; font-size: 12px;
    font-weight: 600; cursor: pointer;
  }
  button.btn:disabled { opacity: .4; cursor: not-allowed; }
  .btn-start { background: linear-gradient(135deg, #2dd4bf, #14b8a6); color: #0f172a; grid-column: span 2; }
  .btn-stop { background: rgba(239,68,68,.15); color: #fca5a5; border: 1px solid rgba(239,68,68,.25); grid-column: span 2; }
  .btn-pick {
    background: rgba(251,191,36,.18); color: #fde68a;
    border: 1px solid rgba(251,191,36,.35); grid-column: span 2;
    animation: pulse 1.2s infinite;
  }
  .mode {
    display: flex; flex-direction: column; gap: 4px; margin-bottom: 8px;
    font-size: 10px; color: #8b95a8;
  }
  .mode label { display: flex; align-items: center; gap: 6px; cursor: pointer; }
  .folder-row { display: none; }
  .folder-row.visible { display: contents; }
  .log {
    max-height: 100px; overflow-y: auto; font-size: 10px; line-height: 1.45;
    color: #8b95a8; padding: 8px; border-radius: 8px;
    background: rgba(0,0,0,.22); border: 1px solid rgba(255,255,255,.04);
  }
  .log .ok { color: #4ade80; }
  .log .warn { color: #fbbf24; }
  .log .err { color: #f87171; }
  .footer { padding: 6px 14px 10px; font-size: 9px; color: #5c6578; text-align: center; }
</style>
<div class="panel" id="pp-panel">
  <div class="head" id="pp-drag">
    <div>
      <span>Producao BRFlow</span>
      <small>v3.3 · manual ou pasta</small>
    </div>
    <button class="icon-btn" id="pp-min" title="Minimizar">-</button>
  </div>
  <div class="body">
    <span class="badge" id="pp-badge">Pronto</span>
    <div class="days" id="pp-days"></div>
    <div class="hint">Modo Manual: apos cada Pesquisar, clique Selecionar CSV e escolha o arquivo em Downloads.</div>
    <div class="mode">
      <label><input type="radio" name="pp-mode" id="pp-mode-manual" value="manual" checked /> Manual (recomendado)</label>
      <label><input type="radio" name="pp-mode" id="pp-mode-folder" value="folder" /> Pasta auto (Chrome/Edge)</label>
    </div>
    <div class="settings">
      <label>Intervalo (s)</label>
      <input type="number" id="pp-intervalo" value="12" min="5" max="120" />
    </div>
    <div class="controls">
      <button class="btn btn-start" id="pp-start">Baixar e tratar</button>
      <button class="btn btn-stop" id="pp-stop" disabled>Parar</button>
      <button class="btn btn-pick" id="pp-pick" style="display:none">Selecionar CSV baixado</button>
      <span class="folder-row" id="pp-folder-row">
        <button class="btn btn-tratar" id="pp-folder" style="grid-column:span 2;background:rgba(255,255,255,.06);color:#cbd5e1;border:1px solid rgba(255,255,255,.08);">Autorizar pasta Downloads</button>
      </span>
    </div>
    <input type="file" id="pp-file" accept=".csv,text/csv" style="display:none" />
    <div class="log" id="pp-log"></div>
  </div>
  <div class="footer">Arraste pelo topo</div>
</div>`;

  const $ = (sel) => shadow.querySelector(sel);
  const panel = $("#pp-panel");

  const fileInput = $("#pp-file");

  function updateModeUi() {
    const folder = state.captureMode === "folder";
    $("#pp-mode-manual").checked = !folder;
    $("#pp-mode-folder").checked = folder;
    $("#pp-folder-row").classList.toggle("visible", folder);
    $("#pp-mode-manual").disabled = state.running;
    $("#pp-mode-folder").disabled = state.running;
  }

  function render() {
    const badge = $("#pp-badge");
    if (state.running) {
      badge.textContent = "Processando…";
      badge.className = "badge running";
    } else if (state.phase === "done") {
      badge.textContent = "Concluido";
      badge.className = "badge done";
    } else if (state.abort) {
      badge.textContent = "Interrompido";
      badge.className = "badge warn";
    } else {
      badge.textContent = "Pronto";
      badge.className = "badge";
    }

    $("#pp-start").disabled = state.running;
    $("#pp-stop").disabled = !state.running;
    $("#pp-intervalo").disabled = state.running;

    const pickBtn = $("#pp-pick");
    if (state.pendingCsvPick) {
      pickBtn.style.display = "block";
      pickBtn.textContent = `Selecionar CSV (${state.pendingCsvPick.diaLabel})`;
      pickBtn.disabled = false;
    } else {
      pickBtn.style.display = "none";
    }

    updateModeUi();

    $("#pp-days").innerHTML = state.dias
      .map(
        (d) =>
          `<div class="day ${d.status}"><span class="day-dot"></span><span>D${d.index + 1} · ${d.label}</span></div>`
      )
      .join("");

    $("#pp-log").innerHTML =
      state.logs.map((l) => `<div class="${l.level}"><b>${l.t}</b> ${l.msg}</div>`).join("") ||
      "<div>Aguardando acao…</div>";

    panel.classList.toggle("minimized", state.minimized);
  }

  let drag = null;
  $("#pp-drag").addEventListener("mousedown", (e) => {
    if (state.minimized || e.target.id === "pp-min") return;
    drag = { x: e.clientX, y: e.clientY, l: root.offsetLeft, t: root.offsetTop };
    root.style.left = `${root.offsetLeft}px`;
    root.style.top = `${root.offsetTop}px`;
    root.style.right = "auto";
  });
  document.addEventListener("mousemove", (e) => {
    if (!drag) return;
    root.style.left = `${drag.l + e.clientX - drag.x}px`;
    root.style.top = `${drag.t + e.clientY - drag.y}px`;
  });
  document.addEventListener("mouseup", () => {
    drag = null;
  });

  $("#pp-start").addEventListener("click", runPipeline);
  $("#pp-stop").addEventListener("click", stopPipeline);
  $("#pp-pick").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files?.[0];
    fileInput.value = "";
    if (!file || !state.pendingCsvPick) return;
    state.pendingCsvPick.resolve(file);
  });
  $("#pp-mode-manual").addEventListener("change", () => {
    if ($("#pp-mode-manual").checked) state.captureMode = "manual";
    render();
  });
  $("#pp-mode-folder").addEventListener("change", () => {
    if ($("#pp-mode-folder").checked) state.captureMode = "folder";
    render();
  });
  $("#pp-folder").addEventListener("click", async () => {
    state.downloadsDir = null;
    state.seenCsvNames.clear();
    try {
      await authorizeFolderFromClick();
    } catch (err) {
      log(String(err.message || err), "err");
    }
    render();
  });
  $("#pp-min").addEventListener("click", (e) => {
    e.stopPropagation();
    state.minimized = !state.minimized;
    render();
  });
  panel.addEventListener("click", () => {
    if (state.minimized) {
      state.minimized = false;
      render();
    }
  });

  initDias();
  log(`Painel ${PANEL_VERSION} — modo Manual: sem autorizar pasta`);
  render();

  window.__BRFLOW_PROD_PANEL__ = {
    open: () => {
      state.minimized = false;
      render();
    },
    close: () => root.remove(),
    toggle: () => {
      state.minimized = !state.minimized;
      render();
    },
    start: runPipeline,
    stop: stopPipeline,
  };
})();
