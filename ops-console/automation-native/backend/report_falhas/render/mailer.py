# -*- coding: utf-8 -*-
from datetime import datetime
from pathlib import Path

# ====== E-MAIL (Novo Outlook / Independente do Outlook) ======
# No Outlook "Novo" (New Outlook), automação via COM (pywin32) não é suportada.
# Estratégia recomendada:
#  1) Gerar um .EML (prévia) com HTML + imagens inline (CID) + anexos (ex.: XLSX)
#  2) Abrir o .EML no Outlook e usar Reenviar mensagem (não Encaminhar — quebra gráficos)
#  3) Colar destinatários (clipboard ou .txt gerado ao lado do .eml)
#  4) (Opcional) Envio via SMTP, se liberado pela TI

def _embed_known_logo_filenames(html: str, images: list, start_idx: int) -> tuple[str, int]:
    """Converte src relativos do logo (ex.: serasa_logo.png) em CID."""
    import re

    try:
        from report_falhas.assets import LOGO_KNOWN_FILENAMES, read_logo_bytes
    except ImportError:
        return html, start_idx

    logo = read_logo_bytes()
    if not logo:
        return html, start_idx

    data, mime = logo
    idx = start_idx
    out = html
    for name in LOGO_KNOWN_FILENAMES:
        pat = re.compile(rf'src=(["\']){re.escape(name)}\1', re.IGNORECASE)
        if not pat.search(out):
            continue
        cid = f"img_inline_{idx}"
        idx += 1
        images.append((cid, data, mime))
        out = pat.sub(f'src="cid:{cid}"', out)
    return out, idx


def prepare_images_for_email(html: str):
    # Converte data URI e logos relativos em CID inline para o .EML.
    import re
    import base64

    pattern = re.compile(
        r'src=(["\'])data:image/(png|jpeg|jpg|gif|webp);base64,([A-Za-z0-9+/=]+)\1',
        re.IGNORECASE,
    )
    images = []
    idx = 1

    def _repl(m):
        nonlocal idx
        mime = m.group(2).lower()
        b64 = m.group(3)
        cid = f"img_inline_{idx}"
        idx += 1
        try:
            bin_data = base64.b64decode(b64)
        except Exception:
            return m.group(0)
        if mime == 'jpg':
            mime = 'jpeg'
        images.append((cid, bin_data, f"image/{mime}"))
        return f'src="cid:{cid}"'

    new_html = pattern.sub(_repl, html)
    new_html, idx = _embed_known_logo_filenames(new_html, images, idx)
    return new_html, images


def build_eml_message(html: str, subject: str, from_addr: str, to_list=None, cc_list=None, attachments=None):
    # Monta uma mensagem MIME (.eml) com HTML, imagens inline via CID e anexos.
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.image import MIMEImage
    from email.mime.base import MIMEBase
    from email import encoders
    from email.utils import formatdate
    import mimetypes

    to_list = to_list or []
    cc_list = cc_list or []
    attachments = attachments or []

    html_cid, inline_imgs = prepare_images_for_email(html)

    msg = MIMEMultipart('related')
    msg['Subject'] = subject
    msg['From'] = from_addr
    if to_list:
        msg['To'] = '; '.join(to_list)
    if cc_list:
        msg['Cc'] = '; '.join(cc_list)
    msg['Date'] = formatdate(localtime=True)

    alt = MIMEMultipart('alternative')
    alt.attach(MIMEText('Este e-mail contém conteúdo HTML. Se você está vendo este texto, seu cliente não renderiza HTML.', 'plain', 'utf-8'))
    alt.attach(MIMEText(html_cid, 'html', 'utf-8'))
    msg.attach(alt)

    # imagens inline
    for cid, data, mime in inline_imgs:
        subtype = mime.split('/')[-1]
        img = MIMEImage(data, _subtype=subtype)
        img.add_header('Content-ID', f"<{cid}>")
        img.add_header('Content-Disposition', 'inline', filename=f"{cid}.{subtype}")
        msg.attach(img)

    # anexos (ex.: .xlsx)
    for fp in attachments:
        try:
            p = Path(fp)
            if not p.exists():
                continue
            ctype, encoding = mimetypes.guess_type(str(p))
            if ctype is None:
                ctype = 'application/octet-stream'
            maintype, subtype = ctype.split('/', 1)
            part = MIMEBase(maintype, subtype)
            part.set_payload(p.read_bytes())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', 'attachment', filename=p.name)
            msg.attach(part)
        except Exception:
            pass

    return msg


def save_eml(msg, output_path: Path) -> Path:
    # Salva a mensagem MIME em arquivo .eml
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(msg.as_bytes())
    return output_path


def open_eml(path: Path) -> bool:
    # Abre o .eml no app padrão do Windows (para abrir no Outlook new, associe .eml ao Outlook (new)).
    try:
        import os
        os.startfile(str(path))
        return True
    except Exception:
        pass
    try:
        import subprocess
        subprocess.Popen(['cmd', '/c', 'start', '', str(path)], shell=False)
        return True
    except Exception:
        return False


def open_html_preview(html: str, out_dir: Path) -> Path:
    # Salva .html e abre no navegador (preview rápido).
    ts = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"preview_{ts}.html"
    html_path.write_text(html, encoding='utf-8')
    try:
        import os
        os.startfile(str(html_path))
    except Exception:
        pass
    return html_path


def format_recipients_for_outlook(to_list=None, cc_list=None) -> str:
    """Texto pronto para colar no campo Para/Cc do Outlook (separador ; )."""
    to_list = to_list or []
    cc_list = cc_list or []
    lines: list[str] = []
    if to_list:
        lines.append(f"Para: {'; '.join(to_list)}")
    if cc_list:
        lines.append(f"Cc: {'; '.join(cc_list)}")
    return '\n'.join(lines)


def clipboard_paste_text(to_list=None, cc_list=None) -> str:
    """Somente e-mails Para (e Cc na linha seguinte) para colagem direta."""
    to_list = to_list or []
    cc_list = cc_list or []
    parts: list[str] = []
    if to_list:
        parts.append('; '.join(to_list))
    if cc_list:
        parts.append('; '.join(cc_list))
    return '\n'.join(parts)


def write_recipients_sidecar(
    out_dir: Path,
    scope_slug: str,
    to_list=None,
    cc_list=None,
) -> Path | None:
    """Grava email_falhas_{scope}_destinatarios.txt ao lado do .eml."""
    to_list = to_list or []
    cc_list = cc_list or []
    if not to_list and not cc_list:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    slug_part = f"{scope_slug}_" if scope_slug else ""
    sidecar_path = out_dir / f"email_falhas_{slug_part}destinatarios.txt"
    lines = format_recipients_for_outlook(to_list, cc_list).splitlines()
    sidecar_path.write_text("\n".join(lines) + "\n", encoding='utf-8')
    return sidecar_path


def copy_recipients_to_clipboard(to_list=None, cc_list=None) -> bool:
    """Copia lista Para (; ) para a área de transferência (Windows)."""
    text = clipboard_paste_text(to_list, cc_list)
    if not text:
        return False
    try:
        import subprocess
        import sys

        if sys.platform == 'win32':
            subprocess.run(['clip'], input=text, text=True, check=True)
            return True
    except Exception:
        pass
    return False


def _make_outlook_safe_html(html: str) -> str:
    # Outlook tende a quebrar com <details>/<summary>; para a prévia, mostramos tudo expandido.
    import re

    if not isinstance(html, str) or not html:
        return html

    html = re.sub(r'<details\b([^>]*)>', r'<div\1>', html, flags=re.IGNORECASE)
    html = re.sub(r'</details>', '</div>', html, flags=re.IGNORECASE)
    html = re.sub(r'<summary\b([^>]*)>', r"<div\1 style='font-weight:800;'>", html, flags=re.IGNORECASE)
    html = re.sub(r'</summary>', '</div>', html, flags=re.IGNORECASE)
    return html


def preview_new_outlook(
    html: str,
    subject: str,
    out_dir: Path,
    to_list=None,
    cc_list=None,
    from_addr: str = '',
    attachments=None,
    scope_slug: str = '',
    resolved_to_list=None,
    resolved_cc_list=None,
    recipients_sidecar_path: Path | None = None,
) -> Path:
    # Gera um .eml com anexos e tenta abrir para revisão no Outlook (new/classic).
    ts = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    from_addr = from_addr or 'no-reply@exemplo.com'
    html_safe = _make_outlook_safe_html(html)
    to_list = to_list or []
    cc_list = cc_list or []
    resolved_to_list = resolved_to_list if resolved_to_list is not None else to_list
    resolved_cc_list = resolved_cc_list if resolved_cc_list is not None else cc_list
    attachments = attachments or []
    msg = build_eml_message(html_safe, subject, from_addr, to_list=to_list, cc_list=cc_list, attachments=attachments)
    slug_part = f"{scope_slug}_" if scope_slug else ""
    eml_path = out_dir / f"email_falhas_{slug_part}{ts}.eml"
    save_eml(msg, eml_path)

    print(f"Prévia e-mail: {eml_path.name}")
    print(f"   Assunto: {subject}")
    if to_list:
        preview = ", ".join(to_list[:3])
        extra = f" (+{len(to_list) - 3})" if len(to_list) > 3 else ""
        print(f"   Para: {len(to_list)} destinatário(s) — {preview}{extra}")
    elif resolved_to_list or resolved_cc_list:
        total = len(resolved_to_list) + len(resolved_cc_list)
        sidecar_name = recipients_sidecar_path.name if recipients_sidecar_path else "(não gerado)"
        print(f"   Destinatários: {total} — ver {sidecar_name}")
        print("   Como enviar:")
        print("     1. Abra o .eml no Outlook")
        print("     2. Reenviar mensagem")
        print("     3. Copie Para/Cc do arquivo .txt de destinatários")
        print("     4. Anexe os arquivos manualmente, se necessário, e envie")
    else:
        print("   Para: (vazio — configure EMAIL_LISTS em config_report.local.py)")
    if cc_list:
        print(f"   Cc: {len(cc_list)} destinatário(s)")
    elif resolved_cc_list and not to_list:
        print(f"   Cc: {len(resolved_cc_list)} destinatário(s) — colar após Para se necessário")
    if attachments:
        print(f"   Anexos: {', '.join(Path(p).name for p in attachments)}")
    print(f"   Arquivo: {eml_path}")

    ok = open_eml(eml_path)
    if not ok:
        open_html_preview(html_safe, out_dir)
    return eml_path


