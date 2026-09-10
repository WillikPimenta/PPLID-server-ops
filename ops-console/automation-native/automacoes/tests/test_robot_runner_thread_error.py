import pytest

from app.orchestration.robot_runner import _raise_thread_error


class _ThreadResult:
    execution_error = None


def test_raise_thread_error_ignora_execucao_bem_sucedida():
    _raise_thread_error(_ThreadResult())


def test_raise_thread_error_propaga_falha_do_robo():
    thread = _ThreadResult()
    thread.execution_error = ValueError("plano vazio")

    with pytest.raises(RuntimeError, match="plano vazio") as exc_info:
        _raise_thread_error(thread)

    assert exc_info.value.__cause__ is thread.execution_error
