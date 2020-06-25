from typing import List


class Motor:
    instances: List["Motor"] = []

    # offsets into the PLC's PVariables for storing the state of axes
    # these names go into long format strings so keep them short for legibility
    PVARS = {
        "hi_lim": 4,
        "lo_lim": 20,
        "homed": 36,
        "not_homed": 52,
        "lim_flags": 68,
        "pos": 84,
    }

    def __init__(self, axis: int, jdist: int, plc_num: int) -> None:
        self.axis = axis
        self.jdist = jdist
        self.index = len(self.instances)
        self.instances.append(self)
        self.post: str = "None"  # TODO need to pass this
        # dict is for terse string formatting code in _all_axes() functions
        self.dict = {
            "axis": axis,
            "index": self.index,
            "jdist": jdist,
            "homed_flag": f"7{self.nx}2",
        }
        for name, start in self.PVARS.items():
            self.dict[name] = plc_num * 100 + start + self.index

    # TODO IMPORTANT - this is used in finding the Home capture flags etc. and is
    # specific to Geobrick - For a full implementation see Motor class in
    #  ... pmacutil/pmacUtilApp/src/motorhome.py
    # HINT: watch out for python 2 vs python 3 handling of integer arithmetic
    @property
    def nx(self) -> str:
        nx = int(int((self.axis - 1) / 4) * 10 + int((self.axis - 1) % 4 + 1))
        return "{:02}".format(nx)