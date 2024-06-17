from docker import DockerClient
import multiprocessing
import unittest


from docker_process import ChildProcessEventType, DockerProcess


class DockerProcessTest(unittest.TestCase):

    def test_start_stop(self):
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

    def test_start_xds_server(self):
        queue = multiprocessing.Queue()
        docker_client = DockerClient.from_env()
        name = None
        with DockerProcess(
            "us-docker.pkg.dev/grpc-testing/psm-interop/cpp-server:master",
            queue,
            docker_client,
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
