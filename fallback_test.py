from pathlib import Path, PosixPath
import shutil
from absl import flags
from datetime import datetime
import docker
from mako.template import Template
from multiprocessing import Process, Queue
import os
import socket
import sys
from typing import List

from docker_process import ChildProcessEvent, ChildProcessEventType, DockerProcess

FLAGS = flags.FLAGS

flags.DEFINE_boolean("dry_run", False, "Don't actually run the test")
flags.DEFINE_string("working_dir", "", "Working directory for the test")
flags.DEFINE_string(
    "client_image",
    "us-docker.pkg.dev/grpc-testing/psm-interop/cpp-client:master",
    "Client image",
)
flags.DEFINE_string(
    "control_plane_image",
    "us-docker.pkg.dev/eostroukhov-xds-interop/docker/control-plane",
    "Control plane (xDS config) server image",
)
flags.DEFINE_string(
    "server_image",
    "us-docker.pkg.dev/grpc-testing/psm-interop/cpp-server:master",
    "Server image",
)
flags.DEFINE_string("node", "test-id", "Node ID")
flags.DEFINE_integer(
    "message_timeout", 5, "Timeout waiting for the messages from the server processes"
)


def get_free_port() -> int:
    with (sock := socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("localhost", 0))
        return sock.getsockname()[1]


def run_docker_test():
    client = docker.from_env()
    script_path = os.path.abspath(__file__)
    test_dir = os.path.dirname(script_path)
    image_grpc_dir = "/grpc"

    print(f"Test directory: {test_dir}")

    try:
        container = client.containers.run(
            # image="us-docker.pkg.dev/grpc-testing/psm-interop/cpp-client:master",
            command="--server xds:///listener_0",
            environment={
                "GRPC_VERBOSITY": "info",
                "GPRC_TRACE": "xds_client",
                "GRPC_XDS_BOOTSTRAP": f"{image_grpc_dir}/bootstrap.json",
            },
            volumes={test_dir: {"bind": image_grpc_dir, "mode": "ro"}},
            extra_hosts={"host.docker.internal": "host-gateway"},
            detach=False,  # Run in foreground
            tty=True,  # Allocate a pseudo-TTY
        )
        for line in container.logs(stream=True):
            print(line.strip().decode("utf-8"))

    except docker.errors.ImageNotFound:
        print("Docker image not found. Please pull the image first.")
    except docker.errors.APIError as e:
        print(f"An error occurred: {e}")


class WorkingDir:
    def __init__(self, base: str, ports: List[int]):
        self.__base = PosixPath(base)
        self.__ports = ports
        self.__working_dir: Path = None

    def __enter__(self):
        self._MakeWorkingDir(self.__base)
        # Use Mako
        template = Template(filename="templates/bootstrap.mako")
        file = template.render(servers=[f"localhost:{port}" for port in self.__ports])
        destination = os.path.join(self.mount_dir(), "bootstrap.json")
        with open(destination, "w") as f:
            f.write(file)
            print(f"Generated bootstrap file at {destination}")
        return self

    # Used to cleanup the folder but now we need to keep the log. May be
    # refactored later to stop supporting context manager protocol
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def _MakeWorkingDir(self, base: str):
        for i in range(100):
            # Date time to string
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            id = f"_{i}" if i > 0 else ""
            self.__working_dir = base / f"testrun_{run_id}{id}"
            if not self.__working_dir.exists():
                print(f"Creating {self.__working_dir}")
                self.mount_dir().mkdir(parents=True)
                self.log_path("a").parent.mkdir(parents=True)
                return
        raise Exception("Couldn't find a free working directory")

    def log_path(self, name: str) -> Path:
        return self.working_dir() / "logs" / f"{name}.log"

    def mount_dir(self) -> Path:
        return self.working_dir() / "mnt"

    def working_dir(self) -> Path:
        if self.__working_dir == None:
            raise RuntimeError("Working dir was not created yet")
        return self.__working_dir


class ProcessManager:
    def __init__(
        self,
        serverImage: str,
        clientImage: str,
        controlPlaneImage: str,
        workingDir: WorkingDir,
    ):
        self.__queue = Queue()
        self.__dockerClient = docker.DockerClient.from_env()
        self.__clientImage = clientImage
        self.__serverImage = serverImage
        self.__controlPlaneImage = controlPlaneImage
        self.__workingDir = workingDir
        self.logs = []

    def StartServer(self, name: str, port: int) -> DockerProcess:
        return self.__StartDockerProcess(
            self.__serverImage,
            name=name,
            ports={3333: port},
            command=["--server_id", name],
        )

    def StartClient(self, port: int, url: str, name="client") -> DockerProcess:
        return self.__StartDockerProcess(
            self.__clientImage,
            name=name,
            ports={3333: port},
            command=["--server", url],
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
        self, image: str, name: str, ports, command: List[str], volumes={}
    ):
        log_name = self.__workingDir.log_path(name)
        self.logs.append(log_name)
        return DockerProcess(
            image,
            self.__queue,
            name,
            self.__dockerClient,
            logFile=log_name,
            extra_hosts={"host.docker.internal": "host-gateway"},
            ports=ports,
            command=command,
            environment={
                "GRPC_VERBOSITY": "info",
                "GRPC_TRACE": "xds_client",
                "GRPC_XDS_BOOTSTRAP": "/grpc/bootstrap.json",
            },
            volumes=volumes,
        )

    def NextMessage(self, timeout: int) -> ChildProcessEvent:
        return self.__queue.get(timeout=timeout)


def run_test():
    FLAGS(sys.argv)
    [primary_port, fallback_port, server1_port, server2_port, client_port] = [
        get_free_port() for _ in range(5)
    ]
    print(
        "Ports: ", primary_port, fallback_port, server1_port, server2_port, client_port
    )
    # Start servers on the free port
    with WorkingDir(
        FLAGS.working_dir, ports=[primary_port, fallback_port]
    ) as working_dir:
        print("Working directory: ", working_dir.working_dir())
        process_manager = ProcessManager(
            serverImage=FLAGS.server_image,
            clientImage=FLAGS.client_image,
            controlPlaneImage=FLAGS.control_plane_image,
            workingDir=working_dir,
        )
        try:
            with (
                # process_manager.StartServer(name="server1", port=server1_port),
                # process_manager.StartServer(name="server2", port=server2_port),
                process_manager.StartClient(port=client_port, url="xds:///listener_0"),
                process_manager.StartControlPlane(
                    port=primary_port,
                    nodeId=FLAGS.node,
                    upstream=f"localhost:{server1_port}",
                ),
            ):
                while True:
                    event = process_manager.NextMessage(timeout=FLAGS.message_timeout)
                    if event.type == ChildProcessEventType.OUTPUT:
                        print(f"[{event.source}] {event.data}")
        except KeyboardInterrupt:
            # Stack trace is useless here, reduce log noise
            print("KeyboardInterrupt", file=sys.stderr)
        finally:
            logs = "\n".join([f"\t{log}" for log in sorted(process_manager.logs)])
            print(f"Run finished:\n{logs}")

    # server2 = Server(server2_port)
    # Start client

    # Test client can't connect

    # Start xDS fallback

    # Client connects to the fallback server

    # Start primary

    # Client connects to the primary

    # Stop primary

    # Verify mesh still works


if __name__ == "__main__":
    run_test()
