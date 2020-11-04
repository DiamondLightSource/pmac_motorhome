import re
import sys
from importlib import import_module, reload
from pathlib import Path
from typing import Sequence

import click

from .shim.plc import PLC

THIS_DIR = Path(__file__).parent

home_include = re.compile(r"^#include \"(PLCs\/PLC[\d]+_[^_]+_HM\.pmc)\"", flags=re.M)


@click.group()
@click.option("--debug/--no-debug", default=False)
@click.version_option()
@click.pass_context
def homing_convert(ctx, debug: bool):
    """Auto conversion of motorhome 1.0 scripts to motorhome 2.0"""


@homing_convert.command()
@click.argument("root", type=click.Path(file_okay=False), default=".")
def motion(root: str):
    """
    Scan a DLS Motion Area for homing PLC generating scripts and convert them
    all to v2.0

    - Looks for Master.pmc files to determine the PLC Numbers.
    - Looks for files called generate_homing_plc.py and converts them to
      generate_homing_plc2.py
    - Generates a set of PLCs using generate_homing_plc2.py and verifies that they
      match the existing PLCs

    Args:
        root (str): The root folder of the Motion Area to be scanned
    """
    root_path = Path(root)
    masters = root_path.glob("*/Master*pmc")

    root_gen = root_path / "configure" / "generate_homing_plcs.py"
    if not root_gen.exists():
        click.echo("using per brick generators")
        for master in masters:
            gen = master.parent / "configure" / "generate_homing_plcs.py"
            if not gen.exists():
                gen = root_gen
    else:
        click.echo("using global generate_homing_plcs")
        outfile = root_path / "generate_homing_plcs2.py"
        relative_includes = []
        for master in masters:
            with master.open("r") as stream:
                master_text = stream.read()
            includes = home_include.findall(master_text)
            master_dir = master.parent.relative_to(root_path)
            relative_includes += [master_dir / Path(path) for path in includes]
        file(root_gen, outfile, relative_includes)


# TODO I failed to get this to work as a click entrypoint and as a
# function to be called from 'motion' - This could be fixed by
# having separate functions for the implementation and click entries

# @homing_convert.command()
# @click.option(
#     "--infile", type=click.Path(dir_okay=False), default="generate_homing_plcs.py"
# )
# @click.option(
#     "--outfile", type=click.Path(dir_okay=False), default="generate_homing_plcs2.py"
# )
# @click.argument("names", nargs=-1)
def file(infile: Path, outfile: Path, names: Sequence[str]):
    """
    Convert a single v1.0 homing PLC generator python file to v2.0

    Args:
        infile (str): the path to the file to be converted
        outfile(str): the path to the output file which will be created or
          overwritten
        names: 0 or more PLC filenames to generate. These should be paths
        relative to generate_homing_plcs2.py. If none are supplied then all
        PLCs are generated, they are given sequential PLC numbers from 11
        and a relative folder 'PLCs'
    """
    inpath = Path(infile)
    outpath = Path(outfile)
    module_name = inpath.stem  # the module name is the filename minus .py

    with inpath.open("r") as stream:
        filetext = stream.read()

    # make sure import_module can find the motorhome_file
    sys.path.append(str(inpath.parent))
    # make sure motorhome_file can import the motorhome.py shim
    sys.path.append(str(THIS_DIR / "shim"))

    if names == ():
        matches = re.findall('^[^#]*if name == "([^"]*)"', filetext, flags=re.M)
        names = [f"PLCs/PLC{i+11}_{m}_HM.pmc" for i, m in enumerate(matches)]

    module = None
    for plc_name in names:
        sys.argv = ["exename", str(plc_name)]
        if not module:
            module = import_module(module_name)
        else:
            reload(module)

    plcs = list(PLC.get_instances())
    # make_code(plcs, outpath)


