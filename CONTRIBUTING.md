# Contributing to TurbineScope

Thank you for contributing to **TurbineScope**!

## Development Setup

```bash
git clone https://github.com/fahim-m0ntasir/TurbineScope.git
cd TurbineScope
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux / macOS:
source .venv/bin/activate

pip install -r requirements.txt
pip install pyflakes pyright
```

## Verification Checks

Before opening a pull request, please ensure all static checks and automated tests pass cleanly:

```bash
pyflakes vawt_post
pyright vawt_post
pytest tests -q
```

For headless Linux environments without an X display server:

```bash
QT_QPA_PLATFORM=offscreen pytest tests -q
```

## Guidelines

1. **Preserve Solver Fidelity**: Never alter aerodynamic physics equations, Fluent sign conventions, or ASME V&V 20 formulas without accompanying unit tests and physical validation.
2. **Keep Tests Deterministic**: Add unit/integration tests under `tests/` for new features or bug fixes using synthetic arrays or fixtures in `tests/data/`. Do not commit large binary `.cas.h5`, `.dat.h5`, or `.msh` simulation outputs.
3. **Cross-Platform Hygiene**: Avoid hardcoding OS-specific file paths in library code and use forward-compatible Qt 6 APIs.
