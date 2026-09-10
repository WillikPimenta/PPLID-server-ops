/**
 * @deprecated Use tools/producao-brflow-kit/brflow_baixar_3dias.js
 */
console.warn("[BRFlow] Use tools/producao-brflow-kit/brflow_baixar_3dias.js");
(async () => {
  const DIAS_ATRAS = [0, 1, 2];
  const INTERVALO_MS = 25000;
  const PAUSA_APOS_CSV_MS = 2000;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const setField = (name, value) => {
    const el = document.querySelector(`[name="${name}"]`);
    if (!el) throw new Error(`Campo não encontrado: ${name}`);
    el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
  };

  const clickEl = (el, desc) => {
    if (!el) throw new Error(`Elemento não encontrado: ${desc}`);
    el.click();
  };

  const configurarMeta = () => {
    const meta = document.querySelector(
      "div.form-group[data-campo='2'] select[name='indMetaConfigurada']"
    );
    if (!meta) {
      console.warn("[BRFlow] Select de meta não encontrado — continuando");
      return;
    }
    meta.value = "1";
    meta.dispatchEvent(new Event("change", { bubbles: true }));
    meta.dispatchEvent(new Event("input", { bubbles: true }));
    if (window.jQuery) {
      try {
        window.jQuery(meta).val("1").trigger("change").trigger("select2:select");
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
      ini: `${dd}/${mm}/${yyyy} 00:00`,
      fim: `${dd}/${mm}/${yyyy} 23:59`,
    };
  };

  console.log("[BRFlow] Iniciando download de produção (3 dias)...");
  configurarMeta();

  for (let i = 0; i < DIAS_ATRAS.length; i++) {
    const diasAtras = DIAS_ATRAS[i];
    const { label, ini, fim } = formatarData(diasAtras);

    console.log(`[BRFlow] Dia ${i + 1}/${DIAS_ATRAS.length}: ${label}`);

    setField("datAnaliseInicial", ini);
    setField("datAnaliseFinal", fim);
    await sleep(1000);

    clickEl(document.getElementById("formato-csv"), "formato-csv");
    await sleep(PAUSA_APOS_CSV_MS);
    clicarPesquisar();

    if (i < DIAS_ATRAS.length - 1) {
      console.log(`[BRFlow] Aguardando ${INTERVALO_MS / 1000}s antes do próximo dia...`);
      await sleep(INTERVALO_MS);
    }
  }

  console.log("Concluído. Verifique 3 CSVs em Downloads e execute:");
  console.log("  python tools/producao-brflow-kit/tratar_producao.py");
})();
