"""Compatibility shim for running the API from the project root.

This project’s backend lives under the `backend/` package. Some commands/docs use
`uvicorn api.main:app` from within `backend/`, while others use
`uvicorn backend.api.main:app` from the repository root.

Having a top-level `api` package keeps `uvicorn api.main:app` working when the
current working directory is the repo root.
"""

