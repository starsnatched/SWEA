from __future__ import annotations

import logging
import os
import re
import select
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import docker
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
from docker.errors import ContainerError, DockerException, ImageNotFound, NotFound

if TYPE_CHECKING:
    from docker.models.containers import Container

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

CONTAINER_NAME = "swea"


@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.exit_code == 0

    def raise_on_error(self) -> None:
        if not self.success:
            raise RuntimeError(
                f"Command failed with exit code {self.exit_code}: {self.stderr}"
            )


@dataclass
class DockerVM:
    image: str = "ubuntu:24.04"
    name: str = CONTAINER_NAME
    working_dir: str = "/root"
    _container: Container | None = field(default=None, init=False, repr=False)
    _client: docker.DockerClient | None = field(default=None, init=False, repr=False)
    _reused: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = docker.from_env()

    def _find_existing_container(self) -> Container | None:
        try:
            container = self._client.containers.get(self.name)
            return container
        except NotFound:
            return None

    def _ensure_image(self) -> None:
        try:
            self._client.images.get(self.image)
            logger.info(f"Image {self.image} already exists locally")
        except ImageNotFound:
            logger.info(f"Pulling image {self.image}...")
            self._client.images.pull(self.image)
            logger.info(f"Successfully pulled {self.image}")

    def start(self) -> DockerVM:
        if self._container is not None:
            raise RuntimeError("Container already started")

        existing = self._find_existing_container()
        if existing is not None:
            existing.reload()
            if existing.status == "running":
                logger.info(f"Reusing existing running container {self.name}")
                self._container = existing
                self._reused = True
                return self
            elif existing.status == "exited":
                logger.info(f"Starting existing stopped container {self.name}")
                existing.start()
                self._container = existing
                self._reused = True
                return self
            else:
                logger.info(f"Removing container {self.name} in state {existing.status}")
                existing.remove(force=True)

        self._ensure_image()

        logger.info(f"Creating container {self.name} from {self.image}...")
        self._container = self._client.containers.run(
            image=self.image,
            name=self.name,
            detach=True,
            tty=True,
            stdin_open=True,
            working_dir=self.working_dir,
            command="/bin/bash",
            user="root",
            restart_policy={"Name": "unless-stopped"},
            volumes={
                f"{self.name}-data": {"bind": "/root/workspace", "mode": "rw"},
            },
            network_mode="host",
        )
        logger.info(f"Container {self.name} started with ID {self._container.short_id}")
        self._reused = False
        self._initialize_container()
        return self

    def _initialize_container(self) -> None:
        logger.info("Installing system dependencies...")
        result = self.execute(
            "apt-get update && apt-get install -y curl ca-certificates gnupg git python3 python3-pip python3-venv"
        )
        if not result.success:
            raise RuntimeError(f"Failed to install prerequisites: {result.stderr}")

        logger.info("Installing Node.js...")
        result = self.execute(
            "curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs"
        )
        if not result.success:
            raise RuntimeError(f"Failed to install Node.js: {result.stderr}")

        logger.info("Installing @openai/codex...")
        result = self.execute("npm install -g @openai/codex")
        if not result.success:
            raise RuntimeError(f"Failed to install @openai/codex: {result.stderr}")

        result = self.execute("codex --version")
        logger.info(f"OpenAI Codex CLI installed: {result.stdout.strip()}")

        logger.info("Installing uv...")
        result = self.execute("curl -LsSf https://astral.sh/uv/install.sh | sh")
        if not result.success:
            raise RuntimeError(f"Failed to install uv: {result.stderr}")

        result = self.execute("echo 'export PATH=\"/root/.local/bin:$PATH\"' >> /root/.bashrc")

        logger.info("Cloning vibetest-use...")
        result = self.execute(
            "cd /root && git clone https://github.com/browser-use/vibetest-use.git",
        )
        if not result.success:
            raise RuntimeError(f"Failed to clone vibetest-use: {result.stderr}")

        logger.info("Setting up vibetest-use virtual environment...")
        result = self.execute(
            "cd /root/vibetest-use && /root/.local/bin/uv venv && . .venv/bin/activate && /root/.local/bin/uv pip install -e ."
        )
        if not result.success:
            raise RuntimeError(f"Failed to setup vibetest-use: {result.stderr}")

        logger.info("Installing Playwright Chromium...")
        result = self.execute(
            "cd /root/vibetest-use && . .venv/bin/activate && playwright install chromium --with-deps"
        )
        if not result.success:
            raise RuntimeError(f"Failed to install Playwright: {result.stderr}")

        self._setup_workspace()
        self._sync_config()
        self._sync_agents()

    def _setup_workspace(self) -> None:
        logger.info("Setting up workspace with git repo...")
        result = self.execute("mkdir -p /root/workspace && cd /root/workspace && git init 2>/dev/null || true")
        if not result.success:
            raise RuntimeError(f"Failed to create workspace directory: {result.stderr}")

        result = self.execute("cd /root/workspace && git config user.email 'swea@local' && git config user.name 'SWEA'")
        if not result.success:
            raise RuntimeError(f"Failed to configure git: {result.stderr}")

    def _sync_config(self) -> None:
        logger.info("Syncing Codex CLI config...")
        result = self.execute("mkdir -p /root/.codex")
        if not result.success:
            raise RuntimeError(f"Failed to create codex config directory: {result.stderr}")

        google_api_key = os.environ.get("GOOGLE_API_KEY", "")
        if not google_api_key:
            logger.warning("GOOGLE_API_KEY not found in environment, vibetest MCP may not work")

        project_root = Path(__file__).parent.parent
        config_template_path = project_root / "SAMPLE_config.toml"
        codex_config = config_template_path.read_text()
        codex_config = codex_config.replace("${GOOGLE_API_KEY}", google_api_key)

        result = self.execute(f"cat > /root/.codex/config.toml << 'EOF'\n{codex_config}EOF")
        if not result.success:
            raise RuntimeError(f"Failed to write codex config: {result.stderr}")

        logger.info("Codex CLI config synced")

    def _sync_agents(self) -> None:
        logger.info("Syncing AGENTS.md...")
        project_root = Path(__file__).parent.parent
        agents_template_path = project_root / "SAMPLE_AGENTS.md"
        agents_content = agents_template_path.read_text()

        result = self.execute(f"cat > /root/workspace/AGENTS.md << 'AGENTS_EOF'\n{agents_content}AGENTS_EOF")
        if not result.success:
            raise RuntimeError(f"Failed to write AGENTS.md: {result.stderr}")

        logger.info("AGENTS.md synced to /root/workspace")

    def reinitialize(self) -> None:
        if self._container is None:
            raise RuntimeError("Container not started. Call start() first.")

        self._container.reload()
        if self._container.status != "running":
            raise RuntimeError(f"Container is not running (status: {self._container.status})")

        logger.info("Re-initializing container configuration...")
        self._setup_workspace()
        self._sync_config()
        self._sync_agents()
        logger.info("Container re-initialized successfully")

    def stop(self) -> None:
        if self._container is None:
            logger.warning("No container to stop")
            return

        try:
            self._container.reload()
            if self._container.status == "running":
                logger.info(f"Stopping container {self.name}...")
                self._container.stop(timeout=10)
                logger.info(f"Container {self.name} stopped")
        except NotFound:
            logger.warning(f"Container {self.name} not found")
        finally:
            self._container = None

    def remove(self) -> None:
        if self._container is None:
            logger.warning("No container to remove")
            return

        try:
            self._container.reload()
            if self._container.status == "running":
                self._container.stop(timeout=10)
            logger.info(f"Removing container {self.name}...")
            self._container.remove(force=True)
            logger.info(f"Container {self.name} removed")
        except NotFound:
            logger.warning(f"Container {self.name} not found")
        finally:
            self._container = None

    def execute(
        self,
        command: str | list[str],
        workdir: str | None = None,
        environment: dict[str, str] | None = None,
        user: str = "root",
    ) -> CommandResult:
        if self._container is None:
            raise RuntimeError("Container not started. Call start() first.")

        self._container.reload()
        if self._container.status != "running":
            raise RuntimeError(f"Container is not running (status: {self._container.status})")

        if isinstance(command, str):
            cmd = ["/bin/bash", "-c", command]
        else:
            cmd = command

        logger.debug(f"Executing command: {cmd}")

        try:
            exit_code, output = self._container.exec_run(
                cmd=cmd,
                workdir=workdir or self.working_dir,
                environment=environment,
                user=user,
                demux=True,
            )
        except ContainerError as e:
            return CommandResult(
                exit_code=e.exit_status,
                stdout="",
                stderr=str(e),
            )

        stdout = output[0].decode("utf-8") if output[0] else ""
        stderr = output[1].decode("utf-8") if output[1] else ""

        return CommandResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )

    def execute_script(
        self,
        script: str,
        interpreter: str = "/bin/bash",
        workdir: str | None = None,
        environment: dict[str, str] | None = None,
    ) -> CommandResult:
        script_path = "/tmp/script_" + uuid.uuid4().hex[:8] + ".sh"

        create_result = self.execute(f"cat > {script_path} << 'SCRIPT_EOF'\n{script}\nSCRIPT_EOF")
        if not create_result.success:
            return create_result

        chmod_result = self.execute(f"chmod +x {script_path}")
        if not chmod_result.success:
            return chmod_result

        result = self.execute(
            f"{interpreter} {script_path}",
            workdir=workdir,
            environment=environment,
        )

        self.execute(f"rm -f {script_path}")

        return result

    def codex_exec(
        self,
        prompt: str,
        workdir: str | None = None,
        command_timeout: int = 20,
    ) -> CommandResult:
        if self._container is None:
            raise RuntimeError("Container not started. Call start() first.")

        work_dir = workdir or "/root/workspace"
        escaped_prompt = prompt.replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
        command = f'codex exec "{escaped_prompt}" --yolo'

        logger.info(f"Executing codex: {prompt[:100]}{'...' if len(prompt) > 100 else ''}")

        stdout, was_stuck = self._execute_with_stuck_detection(
            command=command,
            workdir=work_dir,
            command_timeout=command_timeout,
        )

        if was_stuck:
            self._kill_stuck_processes()
            logger.warning("Codex agent stuck.")
            return CommandResult(
                exit_code=1,
                stdout=stdout,
                stderr="Codex agent stuck.",
            )

        return CommandResult(
            exit_code=0,
            stdout=stdout,
            stderr="",
        )

    def _execute_with_stuck_detection(
        self,
        command: str,
        workdir: str,
        command_timeout: int,
    ) -> tuple[str, bool]:
        exec_start_pattern = re.compile(r"\x1b\[35m\[3mexec\x1b\[0m\x1b\[0m|\bexec\b.*\bbash\s+-lc\b", re.IGNORECASE)
        exec_complete_pattern = re.compile(r"succeeded in \d+m?s:|failed in \d+m?s:", re.IGNORECASE)

        exec_id = self._client.api.exec_create(
            container=self._container.id,
            cmd=["/bin/bash", "-c", command],
            workdir=workdir,
            user="root",
            tty=True,
            stdin=True,
        )

        sock = self._client.api.exec_start(exec_id["Id"], socket=True, tty=True)
        sock._sock.setblocking(False)

        output_buffer = []
        was_stuck = False
        in_command_execution = False
        command_start_time = None

        try:
            while True:
                ready, _, _ = select.select([sock._sock], [], [], 1.0)

                if ready:
                    try:
                        chunk = sock._sock.recv(4096)
                        if not chunk:
                            break
                        decoded = chunk.decode("utf-8", errors="replace")
                        output_buffer.append(decoded)
                        print(decoded, end="", flush=True)

                        if exec_start_pattern.search(decoded):
                            in_command_execution = True
                            command_start_time = time.time()

                        if in_command_execution and exec_complete_pattern.search(decoded):
                            in_command_execution = False
                            command_start_time = None

                    except BlockingIOError:
                        pass
                    except Exception:
                        break
                else:
                    if in_command_execution and command_start_time:
                        elapsed = time.time() - command_start_time
                        if elapsed > command_timeout:
                            logger.warning(f"Command execution timeout after {elapsed:.1f}s")
                            was_stuck = True
                            break

                exec_info = self._client.api.exec_inspect(exec_id["Id"])
                if not exec_info["Running"]:
                    break

        except Exception as e:
            logger.error(f"Error during stuck detection: {e}")
        finally:
            try:
                sock._sock.close()
            except Exception:
                pass

        full_output = "".join(output_buffer)
        return full_output, was_stuck

    def _kill_stuck_processes(self) -> None:
        kill_commands = [
            "pkill -f 'bun dev' || true",
            "pkill -f 'npm run' || true",
            "pkill -f 'node.*server' || true",
            "pkill -f 'python.*-m.*http' || true",
            "pkill -f 'flask run' || true",
            "pkill -f 'uvicorn' || true",
            "pkill -f 'gunicorn' || true",
            "fuser -k 3000/tcp 2>/dev/null || true",
            "fuser -k 5000/tcp 2>/dev/null || true",
            "fuser -k 8000/tcp 2>/dev/null || true",
            "fuser -k 8080/tcp 2>/dev/null || true",
        ]
        for cmd in kill_commands:
            self.execute(cmd)

    @property
    def is_running(self) -> bool:
        if self._container is None:
            return False
        try:
            self._container.reload()
            return self._container.status == "running"
        except NotFound:
            return False

    @property
    def container_id(self) -> str | None:
        if self._container is None:
            return None
        return self._container.id

    @property
    def was_reused(self) -> bool:
        return self._reused

    def __enter__(self) -> DockerVM:
        return self.start()

    def __exit__(self, exc_type: type | None, exc_val: Exception | None, exc_tb: object) -> None:
        pass


def main() -> None:
    import sys

    logger.info("Starting Docker Ubuntu VM...")

    with DockerVM() as vm:
        if vm.was_reused:
            logger.info("Using existing container")
        else:
            logger.info("Created new container")

        if len(sys.argv) > 1:
            prompt = " ".join(sys.argv[1:])
        else:
            prompt = "Create a simple hello world Python script"

        logger.info(f"Running Codex with prompt: {prompt}")
        result = vm.codex_exec(prompt)

        if result.success:
            print(f"\n{'='*60}\nCodex Output:\n{'='*60}\n{result.stdout}")
        else:
            print(f"\n{'='*60}\nCodex Error:\n{'='*60}\n{result.stderr}")
            sys.exit(1)


if __name__ == "__main__":
    main()
