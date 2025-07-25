import re
from enum import IntEnum
from itertools import takewhile

from gfapy import Gfa

from paulssonlab.sequencing.gfa import gfa_name_mapping
from paulssonlab.util.sequence import reverse_complement

# FROM: https://github.com/jeffdaily/parasail/blob/600fb26151ff19899ee39a214972dcf2b9b11ed7/src/cigar.c#L18
# and
# FROM: https://github.com/kcleal/pywfa
# for enum trickery, see https://www.notinventedhere.org/articles/python/how-to-use-strings-as-name-aliases-in-python-enums.html
CigarOp = IntEnum(
    "CigarOp",
    [
        ("M", 0),
        ("I", 1),
        ("D", 2),
        ("N", 3),
        ("S", 4),
        ("H", 5),
        ("P", 6),
        ("=", 7),
        ("X", 8),
        ("B", 9),
    ],
)
CigarOp.__repr__ = lambda self: self.name
CigarOp.__str__ = CigarOp.__repr__
CigarOp.__format__ = lambda self, spec: self.__repr__()

OP_TO_COLUMN_NAME = {
    CigarOp["="]: "matches",
    CigarOp.X: "mismatches",
    CigarOp.I: "insertions",
    CigarOp.D: "deletions",
}


def encode_cigar(cigar):
    return "".join(f"{length}{op}" for op, length in cigar)


def decode_cigar(s):
    return [
        (CigarOp[match[2]], int(match[1]))
        for match in re.finditer(r"(\d+)(M|I|D|N|S|H|P|=|X|B)", s)
    ]


def _parse_variant(s):
    try:
        return int(s)
    except ValueError:
        return s


def _append_cigar(cigar, new):
    if len(cigar) and cigar[-1][0] == new[0]:
        old_length = cigar[-1][1]
        cigar[-1] = (new[0], old_length + new[1])
    elif new[1] != 0:
        cigar.append(new)
    return cigar


def cut_cigar(
    cigar,
    path,
    name_to_seq,
    query_start=None,
    query_end=None,
    query_length=None,
    path_start=None,
    path_end=None,
    sequence=None,
    phred=None,
    segments=None,
    variant_sep="=",
    key_sep="|",
    return_indices=True,
    return_counts=True,
    return_cigars=True,
    cigar_as_string=True,
    return_variants=True,
    separate_ends=True,
):
    if not cigar or not path:
        # fail gracefully if given empty inputs
        # the below code chokes if cigar or path is empty
        return {}
    if isinstance(name_to_seq, Gfa):
        name_to_seq = gfa_name_mapping(name_to_seq)
    segment_lengths = [len(name_to_seq[name]) for name in path]
    path_length = sum(segment_lengths)
    if path_start is not None:
        cigar = [(CigarOp.D, path_start), *cigar]
    if path_end is not None:
        cigar = [*cigar, (CigarOp.D, path_length - path_end)]
    if query_length is None and sequence is not None:
        query_length = len(sequence)
    if query_start is not None:
        cigar = [(CigarOp.I, query_start), *cigar]
    if query_end is not None:
        if query_length is None:
            raise ValueError("query_length is required if query_end is given")
        cigar = [*cigar, (CigarOp.I, query_length - query_end)]
    # zero-length ops cause problems
    cigar = [(op, length) for op, length in cigar if length != 0]
    segment_names = [name[1:] for name in path]
    segment_rc = [name[0] == "<" for name in path]
    if separate_ends:
        segment_names = ["upstream", *segment_names, "downstream"]
        segment_lengths = [0, *segment_lengths, 0]
        segment_rc = [False, *segment_rc, False]
    if segments:
        segments = set(segments)
    segment_idx = 0
    cigar_idx = 0
    query_idx = 0
    res = {}
    first = True
    while True:
        if (first or op != CigarOp.I) and (first or segment_length == 0):
            if not first:
                end_idx = query_idx
                if segments is None or segment_name in segments:
                    if return_indices:
                        res[segment_name]["start"] = start_idx
                        res[segment_name]["end"] = end_idx
                        res[segment_name]["reverse_complement"] = segment_rc[
                            segment_idx
                        ]
                    if sequence is not None:
                        segment_seq = sequence[start_idx:end_idx]
                        if segment_rc[segment_idx]:
                            segment_seq = reverse_complement(segment_seq)
                        res[segment_name]["seq"] = segment_seq
                    if phred is not None:
                        step = -1 if segment_rc[segment_idx] else 1
                        res[segment_name]["phred"] = phred[start_idx:end_idx:step]
                    if return_cigars:
                        if segment_rc[segment_idx]:
                            res[segment_name]["cigar"].reverse()
                        if cigar_as_string:
                            res[segment_name]["cigar"] = encode_cigar(
                                res[segment_name]["cigar"]
                            )
                segment_idx += 1
            if segment_idx == len(segment_lengths):
                if cigar_idx < len(cigar) or op_length != 0:
                    raise ValueError("path contained more bases than cigar")
                break
            else:
                segment_length = segment_lengths[segment_idx]
                segment_name = segment_names[segment_idx]
                if variant_sep is not None and variant_sep in segment_name:
                    sep_idx = segment_name.index(variant_sep)
                    segment_name, variant_name = (
                        segment_name[:sep_idx],
                        _parse_variant(segment_name[sep_idx + 1 :]),
                    )
                else:
                    variant_name = None
                start_idx = query_idx
                if segments is None or segment_name in segments:
                    if segment_name in res:
                        raise ValueError(f"duplicate segment: {segment_name}")
                    res[segment_name] = {}
                    if variant_name is not None and return_variants:
                        res[segment_name]["variant"] = variant_name
                    if return_counts:
                        for col_name in OP_TO_COLUMN_NAME.values():
                            res[segment_name][col_name] = 0
                    if return_cigars:
                        res[segment_name]["cigar"] = []
        if first or op_length == 0:
            if first:
                first = False
            else:
                cigar_idx += 1
            if cigar_idx == len(cigar):
                op = None
            elif cigar_idx >= len(cigar):
                raise ValueError("path contained fewer bases than cigar")
            else:
                op = cigar[cigar_idx][0]
                op_length = cigar[cigar_idx][1]
        if op == CigarOp.I:
            advance = op_length
        else:
            advance = min(x for x in (op_length, segment_length) if x is not None)
        if op in [CigarOp.I, CigarOp["="], CigarOp.X]:
            query_idx += advance
        op_length -= advance
        if op in [CigarOp.D, CigarOp["="], CigarOp.X]:
            segment_length -= advance
        if (segments is None or segment_name in segments) and op is not None:
            if return_counts:
                res[segment_name][OP_TO_COLUMN_NAME[op]] += advance
            if return_cigars:
                _append_cigar(res[segment_name]["cigar"], (op, advance))
    if key_sep is not None:
        row = {}
        for segment_name, segment_info in res.items():
            for k, v in segment_info.items():
                key = f"{segment_name}{key_sep}{k}"
                if key in row:
                    raise Exception(f"duplicate key: {key}")
                row[key] = v
        res = row
    return res
