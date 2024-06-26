import unittest
from absl import flags
import socket
import sys

from process_manager import ProcessManager

from working_dir import WorkingDir

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
flags.DEFINE_boolean(
    "hang", False, "Hang after the test so the state of the mesh could be explored"
)
flags.DEFINE_string(
    "host_name", "host.docker.internal", "Host name all the services are bound on"
)


def get_free_port() -> int:
    with (sock := socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("localhost", 0))
        return sock.getsockname()[1]


class DockerProcessTest(unittest.TestCase):

    @unittest.skip("For time saving")
    def test_fallback_on_startup(self):
        [primary_port, fallback_port, server1_port, server2_port, client_port] = [
            get_free_port() for _ in range(5)
        ]
        # Start servers on the free port
        working_dir = WorkingDir(
            FLAGS.working_dir,
            ports=[primary_port, fallback_port],
            host_name=FLAGS.host_name,
        )
        print("Working directory: ", working_dir.working_dir())
        process_manager = ProcessManager(
            serverImage=FLAGS.server_image,
            clientImage=FLAGS.client_image,
            controlPlaneImage=FLAGS.control_plane_image,
            workingDir=working_dir,
        )
        try:
            with (
                process_manager.StartServer(name="server1", port=server1_port),
                process_manager.StartServer(name="server2", port=server2_port),
                process_manager.StartClient(
                    port=client_port, url="xds:///listener_0"
                ) as client,
            ):
                self.assertTrue(
                    client.ExpectOutput(
                        lambda message: message.find(
                            "UNAVAILABLE: xDS channel for server"
                        )
                        >= 0,
                    )
                )
                self.assertEqual(client.GetStats(5).num_failures, 5)
                # Secondary xDS config start, send traffic to server2
                with process_manager.StartControlPlane(
                    name="fallback_xds_config",
                    port=fallback_port,
                    nodeId=FLAGS.node,
                    upstream=f"{FLAGS.host_name}:{server2_port}",
                ):
                    stats = client.GetStats(5)
                    self.assertGreater(stats.rpcs_by_peer["server2"], 0)
                    self.assertNotIn("server1", stats.rpcs_by_peer)
                    # Primary xDS config server start. Will use it
                    with process_manager.StartControlPlane(
                        name="primary_xds_config",
                        port=primary_port,
                        nodeId=FLAGS.node,
                        upstream=f"{FLAGS.host_name}:{server1_port}",
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
        except KeyboardInterrupt:
            # Stack trace is useless here, reduce log noise
            print("KeyboardInterrupt", file=sys.stderr)
        finally:
            logs = "\n".join([f"\t{log}" for log in sorted(process_manager.logs)])
            print(f"Run finished:\n{logs}")

    @unittest.skip("For time saving")
    def test_fallback_mid_startup(self):
        [primary_port, fallback_port, server1_port, server2_port, client_port] = [
            get_free_port() for _ in range(5)
        ]
        # Start servers on the free port
        working_dir = WorkingDir(
            FLAGS.working_dir,
            ports=[primary_port, fallback_port],
            host_name=FLAGS.host_name,
        )
        print("Working directory: ", working_dir.working_dir())
        process_manager = ProcessManager(
            serverImage=FLAGS.server_image,
            clientImage=FLAGS.client_image,
            controlPlaneImage=FLAGS.control_plane_image,
            workingDir=working_dir,
        )
        try:
            with (
                process_manager.StartServer(name="server1", port=server1_port),
                process_manager.StartServer(name="server2", port=server2_port),
                process_manager.StartControlPlane(
                    name="primary_xds_config_run_1",
                    port=primary_port,
                    nodeId=FLAGS.node,
                    upstream=f"{FLAGS.host_name}:{server1_port}",
                ) as primary,
                process_manager.StartControlPlane(
                    name="fallback_xds_config",
                    port=fallback_port,
                    nodeId=FLAGS.node,
                    upstream=f"{FLAGS.host_name}:{server2_port}",
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
                with (
                    process_manager.StartClient(
                        port=client_port, url="xds:///listener_0"
                    ) as client,
                ):
                    self.assertTrue(
                        client.ExpectOutput(
                            lambda m: m.find("creating xds client") >= 0
                        )
                    )
                    # Secondary xDS config start, send traffic to server2
                    stats = client.GetStats(5)
                    self.assertEqual(stats.num_failures, 0)
                    self.assertGreater(stats.rpcs_by_peer["server2"], 0)
                    self.assertNotIn("server1", stats.rpcs_by_peer)
                    with process_manager.StartControlPlane(
                        name="primary_xds_config_run_2",
                        port=primary_port,
                        nodeId=FLAGS.node,
                        upstream=f"{FLAGS.host_name}:{server1_port}",
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
        except KeyboardInterrupt:
            # Stack trace is useless here, reduce log noise
            print("KeyboardInterrupt", file=sys.stderr)
        finally:
            logs = "\n".join([f"\t{log}" for log in sorted(process_manager.logs)])
            print(f"Run finished:\n{logs}")

    def test_fallback_mid_update(self):
        [
            primary_port,
            fallback_port,
            server1_port,
            server2_port,
            server3_port,
            client_port,
        ] = [get_free_port() for _ in range(6)]
        # Start servers on the free port
        working_dir = WorkingDir(
            FLAGS.working_dir,
            ports=[primary_port, fallback_port],
            host_name=FLAGS.host_name,
        )
        print("Working directory: ", working_dir.working_dir())
        process_manager = ProcessManager(
            serverImage=FLAGS.server_image,
            clientImage=FLAGS.client_image,
            controlPlaneImage=FLAGS.control_plane_image,
            workingDir=working_dir,
        )
        try:
            with (
                process_manager.StartServer(name="server1", port=server1_port),
                process_manager.StartServer(name="server2", port=server2_port),
                process_manager.StartServer(name="server3", port=server3_port),
                process_manager.StartControlPlane(
                    name="primary_xds_config_run_1",
                    port=primary_port,
                    nodeId=FLAGS.node,
                    upstream=f"{FLAGS.host_name}:{server1_port}",
                ) as primary,
                process_manager.StartControlPlane(
                    name="fallback_xds_config",
                    port=fallback_port,
                    nodeId=FLAGS.node,
                    upstream=f"{FLAGS.host_name}:{server2_port}",
                ),
                process_manager.StartClient(
                    port=client_port, url="xds:///listener_0"
                ) as client,
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
                    upstream_port=server3_port,
                    upstream_host=FLAGS.host_name,
                )
                stats = client.GetStats(10)
                self.assertEqual(stats.num_failures, 0)
                self.assertIn(stats.rpcs_by_peer, "server2")
                with process_manager.StartControlPlane(
                    name="primary_xds_config_run_2",
                    port=primary_port,
                    nodeId=FLAGS.node,
                    upstream=f"{FLAGS.host_name}:{server1_port}",
                ):
                    self.assertTrue(
                        primary.ExpectOutput(
                            lambda m: m.find("management server listening on") > 0
                        )
                    )
                    primary.UpdateResources(
                        cluster="test_cluster_2",
                        upstream_port=server3_port,
                        upstream_host=FLAGS.host_name,
                    )
                    stats = client.GetStats(10)
                    self.assertEqual(stats.num_failures, 0)
                    self.assertIn("server3", stats.rpcs_by_peer)
        except KeyboardInterrupt:
            # Stack trace is useless here, reduce log noise
            print("KeyboardInterrupt", file=sys.stderr)
        finally:
            logs = "\n".join([f"\t{log}" for log in sorted(process_manager.logs)])
            print(f"Run finished:\n{logs}")


if __name__ == "__main__":
    FLAGS(sys.argv)
    unittest.main()
