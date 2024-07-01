from datetime import datetime
from pathlib import Path, PosixPath
from typing import List

from absl import logging

from mako.template import Template


class WorkingDir:

    def __init__(self, base: str, ports: List[int], host_name: str):
        self.__base = PosixPath(base)
        self.__host_name = host_name
        self.__ports = ports
        self.__working_dir: Path = None
        self._MakeWorkingDir(self.__base)
        # Use Mako
        template = Template(filename="templates/bootstrap.mako")
        file = template.render(
            servers=[f"{self.__host_name}:{port}" for port in self.__ports]
        )
        destination = self.mount_dir() / "bootstrap.json"
        with open(destination, "w") as f:
            f.write(file)
            logging.debug(f"Generated bootstrap file at %s", destination)

    def _MakeWorkingDir(self, base: str):
        for i in range(100):
            # Date time to string
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            id = f"_{i}" if i > 0 else ""
            self.__working_dir = base / f"testrun_{run_id}{id}"
            if not self.__working_dir.exists():
                logging.debug(f"Creating %s", self.__working_dir)
                self.mount_dir().mkdir(parents=True)
                return
        raise Exception("Couldn't find a free working directory")

    def log_path(self, test_case: str, name: str) -> Path:
        parent = self.working_dir() / "logs" / test_case
        if not parent.exists():
            parent.mkdir(parents=True)
        return parent / f"{name}.log"

    def mount_dir(self) -> Path:
        return self.working_dir() / "mnt"

    def working_dir(self) -> Path:
        if self.__working_dir == None:
            raise RuntimeError("Working dir was not created yet")
        return self.__working_dir

    def xds_config_server_port(self, n: int):
        return self.__ports[n]
