import os
import sys

from app import create_app


def _check_package_installed() -> None:
    try:
        import app.orchestration.robot_runner  # noqa: F401
    except ImportError as exc:
        print(
            "Erro: pacote 'app' não encontrado. Execute: pip install -e .",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc


def main() -> None:
    _check_package_installed()
    app = create_app()
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", "50009"))
    debug = os.getenv("FLASK_DEBUG", "false").strip().lower() in ("1", "true", "yes", "on")
    app.run(host=host, port=port, debug=debug, use_reloader=False)


app = create_app()


if __name__ == "__main__":
    main()