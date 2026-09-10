# -*- coding: utf-8 -*-
"""Logo Serasa embutido no HTML ABAS e conversão CID no mailer."""
import re

from report_falhas.assets import resolve_logo_data_uri, resolve_logo_path
from report_falhas.html_pages import build_tabs_index_html
from report_falhas.render.mailer import prepare_images_for_email


def test_resolve_logo_path_exists():
    assert resolve_logo_path() is not None


def test_resolve_logo_data_uri_is_base64():
    uri = resolve_logo_data_uri()
    assert uri.startswith('data:image/')
    assert ';base64,' in uri


def test_build_tabs_index_html_embeds_logo_not_relative_file():
    html = build_tabs_index_html(
        'Report Teste',
        'oficial.html',
        'total.html',
        'client.html',
        'suporte.html',
        'ult3m.html',
        html_oficial='<html><body><p>ok</p></body></html>',
    )
    assert 'data:image/' in html
    assert 'serasa_logo.png' not in html


def test_prepare_images_for_email_converts_relative_logo_to_cid():
    html = '<img class="brand-logo" src="serasa_logo.png" alt="Serasa">'
    new_html, images = prepare_images_for_email(html)
    assert 'cid:img_inline_' in new_html
    assert 'serasa_logo.png' not in new_html
    assert len(images) == 1
    assert images[0][0].startswith('img_inline_')


def test_prepare_images_for_email_converts_data_uri_to_cid():
    uri = resolve_logo_data_uri()
    html = f'<img src="{uri}" alt="logo">'
    new_html, images = prepare_images_for_email(html)
    assert re.search(r'src="cid:img_inline_\d+"', new_html)
    assert 'base64,' not in new_html
    assert len(images) >= 1
