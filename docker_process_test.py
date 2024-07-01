from queue import Empty, Queue
from docker import DockerClient
import multiprocessing
import unittest

from docker_process import DockerProcess


class DockerProcessTest(unittest.TestCase):

    def test_runs_to_completion(self):
        queue = Queue()
        messages: list[str] = []
        try:
            with DockerProcess("hello-world", queue, "hello", DockerClient.from_env()):
                while True:
                    messages.append(queue.get(timeout=5).data)
        except Empty:
            # Expected
            pass

        self.assertEqual(len(messages), 16)
        self.assertEqual(messages[0], "Hello from Docker!")
        self.assertEqual(messages[-1], " https://docs.docker.com/get-started/")

    def FindContainer(self, client: DockerClient, name: str):
        for container in client.containers.list():
            if container.name == name:
                return container
        return None


if __name__ == "__main__":
    unittest.main()
