import argparse
import json
import os
import sys
from pathlib import Path

from selenium.common.exceptions import TimeoutException, WebDriverException

from app.core.path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

from app.config import okta
from app.infrastructure.selenium_helpers import create_driver, login_okta_resiliente

TIMEOUT = 20


def _prefer_headless() -> bool:
    headless_env = (os.getenv("OKTA_VALIDATE_HEADLESS", "0") or "0").strip().lower()
    return headless_env in {"1", "true", "yes", "y", "on"}


def _chrome_launch_modes():
    """Retorna modos de lançamento do Chrome conforme OKTA_VALIDATE_HEADLESS."""
    if _prefer_headless():
        return [
            ("headless-new", "--headless=new"),
            ("headless-legacy", "--headless"),
            ("headed-fallback", None),
        ]
    return [("headed-default", None)]


def _prepare_visible_browser(driver) -> None:
    if _prefer_headless():
        return
    try:
        driver.set_window_position(0, 0)
    except Exception:
        pass
    try:
        driver.set_window_size(1280, 900)
    except Exception:
        pass
    try:
        driver.maximize_window()
    except Exception:
        pass
    # Traz a janela para frente no Windows (Chrome às vezes abre atrás do portal).
    try:
        driver.execute_script("window.focus();")
    except Exception:
        pass


def _create_chrome_driver():
    prefer_headless = _prefer_headless()
    try:
        driver = create_driver(headless=prefer_headless)
        _prepare_visible_browser(driver)
        return driver
    except Exception as primary_exc:
        if not prefer_headless:
            raise RuntimeError(
                "Falha ao iniciar Chrome visível para validação Okta. "
                f"Detalhes: {primary_exc}"
            ) from primary_exc
        try:
            driver = create_driver(headless=False)
            _prepare_visible_browser(driver)
            return driver
        except Exception as fallback_exc:
            raise RuntimeError(
                "Falha ao iniciar Chrome WebDriver para validação Okta. "
                f"Headless: {primary_exc} | Headed: {fallback_exc}"
            ) from fallback_exc


def _looks_like_challenge_page(driver) -> bool:
    try:
        page = (driver.page_source or "").lower()
        title = (driver.title or "").lower()
    except Exception:
        return False
    challenge_tokens = [
        "captcha",
        "verify you are human",
        "human verification",
        "access denied",
        "unsupported browser",
        "security check",
        "cloudflare",
    ]
    return any(token in page or token in title for token in challenge_tokens)


def _persist_selenium_pids(driver) -> None:
    path = (os.getenv("OKTA_VALIDATE_PIDS_FILE") or "").strip()
    if not path:
        return
    pids: list[int] = []
    try:
        service_proc = getattr(getattr(driver, "service", None), "process", None)
        if service_proc and getattr(service_proc, "pid", None):
            pids.append(int(service_proc.pid))
    except Exception:
        pass
    if not pids:
        return
    try:
        pid_path = Path(path)
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(json.dumps(pids), encoding="utf-8")
    except Exception:
        pass


def validate_okta_credentials(matricula: str, senha: str) -> tuple[bool, str]:
    driver = None
    try:
        driver = _create_chrome_driver()
        _persist_selenium_pids(driver)
        driver.get(okta.O_LINK)

        if _looks_like_challenge_page(driver):
            return False, "Okta exibiu verificação de segurança/CAPTCHA; não foi possível validar automaticamente"

        try:
            login_okta_resiliente(driver, matricula, senha, timeout=TIMEOUT, wait_for_post_login=True)
            return True, "Credenciais Okta validadas com sucesso"
        except TimeoutException:
            current_url = (driver.current_url or "").lower()
            if "login" in current_url or "signin" in current_url:
                return False, "Credenciais inválidas ou login não concluído no Okta"
            return False, "Não foi possível confirmar o login no Okta dentro do tempo limite"
        except WebDriverException as exc:
            return False, f"Sessão do navegador encerrada durante validação Okta: {exc.msg if hasattr(exc, 'msg') else exc}"
        except Exception as exc:
            return False, f"Falha no fluxo resiliente de login Okta: {exc}"
    except Exception as exc:
        return False, f"Falha técnica na validação Okta: {exc}"
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--matricula")
    parser.add_argument("--senha")
    args = parser.parse_args()

    matricula = (args.matricula or os.getenv("OKTA_USER") or "").strip()
    senha = args.senha or os.getenv("OKTA_PASS") or ""
    if not matricula or not senha:
        print(json.dumps({"ok": False, "message": "Matrícula e senha são obrigatórias"}, ensure_ascii=False), flush=True)
        sys.exit(2)

    ok, message = validate_okta_credentials(matricula, senha)
    payload = {"ok": ok, "message": message}
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
