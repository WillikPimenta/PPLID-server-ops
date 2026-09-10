"""
Módulo de utilitários compartilhados para evitar duplicação de código.
Centraliza funções comuns usadas por múltiplos módulos.
"""

import logging
import threading
import time
from typing import Callable, Optional


class ErrorRecoveryHandler:
    """
    Gerenciador centralizado de recuperação de erros e contagem de erros consecutivos.
    
    Elimina código duplicado entre nivel_h, monitor e monitor_excel.
    Padroniza tratamento de exceções e retry logic.
    
    Uso:
        handler = ErrorRecoveryHandler(max_errors=5, retry_delay=2)
        
        try:
            # código
        except Exception as e:
            if not handler.handle_error(drv, "Erro no Selenium", _set_status, log):
                break  # Stop se atingiu limite
            continue  # Retry
        
        handler.reset()  # Ao sucesso
    """
    
    def __init__(self, max_errors: int = 5, retry_delay: int = 2):
        """
        Args:
            max_errors: Número máximo de erros consecutivos antes de parar
            retry_delay: Segundos para aguardar antes de retry
        """
        self.max_errors = max_errors
        self.retry_delay = retry_delay
        self.consecutive_errors = 0
        self._lock = threading.Lock()
    
    def handle_error(
        self,
        drv=None,
        error_msg: str = "Erro",
        set_status_fn: Optional[Callable[[str], None]] = None,
        logger: Optional[logging.Logger] = None
    ) -> bool:
        """
        Trata erro com logging, cleanup e retry logic.
        
        Args:
            drv: Webdriver a fechar (opcional)
            error_msg: Mensagem descritiva do erro
            set_status_fn: Callback para atualizar status
            logger: Logger para registro
        
        Returns:
            True se deve continuar (retry), False se deve parar
        """
        with self._lock:
            self.consecutive_errors += 1
            current_count = self.consecutive_errors
        
        # Log do erro
        if logger:
            logger.exception(f"{error_msg} (tentativa {current_count}/{self.max_errors})")
        
        # Fechar driver se fornecido
        if drv:
            try:
                drv.quit()
            except Exception:
                pass
        
        # Verificar limite
        if current_count >= self.max_errors:
            msg = f"❌ {self.max_errors} erros consecutivos! Parando robô."
            if set_status_fn:
                try:
                    set_status_fn(msg)
                except Exception:
                    pass
            if logger:
                logger.error(msg)
            return False  # Stop
        
        # Aguardar antes de retry
        if self.retry_delay > 0:
            time.sleep(self.retry_delay)
        
        return True  # Continue
    
    def reset(self):
        """Reseta contador ao sucesso."""
        with self._lock:
            self.consecutive_errors = 0
    
    def get_count(self) -> int:
        """Retorna contador atual."""
        with self._lock:
            return self.consecutive_errors
    
    def is_critical(self) -> bool:
        """Verifica se está próximo do limite."""
        with self._lock:
            return self.consecutive_errors >= (self.max_errors - 1)


def setup_logger(name: str, log_file: Optional[str] = None, level: int = logging.INFO) -> logging.Logger:
    """
    Setup padronizado de logger com handlers de arquivo e console.
    
    Args:
        name: Nome do logger
        log_file: Caminho do arquivo de log (opcional)
        level: Nível de logging
    
    Returns:
        Logger configurado
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Avoid duplicate handlers
    if logger.handlers:
        return logger
    
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    
    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    
    # File handler (if provided)
    if log_file:
        try:
            fh = logging.FileHandler(str(log_file), encoding='utf-8')
            fh.setLevel(level)
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        except Exception:
            pass
    
    return logger


class CallbackManager:
    """
    Gerenciador centralizado de callbacks para status e progresso.
    Evita duplicação de _status_cb e _progress_cb em múltiplos módulos.
    """
    
    def __init__(self):
        self._status_callback: Optional[Callable[[str], None]] = None
        self._progress_callback: Optional[Callable[[int, str], None]] = None
        self._lock = threading.Lock()
        self._last_status = ""
        self._last_progress = (-1, "")

    @staticmethod
    def _normalize_message(message: str) -> str:
        """Normaliza mensagem para reduzir ruído visual no painel e callbacks."""
        return " ".join(str(message or "").split()).strip()
    
    def set_status_callback(self, callback: Optional[Callable[[str], None]]):
        """Registra callback de status."""
        with self._lock:
            self._status_callback = callback
    
    def set_progress_callback(self, callback: Optional[Callable[[int, str], None]]):
        """Registra callback de progresso."""
        with self._lock:
            self._progress_callback = callback
    
    def emit_status(self, message: str):
        """Emite evento de status."""
        message_norm = self._normalize_message(message)
        if not message_norm:
            return

        with self._lock:
            cb = self._status_callback
            if message_norm == self._last_status:
                return
            self._last_status = message_norm

        if cb:
            try:
                cb(message_norm)
            except Exception:
                pass
    
    def emit_progress(self, percentage: int, message: str = ""):
        """Emite evento de progresso (0-100%)."""
        try:
            pct = int(float(percentage))
        except Exception:
            pct = 0
        pct = max(0, min(100, pct))
        message_norm = self._normalize_message(message)

        with self._lock:
            cb = self._progress_callback
            current = (pct, message_norm)
            if current == self._last_progress:
                return
            self._last_progress = current

        if cb:
            try:
                cb(pct, message_norm)
            except Exception:
                pass


class StopEvent:
    """
    Wrapper seguro para threading.Event com lock.
    Evita race conditions ao verificar/setar estado de parada.
    """
    
    def __init__(self):
        self._event = threading.Event()
        self._lock = threading.Lock()
    
    def set(self):
        """Sinaliza para parar."""
        with self._lock:
            self._event.set()
    
    def is_set(self) -> bool:
        """Verifica se sinalizado para parar."""
        with self._lock:
            return self._event.is_set()
    
    def wait(self, timeout: Optional[float] = None) -> bool:
        """Aguarda sinal de parada."""
        with self._lock:
            return self._event.wait(timeout)
    
    def clear(self):
        """Limpa sinal de parada."""
        with self._lock:
            self._event.clear()


def safe_close_driver(driver, logger: Optional[logging.Logger] = None):
    """
    Fecha driver de forma segura com tratamento de exceções.
    Reutilizável entre nivel_h, monitor, monitor_excel.
    """
    if not driver:
        return
    try:
        driver.quit()
    except Exception:
        pass


def safe_delete_file(path: str, logger: Optional[logging.Logger] = None):
    """
    Deleta arquivo de forma segura com tratamento de exceções.
    Reutilizável para limpeza de CSV temporários.
    """
    import os
    if not path:
        return
    try:
        os.remove(path)
    except Exception:
        pass


class HealthCheckListener:
    """
    Listener para monitorar saúde durante execução de robôs.
    Permite acesso a checks de saúde sem interromper robôs.
    """
    
    def __init__(self):
        """Inicializa health check listener."""
        self._last_check = None
        self._check_interval = 300  # 5 minutos
        self._last_check_time = 0
        self._lock = threading.Lock()
    
    def get_status(self, force_refresh: bool = False) -> dict:
        """
        Retorna status de saúde atual.
        
        Args:
            force_refresh: Se True, força novo check
        
        Returns:
            Dict com status
        """
        now = time.time()
        
        with self._lock:
            if force_refresh or (now - self._last_check_time) > self._check_interval:
                try:
                    from app.infrastructure.diagnostics.health_check import run_all_checks
                    self._last_check = run_all_checks().to_dict()
                    self._last_check_time = now
                except Exception as e:
                    return {"error": str(e), "timestamp": now}
        
        return self._last_check or {"status": "no_check_yet", "timestamp": now}
    
    def is_healthy(self) -> bool:
        """Verifica se sistema está saudável."""
        status = self.get_status()
        if "error" in status:
            return False
        return status.get("overall_status", "").startswith("✅")


class RobotAPI:
    """
    Interface padrão para robôs (nivel, monitor).
    Define contrato que todos devem seguir para consistência.
    """
    
    def set_status_callback(self, fn: Callable[[str], None]):
        """Registra callback de status."""
        raise NotImplementedError
    
    def set_progress_callback(self, fn: Callable[[int, str], None]):
        """Registra callback de progresso."""
        raise NotImplementedError
    
    def start(self, settings: Optional[dict] = None, intervalo: int = 30, tempo_max: Optional[int] = None) -> bool:
        """Inicia robô."""
        raise NotImplementedError
    
    def stop(self, timeout: int = 5):
        """Para robô."""
        raise NotImplementedError
    
    def is_running(self) -> bool:
        """Verifica se rodando."""
        raise NotImplementedError


class MetricsContext:
    """
    Context manager no-op.

    Mantido apenas para compatibilidade com os bots que ainda chamam
    métodos de métricas, sem persistir ou processar telemetry.
    """
    
    def __init__(self, robot_name: str):
        self.robot_name = robot_name
        self.session_id = ""
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        return False
    
    def record_step(self, step_name: str, success: bool = True, error_msg: Optional[str] = None):
        return None
    
    def increment_tasks(self, count: int = 1, success: bool = True):
        return None
    
    def increment_cycles(self, count: int = 1):
        return None
    
    def add_metric(self, key: str, value):
        return None
