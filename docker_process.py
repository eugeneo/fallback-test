import io
from queue import Queue
from threading import Thread

import docker
import docker.errors
import docker.types


class ChildProcessEvent:
    def __init__(self, source: str, data: str):
        self.source = source
        self.data = data

    def __str__(self) -> str:
        return f"{self.data}, {self.source}"

    def __repr__(self) -> str:
        return f"data={self.data}, source={self.source}"


def _Sanitize(l: str) -> str:
    if l.find("\0") < 0:
        return l
    return l.replace("\0", "�")


class DockerProcess:

    def __init__(
        self,
        image: str,
        queue: Queue,
        name: str,
        docker_client: docker.DockerClient,
        logFile: str = None,
        **config: docker.types.ContainerConfig,
    ):
        self.__docker_client = docker_client
        self.__queue = queue
        self.__container = None
        self.name = name
        self.__logFileName = logFile
        self.__logFile: io.TextIOWrapper = None
        self.__config = config
        self.__config["image"] = image
        self.__config["detach"] = True
        self.__config["remove"] = True

    def __enter__(self):
        if self.__logFileName != None:
            self.__logFile = open(self.__logFileName, "xt")
        self.__container = self.__docker_client.containers.run(**self.__config)
        self.__thread = Thread(
            target=lambda process: process.LogReaderLoop(),
            args=(self,),
        )
        self.__thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self.__container.stop()
            self.__container.wait()
        except docker.errors.NotFound:
            # Ok, container was auto removed
            pass
        finally:
            self.__thread.join()
            if self.__logFile != None:
                self.__logFile.close()

    def LogReaderLoop(self):
        prefix = ""
        for log in self.__container.logs(stream=True):
            s = str(prefix + log.decode("utf-8"))
            prefix = "" if s[-1] == "\n" else s[s.rfind("\n") :]
            for l in s[: s.rfind("\n")].splitlines():
                self.__OnMessage(_Sanitize(l))

    def __OnMessage(self, message: str):
        self.__queue.put(ChildProcessEvent(self.name, message))
        try:
            if self.__logFile:
                self.__logFile.write(message)
                self.__logFile.write("\n")
        except Exception:
            # Ignore, this is just a log.
            pass
