import os

from flask import Flask, make_response, request

from app.config.env import load_local_env
from .routes import api_bp, web_bp


def _cors_origins() -> list[str]:
    raw = os.getenv("FLASK_CORS_ORIGINS", "*")
    return [o.strip() for o in raw.split(",") if o.strip()]


def _apply_cors(response):
    if not request.path.startswith("/api"):
        return response
    allowed = _cors_origins()
    origin = request.headers.get("Origin", "")
    if "*" in allowed:
        response.headers["Access-Control-Allow-Origin"] = "*"
    elif origin and origin in allowed:
        response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


def create_app() -> Flask:
    load_local_env()
    app = Flask(__name__)
    app.register_blueprint(web_bp)
    app.register_blueprint(api_bp)

    @app.before_request
    def handle_api_preflight():
        if request.method == "OPTIONS" and request.path.startswith("/api"):
            return _apply_cors(make_response("", 204))

    @app.after_request
    def add_cors_headers(response):
        return _apply_cors(response)

    return app
