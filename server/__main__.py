"""Run: .venv\\Scripts\\python.exe -m server [--demo] [--port 8765]"""

from __future__ import annotations

import argparse

import uvicorn

from .app import create_app

HOST = "127.0.0.1"   # localhost only, never 0.0.0.0


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m server", description="Kia remote car service")
    parser.add_argument("--demo", action="store_true", help="fake car: no Kia calls, no saved secrets")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", help="folder for presets, logs and passkeys "
                        "(default: kia-remote in %%LOCALAPPDATA%% on Windows, ~/Library/Application Support "
                        "on macOS, ~/.local/share on Linux)")
    args = parser.parse_args()
    app = create_app(demo=args.demo, data_dir=args.data_dir)
    print(f"Kia remote {'(demo) ' if args.demo else ''}on http://{HOST}:{args.port} "
          f"(for fingerprint unlock on this PC use http://localhost:{args.port})")
    uvicorn.run(app, host=HOST, port=args.port, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
