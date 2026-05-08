from __future__ import annotations

import uvicorn

from router_app.app import create_app

app = create_app()


def run() -> None:
    uvicorn.run("router_app.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    run()

