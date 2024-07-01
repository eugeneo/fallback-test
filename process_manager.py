from datetime import datetime, timedelta
import io
from math import ceil
from queue import Queue
from threading import Thread
from typing import List

from absl import logging

from docker import DockerClient
import docker
import grpc

from protos.grpc.testing import messages_pb2
from protos.grpc.testing import test_pb2_grpc
from working_dir import WorkingDir
from protos.grpc.testing.xdsconfig import (
    control_pb2,
    service_pb2_grpc,
)


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


def Configure(config, image: str, name: str, verbosity: str):
    config["detach"] = True
    config["environment"] = {
        "GRPC_EXPERIMENTAL_XDS_FALLBACK": "true",
        "GRPC_TRACE": "xds_client",
        "GRPC_VERBOSITY": verbosity,
        "GRPC_XDS_BOOTSTRAP": "/grpc/bootstrap.json",
    }
    config["extra_hosts"] = {"host.docker.internal": "host-gateway"}
    config["image"] = image
    config["hostname"] = name
    config["remove"] = True
    return config


class DockerProcess:
    def __init__(
        self,
        image: str,
        name: str,
        manager: "ProcessManager",
        **config: docker.types.ContainerConfig,
    ):
        self.__manager = manager
        self.__container = None
        self.name = name
        self.__config = Configure(
            config, image=image, name=name, verbosity=manager.verbosity
        )

    def __enter__(self):
        self.__logFile = open(self.__manager.GetLog(self.name), "xt")
        self.__container = self.__manager.dockerClient.containers.run(**self.__config)
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
            self.__logFile.close()

    def LogReaderLoop(self):
        prefix = ""
        for log in self.__container.logs(stream=True):
            s = str(prefix + log.decode("utf-8"))
            prefix = "" if s[-1] == "\n" else s[s.rfind("\n") :]
            for l in s[: s.rfind("\n")].splitlines():
                self.__OnMessage(_Sanitize(l))

    def __OnMessage(self, message: str):
        self.__manager.OnMessage(self.name, message)
        try:
            self.__logFile.write(message)
            self.__logFile.write("\n")
        except Exception:
            # Ignore, this is just a log.
            pass


class GrpcProcess:

    def __init__(
        self,
        manager: "ProcessManager",
        name: str,
        port: int,
        ports,
        image: str,
        command: List[str],
        volumes={},
    ):
        self.__process = DockerProcess(
            image,
            name,
            manager,
            command=" ".join(command),
            hostname=name,
            ports=ports,
            volumes=volumes,
        )
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

    def ExpectOutput(self, message: str, timeout_s=5) -> bool:
        return self.__manager.ExpectOutput(self.__process.name, message, timeout_s)

    def channel(self) -> grpc.Channel:
        if self.__grpc_channel == None:
            self.__grpc_channel = grpc.insecure_channel(f"localhost:{self.__port}")
        return self.__grpc_channel

    def port(self):
        return self.__port


class ControlPlane(GrpcProcess):

    def __init__(self, manager: "ProcessManager", name: str, port: int, upstream: str):
        super().__init__(
            manager=manager,
            name=name,
            port=port,
            image=manager.controlPlaneImage,
            ports={3333: port},
            command=["--upstream", str(upstream), "--node", manager.nodeId],
        )

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

    def __init__(self, manager: "ProcessManager", port: int, name: str, url: str):
        super().__init__(
            manager=manager,
            port=port,
            image=manager.clientImage,
            name=name,
            command=[f"--server={url}", "--print_response"],
            ports={50052: port},
            volumes={
                manager.mount_dir(): {
                    "bind": "/grpc",
                    "mode": "ro",
                }
            },
        )

    def GetStats(self, num_rpcs: int) -> messages_pb2.LoadBalancerStatsResponse:
        logging.debug(f"Sending {num_rpcs} requests")
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
        testCase: str,
        clientImage: str,
        controlPlaneImage: str,
        serverImage: str,
        workingDir: WorkingDir,
        nodeId: str,
        logToConsole=False,
        verbosity="info",
    ):
        self.logs = []
        self.clientImage = clientImage
        self.controlPlaneImage = controlPlaneImage
        self.dockerClient = DockerClient.from_env()
        self.serverImage = serverImage
        self.nodeId = nodeId
        self.__logToConsole = logToConsole
        self.__outputs = {}
        self.__queue = Queue()
        self.__testCase = testCase
        self.__workingDir = workingDir
        self.verbosity = verbosity

    def GetLog(self, name: str):
        log_name = self.__workingDir.log_path(self.__testCase, name)
        self.logs.append(log_name)
        return log_name

    def StartServer(self, name: str, port: int):
        return GrpcProcess(
            self, name, port, ports={8080: port}, image=self.serverImage, command=[]
        )

    def StartClient(self, port: int, url: str, name="client"):
        return Client(self, port, name, url)

    def StartControlPlane(self, port: int, upstream: str, name="xds_config"):
        return ControlPlane(self, name=name, port=port, upstream=upstream)

    def NextEvent(self, timeout: int) -> ChildProcessEvent:
        event: ChildProcessEvent = self.__queue.get(timeout=timeout)
        source = event.source
        message = event.data
        if self.__logToConsole:
            logging.debug(f"[%s] %s", source, message)
        if not source in self.__outputs:
            self.__outputs[source] = []
        self.__outputs[source].append(message)
        return event

    def ExpectOutput(self, source: str, message: str, timeout_s=5) -> bool:
        logging.debug(f'Waiting for message "%s" on %s', message, source)
        if source in self.__outputs:
            for m in self.__outputs[source]:
                if m.find(message) >= 0:
                    return True
        deadline = datetime.now() + timedelta(seconds=timeout_s)
        while datetime.now() <= deadline:
            event = self.NextEvent(timeout_s)
            if event.source == source and event.data.find(message) >= 0:
                return True
        return False

    def OnMessage(self, source: str, message: str):
        self.__queue.put(ChildProcessEvent(source, message))

    def mount_dir(self):
        return self.__workingDir.mount_dir().absolute()
