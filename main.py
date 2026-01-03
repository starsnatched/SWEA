import sys

from swea.docker_vm import DockerVM, logger


def main() -> None:
    logger.info("Starting Docker Ubuntu VM...")

    with DockerVM() as vm:
        if vm.was_reused:
            logger.info("Using existing container")
        else:
            logger.info("Created new container")

        prompt = "Create a simple hello world web app"
        logger.info(f"Running Codex with prompt: {prompt}")
        result = vm.codex_exec(prompt)

        if result.success:
            print(f"\n{'='*60}\nCodex Output:\n{'='*60}\n{result.stdout}")
        else:
            print(f"\n{'='*60}\nCodex Error:\n{'='*60}\n{result.stderr}")
            sys.exit(1)


if __name__ == "__main__":
    main()
