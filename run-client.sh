#!/bin/bash
# https://pantheon.corp.google.com/artifacts/docker/grpc-testing/us/psm-interop/cpp-client

script_path="$(readlink -f "$0")"
test_dir="$(dirname -- "${script_path}")"
image_grpc_dir=/grpc

echo Test directory: ${test_dir}

docker run -it --rm \
  -v ${test_dir}:${image_grpc_dir}:ro \
  -e GRPC_VERBOSITY=info \
  -e GPRC_TRACE=xds_client \
  -e GRPC_XDS_BOOTSTRAP=${image_grpc_dir}/bootstrap.json \
  --add-host=host.docker.internal:host-gateway \
  us-docker.pkg.dev/grpc-testing/psm-interop/cpp-client:master \
  --server xds:///listener_0

  # -p 9000:9000 \
