"""
Bot Confer Monitor - Automação inicial no Okta/BRFlow via Chrome.

Fluxo:
1) Abre Chrome
2) Faz login no Okta
3) Pesquisa por "confer"
4) Abre aplicação em nova aba e aguarda carregamento inicial
"""
import os
import time
import logging
import csv
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List
import pandas as pd
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException, ElementClickInterceptedException
from app.infrastructure.selenium_helpers import click_element, send_keys_to_element, take_error_screenshot, create_driver, login_okta_resiliente
from app.infrastructure.csv_processing import read_csv as cp_read_csv, wait_for_new_file as cp_wait_for_new_file
from app.config import configure_logging, LOG_LEVEL_NUM
from app.config.settings import Config
from app.config.paths import DEFAULT_DOWNLOAD, DEFAULT_SHAREPOINT_BASES, DEFAULT_SHAREPOINT_PRODUCAO
from app.config.selectors import brflow, okta
from app.core.bot_runtime import BotRuntime
from app.core.common import safe_close_driver, MetricsContext
from app.infrastructure.confer_helpers import (
    CONFER_HOME_XPATH,
    aguardar_arquivo_estavel as _aguardar_arquivo_estavel,
    click_xpath_com_fallback_js as _click_xpath_com_fallback_js,
    fechar_calendario_se_aberto as _fechar_calendario_se_aberto,
    navegar_busca_confer as _navegar_busca_confer_impl,
    recuperar_menu_confer_se_tela_login as _recuperar_menu_confer_se_tela_login_impl,
    renomear_download_com_sufixo as _renomear_download_com_sufixo,
)


log = logging.getLogger("robots.bot_confer")

_runtime = BotRuntime(mode="confer")
set_status_callback = _runtime.set_status_callback
set_progress_callback = _runtime.set_progress_callback
parar_event = _runtime.parar_event
_set_status = _runtime.set_status
_set_progress = _runtime.set_progress
_reset_progress_state = _runtime.reset_progress_state

TIMEOUT_DRIVER = 8
_timing_start = {}
_matriculas_relatorio: List[str] = []
_relatorios_log_eventos: List[str] = []
MAX_RETRY_DOWNLOAD_CONFER = max(1, int(os.getenv("CONFER_DOWNLOAD_RETRY", "4") or "4"))


def _navegar_busca_confer(drv):
	_navegar_busca_confer_impl(
		drv,
		status_fn=_set_status,
		progress_fn=_set_progress,
		timeout_driver=TIMEOUT_DRIVER,
		confer_home_xpath=CONFER_HOME_XPATH,
	)


def _recuperar_menu_confer_se_tela_login(drv, contexto: str = "") -> bool:
	return _recuperar_menu_confer_se_tela_login_impl(
		drv,
		contexto,
		status_fn=_set_status,
		timeout_driver=TIMEOUT_DRIVER,
		logger=log,
	)


def _configure_bot_logging():
	"""Garante configuração de logging (app + robots + arquivo dedicado do confer)."""
	try:
		configure_logging(level=LOG_LEVEL_NUM)
		confer_log = getattr(Config, "ROBOT_LOG_FILES", {}).get("confer")
		if confer_log:
			log.info(f"Log dedicado do bot confer: {confer_log}")
	except Exception as e:
		logging.basicConfig(level=logging.INFO)
		log.warning(f"Falha ao configurar logging centralizado: {e}")


_configure_bot_logging()


def set_status_callback(fn):
	_runtime.set_status_callback(fn)


def set_progress_callback(fn):
	_runtime.set_progress_callback(fn)


def get_matriculas_relatorio() -> List[str]:
	return list(_matriculas_relatorio)


def get_relatorios_log_eventos() -> List[str]:
	return list(_relatorios_log_eventos)


def _timing_start_step(step_name: str):
	_timing_start[step_name] = time.time()


def _timing_end_step(step_name: str):
	if step_name in _timing_start:
		elapsed = time.time() - _timing_start[step_name]
		log.info(f"⏱ {step_name}: {elapsed:.2f}s")
		del _timing_start[step_name]


def _get_tz_br():
	try:
		from zoneinfo import ZoneInfo
		return ZoneInfo("America/Sao_Paulo")
	except Exception:
		return timezone(timedelta(hours=-3))





def _get_confer_download_dir(settings=None) -> Path:
	base_cfg = (os.getenv("CONFER_OUTPUT_DIR") or os.getenv("ROBOT_OUTPUT_DIR") or "").strip()
	if base_cfg:
		download_dir = Path(base_cfg).expanduser() / "confer"
	else:
		download_dir = DEFAULT_DOWNLOAD / "confer"
	download_dir.mkdir(parents=True, exist_ok=True)
	return download_dir


def _create_chrome_driver(headless: bool = True, download_dir: Path = None):
	"""Cria driver Chrome usando o padrão centralizado de selenium_helpers."""
	download_dir = Path(download_dir or _get_confer_download_dir())
	download_dir.mkdir(parents=True, exist_ok=True)
	log.info("Iniciando Chrome driver (via create_driver)")
	return create_driver(headless=headless, download_dir=str(download_dir))


# ---------------------------------------------------------------------------
# Horário operacional – 06:00 até 22:00
# ---------------------------------------------------------------------------
_HORA_INICIO = 6
_HORA_FIM = 22  # exclusivo (bot NÃO roda às 22:00 em diante)


def _hora_atual_local() -> datetime:
	"""Retorna datetime atual no fuso de Brasília."""
	try:
		tz = _get_tz_br()
		return datetime.now(tz)
	except Exception:
		return datetime.now()


def _dentro_janela_operacional() -> bool:
	"""Retorna True se a hora corrente estiver no intervalo [06:00, 22:00)."""
	hora = _hora_atual_local().hour
	return _HORA_INICIO <= hora < _HORA_FIM


def _esperar_com_parar(segundos: int, intervalo_check: int = 30) -> bool:
	"""
	Aguarda `segundos` verificando parar_event a cada `intervalo_check` segundos.
	Retorna True se parar_event foi acionado.
	"""
	deadline = time.time() + segundos
	while time.time() < deadline:
		remaining = deadline - time.time()
		slice_sec = min(intervalo_check, remaining)
		if slice_sec <= 0:
			break
		if parar_event.wait(slice_sec):
			return True
	return False


def start(settings=None):
	parar_event.clear()
	s = settings or {}

	_run_counter = 0
	ultimo_slot_execucao = None
	primeira_execucao_pendente = True
	while not parar_event.is_set():
		if not _dentro_janela_operacional():
			agora = _hora_atual_local()
			_set_status(
				f"Fora do horário operacional ({agora.strftime('%H:%M')}). "
				f"Bot ativo apenas entre {_HORA_INICIO:02d}:00 e {_HORA_FIM:02d}:00. Aguardando..."
			)
			# Verifica a cada 60 s se já entrou na janela ou se deve parar
			if _esperar_com_parar(60):
				break
			continue

		agora = _hora_atual_local()
		slot_atual = agora.replace(minute=0, second=0, microsecond=0)
		esta_na_janela_disparo = agora.minute == 0 and agora.second < 60

		if primeira_execucao_pendente:
			_run_counter += 1
			_set_status(f"Iniciando execução imediata {_run_counter} do bot confer")
			executar_bot_confer(settings=s)
			ultimo_slot_execucao = slot_atual
			primeira_execucao_pendente = False

			if parar_event.is_set():
				break
			continue

		if not esta_na_janela_disparo or ultimo_slot_execucao == slot_atual:
			proximo_disparo = (agora + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
			segundos_espera = max(1, int((proximo_disparo - agora).total_seconds()))
			_set_status(
				f"Aguardando próximo disparo no minuto 00 ({proximo_disparo.strftime('%H:%M')})"
			)
			if _esperar_com_parar(segundos_espera):
				break
			continue

		_run_counter += 1
		_set_status(f"Iniciando execução {_run_counter} do bot confer")
		executar_bot_confer(settings=s)
		ultimo_slot_execucao = slot_atual

		if parar_event.is_set():
			break

	_set_status("Bot confer interrompido")


def stop():
	parar_event.set()


def executar_bot_confer(settings=None):
	_reset_progress_state()
	_set_status("Confer: iniciando execucao")
	_set_progress(0, "Confer: iniciando execucao")

	s = settings or {}
	headless = s.get("headless")
	if headless is None:
		raw = (os.getenv("CONFER_HEADLESS") or os.getenv("ROBOT_HEADLESS") or os.getenv("HEADLESS") or "0").strip().lower()
		headless = raw in ("1", "true", "yes", "on")

	download_dir = _get_confer_download_dir(s)
	drv = _create_chrome_driver(headless=headless, download_dir=download_dir)
	with MetricsContext("bot_confer") as exec_ctx:
		try:
			_executar_ciclo(drv, exec_ctx, s)
		except (TimeoutException, WebDriverException) as e:
			log.exception("Erro Selenium durante execução do bot confer")
			log.error("Detalhe Selenium (repr): %r", e)
			if drv:
				take_error_screenshot(drv, "log_screenshots", "bot_confer_selenium_erro")
		except Exception as e:
			log.error(f"Erro inesperado: {e}", exc_info=True)
			if drv:
				take_error_screenshot(drv, "log_screenshots", "bot_confer_erro")
		finally:
			safe_close_driver(drv)
			_set_status("Confer: execucao finalizada")


def _executar_ciclo(drv, exec_ctx, settings):
	s = settings or {}
	_ = _get_tz_br()
	matricula = os.getenv("OKTA_USER") or ""
	senha = os.getenv("OKTA_PASS") or ""
	global _matriculas_relatorio
	global _relatorios_log_eventos

	_timing_start_step("WebDriver Edge Creation")
	_timing_end_step("WebDriver Edge Creation")
	exec_ctx.record_step("driver_creation", success=True)
	_set_progress(10, "Infra: driver iniciado")

	if okta is None or brflow is None:
		
		_set_status("Seletores (path.py) não configurados")
		safe_close_driver(drv)
		return

	_timing_start_step("Okta Authentication")
	_fazer_login_okta(drv, matricula, senha)
	_timing_end_step("Okta Authentication")


	_timing_start_step("Confer Search")
	_navegar_busca_confer(drv)
	_timing_end_step("Confer Search")
	_matriculas_relatorio = baixar_relatorio_producao(drv, s)
	log.info(f"Matrículas únicas extraídas: {len(_matriculas_relatorio)}")
	_relatorios_log_eventos = log_eventos(drv, _matriculas_relatorio, s)
	log.info(f"Relatórios baixados em Log Eventos: {len(_relatorios_log_eventos)}")
	_set_progress(100, "Confer: processamento concluido")
	_set_status("Confer: execucao concluida")


def _fazer_login_okta(drv, matricula, senha):
	_set_status("Iniciando autenticação Okta")
	_set_progress(20, "Autenticacao: acessando Okta")
	drv.get(okta.O_LINK)
	login_okta_resiliente(drv, matricula, senha, timeout=TIMEOUT_DRIVER)

	_set_progress(45, "Autenticacao: Okta concluido")


def _selecionar_matricula_ngx_select(drv, toggle_xpath: str, input_xpath: str, matricula: str, timeout_sec: int = 8):
	wait = WebDriverWait(drv, timeout_sec)
	_click_xpath_com_fallback_js(drv, toggle_xpath)

	input_xpaths = [
		input_xpath,
		"//app-log-eventos//ngx-select//input",
		"//ngx-select//input",
	]

	input_elemento = None
	for xp in input_xpaths:
		try:
			wait.until(EC.presence_of_element_located((By.XPATH, xp)))
			candidatos = drv.find_elements(By.XPATH, xp)
			for candidato in candidatos:
				if candidato.is_displayed() and candidato.is_enabled():
					input_elemento = candidato
					break
			if input_elemento:
				break
		except Exception:
			continue

	if input_elemento is None:
		raise TimeoutException("Não foi possível localizar o campo de busca do ngx-select para matrícula")

	try:
		input_elemento.click()
	except Exception:
		pass

	try:
		input_elemento.send_keys(Keys.CONTROL, "a")
		input_elemento.send_keys(Keys.DELETE)
	except Exception:
		pass

	input_elemento.send_keys(str(matricula))
	time.sleep(0.2)
	input_elemento.send_keys(Keys.ENTER)


def _remover_arquivo_se_existir(caminho: Path, descricao: str = "arquivo temporário") -> bool:
	if not caminho:
		return False

	try:
		caminho = Path(caminho)
		if not caminho.exists() or not caminho.is_file():
			return False
		caminho.unlink()
		log.info(f"{descricao.capitalize()} removido: {caminho.name}")
		return True
	except Exception as e:
		log.warning(f"Falha ao remover {descricao} {Path(caminho).name}: {e}")
		return False


def _limpar_arquivos_temporarios(caminhos: List[Path], preservar: List[Path] = None):
	preservar_resolvidos = set()
	for caminho in preservar or []:
		try:
			preservar_resolvidos.add(str(Path(caminho).resolve()))
		except Exception:
			continue

	vistos = set()
	for caminho in caminhos or []:
		if not caminho:
			continue

		path_obj = Path(caminho)
		try:
			path_resolvido = str(path_obj.resolve())
		except Exception:
			path_resolvido = str(path_obj)

		if path_resolvido in vistos or path_resolvido in preservar_resolvidos:
			continue

		vistos.add(path_resolvido)
		_remover_arquivo_se_existir(path_obj, "arquivo temporário")


def _identificar_coluna(df, nomes_preferidos=None, termos_contidos=None):
	nomes_preferidos = [n.lower() for n in (nomes_preferidos or [])]
	termos_contidos = [t.lower() for t in (termos_contidos or [])]
	for col in df.columns:
		col_norm = str(col).strip().lower()
		if col_norm in nomes_preferidos:
			return col
	for col in df.columns:
		col_norm = str(col).strip().lower()
		if any(term in col_norm for term in termos_contidos):
			return col
	return None


def _formatar_datahora_sessao(valor) -> str:
	if pd.isna(valor):
		return ""
	if isinstance(valor, pd.Timestamp):
		return valor.strftime("%d/%m/%Y %H:%M:%S")
	return str(valor or "").strip()


def _formatar_datetime_tabela(valor) -> str:
	if pd.isna(valor):
		return ""
	if isinstance(valor, pd.Timestamp):
		return valor.strftime("%Y-%m-%d %H:%M:%S")
	return str(valor or "").strip()


def _formatar_data_base_tabela(valor) -> str:
	if pd.isna(valor):
		return ""
	if isinstance(valor, pd.Timestamp):
		return valor.strftime("%Y-%m-%d")
	return str(valor or "").strip()


def _gerar_csv_sessoes_por_evento(caminho_csv: Path, matricula: str = None) -> Path:
	df = cp_read_csv(
		caminho_csv,
		dtype=str,
		encodings=("utf-8-sig", "utf-8", "cp1252", "latin-1", "iso-8859-1"),
	)
	if df.empty:
		log.warning(f"CSV vazio para processamento de sessões: {caminho_csv.name}")
		return caminho_csv

	col_evento = _identificar_coluna(df, nomes_preferidos=["evento"], termos_contidos=["evento"])
	if col_evento is None:
		log.warning(f"Coluna 'Evento' não encontrada em {caminho_csv.name}")
		return caminho_csv

	col_nome = _identificar_coluna(
		df,
		nomes_preferidos=["nome do colaborador", "nome colaborador", "colaborador", "usuario", "usuário"],
		termos_contidos=["colaborador", "usu", "nome"],
	)
	col_matricula = _identificar_coluna(
		df,
		nomes_preferidos=["matrícula do colaborador", "matricula do colaborador", "matricula", "matrícula"],
		termos_contidos=["matric"],
	)

	col_datahora = _identificar_coluna(
		df,
		nomes_preferidos=["data/hora", "data hora", "datahora", "data e hora"],
		termos_contidos=["data", "hora"],
	)

	df_proc = df.copy()
	df_proc["__ordem"] = range(len(df_proc))
	if col_datahora is not None:
		df_proc["__dt"] = pd.to_datetime(df_proc[col_datahora], errors="coerce", dayfirst=True)
		df_proc = df_proc.sort_values(by=["__dt", "__ordem"], na_position="last")
	else:
		df_proc["__dt"] = pd.NaT
		df_proc = df_proc.sort_values(by=["__ordem"])

	if len(df_proc) == 0:
		log.warning(f"CSV sem linhas para processar sessões: {caminho_csv.name}")
		return caminho_csv

	def _montar_linha_sessao(
		sessao_id: int,
		row_inicio=None,
		row_logout=None,
		renomear_inicio_autenticacao: bool = False,
		forcar_evento_logout: bool = False,
		status_pendente: bool = False,
	):
		tem_inicio = row_inicio is not None
		tem_logout = row_logout is not None
		row_inicio = row_inicio if tem_inicio else {}
		row_logout = row_logout if tem_logout else {}
		matricula_linha = str(
			matricula
			or (row_inicio.get(col_matricula, "") if col_matricula else "")
			or (row_logout.get(col_matricula, "") if col_matricula else "")
			or ""
		).strip()
		nome_linha = ""
		if col_nome:
			nome_linha = str(row_inicio.get(col_nome) or row_logout.get(col_nome) or "").strip()

		dt_inicio = row_inicio.get("__dt") if isinstance(row_inicio, pd.Series) else pd.NaT
		dt_logout = row_logout.get("__dt") if isinstance(row_logout, pd.Series) else pd.NaT
		tempo_logado = ""
		if pd.notna(dt_inicio) and pd.notna(dt_logout) and dt_logout >= dt_inicio:
			tempo_logado = _formatar_timedelta_hhmmss(dt_logout - dt_inicio)

		evento_inicio = str(row_inicio.get(col_evento, "")).strip()
		if renomear_inicio_autenticacao and tem_inicio:
			evento_inicio = "Autenticação com sucesso"

		evento_logout = str(row_logout.get(col_evento, "")).strip()
		# Segundo evento é sempre exibido como "Logout"
		evento_logout = "Logout" if tem_logout else ""

		dt_evento = dt_inicio if pd.notna(dt_inicio) else pd.to_datetime(row_inicio.get(col_datahora, ""), errors="coerce", dayfirst=True)
		dt_segundo_evento = dt_logout if pd.notna(dt_logout) else pd.to_datetime(row_logout.get(col_datahora, ""), errors="coerce", dayfirst=True)

		hora = ""
		if pd.notna(dt_evento):
			hora = str(int(dt_evento.hour))

		return {
			"Data": _formatar_data_base_tabela(dt_evento),
			"Hora": hora,
			"matricula": matricula_linha,
			"Data do Evento": _formatar_datetime_tabela(dt_evento),
			"Evento": evento_inicio,
			"Data segundo evento": _formatar_datetime_tabela(dt_segundo_evento),
			"Segundo evento": evento_logout,
		}

	linhas_sessoes = []
	sessao_id = 1

	if col_nome is not None:
		df_proc["__usuario_key"] = df_proc[col_nome].fillna("").astype(str).str.strip()
	else:
		df_proc["__usuario_key"] = ""

	default_usuario = str(matricula or "usuario").strip() or "usuario"
	df_proc["__usuario_key"] = df_proc["__usuario_key"].replace("", default_usuario)

	for _, grupo in df_proc.groupby("__usuario_key", sort=False):
		inicio_atual = None
		ultimo_evento = None

		for _, row in grupo.iterrows():
			evento = str(row[col_evento]).strip()
			evento_norm = evento.lower()

			# Ignora completamente eventos "Troca de Etapa"
			if "troca de etapa" in evento_norm:
				log.debug("Evento ignorado (Troca de Etapa): %s", evento)
				continue

			ultimo_evento = row
			eh_logout = "logout" in evento_norm

			if eh_logout:
				linhas_sessoes.append(
					_montar_linha_sessao(
						sessao_id,
						row_inicio=inicio_atual,
						row_logout=row,
						renomear_inicio_autenticacao=True,
					)
				)
				sessao_id += 1
				inicio_atual = None
				continue

			if inicio_atual is None:
				inicio_atual = row

		if inicio_atual is not None:
			linhas_sessoes.append(
				_montar_linha_sessao(
					sessao_id,
					row_inicio=inicio_atual,
					row_logout=ultimo_evento,
					renomear_inicio_autenticacao=True,
					forcar_evento_logout=False,
					status_pendente=True,
				)
			)
			sessao_id += 1

	df_evt = pd.DataFrame(
		linhas_sessoes,
		columns=[
			"Data",
			"Hora",
			"matricula",
			"Data do Evento",
			"Evento",
			"Data segundo evento",
			"Segundo evento",
		],
	)

	arquivo_saida = caminho_csv.with_name(f"{caminho_csv.stem}_sessoes.csv")
	df_evt.to_csv(
		arquivo_saida,
		index=False,
		encoding="utf-8-sig",
		sep=";",
		quotechar='"',
		quoting=csv.QUOTE_ALL,
	)
	log.info(f"CSV de sessões gerado: {arquivo_saida.name} ({len(df_evt)} linhas)")
	return arquivo_saida


def _formatar_timedelta_hhmmss(valor_timedelta: pd.Timedelta) -> str:
	if pd.isna(valor_timedelta):
		return "00:00:00"
	total_segundos = int(max(0, valor_timedelta.total_seconds()))
	horas = total_segundos // 3600
	minutos = (total_segundos % 3600) // 60
	segundos = total_segundos % 60
	return f"{horas:02d}:{minutos:02d}:{segundos:02d}"


def _calcular_resumo_tempos_sessoes(df_sessoes: pd.DataFrame) -> pd.DataFrame:
	if df_sessoes.empty:
		return pd.DataFrame(columns=["matricula_ref", "tempo_logado", "tempo_deslogado", "sessoes"]) 

	col_matricula = _identificar_coluna(df_sessoes, nomes_preferidos=["matricula_ref"], termos_contidos=["matric"])
	col_tipo_evento = _identificar_coluna(df_sessoes, nomes_preferidos=["tipo_evento"], termos_contidos=["tipo_evento"])
	col_datahora = _identificar_coluna(
		df_sessoes,
		nomes_preferidos=["data do evento", "data/hora", "data hora", "datahora", "data e hora"],
		termos_contidos=["data", "hora"],
	)

	if not col_matricula or not col_tipo_evento or not col_datahora:
		log.warning("Não foi possível calcular resumo de tempos: colunas obrigatórias ausentes")
		return pd.DataFrame(columns=["matricula_ref", "tempo_logado", "tempo_deslogado", "sessoes"]) 

	df = df_sessoes.copy()
	df["__matricula"] = df[col_matricula].fillna("").astype(str).str.strip()
	df = df[df["__matricula"] != ""].copy()
	if df.empty:
		return pd.DataFrame(columns=["matricula_ref", "tempo_logado", "tempo_deslogado", "sessoes"]) 

	df["__tipo"] = df[col_tipo_evento].fillna("").astype(str).str.strip().str.lower()
	df["__dt"] = pd.to_datetime(df[col_datahora], errors="coerce", dayfirst=True)
	df = df.dropna(subset=["__dt"]).copy()
	if df.empty:
		return pd.DataFrame(columns=["matricula_ref", "tempo_logado", "tempo_deslogado", "sessoes"]) 

	df = df.sort_values(by=["__matricula", "__dt"]).copy()

	linhas_resumo = []
	for matricula, grupo in df.groupby("__matricula", sort=True):
		tempo_logado = pd.Timedelta(0)
		tempo_deslogado = pd.Timedelta(0)
		ultimo_logout = None
		inicio_atual = None
		sessoes = 0

		for _, row in grupo.iterrows():
			tipo = str(row["__tipo"])
			dt = row["__dt"]

			eh_inicio = "inicio" in tipo
			eh_logout = "logout" in tipo

			if eh_inicio:
				sessoes += 1
				if ultimo_logout is not None and dt >= ultimo_logout:
					tempo_deslogado += (dt - ultimo_logout)
				inicio_atual = dt

			if eh_logout:
				if inicio_atual is not None and dt >= inicio_atual:
					tempo_logado += (dt - inicio_atual)
				elif ultimo_logout is None:
					sessoes = max(1, sessoes)
				ultimo_logout = dt
				inicio_atual = None

		linhas_resumo.append({
			"matricula_ref": matricula,
			"tempo_logado": _formatar_timedelta_hhmmss(tempo_logado),
			"tempo_deslogado": _formatar_timedelta_hhmmss(tempo_deslogado),
			"sessoes": int(max(1, sessoes)) if len(grupo) > 0 else 0,
		})

	return pd.DataFrame(linhas_resumo)


def _preparar_dataframe_para_parquet(df: pd.DataFrame) -> pd.DataFrame:
	"""Normaliza colunas para escrita robusta em parquet."""
	if df is None or df.empty:
		return df

	df_parquet = df.copy()
	colunas_convertidas = []

	for coluna in df_parquet.select_dtypes(include=["object"]).columns:
		df_parquet[coluna] = df_parquet[coluna].astype("string")
		colunas_convertidas.append(coluna)

	if colunas_convertidas:
		log.info(f"Parquet: colunas normalizadas para string: {', '.join(colunas_convertidas)}")

	return df_parquet


def _consolidar_csv_sessoes(caminhos_sessoes: List[str], pasta_destino: Path) -> Path:
	arquivos_validos = [Path(p) for p in caminhos_sessoes if p and Path(p).exists()]
	if not arquivos_validos:
		return None

	data_ref = time.strftime("%d%m%Y")
	arquivo_final = DEFAULT_SHAREPOINT_BASES / "confer" / f"Monitor_confer{data_ref}.parquet"

	frames = []
	for arquivo in arquivos_validos:
		try:
			df = cp_read_csv(
				arquivo,
				dtype=str,
				encodings=("utf-8-sig", "utf-8", "cp1252", "latin-1", "iso-8859-1"),
			)
			if not df.empty:
				frames.append(df)
		except Exception as e:
			log.warning(f"Falha ao ler CSV de sessões {arquivo.name}: {e}")

	if not frames:
		return None

	df_final = pd.concat(frames, ignore_index=True)
	colunas_ordenacao = [c for c in ["matricula", "Data do Evento"] if c in df_final.columns]
	if colunas_ordenacao:
		df_final = df_final.sort_values(by=colunas_ordenacao, kind="stable").reset_index(drop=True)

	df_parquet = _preparar_dataframe_para_parquet(df_final)
	df_parquet.to_parquet(
		arquivo_final,
		index=False,
		compression="snappy",
	)

	log.info(f"Parquet consolidado de sessões gerado: {arquivo_final.name} ({len(df_final)} linhas)")
	return arquivo_final


def _esperar_download_relatorio(before=None, s=None) -> Path:
	s = s or {}
	local_download = _get_confer_download_dir(s)
	local_download.mkdir(parents=True, exist_ok=True)

	before = set(before or [])
	_set_progress(92, "Confer: aguardando download")
	novo = cp_wait_for_new_file(before, local_download, "relatorio", ".csv", timeout=180)

	if novo is None:
		raise TimeoutException(f"Arquivo relatorio*.csv não detectado em {local_download}")

	if not _aguardar_arquivo_estavel(novo, timeout_sec=30):
		log.warning(f"Arquivo {novo.name} detectado, mas não confirmou estabilização de tamanho no tempo esperado")

	_set_status(f"Download concluído: {novo.name}")
	return novo


def _baixar_relatorio_com_retry_timeout(
	drv,
	botao_xpath: str,
	before_download,
	settings=None,
	descricao: str = "download do relatório",
	max_tentativas: int = 4,
) -> Path:
	"""Tenta baixar o relatório novamente em caso de timeout apenas reapertando o botão."""
	ultima_excecao = None
	before = set(before_download or [])

	for tentativa in range(1, max_tentativas + 1):
		if tentativa > 1:
			log.warning(
				f"Retry {tentativa}/{max_tentativas} para {descricao}: reapertando botão após timeout"
			)
			_set_status(f"Timeout ao baixar ({descricao}). Nova tentativa {tentativa}/{max_tentativas}...")

		_click_xpath_com_fallback_js(drv, botao_xpath)

		try:
			return _esperar_download_relatorio(before=before, s=settings)
		except TimeoutException as e:
			ultima_excecao = e
			log.warning(
				f"Timeout no {descricao} (tentativa {tentativa}/{max_tentativas})."
			)
			if tentativa < max_tentativas:
				time.sleep(1)

	raise TimeoutException(
		f"Falha no {descricao} após {max_tentativas} tentativas reapertando o botão"
	) from ultima_excecao


def _extrair_matriculas_unicas_csv(caminho_csv: Path) -> List[str]:
	df = cp_read_csv(caminho_csv, dtype=str)
	if df.empty:
		return []

	coluna_alvo = None
	for col in df.columns:
		if str(col).strip().lower() == "matrícula do colaborador":
			coluna_alvo = col
			break
		if str(col).strip().lower() == "matricula do colaborador":
			coluna_alvo = col
			break

	if coluna_alvo is None:
		raise KeyError("Coluna 'Matrícula do Colaborador' não encontrada no relatório")

	matriculas = []
	vistas = set()
	for valor in df[coluna_alvo].fillna("").astype(str):
		matricula = valor.strip()
		if not matricula or matricula in vistas:
			continue
		vistas.add(matricula)
		matriculas.append(matricula)

	return matriculas
	

def baixar_relatorio_producao(drv, settings=None):
	wait = WebDriverWait(drv, TIMEOUT_DRIVER)
	wait_short = WebDriverWait(drv, 6)
	menu_relatorios_xpath = "/html/body/app-root/app-home/main/app-menu/div/div[2]/div[2]/div/div/div[5]/div/div/div/a"
	painel_gestao_xpath = "/html/body/app-root/app-home/main/app-gestao/div/div[1]"
	data_inicial_xpath = "/html/body/app-root/app-home/main/app-gestao/app-relatorios/div/div/div/div[1]/div[1]/div[2]/div[2]/app-datepicker/div/div/div/input"
	data_final_xpath = "/html/body/app-root/app-home/main/app-gestao/app-relatorios/div/div/div/div[1]/div[1]/div[2]/div[3]/app-datepicker/div/div/div/input"
	radio_todos_xpath = "/html/body/app-root/app-home/main/app-gestao/app-relatorios/div/div/div/div[1]/div[2]/app-radio/div/div[2]/div/div[1]/div/input"
	botao_download_xpath = "/html/body/app-root/app-home/main/app-gestao/app-relatorios/div/div/div/div[2]/div/button/i"
	_set_status("Baixando relatório de produção")
	_set_progress(80, "Confer: baixando relatorio")
	_recuperar_menu_confer_se_tela_login(drv, contexto="antes do relatório de produção")
	click_element(drv, "xpath", menu_relatorios_xpath)
	try:
		wait_short.until(EC.presence_of_element_located((By.XPATH, painel_gestao_xpath)))
	except TimeoutException:
		wait.until(EC.presence_of_element_located((By.XPATH, painel_gestao_xpath)))
	click_element(drv, "xpath", painel_gestao_xpath)
	time.sleep(1)
	data_atual = time.strftime("%d/%m/%Y")
	send_keys_to_element(drv, "xpath", data_inicial_xpath, data_atual)
	send_keys_to_element(drv, "xpath", data_final_xpath, data_atual)
	_fechar_calendario_se_aberto(drv)
	_click_xpath_com_fallback_js(drv, radio_todos_xpath)
	local_download = _get_confer_download_dir(settings)
	before_download = {p.name for p in local_download.iterdir()} if local_download.exists() else set()
	novo_csv = _baixar_relatorio_com_retry_timeout(
		drv,
		botao_download_xpath,
		before_download,
		settings=settings,
		descricao="download do relatório de produção",
		max_tentativas=MAX_RETRY_DOWNLOAD_CONFER,
	)
	novo_csv = _renomear_download_com_sufixo(novo_csv, "confer_base")
	pasta_destino_base = Path(DEFAULT_SHAREPOINT_PRODUCAO)
	pasta_destino_base.mkdir(parents=True, exist_ok=True)
	arquivo_destino_base = pasta_destino_base / novo_csv.name
	try:
		matriculas_unicas = _extrair_matriculas_unicas_csv(novo_csv)
		if arquivo_destino_base.exists():
			arquivo_destino_base.unlink()
		shutil.move(str(novo_csv), str(arquivo_destino_base))
		log.info(f"Relatório base salvo em: {arquivo_destino_base}")
	except Exception:
		_remover_arquivo_se_existir(novo_csv, "relatório base de matrículas")
		raise
	log.info(f"Matrículas únicas extraídas do relatório: {len(matriculas_unicas)}")
	click_element(drv, "xpath", "/html/body/app-root/app-home/header/nav/div/div[2]/ul/li[3]/i")
	_set_progress(98, "Confer: relatorio processado")
	return matriculas_unicas

def log_eventos(drv, matriculas=None, settings=None):
	wait = WebDriverWait(drv, TIMEOUT_DRIVER)
	wait_short = WebDriverWait(drv, 6)
	matriculas_lista = list(dict.fromkeys((matriculas or get_matriculas_relatorio())))
	if not matriculas_lista:
		_set_status("Nenhuma matrícula disponível para Log Eventos")
		return []

	menu_log_eventos_xpath = "/html/body/app-root/app-home/main/app-menu/div/div[2]/div[2]/div/div/div[10]/div/div/div/a"
	data_log_eventos_xpath = "/html/body/app-root/app-home/main/app-gestao/app-log-eventos/div/div/div/div[1]/div[1]/app-datepicker/div/div/div/input"
	radio_todos_xpath = "/html/body/app-root/app-home/main/app-gestao/app-log-eventos/div/div/div/div[2]/div/app-radio/div/div[2]/div/div[1]/div/input"
	abrir_o_campo_matricula_xpath = "//app-log-eventos//ngx-select//div[contains(@class,'ngx-select__toggle')]"
	campo_matricula_xpath = "//app-log-eventos//ngx-select//input"
	botao_pesquisar_xpath = "/html/body/app-root/app-home/main/app-gestao/app-log-eventos/div/div/div/div[3]/div/button"
	botao_limpar_matricula_xpath = "/html/body/app-root/app-home/main/app-gestao/app-log-eventos/div/div/div/div[1]/div[2]/app-dropdown/div/div[2]/div/div[2]/button"

	def _abrir_pagina_log_eventos():
		try:
			wait_short.until(EC.element_to_be_clickable((By.XPATH, menu_log_eventos_xpath)))
		except TimeoutException:
			wait.until(EC.element_to_be_clickable((By.XPATH, menu_log_eventos_xpath)))
		click_element(drv, "xpath", menu_log_eventos_xpath)

		try:
			wait_short.until(EC.presence_of_element_located((By.XPATH, data_log_eventos_xpath)))
		except TimeoutException:
			wait.until(EC.presence_of_element_located((By.XPATH, data_log_eventos_xpath)))

		_click_xpath_com_fallback_js(drv, radio_todos_xpath)
		send_keys_to_element(drv, "xpath", data_log_eventos_xpath, time.strftime("%d/%m/%Y"))
		_fechar_calendario_se_aberto(drv)

	def _recuperar_e_reabrir_log_eventos(contexto: str):
		"""Tenta recuperar sessão no login do Confer e reabrir a tela de Log Eventos."""
		try:
			recuperou = _recuperar_menu_confer_se_tela_login(drv, contexto=contexto)
			if recuperou:
				log.info(f"[Confer][LoginRecovery] Sessão recuperada ({contexto})")
		except Exception as exc:
			log.warning(f"[Confer][LoginRecovery] Falha ao tentar recuperar sessão ({contexto}): {exc}")

		_abrir_pagina_log_eventos()

	def _preencher_matricula_log_eventos(matricula_alvo: str, max_tentativas: int = 3):
		ultima_excecao = None
		for tentativa in range(1, max_tentativas + 1):
			try:
				try:
					wait_short.until(EC.presence_of_element_located((By.XPATH, abrir_o_campo_matricula_xpath)))
				except TimeoutException:
					_recuperar_e_reabrir_log_eventos(
						f"entrada da matrícula não encontrada para {matricula_alvo} "
						f"(tentativa {tentativa}/{max_tentativas})"
					)
					try:
						wait_short.until(EC.presence_of_element_located((By.XPATH, abrir_o_campo_matricula_xpath)))
					except TimeoutException:
						wait.until(EC.presence_of_element_located((By.XPATH, abrir_o_campo_matricula_xpath)))

				_selecionar_matricula_ngx_select(drv, abrir_o_campo_matricula_xpath, campo_matricula_xpath, matricula_alvo)
				try:
					drv.find_element(By.XPATH, campo_matricula_xpath).send_keys(Keys.ENTER)
				except Exception:
					pass
				return
			except (TimeoutException, WebDriverException) as exc:
				ultima_excecao = exc
				log.warning(
					f"Falha ao preencher matrícula {matricula_alvo} no Log Eventos "
					f"(tentativa {tentativa}/{max_tentativas}): {exc}"
				)
				if tentativa < max_tentativas:
					_recuperar_e_reabrir_log_eventos(
						f"falha ao preencher matrícula {matricula_alvo} "
						f"(tentativa {tentativa}/{max_tentativas})"
					)

		raise ultima_excecao or TimeoutException(
			f"Não foi possível preencher a matrícula {matricula_alvo} no Log Eventos"
		)


	relatorios_baixados = []
	arquivos_temporarios = []
	local_download = _get_confer_download_dir(settings)
	local_download.mkdir(parents=True, exist_ok=True)
	_recuperar_menu_confer_se_tela_login(drv, contexto="início do Log Eventos")  # Tentativa inicial de recuperação antes de começar o loop
	for idx, matricula in enumerate(matriculas_lista, start=1):
		if _recuperar_menu_confer_se_tela_login(drv, contexto=f"antes do download da matrícula {matricula}"):
			log.info(f"Fluxo da matrícula {matricula}: sessão recuperada, reabrindo tela de Log Eventos")
			_abrir_pagina_log_eventos()

		_set_status(f"Log Eventos {idx}/{len(matriculas_lista)} - Matrícula {matricula}")
		_set_progress(0, "Confer monitor: baixando por matricula")
		_preencher_matricula_log_eventos(matricula)

		before_download = {p.name for p in local_download.iterdir()} if local_download.exists() else set()

		try:
			novo_csv = _baixar_relatorio_com_retry_timeout(
				drv,
				botao_pesquisar_xpath,
				before_download,
				settings=settings,
				descricao=f"download da matrícula {matricula}",
				max_tentativas=MAX_RETRY_DOWNLOAD_CONFER,
			)
			novo_csv = _renomear_download_com_sufixo(novo_csv, matricula)
			csv_sessoes = _gerar_csv_sessoes_por_evento(novo_csv, matricula=matricula)
			if Path(csv_sessoes) != Path(novo_csv):
				_remover_arquivo_se_existir(novo_csv, f"relatório individual da matrícula {matricula}")
			else:
				arquivos_temporarios.append(novo_csv)
			arquivos_temporarios.append(csv_sessoes)
			relatorios_baixados.append(str(csv_sessoes))
		except Exception as e:
			log.warning(f"Falha ao baixar relatório para matrícula {matricula}: {e}")

		try:
			_click_xpath_com_fallback_js(drv, botao_limpar_matricula_xpath)
		except Exception:
			pass

	consolidado = _consolidar_csv_sessoes(relatorios_baixados, local_download)
	if consolidado:
		_limpar_arquivos_temporarios(arquivos_temporarios, preservar=[consolidado])
		_set_status(f"Consolidado criado: {consolidado.name}")
		return [str(consolidado)]

	_limpar_arquivos_temporarios(arquivos_temporarios)

	return relatorios_baixados



if __name__ == "__main__":
	parar_event.clear()
	try:
		executar_bot_confer()

	except RuntimeError as e:
		sys.exit(2)


