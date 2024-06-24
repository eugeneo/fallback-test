from datetime import datetime
from pathlib import Path, PosixPath
from typing import List

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
            print(f"Generated bootstrap file at {destination}")

    def _MakeWorkingDir(self, base: str):
        for i in range(100):
            # Date time to string
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            id = f"_{i}" if i > 0 else ""
            self.__working_dir = base / f"testrun_{run_id}{id}"
            if not self.__working_dir.exists():
                print(f"Creating {self.__working_dir}")
                self.mount_dir().mkdir(parents=True)
                self.log_path("a").parent.mkdir(parents=True)
                return
        raise Exception("Couldn't find a free working directory")

    def log_path(self, name: str) -> Path:
        return self.working_dir() / "logs" / f"{name}.log"

    def mount_dir(self) -> Path:
        return self.working_dir() / "mnt"

    def working_dir(self) -> Path:
        if self.__working_dir == None:
            raise RuntimeError("Working dir was not created yet")
        return self.__working_dir
