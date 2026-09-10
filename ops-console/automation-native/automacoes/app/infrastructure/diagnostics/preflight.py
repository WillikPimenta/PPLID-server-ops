"""Preflight checker para identificar problemas que impedem executar o projeto em outra máquina.

Uso: python -m app.infrastructure.diagnostics.preflight
"""
from pathlib import Path
import sys
import importlib.util
import os
import shutil

PROJECT_ROOT = Path(__file__).resolve().parents[3]

def check_python_version(min_major=3, min_minor=8):
    v = sys.version_info
    ok = (v.major > min_major) or (v.major == min_major and v.minor >= min_minor)
    return ok, f"{v.major}.{v.minor}.{v.micro}"

def module_available(name):
    return importlib.util.find_spec(name) is not None

def check_executable(names):
    for n in names:
        p = shutil.which(n)
        if p:
            return True, p
    return False, None

def main():
    issues = []
    print("Preflight checker para serasa-robos")
    ok, pyver = check_python_version()
    print(f"Python: {pyver} (requer >= 3.8) -> {'OK' if ok else 'FAIL'}")
    if not ok:
        issues.append('Versão do Python incompatível (>=3.8 requerida)')

    # check key modules from requirements
    reqs = [
        ('pandas', 'pandas'),
        ('selenium', 'selenium'),
        ('openpyxl', 'openpyxl'),
    ]
    print('\nDependências Python (import check):')
    for label, mod in reqs:
        available = module_available(mod)
        print(f" - {label}: {'OK' if available else 'MISSING'}")
        if not available:
            issues.append(f'Módulo Python ausente: {mod}')

    # Chrome / Chromedriver checks
    print('\nExecutáveis (navegador / chromedriver):')
    chrome_candidates = ['chrome', 'chrome.exe', 'google-chrome', 'google-chrome-stable', 'msedge', 'msedge.exe']
    okc, pathc = check_executable(chrome_candidates)
    print(f" - Chrome/Chromium found: {pathc or 'none'} -> {'OK' if okc else 'MISSING'}")
    cdriver_ok, cdriver_path = check_executable(['chromedriver', 'chromedriver.exe'])
    print(f" - Chromedriver found: {cdriver_path or 'none'} -> {'OK' if cdriver_ok else 'MISSING'}")
    if not okc:
        issues.append('Chrome/Chromium não encontrado no PATH')
    if not cdriver_ok:
        issues.append('chromedriver não encontrado no PATH (webdriver-manager pode baixar, mas requer internet)')

    # check config XLSX_PATH
    try:
        from app.config import XLSX_PATH
        xp = Path(str(XLSX_PATH))
        exists = xp.exists()
        print(f"\nExcel path configured: {xp} -> {'exists' if exists else 'MISSING'}")
        if not exists:
            issues.append(f'Arquivo Excel não encontrado: {xp}')
    except Exception as e:
        print('\nConfig import failed:', e)
        issues.append('Não foi possível importar config.XLSX_PATH')

    # env vars
    print('\nVariáveis de ambiente importantes (preenchidas pela interface web):')
    envs = ['OKTA_USER', 'OKTA_PASS', 'MONITOR_USER', 'MONITOR_PASS']
    for e in envs:
        v = os.getenv(e)
        print(f" - {e}: {'SET' if v else 'UNSET'}")
    if not (os.getenv('OKTA_USER') and os.getenv('OKTA_PASS')) and not (os.getenv('MONITOR_USER') and os.getenv('MONITOR_PASS')):
        issues.append('Credenciais da sessão atual não foram informadas ao processo')

    # filesystem checks
    try:
        tmp = PROJECT_ROOT / 'tmp_preflight_test'
        tmp.mkdir(exist_ok=True)
        testf = tmp / 'writetest.txt'
        with open(testf, 'w', encoding='utf-8') as f:
            f.write('ok')
        testf.unlink()
        tmp.rmdir()
        print('\nFilesystem: write/read in project root -> OK')
    except Exception as e:
        print('\nFilesystem test failed:', e)
        issues.append('Sem permissão de escrita na pasta do projeto')

    # suggestions
    print('\n\nResumo:')
    if not issues:
        print('Nenhum bloqueador crítico encontrado. Ambiente parece pronto para execução.')
        return 0
    else:
        print('Foram detectados problemas que podem impedir execução:')
        for it in issues:
            print(' -', it)
        print('\nSugestões:')
        print(' - Instalar dependências: pip install -e ".[dev]"')
        print(' - Instalar Chrome/Chromium e/ou chromedriver, ou permitir webdriver-manager baixar drivers (internet requerida)')
        print(' - Ajustar variável EXCEL_XLSX_PATH ou coloque o arquivo no caminho configurado')
        print(' - Iniciar os robôs pela interface web, preenchendo matrícula e senha no formulário')
        return 2

if __name__ == '__main__':
    sys.exit(main())
