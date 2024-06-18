from typing import List
from docker import DockerClient
import multiprocessing
import unittest


from docker_process import ChildProcessEvent, ChildProcessEventType, DockerProcess


class DockerProcessTest(unittest.TestCase):

    def test_runs_to_completion(self):
        queue = multiprocessing.Queue()
        messages: list[str] = []
        with DockerProcess("hello-world", queue, DockerClient.from_env()):
            self.assertEqual(queue.get(timeout=5).type, ChildProcessEventType.START)
            while True:
                message = queue.get(timeout=5)
                if message.type == ChildProcessEventType.STOP:
                    break
                messages.append(message.data)
        self.assertEqual(len(messages), 16)
        self.assertEqual(messages[0], "Hello from Docker!")
        self.assertEqual(messages[-1], " https://docs.docker.com/get-started/")

    def test_xds_server(self):
        queue = multiprocessing.Queue()
        docker_client = DockerClient.from_env()
        name = None
        output: List[str] = []
        try:
            with DockerProcess(
                "us-docker.pkg.dev/grpc-testing/psm-interop/cpp-server:master",
                queue,
                docker_client,
                command="--port 3333",
            ):
                event = queue.get(timeout=5)
                self.assertEqual(event.type, ChildProcessEventType.START)
                name = event.data
                container = self.FindContainer(docker_client, name)
                self.assertIsNotNone(container)
                while True:
                    event: ChildProcessEvent = queue.get(timeout=5)
                    self.assertEqual(event.type, ChildProcessEventType.OUTPUT)
                    output.append(event.data)
                    if event.data.find("Server listening on 0.0.0.0:3333") >= 0:
                        break
        except Exception:
            print("\n".join(output))
            raise
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
