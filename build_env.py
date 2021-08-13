# Copyright 2021 cedar.ai. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and

import collections
import json
import os
import pathlib
import subprocess
import sys
import textwrap
from typing import List, Optional
import venv

import entrypoints


EnvFile = collections.namedtuple("EnvFile", ["path", "env_path"])


def console_script(env_path: pathlib.Path, module: str, func: str) -> str:
    return textwrap.dedent(f"""\
        #!{env_path / "bin/python3"}
        # -*- coding: utf-8 -*-
        import re
        import sys
        from {module} import {func}
        if __name__ == '__main__':
            sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])
            sys.exit({func}())
        """)


def get_env_path(path: str, imports: List[str]) -> Optional[str]:
    if not path.startswith("../"):
        return path
    
    for imp in imports:
        prefix = f"../{imp}/"
        if path.startswith(prefix):
            return path[len(prefix):]
    
    # External file that didn't match imports. Don't include it in the venv.
    return None


def is_external(file_: str) -> bool:
    return file_.startswith("../")


def find_site_packages(env_path: pathlib.Path) -> pathlib.Path:
    lib_path = env_path / "lib"

    # We should find one "pythonX.X" directory in here.
    for child in lib_path.iterdir():
        if child.name.startswith("python"):
            site_packages_path = child / "site-packages"
            if site_packages_path.exists():
                return site_packages_path

    raise Exception("Unable to find site-packages path in venv")


def read_deps_file(filename: str) -> List[EnvFile]:
    files = []
    with open(filename) as f:
        deps = json.load(f)
    
    imports = deps["imports"]
    for depfile in deps["files"]:
        # Bucket files into external and workspace groups.
        # Only generated workspace files are kept.
        type_ = depfile["t"]
        path = depfile["p"]

        env_path = get_env_path(path, imports)
        if not env_path:
            continue

        if is_external(path):
            files.append(EnvFile(path, env_path))
        elif type_ == "G":
            files.append(EnvFile(path, env_path))

    return files


def generate_console_scripts(env_path: pathlib.Path) -> None:
    site_packages = find_site_packages(env_path)
    bin = env_path / "bin"

    entry_points = entrypoints.get_group_all("console_scripts", [str(site_packages)])
    for ep in entry_points:
        script = bin / ep.name
        if script.exists():
            continue
        script.write_text(console_script(env_path, ep.module_name, ep.object_name), encoding="utf-8")
        script.chmod(0o755)


def run_additional_commands(env_path: pathlib.Path, commands: List[str]) -> None:
    lines = [f". {env_path}/bin/activate"]
    for cmd in commands:
        pip_cmd = f"pip --no-input {cmd}"
        # Echo in green what command is being run
        lines.append(fr'echo "\n\033[0;32m> {pip_cmd}\033[0m"')
        lines.append(pip_cmd)

    full_command = ";".join(lines)

    # Prefer using zsh, since on macos (which ships with it), zsh adds support for executing
    # scripts whose shebang lines point to other scripts.
    # If we can't find zsh, use the default and hope for the best.
    shell = None
    for zsh in ["/bin/zsh", "/usr/bin/zsh"]:
        if pathlib.Path(zsh).exists():
            shell = zsh
    ret = subprocess.run(full_command, capture_output=False, shell=True, executable=shell)
    ret.check_returncode()


def main():
    if "DEPS_FILE" not in os.environ:
        raise Exception("Missing DEPS_FILE env var")
    if len(sys.argv) != 2:
        raise Exception(f"Usage: {sys.argv} <venv path>")

    files = read_deps_file(os.environ["DEPS_FILE"])

    # Hack: fully resolve the current interpreter's known path to get venv to link to the
    # files in their actual location
    sys._base_executable = str(pathlib.Path(sys._base_executable).resolve())

    cwd = os.environ.get("BUILD_WORKING_DIRECTORY", os.getcwd())
    env_path = pathlib.Path(cwd) / pathlib.Path(sys.argv[1])

    builder = venv.EnvBuilder(clear=True, symlinks=True, with_pip=True)
    builder.create(str(env_path))

    site_packages_path = find_site_packages(env_path)
    for file in files:
        site_suffix = file.env_path
        path = pathlib.Path(file.path)

        site_path = site_packages_path / site_suffix
        site_path.parent.mkdir(parents=True, exist_ok=True)
        site_path.symlink_to(path.resolve())

    generate_console_scripts(env_path)

    extra_commands = os.environ.get("EXTRA_PIP_COMMANDS")
    if extra_commands:
        run_additional_commands(env_path, extra_commands.split("\n"))


if __name__ == '__main__':
    main()
