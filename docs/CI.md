# CI and local checks

`python scripts/check.py` is the single definition of CI. GitHub Actions runs it, and the
`.githooks/pre-push` hook runs it before anything leaves the machine. Enable the hook once:

```bash
git config core.hooksPath .githooks
```

What it runs: workflow schema (`check-jsonschema`) and **actionlint**, ruff lint and format, mypy for
win32/linux/darwin, pytest (unit), Biome, `tsc` and vitest for the TypeScript workspace.

`check.py` ends with a production build of the web app, which replaces `apps/web/.next`. If the
local stack is up (`dev.ps1` serves the web app with `next start`), that server keeps serving the old
build and the pages stop hydrating (buttons stay disabled, client-rendered text never appears).
Run `.\scripts\dev.ps1 restart web` after `check.py` or after a push, before any browser test.

## Installing actionlint (required, validates workflow expressions)

- Windows (PowerShell): download `actionlint_<ver>_windows_amd64.zip` from
  <https://github.com/rhysd/actionlint/releases> and extract `actionlint.exe` to
  `$env:LOCALAPPDATA\ParallaxOps\tools\actionlint\` (the check script finds it there).
- Linux/macOS: `brew install actionlint`, or put the release binary anywhere on `PATH`.

## Reproducing the Linux runner locally

Linux differs from Windows in system libraries (Open3D needs `libusb-1.0-0 libgomp1 libgl1 libegl1`)
and in `sys.platform` type narrowing. Before pushing a CI change, run the same steps in a Linux
shell (WSL works): clone, `uv venv --python 3.14`, `uv pip install -e "services/vision-worker[dev]"
check-jsonschema types-psutil`, `pnpm install --frozen-lockfile`, `python scripts/check.py`.
Set `PARALLAXOPS_HOME` to a scratch directory so the pnpm virtual store stays outside the checkout.
