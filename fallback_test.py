import unittest
from absl import flags
import socket
import sys

from process_manager import ProcessManager

from working_dir import WorkingDir

FLAGS = flags.FLAGS

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
flags.DEFINE_string(
    "host_name", "host.docker.internal", "Host name all the services are bound on"
)
flags.DEFINE_string("node", "test-id", "Node ID")
flags.DEFINE_string("working_dir", "", "Working directory for the test")


def GetFreePort() -> int:
    with (sock := socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("localhost", 0))
        return sock.getsockname()[1]


class DockerProcessTest(unittest.TestCase):
    working_dir: WorkingDir = None

    @staticmethod
    def setUpClass():
        DockerProcessTest.working_dir = WorkingDir(
            FLAGS.working_dir,
            ports=[GetFreePort() for _ in range(2)],
            host_name=FLAGS.host_name,
        )

    def setUp(self):
        self.__process_manager = ProcessManager(
            clientImage=FLAGS.client_image,
            controlPlaneImage=FLAGS.control_plane_image,
            serverImage=FLAGS.server_image,
            testCase=self.id(),
            workingDir=DockerProcessTest.working_dir,
            logToConsole=True,
        )

    def tearDown(self) -> None:
        logs = "\n".join([f"\t{log}" for log in sorted(self.__process_manager.logs)])
        print(f"Run finished:\n{logs}")

    def StartClient(self, port: int = None):
        return self.__process_manager.StartClient(
            port=GetFreePort() if port == None else port, url="xds:///listener_0"
        )

    def StartControlPlane(self, name: str, index: int, upstream_port: int):
        port = self.working_dir.xds_config_server_port(index)
        return self.__process_manager.StartControlPlane(
            name=name,
            port=port,
            nodeId=FLAGS.node,
            upstream=f"{FLAGS.host_name}:{upstream_port}",
        )

    def StartServer(self, name: str, port: int = None):
        return self.__process_manager.StartServer(
            name, GetFreePort() if port == None else port
        )

    def test_fallback_on_startup(self):
        with (
            self.StartServer(name="server1") as server1,
            self.StartServer(name="server2") as server2,
            self.StartClient() as client,
        ):
            self.assertTrue(
                client.ExpectOutput(
                    lambda message: message.find("UNAVAILABLE: xDS channel for server")
                    >= 0,
                )
            )
            self.assertEqual(client.GetStats(5).num_failures, 5)
            # Secondary xDS config start, send traffic to server2
            with self.StartControlPlane(
                name="fallback_xds_config", index=1, upstream_port=server2.port()
            ):
                stats = client.GetStats(5)
                self.assertGreater(stats.rpcs_by_peer["server2"], 0)
                self.assertNotIn("server1", stats.rpcs_by_peer)
                # Primary xDS config server start. Will use it
                with self.StartControlPlane(
                    name="primary_xds_config", index=0, upstream_port=server1.port()
                ):
                    self.assertTrue(
                        client.ExpectOutput(
                            lambda message: message.find(
                                "parsed Cluster example_proxy_cluster"
                            )
                            >= 0,
                        )
                    )
                    stats = client.GetStats(10)
                    self.assertEqual(stats.num_failures, 0)
                    self.assertIn("server1", stats.rpcs_by_peer)
                    self.assertGreater(stats.rpcs_by_peer["server1"], 0)
                # Primary config server down
                stats = client.GetStats(5)
                self.assertEqual(stats.num_failures, 0)
                self.assertEqual(stats.rpcs_by_peer["server1"], 5)
            # Fallback config server down
            stats = client.GetStats(5)
            self.assertEqual(stats.num_failures, 0)
            self.assertEqual(stats.rpcs_by_peer["server1"], 5)

    def test_fallback_mid_startup(self):
        with (
            self.StartServer(name="server1") as server1,
            self.StartServer(name="server2") as server2,
            self.StartControlPlane(
                name="primary_xds_config_run_1",
                index=0,
                upstream_port=server1.port(),
            ) as primary,
            self.StartControlPlane(
                name="fallback_xds_config",
                index=1,
                upstream_port=server2.port(),
            ),
        ):
            self.assertTrue(
                primary.ExpectOutput(
                    lambda m: m.find("management server listening on") > 0
                )
            )
            primary.StopOnResourceRequest(
                "type.googleapis.com/envoy.config.cluster.v3.Cluster",
                "example_proxy_cluster",
            )
            with (self.StartClient() as client,):
                self.assertTrue(
                    client.ExpectOutput(lambda m: m.find("creating xds client") >= 0)
                )
                # Secondary xDS config start, send traffic to server2
                stats = client.GetStats(5)
                self.assertEqual(stats.num_failures, 0)
                self.assertGreater(stats.rpcs_by_peer["server2"], 0)
                self.assertNotIn("server1", stats.rpcs_by_peer)
                with self.StartControlPlane(
                    name="primary_xds_config_run_2",
                    index=0,
                    upstream_port=server1.port(),
                ):
                    self.assertTrue(
                        primary.ExpectOutput(
                            lambda m: m.find("management server listening on") > 0
                        )
                    )
                    stats = client.GetStats(10)
                    self.assertEqual(stats.num_failures, 0)
                    self.assertIn("server1", stats.rpcs_by_peer)
                    self.assertGreater(stats.rpcs_by_peer["server1"], 0)

    def test_fallback_mid_update(self):
        with (
            self.StartServer(name="server1") as server1,
            self.StartServer(name="server2") as server2,
            self.StartServer(name="server3") as server3,
            self.StartControlPlane(
                name="primary_xds_config_run_1", index=0, upstream_port=server1.port()
            ) as primary,
            self.StartControlPlane(
                name="fallback_xds_config", index=1, upstream_port=server2.port()
            ),
            self.StartClient() as client,
        ):
            self.assertTrue(
                client.ExpectOutput(lambda m: m.find("creating xds client") >= 0)
            )
            # Secondary xDS config start, send traffic to server2
            stats = client.GetStats(5)
            self.assertGreater(stats.rpcs_by_peer["server1"], 0)
            primary.StopOnResourceRequest(
                "type.googleapis.com/envoy.config.cluster.v3.Cluster",
                "test_cluster_2",
            )
            primary.UpdateResources(
                cluster="test_cluster_2",
                upstream_port=server3.port(),
                upstream_host=FLAGS.host_name,
            )
            stats = client.GetStats(10)
            self.assertEqual(stats.num_failures, 0)
            self.assertIn("server2", stats.rpcs_by_peer)
            # Check that post-recovery uses a new config
            with self.StartControlPlane(
                name="primary_xds_config_run_2", index=0, upstream_port=server3.port()
            ) as primary2:
                self.assertTrue(
                    primary2.ExpectOutput(
                        lambda m: m.find("management server listening on") > 0
                    )
                )
                stats = client.GetStats(20)
                self.assertEqual(stats.num_failures, 0)
                self.assertIn("server3", stats.rpcs_by_peer)


if __name__ == "__main__":
    FLAGS(sys.argv)
    unittest.main()
