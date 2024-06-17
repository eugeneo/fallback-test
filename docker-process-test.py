import atexit
from enum import Enum
from typing import Callable
from docker import DockerClient
import docker
import multiprocessing
import signal
import unittest

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


def Sanitize(l: str) -> str:
    if l.find("\0") < 0:
        return l
    return l.replace("\0", "�")


def RunDocker(
    docker_client: DockerClient,
    onStart: Callable[[object], None],
    onConsole: Callable[[str], None],
    image: str,
):
    container = docker_client.containers.run(image, detach=True)
    onStart(container)
    prefix = ""
    for log in container.logs(stream=True):
        s = str(prefix + log.decode("utf-8"))
        prefix = "" if s[-1] == "\n" else s[s.rfind("\n") :]
        for l in s[: s.rfind("\n")].splitlines():
            onConsole(Sanitize(l))


class DockerProcess:
    def __init__(
        self, image: str, queue: multiprocessing.Queue, docker_client: DockerClient
    ):
        self._docker_client = docker_client
        self._queue = queue
        self._reported_done = False
        self.process = multiprocessing.Process(
            target=lambda image: self.Process(image), args=(image,)
        )

    def __enter__(self):
        self.process.start()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.process.terminate()

    def Process(self, image: str):
        atexit.register(lambda: self.OnExit())
        signal.signal(signal.SIGTERM, lambda _signo, _frame: self.OnExit())
        try:
            RunDocker(
                self._docker_client,
                lambda container: self.SetContainer(container),
                lambda message: self._queue.put(
                    ChildProcessEvent(ChildProcessEventType.OUTPUT, message)
                ),
                image,
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


def StartDocker(image: str, docker_client: DockerClient, queue: multiprocessing.Queue):
    return DockerProcess(image, queue, docker_client)


class DockerProcessTest(unittest.TestCase):

    def test_docker_direct(self):
        container = []
        messages = []
        RunDocker(
            DockerClient.from_env(),
            lambda a: container.append(a),
            lambda a: messages.append(a),
            "hello-world",
        )
        self.assertNotEqual(container[0], None)
        self.assertEqual(len(messages), 16)
        self.assertEqual(messages[0], "Hello from Docker!")
        self.assertEqual(messages[-1], " https://docs.docker.com/get-started/")

    def test_start_stop(self):
        queue = multiprocessing.Queue()
        messages: list[str] = []
        with StartDocker("hello-world", DockerClient.from_env(), queue):
            self.assertEqual(queue.get(timeout=5).type, ChildProcessEventType.START)
            while True:
                message = queue.get(timeout=5)
                if message.type == ChildProcessEventType.STOP:
                    break
                messages.append(message.data)
        self.assertEqual(len(messages), 16)
        self.assertEqual(messages[0], "Hello from Docker!")
        self.assertEqual(messages[-1], " https://docs.docker.com/get-started/")

    def test_start_xds_server(self):
        queue = multiprocessing.Queue()
        docker_client = DockerClient.from_env()
        name = None
        with StartDocker(
            "us-docker.pkg.dev/grpc-testing/psm-interop/cpp-server:master",
            docker_client,
            queue,
        ):
            event = queue.get(timeout=5)
            self.assertEqual(event.type, ChildProcessEventType.START)
            name = event.data
            container = self.FindContainer(docker_client, name)
            self.assertIsNotNone(container)
            while True:
                event = queue.get(timeout=5)
                self.assertEqual(event.type, ChildProcessEventType.OUTPUT)
                if str(event.data).find("Server listening on 0.0.0.0:8080") >= 0:
                    break
        event = queue.get(timeout=30)  # longer timeout, Docker stop
        self.assertEqual(event.type, ChildProcessEventType.STOP)
        self.assertIsNone(self.FindContainer(docker_client, name))

    def FindContainer(self, client: DockerClient, name: str):
        for container in client.containers.list():
            if container.name == name:
                return container
        return None


if __name__ == "__main__":
    unittest.main()
