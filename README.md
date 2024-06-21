# xDS Fallback Interop Test

## Build

### Protobuf

```shell
# Create python virtual environment
python3 -m venv venv

# Activate virtual environment
. ./venv/bin/activate

# Install requirements
pip install -r requirements.lock

# Generate protos
python -m grpc_tools.protoc --proto_path=. \
  --python_out=. --grpc_python_out=. --pyi_out=. \
  protos/grpc/testing/*.proto
```
