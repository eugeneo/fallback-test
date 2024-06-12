from absl import flags
from datetime import datetime
import docker
from mako.template import Template
import os
import socket
from typing import List
import sys

FLAGS = flags.FLAGS

flags.DEFINE_boolean("dry_run", False, "Don't actually run the test")
flags.DEFINE_string("working_dir", "", "Working directory for the test")
flags.DEFINE_string(
    "server_image",
    "us-docker.pkg.dev/grpc-testing/psm-interop/cpp-server:master",
    "Server image",
)


def get_free_port() -> int:
    with (sock := socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("localhost", 0))
        return sock.getsockname()[1]


def generate_bootstrap(
    config_servers: List[str], destination: str, dry_run: bool
) -> str:
    # Use Mako
    template = Template(filename="templates/bootstrap.mako")
    file = template.render(servers=config_servers)
    if dry_run:
        print(file)
    else:
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with open(destination, "w") as f:
            f.write(file)
            print(f"Generated bootstrap file at {destination}")


def pick_working_dir(base: str) -> str:
    for i in range(100):
        # Date time to string
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        id = f"_{i}" if i > 0 else ""
        path = os.path.join(base, f"{run_id}{id}")
        if not os.path.exists(path):
            os.makedirs(path)
            return path
    raise Exception("Couldn't find a free working directory")


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


class Server:

    def __init__(
        self,
        port: int,
        image: str,
        id_string: str,
        docker_client=docker.from_env(),
    ):
        self.port = port
        self.image = image
        self.id = id_string
        self.docker_client = docker_client

    def __enter__(self):
        self.container = self.docker_client.containers.run(
            image=self.image,
            environment={
                "GRPC_VERBOSITY": "info",
            },
            ports={"3333/tcp": self.port},
            command=f"--port 3333 --server_id ${id}",
            tty=True,
            detach=True,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.container.stop()
        return False

    def logs(self):
        return self.container.logs(stream=True)

    def stop(self):
        pass

    def is_ready(self) -> bool:
        pass


def run_test():
    FLAGS(sys.argv)
    working_dir = pick_working_dir(FLAGS.working_dir)
    print("Working directory: ", working_dir)
    [primary_port, fallback_port, server1_port, server2_port, client_port] = [
        get_free_port() for _ in range(5)
    ]
    print(
        "Ports: ", primary_port, fallback_port, server1_port, server2_port, client_port
    )
    mnt_dir = os.path.join(working_dir, "mnt")
    os.makedirs(mnt_dir, exist_ok=True)
    generate_bootstrap(
        config_servers=[f"localhost:{primary_port}", f"localhost:{fallback_port}"],
        destination=os.path.join(mnt_dir, "mnt/bootstrap.json"),
        dry_run=FLAGS.dry_run,
    )

    # Start servers on the free port
    with (server1 := Server(server1_port, FLAGS.server_image, "server1")):
        server1_logs = server1.logs()
        while True:
            print(next(server1_logs).decode("utf-8"))

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
