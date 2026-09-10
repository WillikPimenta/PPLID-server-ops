# -*- coding: utf-8 -*-
from datetime import datetime

import pandas as pd

from report_falhas.io.data_loader import safe_str


def _top1(series: pd.Series) -> str:
    s = series.apply(safe_str)
    s = s[s != '']
    if s.empty:
        return ''
    return str(s.value_counts().index[0])


def _kpi_tile_legacy(title: str, value: str, sub: str = None) -> str:
    sub_html = f"<div style='font-size:12px;color:#6c757d;margin-top:2px;'>{sub}</div>" if sub else ""
    return (
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='background:#fff;border:1px solid #e9ecef;border-radius:10px;'>"
        "<tr><td style='padding:12px;'>"
        f"<div style='font-size:12px;color:#6c757d;margin-bottom:4px;white-space:normal;overflow-wrap:anywhere;word-break:break-word;'>{title}</div>"
        f"<div style='font-size:22px;font-weight:700;color:#212529;white-space:normal;overflow-wrap:anywhere;word-break:break-word;line-height:1.05;'>{value}</div>"
        f"{sub_html}"
        "</td></tr>"
        "</table>"
    )


def wrap_simple_page(page_title: str, body_html: str) -> str:
        return f"""<!DOCTYPE html>
<html lang='pt-br'>
<head>
    <meta charset='utf-8'>
    <meta name='viewport' content='width=device-width, initial-scale=1'>
    <title>{page_title}</title>
    <style>
        :root{{
            --bg:#ffffff;
            --ink:#111827;
            --muted:#6b7280;
            --line:#e5e7eb;
            --brand:#174E97;
            --brand2:#0f3d78;
            --card:#ffffff;
            --soft:#f6f7f9;
            --radius:14px;
            --shadow: 0 2px 10px rgba(16,24,40,0.08);
        }}
        *{{box-sizing:border-box;}}
        html, body{{max-width:100%;overflow-x:hidden;}}

        body{{margin:0;background:var(--bg);color:var(--ink);font-family:Segoe UI, Roboto, Arial, Helvetica, sans-serif;line-height:1.45;padding:10px;}}

        /* Desktop: usa mais espaço */
        .wrap{{max-width:1700px;margin:0 auto;}}
        .panel{{border:1px solid var(--line);border-radius:var(--radius);overflow:visible;background:var(--bg);}}

        /* Topbar mais "executivo" sem exagero */
        .topbar{{background:linear-gradient(90deg, var(--brand), var(--brand2));color:#fff;padding:14px 18px;}}
        .topbar h2{{margin:0;font-weight:900;font-size:19px;letter-spacing:0.2px;}}
        .topbar .sub{{margin-top:4px;font-size:12px;opacity:0.95;}}

        .content{{padding:14px 16px;background:var(--bg);}}

        .muted{{color:var(--muted);}}

        /* Cards: branco + sombra leve (não cansa no dia a dia) */
        .card{{
            background:var(--card);
            border:1px solid var(--line);
            border-radius:var(--radius);
            padding:14px;
            margin:12px 0 16px;
            box-shadow:var(--shadow);
        }}

        /* Grid */
        .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px;}}

        /* Tabelas: mais "dashboard" e legíveis */
        table{{border-collapse:separate;border-spacing:0;width:100%;max-width:100%;}}
        thead th{{
            background:#F3F4F6;
            font-weight:900;
            border:1px solid var(--line);
            border-bottom:0;
            padding:9px 10px;
            font-size:12px;
        }}
        thead th:first-child{{border-top-left-radius:12px;}}
        thead th:last-child{{border-top-right-radius:12px;}}

        tbody td{{
            border:1px solid var(--line);
            padding:9px 10px;
            font-size:12px;
            vertical-align:top;
            overflow-wrap:break-word;
            word-break:break-word;
            background:#fff;
        }}
        tbody tr:nth-child(even) td{{background:#FAFAFA;}}
        tbody tr:last-child td:first-child{{border-bottom-left-radius:12px;}}
        tbody tr:last-child td:last-child{{border-bottom-right-radius:12px;}}

        /* Micro-interações só no navegador */
        @media (hover:hover){{
            .card:hover{{box-shadow: 0 6px 18px rgba(16,24,40,0.10);}}
            tbody tr:hover td{{background:#EEF4FF;}}
        }}

        /* Badges (úteis p/ FY, status, dicas) */
        .badge{{display:inline-block;padding:4px 10px;border-radius:999px;border:1px solid var(--line);background:#F8FAFC;font-size:12px;color:#334155;}}
        .badge.brand{{background:#E8F0FE;border-color:#CFE0FF;color:#174E97;font-weight:800;}}
 /* Seções colapsáveis (para reduzir cansaço em leitura diária) */
 details.collapsible{{border:1px solid var(--line);border-radius:var(--radius);background:var(--soft);padding:10px 12px;margin:12px 0 16px;}}
 details.collapsible[open]{{background:#fff;}}
 details.collapsible summary{{cursor:pointer;font-weight:900;list-style:none;}}
 details.collapsible summary::-webkit-details-marker{{display:none;}}
 details.collapsible .hint{{font-weight:700;color:var(--muted);font-size:12px;margin-left:6px;}}
 details.collapsible .inner{{margin-top:10px;}}
 /* Barra de ações (Expandir/Recolher) */
 .actions{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;justify-content:flex-end;margin:10px 0 0;}}
 .btn{{appearance:none;border:1px solid var(--line);background:#fff;color:var(--ink);border-radius:10px;padding:6px 10px;font-size:12px;font-weight:800;cursor:pointer;}}
 .btn:hover{{background:#F3F4F6;}}
 .btn.brand{{border-color:#CFE0FF;background:#E8F0FE;color:#174E97;}}
 /* Botões de filtro (métrica) */
 .filter-buttons{{display:flex;gap:8px;margin:10px 0 14px;flex-wrap:wrap;}}
 .filter-btn{{appearance:none;border:2px solid var(--line);background:#fff;color:var(--ink);border-radius:10px;padding:8px 14px;font-size:12px;font-weight:700;cursor:pointer;transition:all 0.2s ease;}}
 .filter-btn:hover{{border-color:#174E97;background:#E8F0FE;color:#174E97;}}
 .filter-btn.active{{border-color:#174E97;background:#174E97;color:#fff;font-weight:800;}}
 /* Resumo 30s */
 .quick{{border:1px solid var(--line);border-radius:var(--radius);background:#fff;padding:12px 14px;margin:12px 0 16px;box-shadow:var(--shadow);}}
 .quick h3{{margin:0 0 8px;font-size:13px;}}
 .quick ul{{margin:0;padding-left:18px;font-size:12px;}}
 .quick li{{margin:6px 0;}}
 .quick .insight{{margin-top:8px;font-size:12px;color:var(--ink);background:#EEF2FF;border:1px solid #D9E0FF;border-radius:12px;padding:10px 12px;}}
 .quick .insight b{{color:#0f3d78;}}

        img{{max-width:100%;height:auto;}}
        img.zoomable{{cursor:zoom-in;}}


/* Hover detail: matrícula / nome do agente */
.hover-detail{{position:relative;display:inline-flex;align-items:center;max-width:100%;overflow:visible;cursor:help;}}
.hover-trigger{{display:inline-flex;align-items:center;gap:6px;border-bottom:1px dashed #93C5FD;}}
.hover-trigger .hint-dot{{display:inline-block;width:16px;height:16px;line-height:16px;text-align:center;border-radius:999px;background:#E8F0FE;color:#174E97;font-size:11px;font-weight:900;flex:0 0 auto;}}
.hover-panel{{display:none;position:absolute;left:0;top:calc(100% + 8px);z-index:999999;min-width:320px;max-width:680px;background:#fff;border:1px solid #dbe4f0;border-radius:14px;box-shadow:0 12px 30px rgba(15,23,42,.16);padding:12px;}}
.hover-detail:hover{{z-index:99999;}}
.hover-detail:hover .hover-panel{{display:block;}}
.hover-detail:focus-within .hover-panel{{display:block;}}
.hover-panel .ttl{{font-size:12px;font-weight:900;color:#0f172a;margin:0 0 8px;}}
.hover-panel .sub{{font-size:11px;color:#64748b;margin:0 0 10px;}}
.hover-panel table{{border-collapse:collapse;width:100%;table-layout:fixed;}}
/* Hover detail para matrícula em tabelas - modo simplificado */
.hover-detail:hover .hover-trigger{{color:#174E97;text-decoration:underline;}}
.hover-panel th,.hover-panel td{{border:1px solid #E5E7EB;padding:7px 8px;font-size:11px;background:#fff;}}
.hover-panel th{{background:#F8FAFC;font-weight:900;}}
.hover-panel tbody tr:nth-child(even) td{{background:#F8FAFC;}}
.hover-panel .empty{{font-size:12px;color:#6b7280;}}
.mat-hover-cell{{position:relative;cursor:help;}}
.mat-hover-cell .mat-val{{display:inline-block;max-width:100%;}}
.mat-hover-cell .mat-tip{{display:none;position:absolute;left:0;top:calc(100% + 8px);z-index:99999;min-width:260px;max-width:420px;background:#fff;border:1px solid #dbe4f0;border-radius:12px;box-shadow:0 12px 30px rgba(15,23,42,.16);padding:10px;}}
.mat-hover-cell:hover{{z-index:99999;}}
.mat-hover-cell:hover .mat-tip{{display:block;}}
.mat-hover-cell .mat-tip .ttl{{font-size:12px;font-weight:900;color:#0f172a;margin:0 0 4px;}}
.mat-hover-cell .mat-tip .sub{{font-size:12px;color:#64748b;}}
        .trein-resumo{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin:12px 0 10px;}}
        .trein-resumo-card{{border:1px solid var(--line);border-radius:12px;padding:10px 12px;background:#fff;box-shadow:var(--shadow);}}
        .trein-resumo-ttl{{font-size:12px;color:#6B7280;font-weight:700;}}
        .trein-resumo-qtd{{font-size:20px;font-weight:900;margin-top:4px;}}
        .res-finalizado{{background:#ECFDF5;border-color:#A7F3D0;color:#166534;}}
        .res-pendente{{background:#FFFBEB;border-color:#FDE68A;color:#92400E;}}
        .res-previsto{{background:#F8FAFC;border-color:#E5E7EB;color:#334155;}}
        .res-vencido{{background:#FEF2F2;border-color:#FECACA;color:#991B1B;}}
        .res-avencer{{background:#EFF6FF;border-color:#BFDBFE;color:#1D4ED8;}}
        .situacao-cell{{font-weight:800;}}
        .situacao-finalizado{{background:#ECFDF5 !important;color:#166534 !important;}}
        .situacao-pendente{{background:#FFFBEB !important;color:#92400E !important;}}
        .situacao-previsto{{background:#F8FAFC !important;color:#334155 !important;}}
        .situacao-vencido{{background:#FEF2F2 !important;color:#991B1B !important;}}
        .situacao-avencer{{background:#EFF6FF !important;color:#1D4ED8 !important;}}
        .trein-prev-kpi{{cursor:pointer;transition:box-shadow .15s,border-color .15s;}}
        .trein-prev-kpi-active{{outline:2px solid #174E97;outline-offset:2px;}}
        .trein-prev-row-hidden{{display:none;}}
        .trein-prev-sec-hidden{{display:none !important;}}
@media (max-width: 980px){{
    .hover-panel{{display:none !important;}}
    .hover-trigger{{border-bottom:0;cursor:default;}}
}}
/* Lightbox */
        .lb-backdrop{{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.75);z-index:9999;align-items:center;justify-content:center;padding:14px;}}
        .lb-backdrop.open{{display:flex;}}
        .lb-box{{max-width:min(1700px, 96vw);max-height:92vh;background:#111;border-radius:12px;overflow:hidden;border:1px solid rgba(255,255,255,0.12);}}
        .lb-top{{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 12px;color:#fff;background:rgba(0,0,0,0.35);}}
        .lb-title{{font-size:12px;opacity:0.9;}}
        .lb-close{{background:transparent;border:1px solid rgba(255,255,255,0.25);color:#fff;border-radius:10px;padding:6px 10px;cursor:pointer;}}
        .lb-imgwrap{{background:#000;}}
        .lb-imgwrap img{{display:block;max-width:96vw;max-height:86vh;width:auto;height:auto;}}

        /* Acessibilidade */
        @media (prefers-reduced-motion: reduce){{
            *{{scroll-behavior:auto;}}
        }}

        @media (max-width: 980px){{ .wrap{{max-width:100%;}} }}

        @media (max-width: 820px){{
            .content{{padding:12px 12px;}}
            .grid2{{grid-template-columns:1fr;}}
            thead th, tbody td{{font-size:11px;}}

            table.table-stack thead{{display:none;}}
            table.table-stack, table.table-stack tbody, table.table-stack tr, table.table-stack td{{display:block;width:100%;}}
            table.table-stack tr{{border:1px solid var(--line);border-radius:12px;margin:0 0 12px;overflow:hidden;background:#fff;}}
            table.table-stack td{{border:0;border-bottom:1px solid var(--line);padding:10px 12px;background:#fff;}}
            table.table-stack td::before{{content: attr(data-label);font-weight:900;color:var(--muted);display:block;margin:0 0 4px;}}
            table.table-stack td:last-child{{border-bottom:0;}}
        }}

        .tip {{ position: relative; display: inline-block; }}
        .tip .tipbox {{
            display: none;
            position: absolute;
            left: 0;
            top: 100%;
            margin-top: 6px;
            background: #111827;
            color: #F9FAFB;
            padding: 8px 10px;
            border-radius: 8px;
            font-size: 11px;
            line-height: 1.35;
            max-width: 320px;
            z-index: 20;
            box-shadow: 0 6px 18px rgba(0,0,0,0.15);
            white-space: normal;
        }}
        .tip:hover .tipbox {{ display: block; }}
        details.cap-analise {{ margin-top: 12px; border: 1px solid #E5E7EB; border-radius: 10px; padding: 6px 10px; background: #fff; }}
        details.cap-analise > summary {{ cursor: pointer; font-weight: 800; font-size: 12px; color: #111827; }}
    </style>
</head>
<body>
    <div class='wrap'>
        <div class='panel'>
            <div class='topbar'>
                <h2>{page_title}</h2>
                <div class='sub'>🕒 Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}</div>
            </div>
            <div class='content'>
                <div class='actions'><button class='btn brand' type='button' data-action='expand'>Expandir tudo</button><button class='btn' type='button' data-action='collapse'>Recolher tudo</button></div>
{body_html}
            </div>
        </div>
    </div>

    <!-- Lightbox -->
    <div id='lb' class='lb-backdrop' role='dialog' aria-modal='true'>
        <div class='lb-box'>
            <div class='lb-top'>
                <div id='lb-title' class='lb-title'>Visualização</div>
                <button id='lb-close' class='lb-close' type='button'>Fechar</button>
            </div>
            <div class='lb-imgwrap'>
                <img id='lb-img' alt='Zoom'>
            </div>
        </div>
    </div>

    <script>
        (function(){{
            var lb = document.getElementById('lb');
            var img = document.getElementById('lb-img');
            var ttl = document.getElementById('lb-title');
            var closeBtn = document.getElementById('lb-close');
            function close(){{ lb.classList.remove('open'); img.src=''; }}
            function open(src, title){{ img.src = src; ttl.textContent = title || 'Gráfico'; lb.classList.add('open'); }}
            document.addEventListener('click', function(e){{
                var t = e.target;
                if(t && t.classList && t.classList.contains('zoomable')){{
                    open(t.src, t.getAttribute('data-title') || t.getAttribute('alt') || 'Gráfico');
                }}
                if(t === lb) close();
            }});
            closeBtn.addEventListener('click', close);
            document.addEventListener('keydown', function(e){{ if(e.key === 'Escape') close(); }});
      
            // Expandir/Recolher todos os blocos colapsáveis
            function setAll(open){{
                var els = document.querySelectorAll('details.collapsible');
                for(var i=0;i<els.length;i++){{
                    if(open){{ els[i].setAttribute('open','open'); }} else {{ els[i].removeAttribute('open'); }}
                }}
            }}
            document.addEventListener('click', function(e){{
                var b = e.target;
                if(!b || !b.getAttribute) return;
                var act = b.getAttribute('data-action');
                if(act==='expand'){{ setAll(true); }}
                if(act==='collapse'){{ setAll(false); }}
            }});
      
            // Sistema de filtro por métrica (Total/Oficial)
            document.addEventListener('click', function(e){{
                var btn = e.target;
                if(!btn || !btn.classList || !btn.classList.contains('filter-btn')) return;
        
                var filterId = btn.getAttribute('data-filter-id');
                var metrica = btn.getAttribute('data-metric');
        
                if(!filterId || !metrica) return;
        
                // Ativar botão
                var filterGroup = document.querySelector('[data-filter-id="' + filterId + '"]').parentNode;
                var allBtns = filterGroup.querySelectorAll('.filter-btn[data-filter-id="' + filterId + '"]');
                for(var i=0;i<allBtns.length;i++){{
                    allBtns[i].classList.remove('active');
                }}
                btn.classList.add('active');
        
                // Mostrar/esconder painéis
                var container = document.querySelector('[data-metric-container-id="' + filterId + '"]');
                if(container){{
                    var panels = container.querySelectorAll('[data-metric-panel]');
                    for(var i=0;i<panels.length;i++){{
                        var p = panels[i];
                        if(p.getAttribute('data-metric-panel') === metrica){{
                            p.style.display = 'block';
                        }} else {{
                            p.style.display = 'none';
                        }}
                    }}
                }}
            }});;

        }})();
    </script>
</body>
</html>"""


# ====== Helpers movidos de report_falhas_criticas.py (Etapa 3B) ======

# ======== Colunas na Base (HTML) ========
