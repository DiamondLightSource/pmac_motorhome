import logging
import os
import pickle
import re
import subprocess
import sys
from pathlib import Path
from shutil import copy, rmtree
from types import ModuleType
from typing import List, Optional, Tuple

from converter.indent import Indenter

from .pipemessage import IPC_FIFO_NAME, get_message
from .shim.group import Group
from .shim.plc import PLC

log = logging.getLogger(__name__)


class MotionArea:
    """
    This class prepares two temporary DLS motion area copies for use in implementing
    and verifying a conversion from motorhome 1.0 to motorhome 2.0 as follows:

    - old_motion: A copy of the motion area with all of the homing PLCs
    regenerated using the latest version of motorhome 1.0
    This is to provide a meaningful baseline for
    comparison to verify the successful conversion

    - new_motion: A second copy of the motion area with the contents
    of its PLC folders generated using the new motorhome 2.0.

    The two folders are generated in a fixed location /tmp/motorhome for easy
    debugging.
    """

    old_motorhome = Path(__file__).parent / "old_motorhome"
    shim = Path(__file__).parent / "shim"

    home_plc_include = re.compile(
        r"^#include \"(PLCs\/PLC[\d]+_[^_]+_HM\.pmc)\"", flags=re.M
    )
    find_auto_home_plcs = "**/*/PLC*_HM.pmc"
    copy_new_gen = ""
    copy_old_gen = ""

    # tracks the 'generate_homing_plcs' module loaded by load_shim()
    # this needs to be a class variable since the very first load module
    # for the process requires load_module and subsequent require reload_module
    module: Optional[ModuleType] = None

    def __init__(self, original_path: Path) -> None:
        self.root_path = Path("/tmp") / f"motorhome{os.getpid()}"
        self.old_motion = self.root_path / "old_motion"
        self.new_motion = self.root_path / "new_motion"

        self.original_path = Path(original_path).absolute()
        if self.root_path.exists():
            rmtree(self.root_path)

    def _remove_homing_plcs(self, root: Path) -> None:
        plcs = root.glob(self.find_auto_home_plcs)
        for plc in plcs:
            plc.unlink()  # this removes a file

    def _parse_masters(self, root: Path) -> List[Path]:
        """
        Find all the Master.pmc files and use them to generate a list of all
        of the autogenerated homing PLCs in the motion area

        Args:
            root(Path): The root folder to start the search from
        """
        masters = root.glob("**/Master*pmc")

        relative_includes = []
        for master in masters:
            with master.open("r") as stream:
                master_text = stream.read()
            includes = self.home_plc_include.findall(master_text)
            # log.debug("includes: ")
            # log.debug(includes)
            master_dir = master.parent.relative_to(root)
            relative_includes += [master_dir / Path(path) for path in includes]

        return relative_includes

    def _execute_script(
        self,
        script: Path,
        cwd: Path,
        pypath: Path,
        params: str,
        python2: bool = False,
        modules: list = list(),
    ):
        """
        Execute a python script

        Args:
            script (Path): path to script
            cwd (Path): what to set cwd to
            pypath (Path): paths to add to the python path
            params (str): a space separated arguments list
        """
        os.chdir(str(cwd))

        python = sys.executable  # defaults python 3
        if python2:
            # python = "/dls_sw/prod/tools/RHEL7-x86_64/defaults/bin/dls-python"
            python = "/usr/bin/python2.7"
        if len(modules):
            for i in range(len(modules)):
                python += " " + modules[i] + " "
        command = f"cd {cwd}; PYTHONPATH={pypath} {python} {script} {params}"
        log.debug(f"executing: {command}")
        process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE)
        process.wait()

    def make_old_motion(self):
        """
        Create a copy of original_path and re-generate all of its auto generated
        homing PLCs using the latest version of motorhome 1.0
        """
        self.copytree(self.original_path, self.old_motion)
        self._remove_homing_plcs(self.old_motion)

        # either generate from one global generate_homing_plcs.py or from individual
        # generate_homing_plcs.py in each brick's subfolder
        root_gen = self.old_motion / "configure" / "generate_homing_plcs.py"
        if root_gen.exists():
            # single root generator
            plc_files = self._parse_masters(self.old_motion)
            for plc_file in plc_files:
                self._execute_script(
                    root_gen,
                    self.old_motion,
                    self.old_motorhome,
                    str(plc_file),
                    python2=True,
                )
        else:
            # individual per brick generators
            generators = self.old_motion.glob("*/configure/generate_homing_plcs.py")
            for gen in generators:
                brick_folder = gen.parent.parent
                plc_files = self._parse_masters(brick_folder)
                for plc_file in plc_files:
                    self._execute_script(
                        gen,
                        brick_folder,
                        self.old_motorhome,
                        str(plc_file),
                        python2=True,
                    )

    def make_new_motion(self):
        """
        Create a copy of original_path and re-generate all of its auto generated
        homing PLCs using motorhome 2.0.
        """
        self.copytree(self.original_path, self.new_motion)
        self._remove_homing_plcs(self.new_motion)
        script_path = "configure/generate_homing_plcs.py"
        new_script_name = "motorhome.py"
        new_script_path = f"configure/{new_script_name}"

        root_gen = self.new_motion / script_path
        if root_gen.exists():
            # single root generator
            plc_files = self._parse_masters(self.new_motion)

            new_root_gen = self.new_motion / new_script_name
            self.copy_new_gen = new_root_gen
            self.copy_old_gen = self.original_path / new_script_path
            # clear PLC instances in preparation for loading the next motorhome.py
            # TODO: this could be a function which creates a list of PLCS.
            # This list would be than passed to 'make_code'
            # -------- START generating the list of PLCs------------
            PLC.instances = []

            # open a FIFO pipe to collect pickled PLC instances
            os.mkfifo(self.new_motion / IPC_FIFO_NAME)
            fifo = os.open(self.new_motion / IPC_FIFO_NAME, os.O_RDONLY | os.O_NONBLOCK)

            for plc_file in plc_files:

                # set up python path here to insert shim
                pypath = str(":").join(
                    [
                        str(Path()),
                        str(self.shim),
                        str(self.shim.parent.parent),
                        str(plc_file.parent),
                    ]
                )
                # the scrip adds a message to fifo
                # the las command it runs is PLC.write()
                # this is definced in the PLC shim to write class to FIFO
                self._execute_script(
                    root_gen, self.new_motion, pypath, str(plc_file), python2=True,
                )
                # read pickled list of plc instances from fifo pipe
                msg = get_message(fifo)
                if msg is not None:
                    for thing in pickle.loads(msg):
                        PLC.instances.append(thing)

            # close fifo pipe
            os.close(fifo)
            # ------- FINISH GENERATING THE LIST OF PLCS -----------

            self.make_code(new_root_gen)

            # use the motorhoming 2.0 definition code created above to generate PLCs
            # no need for a loop - could be run only once with the same result
            # for plc_file in plc_files:
            self._execute_script(new_root_gen, self.new_motion, Path(), "")
        else:
            # individual per brick generators
            generators = self.new_motion.glob(f"*/{script_path}")
            self.copy_new_gen = list()
            self.copy_old_gen = list()
            for gen in generators:
                # a generator in each brick configure folder
                brick_folder = gen.parent.parent
                plc_files = self._parse_masters(brick_folder)

                self.copy_old_gen.append(
                    self.original_path / brick_folder.parts[-1] / new_script_path
                )

                # open a FIFO pipe to collect pickled PLC instances
                os.mkfifo(brick_folder / IPC_FIFO_NAME)
                fifo = os.open(
                    brick_folder / IPC_FIFO_NAME, os.O_RDONLY | os.O_NONBLOCK
                )
                # clear PLC instances in preparation for loading the next motorhome.py
                PLC.instances = []
                new_gen = brick_folder / new_script_path
                self.copy_new_gen.append(new_gen)
                for plc_file in plc_files:

                    # set up own shim using pypath
                    pypath = str(":").join(
                        [
                            str(Path()),
                            str(self.shim),
                            str(self.shim.parent.parent),
                            str(plc_file.parent),
                        ]
                    )

                    # use _execute_script with python2
                    self._execute_script(
                        gen, brick_folder, pypath, str(plc_file), python2=False
                    )

                    # collect objects from pipe
                    msg = get_message(fifo)

                    # unpickle objects
                    if msg is not None:
                        for thing in pickle.loads(msg):
                            # append objects to PLC.instances list
                            PLC.instances.append(thing)
                self.make_code(new_gen)

                # close fifo pipe
                os.close(fifo)
                # use the motorhoming 2.0 definition code created above to generate PLCs
                self._execute_script(new_gen, brick_folder, Path(), "")

    def check_matches(self):
        count = 0
        mismatches = 0
        mismatch = "The following PLCs did not match the originals:\n"
        log.info("verifying matches ...")

        plcs = self.old_motion.glob(self.find_auto_home_plcs)
        for old_plc in plcs:
            count += 1
            relative = old_plc.relative_to(self.old_motion)
            new_plc = self.new_motion / relative

            command = f"diff -bB {old_plc} {new_plc}"
            process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE)
            process.wait()

            if process.returncode != 0:
                mismatches += 1
                mismatch += f"{old_plc} {new_plc}\n"
        if mismatches == 0:
            # use a warning here so that --silent is more useful
            log.warning(
                f"Success: 0 of {count} new generated PLCs don't match old "
                f"PLCs for {self.original_path}"
            )
        else:
            log.warning(
                f"Failure: {mismatches} of {count} "
                f"PLC files do not match for {self.original_path}\n"
                f"review differences with:\n"
                f"meld {self.old_motion} {self.new_motion}\n"
            )
            log.info(f"{mismatch}")
        # provide copy command
        if isinstance(self.copy_new_gen, list):
            # print list of copy commands for per brick scripts
            copy_string = "To copy the new generatings scripts, use the "
            copy_string += "following commands:\n"
            for new_gen, old_gen in zip(self.copy_new_gen, self.copy_old_gen):
                copy_string += f"mv {new_gen} {old_gen}\n"
            log.warning(copy_string)
        else:
            log.warning(
                f"To copy the new generating script, use the following command:\n"
                f"mv {self.copy_new_gen} {self.original_path}"
                f"/configure/motorhome.py\n"
            )
        assert mismatches == 0, (
            f"{mismatches} of {count} PLC files do not match for"
            f"{self.original_path}\n"
        )

    def copytree(self, source: Path, dest: Path) -> None:
        """
        Copy source directory to dest directory recursively
        only copy the files relevant to the conversion *.pmc and *.py

        Args:
            source (Path): source directory
            dest (Path): destination directory
        """
        log.debug(f"copying {source} to {dest}")

        def glob_files(*patterns: str):
            for pat in patterns:
                for file in source.glob(f"**/{pat}"):
                    yield file

        in_files = list(glob_files("*.pmc", "generate_homing_plcs.py"))
        out_files = [dest / file.relative_to(source) for file in in_files]
        for in_file, out_file in zip(in_files, out_files):
            try:
                if not out_file.parent.exists():
                    out_file.parent.mkdir(parents=True)
                copy(in_file, out_file)
            except Exception as e:
                # it may be OK for some file copys to fail e.g.
                # directories name *.pmc or broken soft links
                log.warning(f"could not copy: {in_file}, {e}")

    def get_shebang(self):
        # get the python path for shebang
        python_path = subprocess.check_output("which python", shell=True).strip()
        python_path = python_path.decode("utf-8")
        text = f"#!/bin/env {python_path}"
        return text

    def collect_imports(self, plcs):
        # collect all the homing sequences used for the import statement
        imports = set()
        for plc in plcs:
            for group in plc.groups.values():
                imports.add(group.sequence.name)

        imps = ", ".join(sorted(imports))
        return imps

    def make_code(self, outpath: Path):
        """
        Converts the list of `converter.shim.PLC` (generated by importing a
        motorhome 1.0 definition file) into code for a motorhome 2.0 definition file.

        Args:
            outpath (Path): The filename to write generator code to
        """

        log.info(f"generating: {outpath}")
        # get the list of instances from a PLC class variable
        plcs = list(PLC.get_instances())

        with outpath.open("w") as stream:
            indent_level0 = Indenter(level=0)
            indent_level1 = Indenter(level=1)
            indent_level2 = Indenter(level=2)
            # add shebang
            stream.write(indent_level0.format_text(self.get_shebang()))
            stream.write(
                indent_level0.format_text(
                    "from pmac_motorhome.commands import ControllerType, "
                    "PostHomeMove, comment, group, motor, plc"
                )
            )
            # collect all the homing sequences used for the import statement
            imports = set()
            for plc in plcs:
                for group in plc.groups.values():
                    imports.add(group.sequence.name)
            if len(imports) > 0:
                imps = ", ".join(sorted(imports))
                text = indent_level0.format_text(
                    f"from pmac_motorhome.sequences import {imps}"
                )
                stream.write(text)
            stream.write(indent_level0.format_text(""))
            for plc in plcs:
                stream.write(indent_level0.format_text("with plc("))
                stream.write(indent_level1.format_text(f"plc_num={plc.plc},"))
                stream.write(indent_level1.format_text(f"controller={plc.bricktype},"))
                stream.write(indent_level1.format_text(f'filepath="{plc.filename}",'))
                if plc.timeout != 600000:
                    stream.write(indent_level1.format_text(f"timeout={plc.timeout}"))
                stream.write(indent_level0.format_text("):"))
                for group_num in sorted(plc.groups.keys()):
                    group = plc.groups[group_num]
                    post_code, extra_args, post_type = self.handle_post(group.post)
                    if group.pre:
                        # replace tab with space
                        pre = re.sub("\t", "    ", str(group.pre))
                        stream.write(
                            indent_level1.format_text(f'pre{group_num} = """{pre} """')
                        )
                        extra_args += f", pre=pre{group_num}"
                        stream.write(indent_level0.format_text(""))
                    if post_code:
                        post = re.sub("\t", "    ", str(post_code))
                        stream.write(
                            indent_level1.format_text(
                                f'post{group_num} = """{post} """'
                            )
                        )
                        extra_args += f", post=post{group_num}"
                        stream.write(indent_level0.format_text(""))
                    stream.write(
                        indent_level1.format_text(
                            f"with group(group_num={group.group_num}"
                            f"{extra_args}):"
                        )
                    )
                    for motor in group.motors:
                        post_code, extra_args, post_type = self.handle_post(motor.post)
                        stream.write(
                            indent_level2.format_text(
                                f"motor(axis={motor.axis},"
                                f" jdist={motor.jdist},"
                                f" index={motor.index}"
                                f"{extra_args})"
                            )
                        )
                    stream.write(
                        indent_level2.format_text(
                            f'comment("{group.sequence.old_name}",'
                            f' "{post_type}")'
                        )
                    )
                    stream.write(
                        indent_level2.format_text(f"{group.sequence.name}()")
                    )
                    stream.write(indent_level0.format_text(""))
            text = indent_level0.format_text("# End of auto converted homing "
                                             "definitions")
            stream.write(text)

    def handle_post(self, post) -> Tuple[str, str, str]:
        post = post
        post_code = ""
        extra_args = ""
        post_type = str(post)
        post_relative_move = re.compile(r"^r(-?\d+)")
        post_relative_hmz_move = re.compile(r"^z(-?\d+)")

        # convert old school post string to new approach
        if post in (None, 0, "0"):
            # no post action
            pass
        elif post == "i":
            # go to initial pos
            extra_args = ", post_home=PostHomeMove.initial_position"
        elif post == "h":
            # go to high soft limit
            extra_args = ", post_home=PostHomeMove.high_limit"
        elif post == "l":
            # go to low soft limit
            extra_args = ", post_home=PostHomeMove.low_limit"
        elif post == "H":
            # go to high hard limit, don't check for limits
            extra_args = ", post_home=PostHomeMove.hard_hi_limit"
        elif post == "L":
            # go to low hard limit, don't check for limits
            extra_args = ", post_home=PostHomeMove.hard_lo_limit"
        elif post_relative_move.match(post_type):
            # go to post[1:]
            extra_args = ", post_home=PostHomeMove.relative_move"
            extra_args += ", post_distance={dist}".format(dist=post_type[1:])
        elif post_relative_hmz_move.match(post_type):
            # go to post[1:] and hmz
            extra_args = ", post_home=PostHomeMove.move_and_hmz"
            extra_args += ", post_distance={dist}".format(dist=post_type[1:])
        elif type(post) in (int,float):
            # go to absolute position
            extra_args = ", post_home=PostHomeMove.move_absolute"
            extra_args += ", post_distance={dist}".format(dist=post)
            post = str(post)
            post_type = post
        else:
            # insert the whole of post as raw code
            post_code = post
            post_type = "None"

        return post_code, extra_args, post_type
