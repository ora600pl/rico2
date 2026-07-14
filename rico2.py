# ------------------------------------------------------------------------------
#
#  Copyright 2018 Kamil Stawiarski ( kstawiarski@ora-600.pl | http://ora-600.pl )
#  Database Whisperers sp. z o. o. sp. k.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# ------------------------------------------------------------------------------

from struct import Struct
import importlib.util
import math
import os
import shlex
import sys
from binascii import hexlify as _hexlify
from binascii import unhexlify as _unhexlify
from decimal import Decimal


def hexlify(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return _hexlify(data).decode("ascii")


def unhexlify(data):
    if isinstance(data, str):
        data = data.encode("ascii")
    return _unhexlify(data)


def text_to_bytes(data):
    if isinstance(data, bytes):
        return data
    return data.encode("utf-8")


class OracleType(object):
    def __init__(self, data_hex, type_name = None):
        self.value_string = None
        self.ubyte = Struct("B")
        
        try:
            if data_hex == "*NULL*":
                self.value_string = data_hex
            elif type_name == 't':
                self.value_string = self.decode_date(data_hex)
            elif type_name == 'n':
                self.value_string = self.decode_number(data_hex)
            elif type_name == 'c':
                self.value_string = self.decode_string(data_hex)
        except:
            self.value_string = data_hex + "(" + type_name + ")"
            raise

    def decode_date(self, data_hex):
        data_hex_b = unhexlify(data_hex)
        century = "{0:02d}".format(data_hex_b[0] - 100)
        year = "{0:02d}".format(data_hex_b[1] - 100)
        month = "{0:02d}".format(data_hex_b[2])
        day = "{0:02d}".format(data_hex_b[3])
        hour = "{0:02d}".format(data_hex_b[4] - 1)
        minute = "{0:02d}".format(data_hex_b[5] - 1)
        second = "{0:02d}".format(data_hex_b[6] - 1)

        date_string = century + year + "-" + month + "-" + day + ":" + hour + ":" + minute + ":" + second
        return date_string

    def decode_string(self, data_hex, characterset=None):
        return unhexlify(data_hex).decode(characterset or "latin-1")

    def decode_number(self, data_hex):
        data_hex_b = unhexlify(data_hex)
        if data_hex == "80":
            return 0

        if data_hex_b[-1] != 102:
            exPot = data_hex_b[0] - 193
            numberValue = "0."
            exPot = exPot * 2 + 2

            for value in data_hex_b[1:]:
                numberValue += "{0:02d}".format(value - 1)
        else:
            exPot = 62 - data_hex_b[0]
            numberValue = "-0."
            exPot = exPot * 2 + 2

            for value in data_hex_b[1:-1]:
                numberValue += "{0:02d}".format(101 - value)

        fVal = Decimal(numberValue)
        powVal = Decimal(10) ** Decimal(exPot)
        return str(fVal * powVal).rstrip("0").rstrip(".")


class Rico(object):
    def __init__(self, pid = None):
        self.uint = Struct("I")
        self.uint2 = Struct("II")
        self.ubyte = Struct("B")
        self.ubyte2 = Struct("BB")
        self.ushort = Struct("H")
        self.ulong = Struct("Q")
        self.block_size = 8192
        self.max_block = 4194304

        self.block_type = {6: "DATA", 32: "FIRST LEVEL BITMAP BLOCK", 33: "SECOND LEVEL BITMAP BLOCK",
                           24: "THIRD LEVEL BITMAP BLOCK", 35: "PAGETABLE SEGMENT HEADER"}
        self.block_subtype = {1: "Table/Cluster", 2: "INDEX"}

        # Internal block offsets:
        self.offset_objd = {6: 24, 32: 192, 33: 104, 34: 192, 35: 272}
        self.numOfRowsOffset = 54  # you have to add itl count at offset 36
        self.rowDataOffset = 70  # you have to add itl count at offset 36
        self.ktbbhictOffset = 36  # offset of numer of ITL slots
        self.kdbhntabOffset = 53  # offset of the kdbt - 4B structure which has to be added to rowDataOffset to find row
        self.ktbbhtypOffset = 20  # for block type 6, byte 20 specifies 1 for table data and 2 for index data
        self.flg_kcbh_pos = 15
        self.offset_mod = 0
        self.manual_offset = 0

        self.min_rowdata = -1
        self.max_rowdata = -1
        self.current_rowp = 0

        self.block_data = None
        self.block_data_backup = None
        self.file_names = {}

        # type_kcbh, frmt_kcbh, spare1_kcbh, spare2_kcbh, rdba_kcbh, bas_kcbh, wrp_kcbh, seq_kcbh, flg_kcbh,
        # chkval_kcbh, spare3_kcbh
        self.struct_kcbh = Struct("BBBBIIHBBHH")

        # First 44 bytes of ktbbh
        self.struct_ktbbh = Struct("BIIIHBBI")

        # 24 bytes for ITL slot
        self.struct_ktbbhitl = Struct("HHIIHBHHI")

        # 14 bytes for kdbh struct
        self.struct_kdbh = Struct("Bbhhhhhh")

        # Index data block headers. Both start with the 16-byte kdxco
        # common header. Branch headers are 24 bytes; leaf headers are 32.
        self.struct_kdxbr = Struct("<BBBBIhhhhIh2x")
        self.struct_kdxle = Struct("<BBBBIhhhhhhIIBB2x")

        self.edit_mode = False
        self.current_offset = 0
        self.count = 512
        self.width = 80
        self.ibase = 10
        self.obase = 10
        self.listfile = None
        self.dirty = False

        self.current_block_desc = {}
        self.kdbr = []
        self.kdbr_data = []
        self.index_header = None
        self.index_offsets = []
        self.index_entries = []

        self.row_header = {"cluster_key": 128, "head_piece": 32, "first_data": 8, "first_column_from_prev_piece": 2,
                           "last_column_in_next_piece": 1, "clustered_table_member": 64, "deleted": 16, "last_data": 4}

        self.yara_offsets = []
        self.yara_offsets_xbh = []

        self.PID = 26807
        self.OBJ = 75178
        self.ROW = "ncccctcnnnn"

        if pid is not None:
            self.PID = pid

    @staticmethod
    def help():
        print("RICO v2 by Kamil Stawiarski (@ora600pl | www.ora-600.pl)")
        print("This is open source project to map BBED functionality.")
        print("If you know how to use BBED, you will know how to use this one.")
        print("Not everything is documented but in most cases the code is trivial to interpret it.")
        print("So if you don't know how to use this tool - then maybe you shouldn't ;)")
        print("\nUsage: python3 rico2.py listfile.txt")
        print("The listfile.txt should contain the list of the DBF files you want to read")

        print("\n !!! CAUTION !!!! \n")
        print("This tool should be used only to learn or in critical situations!")
        print("The usage is not supported!")
        print("If found on production system, this software should be considered as malware and deleted immediately!\n")

    def set_blocksize(self, bs):
        if bs <= 0 or bs % 512 != 0:
            raise ValueError("Block size must be a positive multiple of 512")
        self.block_size = bs

    def set_offset(self, offset):
        if offset < 0 or offset >= self.block_size:
            raise ValueError("Offset must be between 0 and " + str(self.block_size - 1))
        self.current_offset = offset

    def require_block(self):
        if self.block_data is None:
            raise RuntimeError("No block selected. Use: set dba <file>,<block>")

    def set_file(self, file_id):
        block_id = 1
        if self.current_block_desc:
            block_id = int(str(self.current_block_desc["BLOCK_ID"]).split()[0])
        self.get_block(file_id, block_id)

    def set_block(self, block_id):
        if not self.current_block_desc:
            file_id = sorted(self.file_names)[0]
        else:
            file_id = self.current_block_desc["FILE_ID"]
        self.get_block(file_id, block_id)

    @staticmethod
    def _base_name(base):
        return {8: "Oct", 10: "Dec", 16: "Hex"}[base]

    def show(self, parameter=None):
        values = {
            "FILE": self.current_block_desc.get("FILE_ID", "N/A"),
            "BLOCK": self.current_block_desc.get("BLOCK_ID", "N/A"),
            "OFFSET": self.current_offset,
            "DBA": self.current_block_desc.get("DBA", "N/A"),
            "FILENAME": self.current_block_desc.get("FILE_NAME", "N/A"),
            "LISTFILE": self.listfile or "N/A",
            "BLOCKSIZE": self.block_size,
            "MODE": "Edit" if self.edit_mode else "Browse",
            "IBASE": self._base_name(self.ibase),
            "OBASE": self._base_name(self.obase),
            "WIDTH": self.width,
            "COUNT": self.count,
            "DIRTY": "Yes" if self.dirty else "No",
        }
        if parameter:
            key = parameter.upper().rstrip("#")
            if key == "DBA" and values[key] != "N/A":
                value = values[key]
                print("\t{0:<15}\t{1} ({2} {3},{4})".format(
                    key, hex(value), value, values["FILE"], values["BLOCK"]))
                return
            if key not in values:
                raise ValueError("Unknown SHOW parameter: " + parameter)
            print("\t{0:<15}\t{1}".format(key, values[key]))
            return

        for key in ("FILE", "BLOCK", "OFFSET", "DBA", "FILENAME", "LISTFILE",
                    "BLOCKSIZE", "MODE", "IBASE", "OBASE", "WIDTH", "COUNT", "DIRTY"):
            if key == "DBA" and values[key] != "N/A":
                value = values[key]
                display = "{0} ({1} {2},{3})".format(hex(value), value, values["FILE"], values["BLOCK"])
            else:
                display = values[key]
            print("\t{0:<15}\t{1}".format(key, display))

    def info(self):
        print(" File#  Name                                                        Size(blks)")
        print(" -----  ----                                                        ----------")
        for file_id in sorted(self.file_names):
            path = self.file_names[file_id]
            blocks = os.path.getsize(path) // self.block_size
            display_path = path if len(path) <= 58 else "..." + path[-55:]
            print("{0:6d}  {1:<58s}  {2:10d}".format(file_id, display_path, blocks))

    def add_file(self, dbf):
        self.listfile = os.path.abspath(dbf)
        with open(dbf, "r") as listfile:
            dbfs = listfile.readlines()
        for f in dbfs:
            fields = f.split()
            if not fields or fields[0].startswith("#"):
                continue
            file_id = int(fields[0])
            file_name = fields[1]
            self.file_names[file_id] = file_name
            print(str(file_id) + "\t" + file_name)

    def decode_rowid(self, rowid_data):
        if len(rowid_data) != 6:
            raise ValueError("A physical rowid must contain 6 bytes")
        dba = int.from_bytes(rowid_data[:4], "big")
        slot = int.from_bytes(rowid_data[4:], "big")
        return {
            "RAW": hexlify(rowid_data),
            "DBA": dba,
            "FILE_ID": dba // self.max_block,
            "BLOCK_ID": dba % self.max_block,
            "SLOT": slot,
        }

    def _parse_columns(self, start, count, end=None, allow_trailing_marker=False):
        end = self.block_size - 4 if end is None else min(end, self.block_size - 4)
        columns = []
        pos = start
        for _ in range(count):
            if pos >= end:
                break
            col_offset = pos
            col_len = self.block_data[pos]
            pos += 1
            if col_len == 255:
                columns.append([0, col_offset, "*NULL*"])
                continue
            if col_len == 254:
                if allow_trailing_marker and pos == end:
                    break
                if pos + 2 > end:
                    raise ValueError("Truncated extended column length at offset " + str(col_offset))
                col_len = self.ushort.unpack(self.block_data[pos:pos + 2])[0]
                pos += 2
            if pos + col_len > end:
                raise ValueError("Column at offset {0} exceeds its row boundary".format(col_offset))
            columns.append([col_len, col_offset, hexlify(self.block_data[pos:pos + col_len])])
            pos += col_len
        return columns, pos

    def get_index_details(self):
        base = 20 + 24 + self.current_block_desc["ITLS"] * 24
        level = self.block_data[base]
        if level == 0:
            values = self.struct_kdxle.unpack(self.block_data[base:base + self.struct_kdxle.size])
            header_size = self.struct_kdxle.size
            kind = "LEAF"
        else:
            values = self.struct_kdxbr.unpack(self.block_data[base:base + self.struct_kdxbr.size])
            header_size = self.struct_kdxbr.size
            kind = "BRANCH"

        header = {
            "OFFSET": base,
            "SIZE": header_size,
            "KIND": kind,
            "LEVEL": values[0],
            "LOCK": values[1],
            "OPCODE": values[2],
            "NCOLS": values[3],
            "SDC": values[4],
            "NROWS": values[5],
            "FBO": values[6],
            "FEO": values[7],
            "AVS": values[8],
        }
        if kind == "LEAF":
            header.update({
                "SPL": values[9], "ENDE": values[10], "NEXT": values[11],
                "PREV": values[12], "DSZ": values[13], "FLAGS": values[14],
            })
            header["ENTRY_FORMAT"] = "RAW" if header["FLAGS"] & 0x10 else "BTREE_LEAF"
        else:
            header.update({"LMC": values[9], "SNO": values[10], "ENTRY_FORMAT": "BTREE_BRANCH"})

        nrows = max(0, header["NROWS"])
        offsets_start = base + header_size
        self.index_offsets = []
        for row in range(nrows):
            pos = offsets_start + row * 2
            if pos + 2 > self.block_size - 4:
                raise ValueError("Index offset table exceeds the block")
            pointer = self.ushort.unpack(self.block_data[pos:pos + 2])[0]
            self.index_offsets.append([pos, pointer, base + pointer])

        header["OFFSETS_START"] = offsets_start
        header["FREESPACE_START"] = base + header["FBO"] - 4
        header["ROWDATA_START"] = base + header["FEO"] - 4
        real_offsets = [item[2] for item in self.index_offsets if 0 <= item[2] < self.block_size - 4]
        header["ROWDATA_END"] = max(real_offsets) - 4 if real_offsets else self.block_size - 4
        if header["ROWDATA_END"] < header["ROWDATA_START"]:
            header["ROWDATA_END"] = self.block_size - 4

        boundaries = sorted(set(real_offsets + [self.block_size - 4]))
        self.index_entries = []
        for slot, (_, pointer, real) in enumerate(self.index_offsets):
            if not (header["ROWDATA_START"] <= real < header["ROWDATA_END"]):
                continue
            next_offsets = [value for value in boundaries if value > real]
            end = next_offsets[0] if next_offsets else header["ROWDATA_END"]
            entry = {"SLOT": slot, "POINTER": pointer, "OFFSET": real, "END": end}
            try:
                if kind == "LEAF":
                    if header["ENTRY_FORMAT"] == "RAW":
                        entry["RAW"] = hexlify(self.block_data[real:end])
                        self.index_entries.append(entry)
                        continue
                    if real + 8 > end:
                        raise ValueError("truncated leaf entry")
                    entry["FLAG"] = self.block_data[real]
                    entry["LOCK"] = self.block_data[real + 1]
                    entry["ROWID"] = self.decode_rowid(self.block_data[real + 2:real + 8])
                    entry["COL_DATA"], entry["DATA_END"] = self._parse_columns(
                        real + 8, max(0, header["NCOLS"] - 1), end)
                else:
                    if real + 4 > end:
                        raise ValueError("truncated branch entry")
                    child_dba = self.uint.unpack(self.block_data[real:real + 4])[0]
                    child_file = child_dba // self.max_block
                    if child_file not in self.file_names:
                        continue
                    entry["CHILD_DBA"] = child_dba
                    entry["CHILD_FILE"] = child_file
                    entry["CHILD_BLOCK"] = child_dba % self.max_block
                    entry["COL_DATA"], entry["DATA_END"] = self._parse_columns(
                        real + 4, header["NCOLS"], end, allow_trailing_marker=True)
            except (ValueError, IndexError) as error:
                entry["PARSE_ERROR"] = str(error)
            self.index_entries.append(entry)

        self.index_header = header
        self.current_block_desc["INDEX_KIND"] = kind
        self.current_block_desc["INDEX_LEVEL"] = header["LEVEL"]
        self.current_block_desc["INDEX_ROWS"] = len(self.index_entries)

    def get_row_details(self):
        num_of_itls = self.current_block_desc["ITLS"]

        delcared_rows_offset = 24 * num_of_itls + self.numOfRowsOffset + self.offset_mod
        declared_rows = self.ubyte.unpack(self.block_data[delcared_rows_offset:delcared_rows_offset + 1])[0]
        actual_rows = 0

        num_of_tables_offset = 24 * num_of_itls + self.kdbhntabOffset + self.offset_mod
        num_of_tables = self.ubyte.unpack(self.block_data[num_of_tables_offset:num_of_tables_offset + 1])[0]
        # first row pointer in a block
        row_pointer_offset = self.rowDataOffset + 24 * num_of_itls + 4 * (num_of_tables - 1) + self.offset_mod
        self.current_block_desc["FIRST_KDBR"] = row_pointer_offset

        if declared_rows > 0:

            for row in range(declared_rows):
                try:
                    self.kdbr_data.append({})
                    stored_pointer = self.ushort.unpack(self.block_data[row_pointer_offset:row_pointer_offset + 2])[0]
                    if stored_pointer == 0xffff:
                        self.kdbr_data[row]["UNUSED"] = True
                        self.kdbr_data[row]["POINTER"] = -1
                        row_pointer_offset += 2
                        continue
                    row_pointer = stored_pointer + 100 + 24 * (num_of_itls - 2) + self.offset_mod

                    row_header = self.ubyte2.unpack(self.block_data[row_pointer:row_pointer + 2])

                    self.kdbr_data[row]["OFFSET"] = row_pointer
                    self.kdbr_data[row]["FLAG"] = row_header[0]
                    self.kdbr_data[row]["LOCK"] = row_header[1]

                    hfl = self.row_header["head_piece"] + self.row_header["first_data"] + self.row_header["last_data"]
                    hfld = self.row_header["head_piece"] + self.row_header["first_data"] + self.row_header["last_data"] + self.row_header["deleted"]
                    cluster_member_flag = self.row_header["head_piece"] + self.row_header["first_data"] + self.row_header["last_data"] + self.row_header["clustered_table_member"]
                    cluster_key_flag = self.row_header["cluster_key"] + self.row_header["head_piece"] + self.row_header["first_data"] + self.row_header["last_data"]
                    hfcn = self.row_header["head_piece"] + self.row_header["first_data"] + self.row_header["last_column_in_next_piece"]
                    hfln = self.row_header["head_piece"] + self.row_header["last_data"] + self.row_header["first_column_from_prev_piece"]
                    h = self.row_header["head_piece"]
                    fl = self.row_header["first_data"] + self.row_header["last_data"]
                    f = self.row_header["first_data"]
                    if row_header[0] in (hfl, cluster_member_flag, cluster_key_flag, h):
                        actual_rows += 1

                    row_pos = row_pointer + 2
                    if row_header[0] == hfl or row_header[0] == hfld or row_header[0] == fl or row_header[0] == f:
                        ncols = self.ubyte.unpack(self.block_data[row_pos:row_pos+1])[0]
                        row_pos += 1

                        if ncols == 254:
                            ncols = self.ushort.unpack(self.block_data[row_pos:row_pos + 2])[0]
                            row_pos += 2

                        self.kdbr_data[row]["NCOLS"] = ncols
                        if row_header[0] == fl:
                            print(self.block_data[row_pos:row_pos + 4])
                            hrid_b = hexlify(self.block_data[row_pos:row_pos + 4])
                            print(hrid_b)
                            block_no = int(hrid_b, 16) % self.max_block
                            row_pos += 4
                            hrid_r = hexlify(self.block_data[row_pos:row_pos + 2])
                            hrid = "0x" + hrid_b + "." + hrid_r
                            print(hrid)
                            row_no = int(hrid_r, 16)
                            file_no = int(hrid_b, 16) // self.max_block
                            self.kdbr_data[row]["HRID"] = hrid + " [file: " + str(file_no) + " block: " \
                                                          + str(block_no) + " kdbr: " + str(row_no) + " ]"
                            row_pos += 2
                        elif row_header[0] == f:
                            print(self.block_data[row_pos:row_pos + 4])
                            hrid_b = hexlify(self.block_data[row_pos:row_pos + 4])
                            print(hrid_b)
                            block_no = int(hrid_b, 16) % self.max_block
                            row_pos += 4
                            hrid_r = hexlify(self.block_data[row_pos:row_pos + 2])
                            hrid = "0x" + hrid_b + "." + hrid_r
                            print(hrid)
                            row_no = int(hrid_r, 16)
                            file_no = int(hrid_b, 16) // self.max_block
                            self.kdbr_data[row]["HRID"] = hrid + " [file: " + str(file_no) + " block: " \
                                                          + str(block_no) + " kdbr: " + str(row_no) + " ]"
                            row_pos += 2
                            nrid_b = hexlify(self.block_data[row_pos:row_pos + 4])
                            block_no = int(nrid_b, 16) % self.max_block
                            row_pos += 4
                            nrid_r = hexlify(self.block_data[row_pos:row_pos + 2])
                            nrid = "0x" + nrid_b + "." + nrid_r
                            row_no = int(nrid_r, 16)
                            file_no = int(nrid_b, 16) // self.max_block
                            self.kdbr_data[row]["NRID"] = nrid + " [file: " + str(file_no) + " block: " \
                                                          + str(block_no) + " kdbr: " + str(row_no) + " ]"
                            row_pos += 2


                        self.kdbr_data[row]["COL_DATA"] = []
                        for i in range(ncols):
                            col_offset = row_pos
                            col_len = self.ubyte.unpack(self.block_data[row_pos:row_pos+1])[0]
                            row_pos += 1

                            if col_len == 255:
                                col_data_hex = "*NULL*"
                                col_len = 0
                            else:
                                if col_len == 254:
                                    col_len = self.ushort.unpack(self.block_data[row_pos:row_pos + 2])[0]
                                    row_pos += 2

                                col_data_hex = hexlify(self.block_data[row_pos:row_pos+col_len])

                            self.kdbr_data[row]["COL_DATA"].append([col_len, col_offset, col_data_hex])

                            row_pos += col_len

                        if row_pos > self.max_rowdata:
                            self.max_rowdata = row_pos

                    elif row_header[0] == h:
                        ncols = self.ubyte.unpack(self.block_data[row_pos:row_pos + 1])[0]
                        row_pos += 1

                        if ncols == 254:
                            ncols = self.ushort.unpack(self.block_data[row_pos:row_pos + 2])[0]
                            row_pos += 2

                        self.kdbr_data[row]["NCOLS"] = ncols

                        nrid_b = hexlify(self.block_data[row_pos:row_pos + 4])
                        block_no = int(nrid_b, 16) % self.max_block
                        row_pos += 4
                        nrid_r = hexlify(self.block_data[row_pos:row_pos + 2])
                        nrid = "0x" + nrid_b + "." + nrid_r
                        row_no = int(nrid_r, 16)
                        file_no = int(nrid_b, 16) // self.max_block
                        self.kdbr_data[row]["NRID"] = nrid + " [file: " + str(file_no) + " block: " \
                                                      + str(block_no) + " kdbr: " + str(row_no) + " ]"

                    elif row_header[0] == cluster_member_flag:
                        ncols = self.ubyte.unpack(self.block_data[row_pos:row_pos + 1])[0]
                        row_pos += 1
                        table_number = self.ubyte.unpack(self.block_data[row_pos:row_pos + 1])[0]
                        row_pos += 1
                        self.kdbr_data[row]["NCOLS"] = ncols
                        self.kdbr_data[row]["TABNO"] = table_number
                        self.kdbr_data[row]["CLUSTER_MEMBER"] = True
                        self.kdbr_data[row]["COL_DATA"], row_pos = self._parse_columns(row_pos, ncols)

                        if row_pos > self.max_rowdata:
                            self.max_rowdata = row_pos

                    elif row_header[0] == cluster_key_flag:
                        ncols = self.ubyte.unpack(self.block_data[row_pos:row_pos + 1])[0]
                        row_pos += 1
                        first_slot = self.ushort.unpack(self.block_data[row_pos:row_pos + 2])[0]
                        last_slot = self.ushort.unpack(self.block_data[row_pos + 2:row_pos + 4])[0]
                        row_pos += 4
                        first_rowid = self.decode_rowid(self.block_data[row_pos:row_pos + 6])
                        last_rowid = self.decode_rowid(self.block_data[row_pos + 6:row_pos + 12])
                        row_pos += 12
                        self.kdbr_data[row]["NCOLS"] = ncols
                        self.kdbr_data[row]["CLUSTER_KEY"] = True
                        self.kdbr_data[row]["FIRST_SLOT"] = first_slot
                        self.kdbr_data[row]["LAST_SLOT"] = last_slot
                        self.kdbr_data[row]["FIRST_ROWID"] = first_rowid
                        self.kdbr_data[row]["LAST_ROWID"] = last_rowid
                        self.kdbr_data[row]["COL_DATA"], row_pos = self._parse_columns(row_pos, ncols)

                        if row_pos > self.max_rowdata:
                            self.max_rowdata = row_pos

                    row_pointer_offset += 2

                except BaseException as error:
                    if row < len(self.kdbr_data):
                        self.kdbr_data[row]["PARSE_ERROR"] = str(error)
                    row_pointer_offset += 2

        self.current_block_desc["DECLARED_ROWS"] = declared_rows
        self.current_block_desc["NTAB"] = num_of_tables
        self.current_block_desc["ACTUAL_ROWS"] = actual_rows
        self.current_block_desc["IS_CLUSTER"] = num_of_tables > 1 or any(
            row.get("CLUSTER_KEY") or row.get("CLUSTER_MEMBER") for row in self.kdbr_data)

    def get_block(self, file_id, block_id):
        if file_id not in self.file_names:
            raise ValueError("Unknown file number: " + str(file_id))
        if block_id < 0:
            raise ValueError("Block number cannot be negative")
        dbf = open(self.file_names[file_id], "rb")
        dbf.seek(block_id * self.block_size)
        self.block_data = dbf.read(self.block_size)
        if len(self.block_data) != self.block_size:
            dbf.close()
            raise ValueError("Block is outside the file or is truncated")
        self.block_data_backup = self.block_data
        dbf.close()
        dba = file_id * self.max_block + block_id

        self.current_block_desc = {"DBA": dba, "FILE_ID": file_id, "FILE_NAME": self.file_names[file_id], "BLOCK_ID": block_id}
        self.dirty = False
        self.parse_block()

    def get_block_memdump(self, fname, offset):
        memfile = open(fname, "rb")
        memfile.seek(offset)
        self.block_data = memfile.read(self.block_size)
        self.block_data_backup = self.block_data
        memfile.close()
        kcbh = self.struct_kcbh.unpack(self.block_data[0:20])
        dba = kcbh[4]
        file_id = dba // self.max_block
        block_id = dba % self.max_block
        self.current_block_desc = {"DBA": dba, "FILE_ID": file_id, "FILE_NAME": "N/A", "BLOCK_ID": str(block_id) + " @ " + str(offset)}
        self.parse_block()

    def get_block_memory(self, offset):
        pid = self.PID
        memfile = open("/proc/" + str(pid) + "/mem", "rb")
        memfile.seek(offset)
        self.block_data = memfile.read(self.block_size)
        self.block_data_backup = self.block_data
        memfile.close()
        kcbh = self.struct_kcbh.unpack(self.block_data[0:20])
        dba = kcbh[4]
        file_id = dba // self.max_block
        block_id = dba % self.max_block
        self.current_block_desc = {"DBA": dba, "FILE_ID": file_id, "FILE_NAME": "N/A", "PID": str(pid), "BLOCK_ID": str(block_id) + " @ " + str(offset)}
        self.parse_block()

    def yara_scan(self, data_object_id, more_str="N/A"):
        pid = self.PID
        try:
            if importlib.util.find_spec('yara') is None:
                raise ImportError
            import yara
            if more_str == "N/A":
                yara_rule_txt = "rule obj { strings: $hs = { 06a2 [0-201] 01000000" + hexlify(self.uint.pack(data_object_id)) + " } condition: $hs }"
                rules = yara.compile(source=yara_rule_txt)
                matches = rules.match(pid=pid)
                for m in matches:
                    for s in m.strings:
                        print(str(s[0]) + "\t" + hexlify(s[2]))
                        if len(hexlify(s[2])) == 56:
                            self.yara_offsets.append(s[0])

        except ImportError:
            print("You don't have YARA installed!")


    def yara_scan_bh(self, data_object_id, more_str="N/A"):
        pid = self.PID
        try:
            if importlib.util.find_spec('yara') is None:
                raise ImportError
            import yara
            if more_str == "N/A":
                yara_rule_txt = "rule xbh { strings: $hs = { " + hexlify(self.uint.pack(data_object_id)) + " 000000010000200000000000 } $hs2 = { " + hexlify(self.uint.pack(data_object_id)) + " 0000000100000800000000} $hs3 = { " + hexlify(self.uint.pack(data_object_id)) + " 0000000100202800000000} $hs4 = { " + hexlify(self.uint.pack(data_object_id)) + " 0000000100200800000000}  $hs5 = { " + hexlify(self.uint.pack(data_object_id)) + " 0000000100200000000000}  condition: $hs or $hs2 or $hs3 or $hs4 or $hs5 }"
                rules = yara.compile(source=yara_rule_txt)
                matches = rules.match(pid=pid)
                for m in matches:
                    for s in m.strings:
                        print(str(s[0]) + "\t" + hexlify(s[2]))
                        self.yara_offsets_xbh.append(s[0])

        except ImportError:
            print("You don't have YARA installed!")

    def dump_memory_offset(self, offset, size):
        pid = self.PID
        f = open("/proc/" + str(pid) + "/mem", "rb")
        f.seek(offset)
        print(hexlify(f.read(size)))
        f.close()

    def set_dirty_flag_bh(self, offset):
        pid = self.PID
        flag = unhexlify("01000000")
        f = open("/proc/" + str(pid) + "/mem", "rb+")
        f.seek(offset)
        f.write(flag)
        f.close()

    def dump_rows(self, pattern, file_name):
        pid = self.PID
        f = open(file_name, "w")
        for offset in self.yara_offsets:
            self.get_block_memory(offset)
            rown = -1
            for row in self.kdbr_data:
                rown += 1
                row_data = ""
                if row.get('COL_DATA') and len(row['COL_DATA']) > 0:
                    print("Dumping row: ", rown, " block: ", self.current_block_desc["BLOCK_ID"])
                    dumpColumns = len(row['COL_DATA'])
                    if len(pattern) < dumpColumns:
                        dumpColumns = len(pattern)

                    for col in range(dumpColumns):
                        try:
                            ot = OracleType(row['COL_DATA'][col][2], pattern[col])
                            row_data += str(ot.value_string) + " "
                        except BaseException as e:
                            print(str(e))
                            print(row['COL_DATA'][col][2], pattern[col], rown)
                            #raise
                    f.write(row_data + "\n")
        f.close()


    def parse_block(self):
        block_type = self.ubyte.unpack(self.block_data[0:1])[0]
        block_subtype = self.ubyte.unpack(self.block_data[20:21])[0]


        self.kdbr = []
        self.kdbr_data = []
        self.index_header = None
        self.index_offsets = []
        self.index_entries = []
        self.min_rowdata = -1
        self.max_rowdata = -1
        self.current_rowp = 0
        self.current_offset = 0
        self.offset_mod = self.manual_offset
        self.current_block_desc["BLOCK_TYPE"] = block_type
        self.current_block_desc["BLOCK_SUBTYPE"] = block_subtype

        if block_type == 6:
            self.current_block_desc["ITLS"] = \
                self.ubyte.unpack(self.block_data[self.ktbbhictOffset:self.ktbbhictOffset + 1])[0]

            end_of_ktbbh = 20 + 24 + self.current_block_desc["ITLS"] * 24
            mod_flags = self.uint2.unpack(self.block_data[end_of_ktbbh:end_of_ktbbh+8])
            if mod_flags[0] == 0 and mod_flags[1] == 0:
                self.offset_mod = 0
            elif mod_flags[0] == 0 and mod_flags[1] > 0:
                self.offset_mod = -4
            elif mod_flags[0] > 0 and mod_flags[1] > 0:
                self.offset_mod = -8

        if block_type == 6 and block_subtype == 1:
            self.get_row_details()
            nrows = self.current_block_desc["DECLARED_ROWS"]
            row_pointer_offset = self.current_block_desc["FIRST_KDBR"]
            num_of_itls = self.current_block_desc["ITLS"]
            for i in range(nrows):
                row_pointer = self.ushort.unpack(self.block_data[row_pointer_offset:row_pointer_offset + 2])[0]
                if row_pointer == 0xffff:
                    self.kdbr.append([row_pointer_offset, -1, None])
                    row_pointer_offset += 2
                    continue
                row_pointer_real = row_pointer + 100 + 24 * (num_of_itls - 2) + self.offset_mod
                self.kdbr.append([row_pointer_offset, row_pointer, row_pointer_real])
                if row_pointer_real < self.min_rowdata or self.min_rowdata == -1:
                    self.min_rowdata = row_pointer_real

                if row_pointer_real > self.max_rowdata:
                    self.max_rowdata = row_pointer_real

                row_pointer_offset += 2

        elif block_type == 6 and block_subtype == 2:
            self.get_index_details()


        dba = self.current_block_desc['DBA']
        file_id = self.current_block_desc['FILE_ID']
        block_id = self.current_block_desc['BLOCK_ID']
        print("\tDBA\t\t" + str(hex(dba)) + " (" + str(dba) + " " + str(file_id) + "," + str(block_id) + ")")

    def p_kcbh(self):
        self.current_offset = 0
        kcbh = self.struct_kcbh.unpack(self.block_data[0:20])
        print("struct kcbh, 20 bytes\t\t\t@0")
        print("\tub1 type_kcbh\t\t\t@0\t0x0" + str(kcbh[0]))
        print("\tub1 frmt_kcbh\t\t\t@1\t" + str(hex(kcbh[1])))
        print("\tub1 spare1_kcbh\t\t\t@2\t" + str(hex(kcbh[2])))
        print("\tub1 spare2_kcbh\t\t\t@3\t0x0" + str(kcbh[3]))
        print("\tub4 rdba_kcbh\t\t\t@4\t" + str(hex(kcbh[4])))
        print("\tub4 bas_kcbh\t\t\t@8\t" + str(hex(kcbh[5])))
        print("\tub2 wrp_kcbh\t\t\t@12\t" + str(hex(kcbh[6])))
        print("\tub1 seq_kcbh\t\t\t@14\t0x0" + str(kcbh[7]))
        print("\tub1 flg_kcbh\t\t\t@15\t0x0" + str(kcbh[8]))
        print("\tub2 chkval_kcbh\t\t\t@16\t" + str(hex(kcbh[9])))
        print("\tub2 spare3_kcbh\t\t\t@18\t" + str(hex(kcbh[10])))
        print("\n")

    def map(self):
        print(" File: " + self.current_block_desc["FILE_NAME"] + "(" + str(self.current_block_desc["FILE_ID"]) + ")")
        print(" Block: " + str(self.current_block_desc["DBA"] & (self.max_block-1))
              + "\t\t\tDba: " + str(hex(self.current_block_desc["DBA"])))
        print("------------------------------------------------------------")

        block_type = self.ubyte.unpack(self.block_data[0:1])[0]
        block_subtype = self.ubyte.unpack(self.block_data[20:21])[0]
        if block_type == 6 and block_subtype == 2 and self.index_header:
            description = "KTB Data Block (Index {0})".format(self.index_header["KIND"].title())
        else:
            description = self.block_type.get(block_type, "OTHER") + " " + self.block_subtype.get(block_subtype, "")
        print(" " + description + "\n")
        print(" struct kcbh, 20 bytes\t\t\t\t@0\n")
        print(" struct ktbbh, {0:>3s} bytes \t\t\t@20\n".format(str(24 + self.current_block_desc["ITLS"]*24)))

        if block_type == 6 and block_subtype == 1:
            kdbh_offset = 20 + 24 + self.current_block_desc["ITLS"]*24 + self.offset_mod + 8
            print(" struct kdbh, 14 bytes \t\t\t\t@" + str(kdbh_offset) + "\n")
            kdbt_offset = kdbh_offset + 14
            kdbt_size = self.current_block_desc["NTAB"] * 4
            print(" struct kdbt[" + str(self.current_block_desc["NTAB"]) + "], " + str(kdbt_size) + " bytes\t\t\t" +
                  "@" + str(kdbt_offset) + "\n")
            print(" sb2 kdbr[" + str(self.current_block_desc["DECLARED_ROWS"]) + "]\t\t\t\t\t@"
                  + str(self.current_block_desc["FIRST_KDBR"]))
            free_space_start = self.current_block_desc["FIRST_KDBR"] + self.current_block_desc["DECLARED_ROWS"] * 2
            free_space_size = self.min_rowdata - free_space_start
            print("\n ub1 freespace[" + str(free_space_size) + "]\t\t\t\t@" + str(free_space_start))
            rowdata_size = self.max_rowdata - self.min_rowdata
            print("\n ub1 rowdata[" + str(rowdata_size) + "]\t\t\t\t@" + str(self.min_rowdata))

        elif block_type == 6 and block_subtype == 2 and self.index_header:
            header = self.index_header
            structure = "kdxle" if header["KIND"] == "LEAF" else "kdxbr"
            print(" struct {0}, {1} bytes \t\t\t\t@{2}\n".format(
                structure, header["SIZE"], header["OFFSET"]))
            print(" sb2 kd_off[{0}]\t\t\t\t\t@{1}".format(
                header["NROWS"], header["OFFSETS_START"]))
            free_size = max(0, header["ROWDATA_START"] - header["FREESPACE_START"])
            print("\n ub1 freespace[{0}]\t\t\t\t@{1}".format(
                free_size, header["FREESPACE_START"]))
            rowdata_size = max(0, header["ROWDATA_END"] - header["ROWDATA_START"])
            print("\n ub1 rowdata[{0}]\t\t\t\t@{1}".format(
                rowdata_size, header["ROWDATA_START"]))

        print("\n ub4 tailchk\t\t\t\t\t@" + str(self.block_size-4))

        print("\n")

    def p_kdx(self):
        if not self.index_header:
            raise RuntimeError("The selected block is not an index data block")
        h = self.index_header
        name = "kdxle" if h["KIND"] == "LEAF" else "kdxbr"
        offset = h["OFFSET"]
        print("struct {0}, {1} bytes\t\t\t@{2}".format(name, h["SIZE"], offset))
        print("  ub1 kdxcolev\t\t\t@{0}\t{1}".format(offset, h["LEVEL"]))
        print("  ub1 kdxcolok\t\t\t@{0}\t{1}".format(offset + 1, hex(h["LOCK"])))
        print("  ub1 kdxcoopc\t\t\t@{0}\t{1}".format(offset + 2, hex(h["OPCODE"])))
        print("  ub1 kdxconco\t\t\t@{0}\t{1}".format(offset + 3, h["NCOLS"]))
        print("  ub4 kdxcosdc\t\t\t@{0}\t{1}".format(offset + 4, hex(h["SDC"])))
        print("  sb2 kdxconro\t\t\t@{0}\t{1}".format(offset + 8, h["NROWS"]))
        print("  sb2 kdxcofbo\t\t\t@{0}\t{1}".format(offset + 10, h["FBO"]))
        print("  sb2 kdxcofeo\t\t\t@{0}\t{1}".format(offset + 12, h["FEO"]))
        print("  sb2 kdxcoavs\t\t\t@{0}\t{1}".format(offset + 14, h["AVS"]))
        if h["KIND"] == "LEAF":
            print("  sb2 kdxlespl\t\t\t@{0}\t{1}".format(offset + 16, h["SPL"]))
            print("  sb2 kdxlende\t\t\t@{0}\t{1}".format(offset + 18, h["ENDE"]))
            print("  ub4 kdxlenxt\t\t\t@{0}\t{1}".format(offset + 20, hex(h["NEXT"])))
            print("  ub4 kdxleprv\t\t\t@{0}\t{1}".format(offset + 24, hex(h["PREV"])))
            print("  ub1 kdxledsz\t\t\t@{0}\t{1}".format(offset + 28, h["DSZ"]))
            print("  ub1 kdxleflg\t\t\t@{0}\t{1}".format(offset + 29, hex(h["FLAGS"])))
            print("  entry format\t\t\t\t{0}".format(h["ENTRY_FORMAT"]))
        else:
            print("  ub4 kdxbrlmc\t\t\t@{0}\t{1}".format(offset + 16, hex(h["LMC"])))
            print("  sb2 kdxbrsno\t\t\t@{0}\t{1}".format(offset + 20, h["SNO"]))
        print("")

    def p_kd_off(self, slot=-1):
        if not self.index_header:
            raise RuntimeError("The selected block is not an index data block")
        offsets = enumerate(self.index_offsets) if slot == -1 else [(slot, self.index_offsets[slot])]
        for number, item in offsets:
            print("sb2 kd_off[{0}]\t\t\t@{1}\t{2} => {3}".format(
                number, item[0], item[1], item[2]))
        print("")

    def p_index_entry(self, slot):
        if not self.index_header:
            raise RuntimeError("The selected block is not an index data block")
        matches = [entry for entry in self.index_entries if entry["SLOT"] == slot]
        if not matches:
            raise ValueError("kd_off[{0}] is a sentinel or does not point to rowdata".format(slot))
        entry = matches[0]
        self.current_offset = entry["OFFSET"]
        print("index entry kd_off[{0}]\t\t@{1}".format(slot, entry["OFFSET"]))
        if entry.get("PARSE_ERROR"):
            print("parse error: " + entry["PARSE_ERROR"])
            return
        if entry.get("RAW") is not None:
            print("raw={0}".format(entry["RAW"]))
            print("special index entry format; semantic decoding is not available")
            return
        if self.index_header["KIND"] == "LEAF":
            rowid = entry["ROWID"]
            print("flag={0} lock={1}".format(hex(entry["FLAG"]), hex(entry["LOCK"])))
            print("rowid={0} [file: {1} block: {2} slot: {3}]".format(
                rowid["RAW"], rowid["FILE_ID"], rowid["BLOCK_ID"], rowid["SLOT"]))
        else:
            print("child dba={0} [file: {1} block: {2}]".format(
                hex(entry["CHILD_DBA"]), entry["CHILD_FILE"], entry["CHILD_BLOCK"]))
        for number, column in enumerate(entry.get("COL_DATA", [])):
            print("col{0:>5s}[{1:6s} @{2}: {3}".format(
                str(number), str(column[0]) + "]", column[1], column[2]))
        print("")

    def p_tailchk(self):
        tailchk_off = self.block_size-4
        tailchk_val = self.uint.unpack(self.block_data[tailchk_off:tailchk_off+4])[0]
        print("ub4 tailchk\t\t\t\t@" + str(tailchk_off) + "\t" + str(hex(tailchk_val)) + "\n")

    def _require_table_data_block(self):
        if self.current_block_desc.get("BLOCK_TYPE") != 6 or self.current_block_desc.get("BLOCK_SUBTYPE") != 1:
            raise RuntimeError("This structure is available only for table/cluster data blocks")

    def p_kdbt(self):
        self._require_table_data_block()
        kdbt_offset = 20 + 24 + self.current_block_desc["ITLS"] * 24 + self.offset_mod + 8 + 14
        for i in range(self.current_block_desc["NTAB"]):
            print("struct kdbt[" + str(i) + "], 4 bytes \t\t\t@" + str(kdbt_offset + i*4))
            kdbtoffs = self.ushort.unpack(self.block_data[kdbt_offset + i * 4 : kdbt_offset + i * 4 + 2])[0]
            kdbtnrow = self.ushort.unpack(self.block_data[kdbt_offset + i * 4 + 2 : kdbt_offset + i * 4 + 4])[0]
            print("\tsb2 kdbtoffs\t\t\t\t@" + str(kdbt_offset + i * 4) + "\t" + str(kdbtoffs))
            print("\tsb2 kdbtnrow\t\t\t\t@" + str(kdbt_offset + i * 4 + 2) + "\t" + str(kdbtnrow))

        print(" ")

    def p_kdbh(self):
        self._require_table_data_block()
        kdbh_offset = 20 + 24 + self.current_block_desc["ITLS"] * 24 + self.offset_mod + 8
        print("struct kdbh, 14 bytes \t\t\t\t@" + str(kdbh_offset))
        kdbh = self.struct_kdbh.unpack(self.block_data[kdbh_offset:kdbh_offset+14])
        print("\tub1 kdbhflag\t\t\t\t@" + str(kdbh_offset) + "\t" + str(hex(kdbh[0])))
        print("\tsb1 kdbhntab\t\t\t\t@" + str(kdbh_offset + 1) + "\t" + str(kdbh[1]))
        print("\tsb2 kdbhnrow\t\t\t\t@" + str(kdbh_offset + 2) + "\t" + str(kdbh[2]))
        print("\tsb2 kdbhfrre\t\t\t\t@" + str(kdbh_offset + 4) + "\t" + str(kdbh[3]))
        print("\tsb2 kdbhfsbo\t\t\t\t@" + str(kdbh_offset + 6) + "\t" + str(kdbh[4]))
        print("\tsb2 kdbhfseo\t\t\t\t@" + str(kdbh_offset + 8) + "\t" + str(kdbh[5]))
        print("\tsb2 kdbhavsp\t\t\t\t@" + str(kdbh_offset + 10) + "\t" + str(kdbh[6]))
        print("\tsb2 kdbhtosp\t\t\t\t@" + str(kdbh_offset + 12) + "\t" + str(kdbh[7]))
        print("\n")

    def p_kdbr(self, rowp=-1):
        self._require_table_data_block()
        if rowp == -1:
            rowp = 0
            self.current_offset = self.current_block_desc["FIRST_KDBR"]
            for i in self.kdbr:
                print("sb2 kdbr[" + str(rowp) + "]\t\t\t@" + str(i[0]) + "\t" + str(i[1]) + " => " + str(i[2]))
                rowp += 1
        else:
            i = self.kdbr[rowp]
            print("sb2 kdbr[" + str(rowp) + "]\t\t\t@" + str(i[0]) + "\t" + str(i[1]) + " => " + str(i[2]))

        print("\n")

    def p_kdbr_data(self, rowp, types=None):
        self._require_table_data_block()
        self.current_rowp = rowp
        row = self.kdbr_data[rowp]
        if row.get("UNUSED"):
            print("kdbr[{0}] is unused (-1)".format(rowp))
            return
        self.current_offset = row["OFFSET"]
        print("rowdata[" + str(row["OFFSET"] - self.min_rowdata) + "]\t\t\t\t@"
              + str(row["OFFSET"]) + "\t" + str(hex(row["FLAG"])))
        print("-------------")
        print("flag@" + str(row["OFFSET"]) + ":\t" + str(hex(row["FLAG"])))
        print("lock@" + str(row["OFFSET"] + 1) + ":\t" + str(hex(row["LOCK"])))
        if row.get("PARSE_ERROR"):
            print("parse error: " + row["PARSE_ERROR"])
            return
        print("cols@" + str(row["OFFSET"] + 2) + ":\t" + str(row["NCOLS"]))

        if row.get("CLUSTER_MEMBER"):
            print("cluster table@" + str(row["OFFSET"] + 3) + ":\t" + str(row["TABNO"]))
        elif row.get("CLUSTER_KEY"):
            print("cluster key slots:\t" + str(row["FIRST_SLOT"]) + " to " + str(row["LAST_SLOT"]))
            for label, rowid in (("first rowid", row["FIRST_ROWID"]), ("last rowid", row["LAST_ROWID"])):
                print("{0}:\t{1} [file: {2} block: {3} slot: {4}]".format(
                    label, rowid["RAW"], rowid["FILE_ID"], rowid["BLOCK_ID"], rowid["SLOT"]))

        if row.get("HRID") is not None:
            print("hrid@" + str(row["OFFSET"] + 3) + ":\t" + str(row["HRID"]))

        if row.get("NRID") is not None:
            print("nrid@" + str(row["OFFSET"] + 3) + ":\t" + str(row["NRID"]))

        print("\n")

        for i in range(row["NCOLS"]):
            if types is None:
                print("col{0:>5s}[{1:6s} {2}:  {3}".format(str(i), str(row["COL_DATA"][i][0]) + "]",
                                                           "@" + str(row["COL_DATA"][i][1]),
                                                           row["COL_DATA"][i][2]))
            else:
                if len(types) > i and row["COL_DATA"][i][2] != "*NULL*":
                    ot = OracleType(row["COL_DATA"][i][2], types[i])
                    value_string = ot.value_string
                else:
                    value_string = " "

                print("col{0:>5s}[{1:6s} {2}:  {3:40s} {4}".format(str(i),
                                                                   str(row["COL_DATA"][i][0]) + "]",
                                                                   "@" + str(row["COL_DATA"][i][1]),
                                                                   row["COL_DATA"][i][2],
                                                                   value_string))

        print("\n")

    def examine(self, pattern):
        if pattern[0:2] == "/r":
            self.p_kdbr_data(self.current_rowp, pattern[2:])
            return

        self.require_block()
        mode = pattern.lower()
        if mode not in ("/x", "/c", "/d", "/u", "/o"):
            raise ValueError("EXAMINE format must be /x, /c, /d, /u, /o or /r<types>")
        data = self.block_data[self.current_offset:min(self.current_offset + self.count, self.block_size)]
        print(" File: {0} ({1}) Block: {2} Offset: {3}".format(
            self.current_block_desc["FILE_NAME"], self.current_block_desc["FILE_ID"],
            self.current_block_desc["BLOCK_ID"], self.current_offset))
        if mode == "/c":
            print(data.decode("latin-1", errors="replace"))
        elif mode == "/x":
            print(hexlify(data))
        else:
            signed = mode == "/d"
            base = 8 if mode == "/o" else 10
            values = []
            for value in data:
                if signed and value >= 128:
                    value -= 256
                values.append(format(value, "o") if base == 8 else str(value))
            print(" ".join(values))

    def print_structure(self, name=None):
        self.require_block()
        if not name:
            self.map()
            return
        name = name.lower()
        handlers = {
            "kcbh": self.p_kcbh,
            "ktbbh": self.p_ktbbh,
            "kdbt": self.p_kdbt,
            "kdbh": self.p_kdbh,
            "kdbr": self.p_kdbr,
            "kdxle": self.p_kdx,
            "kdxbr": self.p_kdx,
            "kd_off": self.p_kd_off,
            "tailchk": self.p_tailchk,
        }
        if name.startswith("*kd_off["):
            self.p_index_entry(int(name.split("[")[1][:-1]))
        elif name.startswith("kd_off["):
            self.p_kd_off(int(name.split("[")[1][:-1]))
        elif name.startswith("*kdbr["):
            self.p_kdbr_data(int(name.split("[")[1][:-1]))
        elif name.startswith("kdbr["):
            self.p_kdbr(int(name.split("[")[1][:-1]))
        elif name in handlers:
            handlers[name]()
        else:
            raise ValueError("Unsupported PRINT structure: " + name)

    def select(self, col_desc, search_pattern):
        rowp = 0
        data_type = search_pattern[0]
        search_string = search_pattern[2:]
        col = int(col_desc.split()[1][3:])
        found_row = -1

        for r in self.kdbr_data:
            col_data = OracleType(r["COL_DATA"][col][2], data_type)
            if col_data.value_string == search_string:
                found_row = rowp
                print("Found at *kdbr[" + str(found_row) + "]")

            rowp += 1

        if found_row == -1:
            print("Nothing")


    def p_ktbbh(self):
        self.current_offset = 20
        ktbh_head = self.struct_ktbbh.unpack(self.block_data[20:44])
        ktbh_size = 24 + self.current_block_desc["ITLS"] * 24

        print("struct ktbbh, " + str(ktbh_size) + " bytes\t\t\t@20")
        print("  ub1 ktbbhtyp\t\t\t\t@20\t" + str(hex(ktbh_head[0])))
        print("  union ktbbhsid, 4 bytes\t\t@24")
        print("\tub4 ktbbhsg1\t\t\t@24\t" + str(hex(ktbh_head[1]))
              + "\t\t\t[raw hex: " + hexlify(self.uint.pack(ktbh_head[1]))
              + " OBJD: " + str(ktbh_head[1]) + "]")
        print("\tub4 ktbbhod1\t\t\t@24\t" + str(hex(ktbh_head[1])))
        print("  struct ktbbhcsc, 8 bytes\t\t@28")
        print("\tub4 kscnbas\t\t\t@28\t" + str(hex(ktbh_head[2]))
              + "\t\t[raw hex: " + hexlify(self.uint.pack(ktbh_head[2])) + "]")
        print("\tub2 kscnwrp\t\t\t@32\t" + str(hex(ktbh_head[3])))
        print("  sb2 ktbbhict\t\t\t\t@36\t" + str(hex(ktbh_head[4])))
        print("  ub1 ktbbhflg\t\t\t\t@38\t" + str(hex(ktbh_head[5])))
        print("  ub1 ktbbhfsl\t\t\t\t@39\t" + str(hex(ktbh_head[6])))
        print("  ub4 ktbbhfnx\t\t\t\t@40\t" + str(hex(ktbh_head[7])).ljust(8, ' ')
              + "\t\t[raw hex: " + hexlify(self.uint.pack(ktbh_head[7])) + "]")

        itl_pos = 44
        for i in range(self.current_block_desc["ITLS"]):
            itl_data = self.struct_ktbbhitl.unpack(self.block_data[itl_pos:itl_pos+24])
            print("  struct ktbbhitl[" + str(i) + "], 24 bytes\t\t@" + str(itl_pos))

            print("    struct ktbitxid, 8 bytes\t\t@" + str(itl_pos))
            print("\t  ub2 kxidusn\t\t\t@" + str(itl_pos) + "\t" + str(hex(itl_data[0]))
                  + "\t\t\t[raw hex: " + hexlify(self.ushort.pack(itl_data[0])) + "]")
            print("\t  ub2 kxidslt\t\t\t@" + str(itl_pos+2) + "\t" + str(hex(itl_data[1]))
                  + "\t\t\t[raw hex: " + hexlify(self.ushort.pack(itl_data[1])) + "]")
            print("\t  ub4 kxidsqn\t\t\t@" + str(itl_pos + 4) + "\t" + str(hex(itl_data[2]))
                  + "\t\t\t[raw hex: " + hexlify(self.uint.pack(itl_data[2])) + "]")

            print("    struct ktbituba, 8 bytes\t\t@" + str(itl_pos + 8))
            print("{0:>21s}{1:>19s}{2:8s}{3:24s}{4:s}".format("ub4 kubadba", " ", "@" + str(itl_pos + 8),
                                                              str(hex(itl_data[3])),
                                                              "[raw hex: "
                                                              + hexlify(self.uint.pack(itl_data[3])) + "]"))

            print("\t  ub2 kubaseq\t\t\t@" + str(itl_pos + 12) + "\t" + str(hex(itl_data[4]))
                  + "\t\t\t[raw hex: " + hexlify(self.ushort.pack(itl_data[4])) + "]")
            print("\t  ub1 kubarec\t\t\t@" + str(itl_pos + 14) + "\t" + str(hex(itl_data[5])))

            print("    ub2 ktbitflg\t\t\t@" + str(itl_pos + 16) + "\t" + str(hex(itl_data[6]))
                  + "\t\t\t[raw hex: " + hexlify(self.ushort.pack(itl_data[6])) + "]")

            print("    union _ktbitun, 2 bytes\t\t@" + str(itl_pos + 18))
            print("\t  sb2 _ktbitfsc\t\t\t@" + str(itl_pos + 18) + "\t" + str(hex(itl_data[7]))
                  + "\t\t\t[raw hex: " + hexlify(self.ushort.pack(itl_data[7])) + "]")
            print("\t  ub2 _ktbitwrp\t\t\t@" + str(itl_pos + 18) + "\t" + str(hex(itl_data[7]))
                  + "\t\t\t[raw hex: " + hexlify(self.ushort.pack(itl_data[7])) + "]")

            print("{0:>16s}{1:>24s}{2:8s}{3:24s}{4:s}".format("ub4 ktbitbas", " ", "@" + str(itl_pos + 20),
                                                              str(hex(itl_data[8])),
                                                              "[raw hex: "
                                                              + hexlify(self.uint.pack(itl_data[8])) + "]" ))

            itl_pos += 24

        print("\n")

    def checksum(self, apply_sum):
        self.require_block()
        block = self.block_data[0:16]
        block += b"\x00\x00"
        block += self.block_data[18:]
        checksum_value = 0

        for i in range(int(self.block_size / 8)):
            checksum_value = checksum_value ^ self.ulong.unpack(block[i * 8:i * 8 + 8])[0]

        tmp = checksum_value >> 32
        checksum_value = checksum_value ^ tmp
        tmp = checksum_value >> 16
        checksum_value = checksum_value ^ tmp

        final_checksum = self.ushort.unpack(self.ulong.pack(checksum_value)[0:2])[0]

        print("checksum int = " + str(final_checksum))
        print("checksum hex = " + str(hex(final_checksum)))

        if apply_sum:
            checksum_byte = self.ushort.pack(final_checksum)
            self.block_data = block[0:16]
            self.block_data += checksum_byte
            self.block_data += block[18:]
            self.dirty = True
            print("Block data changed. To save changes set edit mode and type: save")

        return self.ushort.unpack(self.block_data[16:18])[0], final_checksum

    def verify(self):
        self.require_block()
        problems = []
        if len(self.block_data) != self.block_size:
            problems.append("block length is {0}, expected {1}".format(len(self.block_data), self.block_size))
        header_dba = self.uint.unpack(self.block_data[4:8])[0]
        expected_dba = self.current_block_desc["DBA"]
        if header_dba not in (0, expected_dba):
            problems.append("header DBA is {0}, expected {1}".format(hex(header_dba), hex(expected_dba)))
        current_sum, required_sum = self.checksum(False)
        if current_sum != required_sum and self.block_data[15] & 4:
            problems.append("checksum is {0}, required {1}".format(hex(current_sum), hex(required_sum)))

        block_type = self.current_block_desc.get("BLOCK_TYPE")
        block_subtype = self.current_block_desc.get("BLOCK_SUBTYPE")
        if block_type == 6 and block_subtype == 2:
            if not self.index_header:
                problems.append("index header was not parsed")
            else:
                header = self.index_header
                expected_offsets_end = header["OFFSETS_START"] + header["NROWS"] * 2
                if header["FREESPACE_START"] != expected_offsets_end:
                    problems.append("kd_off table overlaps or does not reach freespace")
                if not (expected_offsets_end <= header["ROWDATA_START"] <= header["ROWDATA_END"] <= self.block_size - 4):
                    problems.append("invalid index freespace/rowdata boundaries")
                for entry in self.index_entries:
                    if entry.get("PARSE_ERROR"):
                        problems.append("index entry {0}: {1}".format(entry["SLOT"], entry["PARSE_ERROR"]))
                    elif header.get("ENTRY_FORMAT") == "BTREE_LEAF" and len(entry.get("COL_DATA", [])) != max(0, header["NCOLS"] - 1):
                        problems.append("index leaf entry {0} has an unexpected column count".format(entry["SLOT"]))
        elif block_type == 6 and block_subtype == 1:
            if len(self.kdbr) != self.current_block_desc.get("DECLARED_ROWS", -1):
                problems.append("kdbr count differs from kdbhnrow")
            for number, row in enumerate(self.kdbr_data):
                if row.get("PARSE_ERROR"):
                    problems.append("row {0}: {1}".format(number, row["PARSE_ERROR"]))
                if (row.get("CLUSTER_KEY") or row.get("CLUSTER_MEMBER")) and len(row.get("COL_DATA", [])) != row.get("NCOLS"):
                    problems.append("cluster row {0} has an unexpected column count".format(number))

        if problems:
            print("Verification failed:")
            for problem in problems:
                print("  - " + problem)
            return False
        print("Verification passed for File {0}, Block {1}".format(
            self.current_block_desc["FILE_ID"], self.current_block_desc["BLOCK_ID"]))
        return True


    def mask_printable(self, in_bytes):
        ret_str = ""
        for c in in_bytes:
            if isinstance(c, int):
                value = c
            else:
                value = ord(c)

            if value < 128 and value >= 32:
                ret_str += chr(value)
            else:
                ret_str += "."

        return ret_str

    def save(self):
        self.require_block()
        dbf = open(self.current_block_desc["FILE_NAME"], "r+b")
        block_id = self.current_block_desc["DBA"] & (self.max_block - 1)
        dbf.seek(block_id*self.block_size)
        self.block_data_backup = dbf.read(self.block_size)
        dbf.seek(block_id * self.block_size)
        dbf.write(self.block_data)
        dbf.close()
        self.dirty = False
        print("Current block data successfully saved to disk. To revert changes, type: dupa")

    def dupa(self):
        self.require_block()
        dbf = open(self.current_block_desc["FILE_NAME"], "r+b")
        block_id = self.current_block_desc["DBA"] & (self.max_block - 1)
        dbf.seek(block_id * self.block_size)
        dbf.write(self.block_data_backup)
        dbf.close()
        print("Backup of block data successfully saved to disk.")

    def undo(self):
        self.require_block()
        self.block_data = self.block_data_backup
        self.dirty = False
        self.parse_block()
        print("In-memory changes discarded.")

    def copy_to(self, file_id, block_id):
        self.require_block()
        if not self.edit_mode:
            raise RuntimeError("COPY requires edit mode. Type: set mode edit")
        if file_id not in self.file_names:
            raise ValueError("Unknown file number: " + str(file_id))
        if block_id < 0:
            raise ValueError("Block number cannot be negative")
        path = self.file_names[file_id]
        with open(path, "r+b") as dbf:
            dbf.seek(block_id * self.block_size)
            old_data = dbf.read(self.block_size)
            if len(old_data) != self.block_size:
                raise ValueError("Target block is outside the file or is truncated")
            dbf.seek(block_id * self.block_size)
            dbf.write(self.block_data)
        print("Copied File {0}, Block {1} to File {2}, Block {3}".format(
            self.current_block_desc["FILE_ID"], self.current_block_desc["BLOCK_ID"], file_id, block_id))

    def dump(self, count=None):
        self.require_block()
        count = self.count if count is None else count
        if count < 1:
            raise ValueError("COUNT must be greater than zero")
        end = min(self.current_offset + count, self.block_size)
        print(" File: " + self.current_block_desc["FILE_NAME"] + "(" + str(self.current_block_desc["FILE_ID"]) + ")")
        print(" Block: " + str(self.current_block_desc["DBA"] & (self.max_block - 1))
              + " Offsets: " + str(self.current_offset) + " to " + str(end - 1)
              + "\t\tDba: " + str(hex(self.current_block_desc["DBA"])))
        print("---------------------------------------------------------------")

        start = self.current_offset
        for _ in range(int(math.ceil((end - start) / 16.0))):
            line = self.block_data[start:min(start + 16, end)]
            groups = [hexlify(line[pos:pos + 4]).ljust(8) for pos in range(0, 16, 4)]
            print("{0} {1} {2} {3} | {4:16s}".format(
                groups[0], groups[1], groups[2], groups[3], self.mask_printable(line)))
            start += 16

        print("\n<16 bytes per line>\n")

    def modify(self, hex_string, byte_string):
        self.require_block()
        if hex_string != "42bee125":
            byte_string = unhexlify(hex_string)
        else:
            byte_string = text_to_bytes(byte_string)

        if self.current_offset + len(byte_string) > self.block_size:
            raise ValueError("Modification would extend beyond the selected block")

        block_id = self.current_block_desc["DBA"] & (self.max_block - 1)
        print("You want to modify block: " + str(block_id) + " at offset: " + str(self.current_offset))
        print("New value: " + hexlify(byte_string))

        yesno = input("Are you sure? (Y/N)  ").upper()

        if yesno == "Y":
            new_value_len = len(byte_string)
            block_swap = self.block_data[0:self.current_offset]
            block_swap += byte_string
            block_swap += self.block_data[self.current_offset + new_value_len:]
            self.block_data = block_swap
            self.dirty = True
            print("Block data changed. To save changes set edit mode and type: save")
        else:
            print("Nothing changed. You are annoying.")

    def find(self, file_id, block_id, data_object_id, search_string, search_hex, search_xo):
        search_in_one_dba = False
        search_only_blocks_for_objd = False

        if search_xo == "42bee125":
            if search_hex != "42bee125":
                search_string = unhexlify(search_hex)
            elif search_string == ".":
                search_only_blocks_for_objd = True
            else:
                search_string = text_to_bytes(search_string)

            if block_id == -1 and file_id == -1:
                file_id = self.current_block_desc["FILE_ID"]
                block_id = self.current_block_desc["DBA"] & (self.max_block - 1)
                search_in_one_dba = True
            elif block_id > -1 and file_id == -1:
                file_id = self.current_block_desc["FILE_ID"]
                search_in_one_dba = True
            elif block_id > -1 and file_id > -1:
                search_in_one_dba = True

            if search_in_one_dba:
                dbf = open(self.file_names[file_id], "rb")
                dbf.seek(block_id*self.block_size)
                block = dbf.read(self.block_size)
                dbf.close()
                pos = block.find(search_string)
                while pos != -1:
                    print("Found at offset: " + str(pos))
                    pos = block.find(search_string, pos + 1)

                print("\nSearch finished.\n")

            else:
                dbf = open(self.file_names[file_id], "rb")
                dbf.seek(0, os.SEEK_END)
                fsize = dbf.tell()
                blocks = fsize // self.block_size

                for i in range(1, blocks):
                    dbf.seek(i * self.block_size)
                    block = dbf.read(self.block_size)
                    block_type = self.ubyte.unpack(block[0:1])[0]
                    objd_offset = self.offset_objd.get(block_type, -1)
                    if objd_offset != -1:
                        objd = self.uint.unpack(block[objd_offset:objd_offset+4])[0]
                    else:
                        objd = 0

                    if not search_only_blocks_for_objd and (objd == data_object_id or data_object_id == -1):
                        pos = block.find(search_string)
                        while pos != -1:
                            print("Found in block: " + str(i) + " at offset: " + str(pos))
                            pos = block.find(search_string, pos + 1)
                    elif search_only_blocks_for_objd and objd == data_object_id:
                        print("Found in block: " + str(i) + " block type: " + self.block_type.get(block_type, "OTHER"))

                dbf.close()

        else:
            data_object_id = int(search_xo.split(":")[1])
            search_xid = search_xo.split(":")[0].lower()
            if file_id == -1:
                file_names = self.file_names
            else:
                file_names = {}
                file_names[file_id] = self.file_names[file_id]

            for file_id in file_names:
                dbf = open(file_names[file_id], "rb")
                dbf.seek(0, os.SEEK_END)
                fsize = dbf.tell()
                blocks = fsize // self.block_size
                # print("Searching in file: " + self.file_names[file_id] + " blocks: " + str(blocks))
                for i in range(1, blocks):
                    # print("\tsearching block: " + str(i))
                    dbf.seek(i * self.block_size)
                    block = dbf.read(self.block_size)
                    block_type = self.ubyte.unpack(block[0:1])[0]
                    objd_offset = self.offset_objd.get(block_type, -1)
                    if objd_offset != -1:
                        objd = self.uint.unpack(block[objd_offset:objd_offset+4])[0]
                    else:
                        objd = 0

                    if objd == data_object_id and block_type == 6:
                        itls = self.ubyte.unpack(block[self.ktbbhictOffset:self.ktbbhictOffset + 1])[0]
                        itl_pos = 44
                        for j in range(itls):
                            itl_data = self.struct_ktbbhitl.unpack(block[itl_pos:itl_pos + 24])
                            xid = hexlify(self.ushort.pack(itl_data[0])) + hexlify(self.ushort.pack(itl_data[1])) \
                                  + hexlify(self.uint.pack(itl_data[2]))
                            if xid == search_xid or search_xid == "all":
                                print("Found in block: " + str(file_id) + "," + str(i)
                                      + " block type: " + self.block_type.get(block_type, "OTHER"))
                            itl_pos += 24
                dbf.close()



            print("\nSearch finished.\n")


COMMAND_HELP = """BBED-compatible commands:
  SET DBA file,block | SET FILE file | SET BLOCK block | SET OFFSET offset
  SET COUNT count | SET BLOCKSIZE size | SET MODE BROWSE|EDIT
  SET IBASE DEC|HEX|OCT | SET OBASE DEC|HEX|OCT | SET WIDTH width
  SHOW [parameter] | INFO
  DUMP [/V] [DBA file,block] [OFFSET offset] [COUNT count]
  MAP
  PRINT [kcbh|ktbbh|kdbh|kdbt|kdbr|kdbr[n]|*kdbr[n]|kdxle|kdxbr]
  PRINT [kd_off|kd_off[n]|*kd_off[n]|tailchk]
  EXAMINE /X|/C|/D|/U|/O|/r<types> [OFFSET offset] [COUNT count]
  FIND /X hex | FIND /C string
  COPY TO DBA file,block | COPY FILE file BLOCK block
  MODIFY -H hex | MODIFY -S string
  SUM [APPLY] | CHECKSUM [APPLY]
  VERIFY
  UNDO | REVERT
  SAVE
  HELP
  EXIT | QUIT

RICO2 extensions retained: FIND -f/-b/-o/-s/-h/-xo, SELECT, SET MANUALOFFSET.
"""


def _parse_number(rico, value):
    aliases = {"dec": 10, "decimal": 10, "hex": 16, "hexadecimal": 16, "oct": 8, "octal": 8}
    lowered = value.lower()
    if lowered in aliases:
        return aliases[lowered]
    return int(value, rico.ibase)


def _location_from_tokens(rico, tokens):
    file_id = None
    block_id = None
    offset = None
    count = None
    i = 0
    while i < len(tokens):
        token = tokens[i].lower()
        if token == "to":
            i += 1
            continue
        if token == "dba":
            parts = tokens[i + 1].split(",")
            if len(parts) != 2:
                raise ValueError("DBA must be written as file,block")
            file_id, block_id = (_parse_number(rico, part.strip()) for part in parts)
            i += 2
        elif token in ("file", "file#"):
            file_id = _parse_number(rico, tokens[i + 1])
            i += 2
        elif token in ("block", "block#"):
            block_id = _parse_number(rico, tokens[i + 1])
            i += 2
        elif token == "offset":
            offset = _parse_number(rico, tokens[i + 1])
            i += 2
        elif token == "count":
            count = _parse_number(rico, tokens[i + 1])
            i += 2
        elif token == "/v":
            i += 1
        else:
            raise ValueError("Unexpected argument: " + tokens[i])
    return file_id, block_id, offset, count


def _select_location(rico, file_id, block_id):
    if file_id is None and block_id is None:
        return
    if file_id is None:
        rico.set_block(block_id)
    elif block_id is None:
        rico.set_file(file_id)
    else:
        rico.get_block(file_id, block_id)


def execute_command(rico, command):
    tokens = shlex.split(command)
    if not tokens:
        return True
    verb = tokens[0].lower()

    if verb in ("exit", "quit"):
        return False
    if verb in ("help", "?"):
        print(COMMAND_HELP)
    elif verb == "show":
        rico.show(tokens[1] if len(tokens) > 1 else None)
    elif verb == "info":
        rico.info()
    elif verb == "set":
        if len(tokens) < 3:
            raise ValueError("SET requires a parameter and value")
        parameter = tokens[1].lower().rstrip("#")
        value = tokens[2]
        if parameter == "dba":
            parts = value.split(",")
            rico.get_block(_parse_number(rico, parts[0]), _parse_number(rico, parts[1]))
        elif parameter == "file":
            rico.set_file(_parse_number(rico, value))
        elif parameter == "block":
            rico.set_block(_parse_number(rico, value))
        elif parameter == "offset":
            rico.set_offset(_parse_number(rico, value))
        elif parameter == "count":
            rico.count = _parse_number(rico, value)
        elif parameter == "width":
            rico.width = _parse_number(rico, value)
        elif parameter == "blocksize":
            rico.set_blocksize(_parse_number(rico, value))
        elif parameter == "manualoffset":
            rico.manual_offset = _parse_number(rico, value)
        elif parameter == "mode":
            if value.lower() not in ("browse", "edit"):
                raise ValueError("MODE must be BROWSE or EDIT")
            rico.edit_mode = value.lower() == "edit"
        elif parameter in ("ibase", "obase"):
            base = _parse_number(rico, value)
            if base not in (8, 10, 16):
                raise ValueError(parameter.upper() + " must be DEC, HEX or OCT")
            setattr(rico, parameter, base)
        else:
            raise ValueError("Unsupported SET parameter: " + parameter)
    elif verb in ("dump", "d"):
        file_id, block_id, offset, count = _location_from_tokens(rico, tokens[1:])
        _select_location(rico, file_id, block_id)
        if offset is not None:
            rico.set_offset(offset)
        rico.dump(count)
    elif verb == "map":
        rico.map()
    elif verb in ("print", "p"):
        rico.print_structure(tokens[1] if len(tokens) > 1 else None)
    elif verb in ("examine", "x"):
        if len(tokens) < 2:
            raise ValueError("EXAMINE requires a format")
        file_id, block_id, offset, count = _location_from_tokens(rico, tokens[2:])
        _select_location(rico, file_id, block_id)
        if offset is not None:
            rico.set_offset(offset)
        old_count = rico.count
        if count is not None:
            rico.count = count
        try:
            rico.examine(tokens[1])
        finally:
            rico.count = old_count
    elif verb in ("sum", "checksum"):
        apply_sum = len(tokens) > 1 and tokens[1].lower() == "apply"
        if apply_sum and not rico.edit_mode:
            raise RuntimeError("SUM APPLY requires edit mode. Type: set mode edit")
        rico.checksum(apply_sum)
    elif verb == "verify":
        file_id, block_id, _, _ = _location_from_tokens(rico, tokens[1:])
        _select_location(rico, file_id, block_id)
        rico.verify()
    elif verb == "copy":
        file_id, block_id, _, _ = _location_from_tokens(rico, tokens[1:])
        if file_id is None or block_id is None:
            raise ValueError("COPY target requires FILE and BLOCK or DBA")
        rico.copy_to(file_id, block_id)
    elif verb == "undo":
        rico.undo()
    elif verb == "revert":
        if not rico.edit_mode:
            raise RuntimeError("REVERT requires edit mode. Type: set mode edit")
        rico.dupa()
    elif verb == "save":
        if not rico.edit_mode:
            raise RuntimeError("SAVE requires edit mode. Type: set mode edit")
        rico.save()
    elif verb == "dupa":
        if not rico.edit_mode:
            raise RuntimeError("DUPA requires edit mode. Type: set mode edit")
        rico.dupa()
    elif verb in ("modify", "assign"):
        if len(tokens) != 3 or tokens[1].lower() not in ("-h", "-s", "/x", "/c"):
            raise ValueError("Usage: MODIFY -H hex | MODIFY -S string")
        if tokens[1].lower() in ("-h", "/x"):
            rico.modify(tokens[2], ".")
        else:
            rico.modify("42bee125", tokens[2])
    elif verb == "find":
        if len(tokens) == 1:
            print("Usage: FIND /X hex | FIND /C string, or RICO2 FIND -f/-b/-o/-s/-h/-xo")
        elif tokens[1].lower() in ("/x", "/c"):
            if tokens[1].lower() == "/x":
                rico.find(-1, -1, -1, ".", tokens[2], "42bee125")
            else:
                rico.find(-1, -1, -1, tokens[2], "42bee125", "42bee125")
        else:
            options = {tokens[i]: tokens[i + 1] for i in range(1, len(tokens), 2)}
            file_id = int(options.get("-f", -1))
            block_id = int(options.get("-b", -1))
            data_object_id = int(options.get("-o", -1))
            search_string = options.get("-s", ".")
            search_hex = options.get("-h", "42bee125")
            search_xo = options.get("-xo", "42bee125")
            if search_hex != "42bee125" and search_string != ".":
                raise ValueError("Choose either HEX or STRING search")
            rico.find(file_id, block_id, data_object_id, search_string, search_hex, search_xo)
    elif verb == "select":
        if len(tokens) == 1:
            print("Usage: SELECT col0=c:value")
        else:
            where, what = command[len(tokens[0]):].strip().split("=", 1)
            rico.select(where, what)
    else:
        raise ValueError("Unknown command: " + tokens[0] + ". Type HELP for a command list.")
    return True


if __name__ == '__main__':
    rico = Rico()
    rico.help()
    if len(sys.argv) != 2:
        sys.exit(1)
    rico.add_file(sys.argv[1])
    running = True
    while running:
        try:
            running = execute_command(rico, input("rico2 > ").strip())
        except (EOFError, KeyboardInterrupt):
            print("")
            running = False
        except BaseException as e:
            print("Command failed: " + str(e))
