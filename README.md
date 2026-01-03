# SWEA (Sandboxed Workspace Environment for Agents)

A Docker-based isolated execution environment for running AI code generation tasks using OpenAI's Codex CLI. SWEA provides a secure, reproducible sandbox where AI agents can generate, execute, and test code without affecting the host system.

## Features

- **Isolated Docker Environment**: Runs all code generation in an Ubuntu 24.04 container
- **Container Reuse**: Automatically reuses existing containers to save initialization time
- **Pre-configured Tooling**: Comes with Node.js 22.x, Python 3, uv, and Playwright pre-installed
- **Codex CLI Integration**: Built-in support for OpenAI's Codex CLI with `--yolo` mode for autonomous execution
- **Visual Testing Support**: Integrated vibetest-use for browser-based UI testing via MCP
- **Stuck Process Detection**: Automatically detects and kills hung processes
- **Persistent Workspace**: Docker volume mounts preserve workspace data across restarts
- **Git Integration**: Workspace is initialized as a git repository for version control

## Requirements

- Python 3.13+
- Docker Engine (running and accessible)
- [uv](https://docs.astral.sh/uv/) package manager

## Installation

```bash
git clone <repository-url>
cd SWEA

uv sync
```

## Configuration

### Environment Variables

Create a `.env` file in the project root:

```env
GOOGLE_API_KEY=your-google-api-key-here
```

The `GOOGLE_API_KEY` is required for vibetest MCP functionality.

### Codex CLI Configuration

The container is configured via `SAMPLE_config.toml` which is synced to `/root/.codex/config.toml` inside the container:

```toml
model = "gpt:latest"
model_provider = "ollama"

[model_providers.ollama]
name = "Ollama"
base_url = "http://localhost:11434/v1"
wire_api = "responses"

[mcp_servers.vibetest]
command = "/root/vibetest-use/.venv/bin/vibetest-mcp"

[mcp_servers.vibetest.env]
GOOGLE_API_KEY = "${GOOGLE_API_KEY}"

[projects]
[projects."/root/workspace"]
trust_level = "trusted"
```

### Agent Instructions

The `SAMPLE_AGENTS.md` file contains instructions that guide the AI agent's behavior. This file is synced to `/root/workspace/AGENTS.md` inside the container and defines coding standards, testing requirements, and output constraints.

## Usage

### Basic Usage

```python
from swea.docker_vm import DockerVM

with DockerVM() as vm:
    result = vm.codex_exec("Create a simple hello world web app")
    
    if result.success:
        print(result.stdout)
    else:
        print(result.stderr)
```

### Running via CLI

```bash
uv run main
```

Or with a custom prompt:

```bash
uv run python -c "from swea.docker_vm import main; main()" "Build a REST API with FastAPI"
```

### Command Execution

Execute arbitrary commands inside the container:

```python
with DockerVM() as vm:
    result = vm.execute("ls -la /root/workspace")
    print(result.stdout)
```

### Script Execution

Execute multi-line scripts:

```python
with DockerVM() as vm:
    script = """
    cd /root/workspace
    echo "Hello from SWEA"
    python3 --version
    """
    result = vm.execute_script(script)
    print(result.stdout)
```

## API Reference

### DockerVM

The main class for managing Docker containers and executing commands.

#### Constructor Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `image` | `str` | `"ubuntu:24.04"` | Docker image to use |
| `name` | `str` | `"swea"` | Container name |
| `working_dir` | `str` | `"/root"` | Default working directory |

#### Methods

##### `start() -> DockerVM`
Starts the container or reuses an existing one. Called automatically when using context manager.

##### `stop() -> None`
Stops the running container without removing it.

##### `remove() -> None`
Stops and removes the container.

##### `execute(command, workdir=None, environment=None, user="root") -> CommandResult`
Executes a command inside the container.

| Parameter | Type | Description |
|-----------|------|-------------|
| `command` | `str \| list[str]` | Command to execute |
| `workdir` | `str \| None` | Working directory (defaults to `/root`) |
| `environment` | `dict[str, str] \| None` | Environment variables |
| `user` | `str` | User to run as (defaults to `root`) |

##### `execute_script(script, interpreter="/bin/bash", workdir=None, environment=None) -> CommandResult`
Executes a multi-line script inside the container.

| Parameter | Type | Description |
|-----------|------|-------------|
| `script` | `str` | Script content |
| `interpreter` | `str` | Script interpreter (defaults to `/bin/bash`) |
| `workdir` | `str \| None` | Working directory |
| `environment` | `dict[str, str] \| None` | Environment variables |

##### `codex_exec(prompt, workdir=None, command_timeout=20) -> CommandResult`
Executes a Codex CLI prompt with stuck detection.

| Parameter | Type | Description |
|-----------|------|-------------|
| `prompt` | `str` | The prompt to send to Codex |
| `workdir` | `str \| None` | Working directory (defaults to `/root/workspace`) |
| `command_timeout` | `int` | Timeout in seconds for individual commands (defaults to 20) |

##### `reinitialize() -> None`
Re-syncs configuration files without recreating the container.

#### Properties

| Property | Type | Description |
|----------|------|-------------|
| `is_running` | `bool` | Whether the container is currently running |
| `container_id` | `str \| None` | Docker container ID |
| `was_reused` | `bool` | Whether an existing container was reused |

### CommandResult

Dataclass representing the result of a command execution.

#### Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `exit_code` | `int` | Command exit code |
| `stdout` | `str` | Standard output |
| `stderr` | `str` | Standard error |

#### Properties

| Property | Type | Description |
|----------|------|-------------|
| `success` | `bool` | `True` if exit code is 0 |

#### Methods

##### `raise_on_error() -> None`
Raises `RuntimeError` if the command failed.

## Container Initialization

When a new container is created, SWEA automatically installs:

1. **System Dependencies**: curl, ca-certificates, gnupg, git, python3, python3-pip, python3-venv
2. **Node.js 22.x**: Via NodeSource repository
3. **OpenAI Codex CLI**: Global npm installation of `@openai/codex`
4. **uv**: Astral's fast Python package manager
5. **vibetest-use**: Browser automation for visual testing
6. **Playwright Chromium**: Browser automation driver

The container uses:
- Host network mode for seamless localhost access
- Named volume `swea-data` mounted at `/root/workspace`
- Restart policy `unless-stopped` for persistence

## Project Structure

```
SWEA/
├── main.py              # Example entry point
├── pyproject.toml       # Project configuration and dependencies
├── uv.lock              # Locked dependencies
├── SAMPLE_config.toml   # Codex CLI configuration template
├── SAMPLE_AGENTS.md     # Agent instructions template
├── LICENSE              # Apache 2.0 License
├── README.md            # This file
└── swea/
    ├── __init__.py      # Package exports
    └── docker_vm.py     # Core DockerVM implementation
```

## Stuck Process Handling

SWEA includes automatic detection of stuck processes during Codex execution. When a command execution exceeds the timeout:

1. The execution is terminated
2. Common development server processes are killed:
   - `bun dev`, `npm run`
   - Node.js servers
   - Python HTTP servers, Flask, uvicorn, gunicorn
3. Ports 3000, 5000, 8000, 8080 are forcefully freed

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `docker` | ≥7.1.0 | Docker SDK for Python |
| `python-dotenv` | ≥1.0.0 | Environment variable loading |
| `browser-use` | ≥0.11.2 | Browser automation utilities |

## Development

### Running Tests

```bash
uv run pytest
```

### Linting

```bash
uv run ruff check .
```

### Rebuilding the Container

To force a fresh container creation:

```python
from swea.docker_vm import DockerVM

vm = DockerVM()
vm.start()
vm.remove()

with DockerVM() as fresh_vm:
    pass
```

## Troubleshooting

### Container Not Starting

Ensure Docker is running:
```bash
docker info
```

### Permission Denied

The container runs as root by default. If you encounter permission issues with the Docker socket:
```bash
sudo usermod -aG docker $USER
newgrp docker
```

### Codex CLI Not Working

Verify Codex is installed inside the container:
```python
with DockerVM() as vm:
    result = vm.execute("codex --version")
    print(result.stdout)
```

### vibetest MCP Issues

Ensure `GOOGLE_API_KEY` is set in your `.env` file and the container is reinitialized:
```python
with DockerVM() as vm:
    vm.reinitialize()
```

## License

Apache License 2.0 - see [LICENSE](LICENSE) for details.
