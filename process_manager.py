from datetime import datetime, timedelta
from math import ceil
from multiprocessing import Queue
from typing import Callable, List

from docker import DockerClient
import grpc

from docker_process import ChildProcessEvent, DockerProcess
from protos.grpc.testing import messages_pb2
from protos.grpc.testing import test_pb2_grpc
from working_dir import WorkingDir
from protos.grpc.testing.xdsconfig import (
    control_pb2,
    service_pb2_grpc,
)


class GrpcProcess:

    def __init__(self, process: DockerProcess, manager: "ProcessManager", port: int):
        self.__process = process
        self.__manager = manager
        self.__port = port
        self.__grpc_channel: grpc.Channel = None

    def __enter__(self) -> "Client":
        self.__process.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> "Client":
        if self.__grpc_channel != None:
            self.__grpc_channel.close()
        self.__process.__exit__(exc_type=exc_type, exc_val=exc_val, exc_tb=exc_tb)

    def ExpectOutput(self, predicate: Callable[[str], bool], timeout_s=5) -> bool:
        return self.__manager.ExpectOutput(self.__process.name, predicate, timeout_s)

    def channel(self) -> grpc.Channel:
        if self.__grpc_channel == None:
            self.__grpc_channel = grpc.insecure_channel(f"localhost:{self.__port}")
        return self.__grpc_channel


class ControlPlane(GrpcProcess):
    def __init__(self, process: DockerProcess, manager: "ProcessManager", port: int):
        super().__init__(process, manager, port)

    def StopOnResourceRequest(
        self, resource_type: str, resource_name: str
    ) -> control_pb2.StopOnRequestResponse:
        stub = service_pb2_grpc.XdsConfigControlServiceStub(self.channel())
        res = stub.StopOnRequest(
            control_pb2.StopOnRequestRequest(
                resource_type=resource_type, resource_name=resource_name
            )
        )
        return res

    def UpdateResources(
        self, cluster: str, upstream_port: int, upstream_host="localhost"
    ):
        stub = service_pb2_grpc.XdsConfigControlServiceStub(self.channel())
        return stub.UpsertResources(
            control_pb2.UpsertResourcesRequest(
                cluster=cluster,
                upstream_host=upstream_host,
                upstream_port=upstream_port,
            )
        )


class Client(GrpcProcess):
    def __init__(self, process: DockerProcess, manager: "ProcessManager", port: int):
        super().__init__(process, manager, port)

    def GetStats(self, num_rpcs: int) -> messages_pb2.LoadBalancerStatsResponse:
        stub = test_pb2_grpc.LoadBalancerStatsServiceStub(self.channel())
        res = stub.GetClientStats(
            messages_pb2.LoadBalancerStatsRequest(
                num_rpcs=num_rpcs, timeout_sec=ceil(num_rpcs * 1.5)
            )
        )
        return res


class ProcessManager:

    def __init__(
        self,
        serverImage: str,
        clientImage: str,
        controlPlaneImage: str,
        workingDir: WorkingDir,
        logToConsole=False,
    ):
        self.__queue = Queue()
        self.__dockerClient = DockerClient.from_env()
        self.__clientImage = clientImage
        self.__serverImage = serverImage
        self.__controlPlaneImage = controlPlaneImage
        self.__workingDir = workingDir
        self.logs = []
        self.__outputs = {}
        self.__logToConsole = logToConsole

    def StartServer(self, name: str, port: int) -> DockerProcess:
        return self.__StartDockerProcess(
            self.__serverImage,
            name=name,
            ports={8080: port},
            command=[],
        )

    def StartClient(self, port: int, url: str, name="client") -> Client:
        return Client(
            self.__StartDockerProcess(
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
            ),
            self,
            port,
        )

    def StartControlPlane(
        self, port: int, nodeId: str, upstream: str, name="xds_config"
    ) -> ControlPlane:
        return ControlPlane(
            self.__StartDockerProcess(
                self.__controlPlaneImage,
                name=name,
                ports={3333: port},
                command=["--upstream", upstream, "--node", nodeId],
            ),
            self,
            port,
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
        if self.__logToConsole:
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
