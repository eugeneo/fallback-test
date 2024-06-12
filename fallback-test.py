from absl import flags
from datetime import datetime
from mako.template import Template
import os
import socket
from typing import List
import sys

FLAGS = flags.FLAGS

flags.DEFINE_boolean("dry_run", False, "Don't actually run the test")
flags.DEFINE_string("working_dir", "", "Working directory for the test")


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


class Server:
    def __init__(self, port: int):
        self.port = port
        # Run docker image
        

    def stop(self):
        pass

    def is_ready(self) -> bool:
        pass

def run_test():
    FLAGS(sys.argv)
    working_dir = pick_working_dir(FLAGS.working_dir)
    print("Working directory: ", working_dir)
    # Pick four random ports
    [primary_port, fallback_port, server1_port, server2_port, client_port] = [
        get_free_port() for _ in range(5)
    ]
    print("Ports: ", primary_port, fallback_port, server1_port, server2_port client_port)
    mnt_dir = os.path.join(working_dir, "mnt")
    os.makedirs(mnt_dir, exist_ok=True)
    # Generate bootstrap
    generate_bootstrap(
        config_servers=[f"localhost:{primary_port}", f"localhost:{fallback_port}"],
        destination=os.path.join(mnt_dir, "mnt/bootstrap.json"),
        dry_run=FLAGS.dry_run,
    )

    # Start servers on the free port
    server1 = Server(server1_port)
    server2 = Server(server2_port)
    # Start client

    # Test client can't connect

    # Start xDS fallback

    # Client connects to the fallback server

    # Start primary

    # Client connects to the primary

    # Stop primary

    # Verify mesh still works
    pass


if __name__ == "__main__":
    run_test()
