# Contributing to hikvision_local
Everybody is invited and welcome to contribute to hikvision_local integration.

-   Pull requests are created against the [**main**](https://github.com/brave0d/hikvision_local/tree/main) branch.
- Code must follow [Home Assistant code style guidelines](https://contributors.home-assistant.io/core/development/style/)
- Line length limit must be 120 characters
- Code is formatted and linted with ruff (`ruff format` and `ruff check`); `pre-commit install` runs both before each commit
- One change per commit, with a semantic prefix: `feat`, `fix`, `refactor`, `test`, `chore` or `docs`
- Pull requests are squash-merged; the PR title becomes the commit on `main`

## Development Environment
1. Set up the Home Assistant core development container as [Set up the development environment](https://contributors.home-assistant.io/core/development/environment/#development-container) describes. On Windows it runs inside WSL2.
2. Mount the integration into the container's config directory in `.devcontainer/devcontainer.json` of your core checkout. On Windows, clone hikvision_local inside WSL2 too and use its WSL path as the source:
```json
  "mounts": [
    "source=/path/on/your/disk/hikvision_local/custom_components/hikvision_local,target=${containerWorkspaceFolder}/config/custom_components/hikvision_local,type=bind"
  ]
```
3. Rebuild the container, then run the **Run Home Assistant Core** task or start debugging.

NOTE: the first container build and launch Home Assistant may take longer.

## Test Environment

Install Python 3.14.2 or later and project dependencies:
```
pip install -r requirements.test.txt
```
and run `pytest`. The tests use [pytest-homeassistant](https://github.com/home-assistant/core/tree/dev/packages/pytest-homeassistant), the test package Home Assistant publishes with each release; its README lists the fixtures and helpers it provides. CI runs the tests on the minimum supported Home Assistant (see `hacs.json`) and on the latest stable release. For running tests in debug mode vscode is recommended.

There are 2 types of tests:

### ISAPI communication
They test specific ISAPI requests and use data from `tests/fixtures/ISAPI`. They are focused on data processing. The folder structure corresponds to ISAPI endpoints, and the XML files contain device responses.

### The behavior of the Hikvision device in HomeAssistant

They initialize the entire device in the HomeAssistant environment and use data from `tests/fixtures/devices`. Each JSON file contains all the responses to GET requests sent by the given device.

The fixtures can be recorded for any device in the Device Info window by clicking DOWNLOAD DIAGNOSTICS button. All sensitive data such as MAC addresses, IPs, and serial numbers are anonymized so they can be safely made public.

This approach should make it easier to develop this integration for an even greater number of devices without the need for physical access to the device.

### Test Environment on Windows

Home Assistant development on Windows runs inside WSL2. Set it up as [Install Docker on Windows](https://contributors.home-assistant.io/core/development/environment/#install-docker-on-windows) describes, then follow the steps above inside it.

#### Run the tests in Docker
If you just want to run the tests, you can use Docker instead:

1. Install [Docker](https://docs.docker.com/desktop/setup/install/windows-install/).

2. In a cmd window run:
```
REM go to your hikvision_local directory. example:
cd C:\Users\john\Documents\github\hikvision_local

REM Build a docker container
docker build -f run_test.dockerfile -t hikvision-local-tests:latest .

REM Clear console and Run test in your container
cls && docker run --rm -v .:/app hikvision-local-tests:latest
```

#### Debug the tests in VS Code with Docker
The repository's `.vscode/tasks.json` builds the same image and starts pytest in it, and `.vscode/launch.json` attaches the debugger. Install [Docker](https://docs.docker.com/desktop/setup/install/windows-install/), add a breakpoint, then run "Docker: Python tests debug" (or "Docker: Python tests debug 1 file") in VS Code.
