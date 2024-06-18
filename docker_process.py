import atexit
import copy
from enum import Enum
import multiprocessing
import signal
from typing import Callable

import docker
import docker.types


ChildProcessEventType = Enum("ChildProcessEventType", ["START", "STOP", "OUTPUT"])


class ChildProcessEvent:
    def __init__(self, type: ChildProcessEventType, data: str = None):
        self.type = type
        self.data = data

    def __str__(self) -> str:
        return f"{self.type}, {self.data}"

    def __repr__(self) -> str:
        return f"type={self.type}, data={self.data}"


def _Sanitize(l: str) -> str:
    if l.find("\0") < 0:
        return l
    return l.replace("\0", "�")


def _RunDocker(
    docker_client: docker.DockerClient,
    onStart: Callable[[object], None],
    onOutput: Callable[[str], None],
    image: str,
    config: docker.types.ContainerConfig | None,
):
    cfg = config.copy()
    cfg["image"] = image
    cfg["detach"] = True
    container = docker_client.containers.run(**cfg)
    onStart(container)
    prefix = ""
    for log in container.logs(stream=True):
        s = str(prefix + log.decode("utf-8"))
        prefix = "" if s[-1] == "\n" else s[s.rfind("\n") :]
        for l in s[: s.rfind("\n")].splitlines():
            onOutput(_Sanitize(l))


class DockerProcess:

    def __init__(
        self,
        image: str,
        queue: multiprocessing.Queue,
        docker_client: docker.DockerClient,
        **config: docker.types.ContainerConfig,
    ):
        self._docker_client = docker_client
        self._queue = queue
        self._reported_done = False
        self._container = None
        self._exit_code = None
        self.process = multiprocessing.Process(
            target=lambda image, config: self.Process(image, config),
            args=(image, config),
        )

    def __enter__(self):
        self.process.start()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.process.terminate()

    def Process(self, image: str, config: docker.types.ContainerConfig | None):
        atexit.register(lambda: self.OnExit())
        signal.signal(signal.SIGTERM, lambda _signo, _frame: self.OnExit())
        try:
            _RunDocker(
                self._docker_client,
                lambda container: self.SetContainer(container),
                lambda message: self._queue.put(
                    ChildProcessEvent(ChildProcessEventType.OUTPUT, message)
                ),
                image,
                config,
            )
        finally:
            self.OnExit()

    def SetContainer(self, container):
        self._container = container
        self._queue.put(ChildProcessEvent(ChildProcessEventType.START, container.name))

    def OnExit(self):
        if not self._reported_done:
            if self._container != None:
                self._container.stop(timeout=5)
                self._exit_code = self._container.wait(timeout=5)
            self._queue.put(
                ChildProcessEvent(ChildProcessEventType.STOP, self._exit_code)
            )
            self._reported_done = True
