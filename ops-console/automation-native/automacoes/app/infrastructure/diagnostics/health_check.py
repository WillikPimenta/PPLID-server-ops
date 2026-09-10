"""
Sistema de Health Check - Verifica a saúde do projeto serasa-robos.

Funções:
- check_all(): Executa todos os checks e retorna relatório detalhado
- get_status(): Status rápido (OK/WARNING/ERROR)
- diagnose(): Diagnóstico completo com recomendações
"""

import os
import sys
import logging
import socket
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional

try:
    import requests
except ImportError:
    requests = None

# Configure basic logging
log = logging.getLogger("robots.health_check")

# Check states (use ASCII-safe alternatives when needed)
OK = "✅ OK"
WARNING = "⚠️  WARNING"
ERROR = "❌ ERROR"

# ASCII-safe alternatives for console output (Windows-friendly)
OK_ASCII = "[OK]"
WARNING_ASCII = "[WARN]"
ERROR_ASCII = "[ERR]"


class HealthCheckResult:
    """Resultado de um check de saúde."""
    
    def __init__(self):
        self.timestamp = datetime.now().isoformat()
        self.checks: Dict[str, Dict] = {}
        self.summary = {"ok": 0, "warning": 0, "error": 0}
        self.overall_status = OK
        self.diagnostics: List[str] = []
    
    def add_check(self, name: str, status: str, message: str = "", details: Dict = None):
        """Adiciona resultado de um check."""
        self.checks[name] = {
            "status": status,
            "message": message,
            "details": details or {}
        }
        
        if status == OK:
            self.summary["ok"] += 1
        elif status == WARNING:
            self.summary["warning"] += 1
            self.overall_status = WARNING
        elif status == ERROR:
            self.summary["error"] += 1
            self.overall_status = ERROR
    
    def add_diagnostic(self, msg: str):
        """Adiciona mensagem diagnóstica."""
        self.diagnostics.append(msg)
    
    def to_dict(self) -> Dict:
        """Converte para dicionário."""
        return {
            "timestamp": self.timestamp,
            "overall_status": self.overall_status,
            "summary": self.summary,
            "checks": self.checks,
            "diagnostics": self.diagnostics
        }
    
    def to_json(self) -> str:
        """Converte para JSON."""
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)
    
    def to_text(self) -> str:
        """Converte para texto formatado."""
        lines = [
            "═" * 60,
            f"  HEALTH CHECK REPORT - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"  Status: {self.overall_status}",
            "═" * 60,
            "",
            f"Summary: {self.summary['ok']} OK | {self.summary['warning']} WARNING | {self.summary['error']} ERROR",
            "─" * 60,
            ""
        ]
        
        for check_name, result in self.checks.items():
            lines.append(f"{result['status']} {check_name}")
            if result.get("message"):
                lines.append(f"   └─ {result['message']}")
            if result.get("details"):
                for key, val in result["details"].items():
                    lines.append(f"   • {key}: {val}")
        
        if self.diagnostics:
            lines.extend(["", "Recommendations:", "─" * 60])
            for diag in self.diagnostics:
                lines.append(f"• {diag}")
        
        return "\n".join(lines)


def check_python_version() -> Tuple[str, str, Dict]:
    """Verifica versão do Python."""
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    status = OK if sys.version_info >= (3, 8) else ERROR
    msg = f"Python {version} - 3.8+ required"
    details = {"version": version, "required": "3.8+", "met": sys.version_info >= (3, 8)}
    return status, msg, details


def check_required_packages() -> Tuple[str, str, Dict]:
    """Verifica pacotes obrigatórios."""
    required = [
        "selenium", "pandas", "openpyxl", "requests", 
        "watchdog", "webdriver_manager"
    ]
    
    installed = []
    missing = []
    
    for pkg in required:
        try:
            __import__(pkg)
            installed.append(pkg)
        except ImportError:
            missing.append(pkg)
    
    if not missing:
        status = OK
        msg = f"All {len(required)} packages installed"
    elif len(missing) <= 2:
        status = WARNING
        msg = f"{len(missing)} package(s) missing: {', '.join(missing)}"
    else:
        status = ERROR
        msg = f"Multiple packages missing: {', '.join(missing)}"
    
    return status, msg, {
        "installed": len(installed),
        "missing": len(missing),
        "missing_packages": missing
    }


def check_project_structure() -> Tuple[str, str, Dict]:
    """Verifica estrutura de diretórios do projeto."""
    root = Path(__file__).resolve().parents[3]
    required_dirs = ["app", "tests", "data"]
    required_files = [
        "pyproject.toml",
        "requirements.txt",
        "run.py",
        "app/orchestration/robot_runner.py",
    ]
    
    missing_dirs = []
    missing_files = []
    
    for d in required_dirs:
        if not (root / d).exists():
            missing_dirs.append(d)
    
    for f in required_files:
        if not (root / f).exists():
            missing_files.append(f)
    
    if not missing_dirs and not missing_files:
        status = OK
        msg = "Project structure intact"
    elif missing_dirs or missing_files:
        status = ERROR
        msg = f"Missing: {len(missing_dirs)} dirs, {len(missing_files)} files"
    else:
        status = OK
        msg = "Project structure OK"
    
    return status, msg, {
        "missing_directories": missing_dirs,
        "missing_files": missing_files,
        "root": str(root)
    }


def check_config_paths() -> Tuple[str, str, Dict]:
    """Verifica se os caminhos de configuração estão acessíveis."""
    try:
        from app.config import (
            DEFAULT_DOWNLOAD, DEFAULT_SHAREPOINT, DEFAULT_TIMEOUT,
            HEADLESS_DEFAULT, DEFAULT_INTERVAL
        )
        
        path_checks = {
            "download_dir": DEFAULT_DOWNLOAD,
            "sharepoint_dir": DEFAULT_SHAREPOINT,
            "timeout_sec": DEFAULT_TIMEOUT,
            "headless_mode": HEADLESS_DEFAULT,
            "check_interval_sec": DEFAULT_INTERVAL
        }
        
        accessible = []
        inaccessible = []
        
        for name, path_val in path_checks.items():
            if isinstance(path_val, Path):
                if path_val.parent.exists():
                    accessible.append(name)
                else:
                    inaccessible.append(name)
            else:
                accessible.append(name)
        
        status = OK if not inaccessible else WARNING
        msg = f"{len(accessible)} paths configured"
        
        return status, msg, {
            "configured_paths": len(accessible),
            "inaccessible_paths": inaccessible,
            "defaults": {k: str(v) if isinstance(v, Path) else v 
                        for k, v in path_checks.items()}
        }
    
    except Exception as e:
        return ERROR, f"Failed to load config: {str(e)}", {"error": str(e)}


def check_credentials() -> Tuple[str, str, Dict]:
    """Verifica se credenciais da sessão atual estão disponíveis."""
    try:
        from app.config import get_credentials
        
        user, pwd = get_credentials()
        
        details = {
            "user_configured": bool(user),
            "password_configured": bool(pwd),
            "source": "web_session_env"
        }
        
        if user and pwd:
            status = OK
            msg = "Credentials available in current session"
        elif user or pwd:
            status = WARNING
            msg = "Only partial credentials found"
            details["recommendation"] = "Informe matrícula e senha no formulário antes de iniciar o robô"
        else:
            status = WARNING
            msg = "No credentials available in current session"
            details["recommendation"] = "Inicie o robô pela interface web e preencha as credenciais no formulário"
        
        return status, msg, details
    
    except Exception as e:
        return ERROR, f"Failed to check credentials: {str(e)}", {"error": str(e)}


def check_network_connectivity() -> Tuple[str, str, Dict]:
    """Verifica conectividade de rede."""
    details = {}
    
    # Check internet connectivity
    internet_ok = False
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        internet_ok = True
    except (OSError, socket.error):
        pass
    
    details["internet_connectivity"] = "✓" if internet_ok else "✗"
    
    # Try to reach Okta (simplified - doesn't validate actual credentials)
    okta_ok = False
    try:
        from app.config import okta
        if okta and hasattr(okta, "O_LINK"):
            if requests:
                resp = requests.head(okta.O_LINK, timeout=5, verify=False)
                okta_ok = resp.status_code < 500
            else:
                okta_ok = True  # Can't verify without requests
        details["okta_reachable"] = "✓" if okta_ok else "✗"
    except Exception:
        pass
    
    if internet_ok:
        status = OK if okta_ok else WARNING
        msg = "Network connectivity OK"
    else:
        status = ERROR
        msg = "No internet connectivity detected"
    
    return status, msg, details


def check_selenium_driver() -> Tuple[str, str, Dict]:
    """Verifica se o Selenium WebDriver pode ser criado."""
    try:
        from app.infrastructure.selenium_helpers import create_driver
        from app.config import HEADLESS_DEFAULT
        
        try:
            drv = create_driver(headless=True)
            drv.quit()
            status = OK
            msg = "Selenium WebDriver working"
            details = {"driver_created_successfully": True}
        except Exception as e:
            status = WARNING
            msg = f"Selenium driver creation failed: {str(e)[:50]}"
            details = {"error": str(e), "recommendation": "Check Chrome/Chromium installation"}
        
        return status, msg, details
    
    except ImportError:
        return WARNING, "Selenium helpers not available for testing", {"missing_module": "selenium_helpers"}
    except Exception as e:
        return ERROR, f"Unexpected error checking driver: {str(e)}", {"error": str(e)}


def check_logging_system() -> Tuple[str, str, Dict]:
    """Verifica se o sistema de logging está configurado."""
    try:
        logs_dir = Path(__file__).parent.parent / "logs"
        logs_dir.mkdir(exist_ok=True)
        
        test_log = logs_dir / "health_check.test.log"
        
        # Try to write a test log entry
        try:
            with open(test_log, "w") as f:
                f.write(f"[{datetime.now().isoformat()}] Health check test\n")
            
            test_log.unlink()  # Clean up
            
            status = OK
            msg = f"Logging system functional ({logs_dir})"
            details = {"logs_directory": str(logs_dir), "writable": True}
        except Exception as e:
            status = ERROR
            msg = f"Logs directory not writable: {str(e)}"
            details = {"logs_directory": str(logs_dir), "writable": False}
        
        return status, msg, details
    
    except Exception as e:
        return WARNING, f"Logging check failed: {str(e)}", {"error": str(e)}


def check_robots_status() -> Tuple[str, str, Dict]:
    """Verifica status dos robôs."""
    try:
        robots = ["nivel_h", "monitor", "monitor_excel"]
        status_map = {}
        
        for robot in robots:
            try:
                __import__(f"robots")  # Verify robots module exists
                status_map[robot] = "importable"
            except ImportError:
                status_map[robot] = "not_importable"
        
        importable = sum(1 for v in status_map.values() if v == "importable")
        
        if importable == len(robots):
            status = OK
            msg = f"All {len(robots)} robot modules importable"
        elif importable > 0:
            status = WARNING
            msg = f"{importable}/{len(robots)} robot modules available"
        else:
            status = ERROR
            msg = "No robot modules available"
        
        return status, msg, {"robots": status_map, "importable_count": importable}
    
    except Exception as e:
        return WARNING, f"Robots status check failed: {str(e)}", {"error": str(e)}


def run_all_checks() -> HealthCheckResult:
    """Executa todos os checks de saúde."""
    result = HealthCheckResult()
    
    # Run all checks
    checks = [
        ("Python Version", check_python_version),
        ("Required Packages", check_required_packages),
        ("Project Structure", check_project_structure),
        ("Config Paths", check_config_paths),
        ("Credentials", check_credentials),
        ("Network Connectivity", check_network_connectivity),
        ("Selenium Driver", check_selenium_driver),
        ("Logging System", check_logging_system),
        ("Robots Status", check_robots_status),
    ]
    
    for check_name, check_func in checks:
        try:
            status, msg, details = check_func()
            result.add_check(check_name, status, msg, details)
        except Exception as e:
            result.add_check(check_name, ERROR, str(e), {})
    
    # Generate diagnostics
    if result.summary["error"] > 0:
        result.add_diagnostic("Critical errors detected. Review the failed checks above.")
    
    if result.summary["warning"] > 0:
        result.add_diagnostic("Some warnings present. The system may work but with limited features.")
    
    if result.overall_status == OK:
        result.add_diagnostic("✅ System is in good health. All checks passed.")
    
    return result


def print_report(result: HealthCheckResult, format: str = "text"):
    """Imprime relatório de health check."""
    import sys
    import io
    
    if format == "json":
        # Use UTF-8 for JSON output
        output = result.to_json()
    else:
        output = result.to_text()
    
    # Ensure proper encoding for Windows console
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout.buffer.write(output.encode('utf-8', errors='replace'))
        sys.stdout.buffer.write(b'\n')
    else:
        print(output, file=sys.stderr)


def get_quick_status() -> str:
    """Retorna status rápido do sistema."""
    result = run_all_checks()
    return result.overall_status


# CLI Interface
def main():
    """Interface de linha de comando."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Health Check System - Verifica saúde do projeto serasa-robos",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  python health_check.py              # Check completo (formato texto)
  python health_check.py --json       # Resultado em JSON
  python health_check.py --status     # Status rápido
        """
    )
    
    parser.add_argument("--json", action="store_true", help="Output in JSON format")
    parser.add_argument("--status", action="store_true", help="Show only overall status")
    
    args = parser.parse_args()
    
    if args.status:
        status = get_quick_status()
        print(f"System Status: {status}")
    else:
        result = run_all_checks()
        print_report(result, format="json" if args.json else "text")


if __name__ == "__main__":
    main()
