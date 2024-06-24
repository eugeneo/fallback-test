import atexit
import copy
from enum import Enum
import multiprocessing
import signal
import sys
from typing import Callable

import docker
import docker.types


ChildProcessEventType = Enum("ChildProcessEventType", ["START", "STOP", "OUTPUT"])


class ChildProcessEvent:

    def __init__(self, type: ChildProcessEventType, source: str, data: str | None):
        self.source = source
        self.data = data
        self.type = type

    def __str__(self) -> str:
        return f"{self.type}, {self.data}, {self.source}"

    def __repr__(self) -> str:
        return f"type={self.type}, data={self.data}, source={self.source}"


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
        name: str,
        docker_client: docker.DockerClient,
        logFile: str = None,
        **config: docker.types.ContainerConfig,
    ):
        self.__docker_client = docker_client
        self.__queue = queue
        self.__reported_done = False
        self.__container = None
        self.__exit_code = None
        self.__name = name
        self.process = multiprocessing.Process(
            target=lambda image, config: self.Process(image, config),
            args=(image, config),
        )
        self.__logFileName = logFile

    def __enter__(self):
        self.process.start()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.process.terminate()
        self.process.join()

    def Process(self, image: str, config: docker.types.ContainerConfig | None):
        if self.__logFileName != None:
            self.__logFile = open(self.__logFileName, "w+")
        atexit.register(lambda: self.__OnExit())
        signal.signal(signal.SIGTERM, lambda _signo, _frame: self.__OnExit())
        process = self
        try:
            _RunDocker(
                self.__docker_client,
                lambda container: process.__SetContainer(container),
                lambda message: process.__OnMessage(message),
                image,
                config,
            )
        except KeyboardInterrupt:
            # Less noise by removing the useless stack trace
            print(f"KeyboardInterrupt in {self.__name}", file=sys.stderr)
        finally:
            self.__OnExit()

    def __SetContainer(self, container):
        self.__container = container
        self.__queue.put(
            ChildProcessEvent(ChildProcessEventType.START, self.__name, container.name)
        )

    def __OnMessage(self, message: str):
        self.__queue.put(
            ChildProcessEvent(ChildProcessEventType.OUTPUT, self.__name, message)
        )
        try:
            if self.__logFile:
                self.__logFile.write(message)
                self.__logFile.write("\n")
        except Exception:
            # Ignore, this is just a log.
            pass

    def __OnExit(self):
        if not self.__reported_done:
            try:
                if self.__logFile:
                    self.__logFile.close()
            except Exception:
                # Don't care
                pass
            if self.__container != None:
                self.__container.stop(timeout=5)
                self.__exit_code = self.__container.wait(timeout=5)
            self.__queue.put(
                ChildProcessEvent(
                    ChildProcessEventType.STOP, self.__name, self.__exit_code
                )
            )
            self.__reported_done = True
