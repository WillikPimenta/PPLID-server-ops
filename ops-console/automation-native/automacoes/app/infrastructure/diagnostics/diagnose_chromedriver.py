#!/usr/bin/env python
"""
Script de diagnóstico para problemas com Chromedriver.

Executa: python -m app.infrastructure.diagnostics.diagnose_chromedriver
"""

import os
import sys
import struct
import subprocess
from pathlib import Path
import shutil


def print_header(title):
    """Imprime cabeçalho de seção."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def check_python_arch():
    """Verifica arquitetura do Python."""
    print_header("1. ARQUITETURA DO PYTHON")
    arch = 64 if struct.calcsize("P") == 8 else 32
    print(f"Python: {sys.executable}")
    print(f"Versão: {sys.version}")
    print(f"Arquitetura: {arch}-bit")
    return arch


def check_chrome_browser():
    """Verifica se Chrome/Chromium está instalado."""
    print_header("2. NAVEGADOR CHROME")
    
    chrome_paths = [
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
        "C:\\Program Files\\Chromium\\Application\\chrome.exe",
    ]
    
    found = False
    for path in chrome_paths:
        if Path(path).exists():
            try:
                result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
                print(f"✓ {path}")
                print(f"  {result.stdout.strip()}")
                found = True
            except Exception as e:
                print(f"? {path} (não conseguiu verificar versão: {e})")
                found = True
    
    if not found:
        print("✗ Chrome/Chromium NÃO ENCONTRADO")
        print("  Baixe em: https://www.google.com/chrome/")
    
    return found


def check_chromedriver():
    """Verifica Chromedriver instalado."""
    print_header("3. CHROMEDRIVER")
    
    # Tenta importar webdriver-manager
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        print("✓ webdriver-manager importado com sucesso")
        
        # Tenta encontrar/instalar chromedriver
        try:
            driver_path = ChromeDriverManager().install()
            print(f"✓ Chromedriver encontrado/instalado: {driver_path}")
            
            # Valida arquivo
            if Path(driver_path).exists():
                size_mb = Path(driver_path).stat().st_size / (1024*1024)
                print(f"  Tamanho: {size_mb:.1f} MB")
                
                # Tenta executar
                try:
                    result = subprocess.run([driver_path, "--version"], capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        print(f"✓ Funcional: {result.stdout.strip()}")
                        return True
                    else:
                        print(f"✗ Erro ao executar: {result.stderr}")
                        return False
                except OSError as e:
                    print(f"✗ OSError ao executar: {e}")
                    if "193" in str(e):
                        print("  → Possível mismatch de arquitetura (32-bit vs 64-bit)")
                    return False
                except Exception as e:
                    print(f"✗ Erro ao validar: {e}")
                    return False
            else:
                print(f"✗ Arquivo não encontrado: {driver_path}")
                return False
        except Exception as e:
            print(f"✗ Erro ao instalar: {e}")
            return False
    
    except ImportError:
        print("✗ webdriver-manager não instalado")
        print("  Execute: pip install webdriver-manager")
        return False


def check_cache():
    """Verifica cache do webdriver-manager."""
    print_header("4. CACHE DO WEBDRIVER-MANAGER")
    
    cache_dir = Path.home() / ".wdm"
    if cache_dir.exists():
        print(f"✓ Cache encontrado: {cache_dir}")
        
        # Lista conteúdo
        chromedriver_dirs = list(cache_dir.glob("*/drivers/chromedriver/*"))
        if chromedriver_dirs:
            print(f"  Versões em cache: {len(chromedriver_dirs)}")
            for d in chromedriver_dirs[:3]:
                print(f"    - {d.name}")
        
        print("\n⚠️  Para limpar cache (forçar novo download):")
        print(f"    del /s /q \"{cache_dir}\"")
    else:
        print(f"✓ Cache não encontrado (será criado na próxima execução)")


def check_path():
    """Verifica se chromedriver está no PATH."""
    print_header("5. CHROMEDRIVER NO PATH")
    
    result = shutil.which("chromedriver")
    if result:
        print(f"✓ Encontrado no PATH: {result}")
        try:
            output = subprocess.run([result, "--version"], capture_output=True, text=True, timeout=5)
            print(f"  {output.stdout.strip()}")
        except Exception as e:
            print(f"  (não conseguiu verificar versão: {e})")
    else:
        print("✗ Não encontrado no PATH")


def check_selenium():
    """Verifica Selenium."""
    print_header("6. SELENIUM")
    
    try:
        import selenium
        print(f"✓ Selenium {selenium.__version__} instalado")
        from selenium import webdriver
        print("✓ webdriver importável")
        return True
    except ImportError as e:
        print(f"✗ Erro ao importar Selenium: {e}")
        print("  Execute: pip install selenium")
        return False


def recommend_actions(python_arch):
    """Fornece ações recomendadas."""
    print_header("AÇÕES RECOMENDADAS")
    
    print("\n1️⃣  Se o erro é 'OSError 193' (Win32 inválido):")
    print("   Probablemente mismatch de arquitetura. Escolha UMA opção:")
    print(f"   a) Usar Python {python_arch}-bit (compatível com seu sistema)")
    print(f"   b) OU reinstalar Python {64 if python_arch == 32 else 32}-bit")
    
    print("\n2️⃣  Limpar cache e reinstalar webdriver-manager:")
    print("   python -c \"from pathlib import Path; import shutil; shutil.rmtree(Path.home() / '.wdm', ignore_errors=True)\"")
    print("   pip install --upgrade webdriver-manager selenium")
    
    print("\n3️⃣  Validar instalação:")
    print("   python src/diagnose_chromedriver.py")
    
    print("\n4️⃣  Testar manualmente:")
    print("   python -c \"from src.selenium_helpers import create_driver; d = create_driver(); d.quit()\"")


def main():
    """Executa todos os diagnósticos."""
    print("\n" + "="*60)
    print("DIAGNÓSTICO DE CHROMEDRIVER - SERASA ROBÔS")
    print("="*60)
    
    python_arch = check_python_arch()
    chrome_ok = check_chrome_browser()
    chromedriver_ok = check_chromedriver()
    check_cache()
    check_path()
    selenium_ok = check_selenium()
    
    # Resumo
    print_header("RESUMO")
    print(f"Python {python_arch}-bit: ✓")
    print(f"Chrome/Chromium: {'✓' if chrome_ok else '✗ NECESSÁRIO'}")
    print(f"Chromedriver: {'✓' if chromedriver_ok else '✗ PROBLEMA'}")
    print(f"Selenium: {'✓' if selenium_ok else '✗ PROBLEMA'}")
    
    if not (chrome_ok and selenium_ok):
        print("\n❌ Problemas encontrados. Ver recomendações abaixo.")
        recommend_actions(python_arch)
        return 1
    elif not chromedriver_ok:
        print("\n⚠️  Chromedriver com problema. Executando ações recomendadas...")
        recommend_actions(python_arch)
        return 2
    else:
        print("\n✅ Tudo parece OK! Tente rodar os robôs:")
        print("   python src/main.py")
        return 0


if __name__ == "__main__":
    sys.exit(main())
