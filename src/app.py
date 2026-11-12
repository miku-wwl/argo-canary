"""Deterministic demo responses; fault settings belong to each Pod revision."""

import os
import socket
import time

from flask import Flask, jsonify


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        RELEASE=os.getenv("APP_RELEASE", "development"),
        FAULT_MODE=os.getenv("FAULT_MODE", "healthy"),
        FAULT_DELAY_MS=int(os.getenv("FAULT_DELAY_MS", "800")),
        POD_NAME=os.getenv("HOSTNAME", socket.gethostname()),
    )
    if config:
        app.config.update(config)
    if app.config["FAULT_MODE"] not in {"healthy", "http500", "latency"}:
        raise ValueError("FAULT_MODE must be healthy, http500 or latency")
    if not 0 <= app.config["FAULT_DELAY_MS"] <= 10000:
        raise ValueError("FAULT_DELAY_MS must be between 0 and 10000")

    @app.get("/")
    def home():
        mode = app.config["FAULT_MODE"]
        if mode == "latency":
            time.sleep(app.config["FAULT_DELAY_MS"] / 1000)
        status = 500 if mode == "http500" else 200
        response = jsonify(
            message="Hello Auckland k8s Demo",
            release=app.config["RELEASE"],
            pod=app.config["POD_NAME"],
        )
        response.headers["X-App-Release"] = app.config["RELEASE"]
        return response, status

    @app.get("/health")
    @app.get("/healthz")
    def health():
        return "ok"

    @app.get("/readyz")
    def ready():
        # Configuration is validated before accepting requests. Business faults
        # deliberately leave probes healthy so analysis can measure them.
        return "ready"

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
