# Third-Party Notices

## browser-use

ProcureAgent was created as a business-specific refactor of an existing
open-source web-agent project. Its design references the original project's
ideas around CDP session lifecycle, bounded structured actions, structured
output, and keeping browser concerns behind an executor boundary.

No substantial `browser_use` source code is copied into ProcureAgent. The web
executor is a clean-room implementation using Playwright's public API.

The original project's MIT license is retained in `LICENSE` for attribution.

- Project: browser-use
- License: MIT
- Copyright: Copyright (c) 2024 Gregor Zunic

## Runtime dependencies

The application uses the following packages through their public APIs:

- FastAPI (MIT)
- Pydantic (MIT)
- SQLAlchemy (MIT)
- httpx (BSD-3-Clause)
- Playwright (Apache-2.0)
- uvicorn (BSD-3-Clause)
- pydantic-settings (MIT)
- python-dotenv (BSD-3-Clause)
