from datetime import datetime, timedelta
from multiprocessing import Queue
from typing import Callable, List

from docker import DockerClient

from docker_process import ChildProcessEvent, DockerProcess
from working_dir import WorkingDir


class ProcessManager:
    def __init__(
        self,
        serverImage: str,
        clientImage: str,
        controlPlaneImage: str,
        workingDir: WorkingDir,
    ):
        self.__queue = Queue()
        self.__dockerClient = DockerClient.from_env()
        self.__clientImage = clientImage
        self.__serverImage = serverImage
        self.__controlPlaneImage = controlPlaneImage
        self.__workingDir = workingDir
        self.logs = []
        self.__outputs = {}

    def StartServer(self, name: str, port: int) -> DockerProcess:
        return self.__StartDockerProcess(
            self.__serverImage,
            name=name,
            ports={8080: port},
            command=[],
        )

    def StartClient(self, port: int, url: str, name="client") -> DockerProcess:
        return self.__StartDockerProcess(
            self.__clientImage,
            command=[f"--server={url}", "--print_response"],
            name=name,
            ports={50052: port},
            verbosity="debug",
            volumes={
                self.__workingDir.mount_dir().absolute(): {
                    "bind": "/grpc",
                    "mode": "ro",
                }
            },
        )

    def StartControlPlane(
        self, port: int, nodeId: str, upstream: str, name="xds_config"
    ):
        return self.__StartDockerProcess(
            self.__controlPlaneImage,
            name=name,
            ports={3333: port},
            command=["--upstream", upstream, "--node", nodeId],
        )

    def __StartDockerProcess(
        self,
        image: str,
        name: str,
        ports,
        command: List[str],
        volumes={},
        verbosity="info",
    ):
        log_name = self.__workingDir.log_path(name)
        self.logs.append(log_name)
        return DockerProcess(
            image,
            self.__queue,
            name,
            self.__dockerClient,
            command=command,
            environment={
                "GRPC_EXPERIMENTAL_XDS_FALLBACK": "true",
                "GRPC_TRACE": "xds_client",
                "GRPC_VERBOSITY": verbosity,
                "GRPC_XDS_BOOTSTRAP": "/grpc/bootstrap.json",
            },
            extra_hosts={"host.docker.internal": "host-gateway"},
            hostname=name,
            logFile=log_name,
            ports=ports,
            volumes=volumes,
        )

    def NextEvent(self, timeout: int) -> ChildProcessEvent:
        event: ChildProcessEvent = self.__queue.get(timeout=timeout)
        source = event.source
        message = event.data
        print(f"[{source}] {message}")
        if not source in self.__outputs:
            self.__outputs[source] = []
        self.__outputs[source].append(message)
        return event

    def ExpectOutput(
        self, source: str, predicate: Callable[[str], bool], timeout_s=5
    ) -> bool:
        if source in self.__outputs:
            for message in self.__outputs[source]:
                if predicate(message):
                    return True
        deadline = datetime.now() + timedelta(seconds=timeout_s)
        while datetime.now() <= deadline:
            event = self.NextEvent(timeout_s)
            if event.source == source and predicate(event.data):
                return True
        return False
