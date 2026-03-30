import asyncio
import argparse
import sys


def _configure_windows_event_loop_policy() -> None:
    if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the MediArch API development server.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--log-level", default="info")
    parser.add_argument("--reload", action="store_true", default=True)
    parser.add_argument("--no-reload", action="store_false", dest="reload")
    return parser


def main() -> None:
    _configure_windows_event_loop_policy()
    args = _build_parser().parse_args()

    import uvicorn

    uvicorn.run(
        "backend.api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
