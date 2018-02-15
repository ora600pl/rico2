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
import os
import sys
from binascii import hexlify
from binascii import unhexlify
from decimal import Decimal


class OracleType(object):
    def __init__(self, data_hex, type_name = None):
        self.value_string = None
        self.ubyte = Struct("B")

        if type_name == 't':
            self.value_string = self.decode_date(data_hex)
        elif type_name == 'n':
            self.value_string = self.decode_number(data_hex)
        elif type_name == 'c':
            self.value_string = self.decode_string(data_hex)

    def decode_date(self, data_hex):
        data_hex_b = unhexlify(data_hex)
        century = "{0:02d}".format(self.ubyte.unpack(data_hex_b[0])[0] - 100)
        year = "{0:02d}".format(self.ubyte.unpack(data_hex_b[1])[0] - 100)
        month = "{0:02d}".format(self.ubyte.unpack(data_hex_b[2])[0])
        day = "{0:02d}".format(self.ubyte.unpack(data_hex_b[3])[0])
        hour = "{0:02d}".format(self.ubyte.unpack(data_hex_b[4])[0] - 1)
        minute = "{0:02d}".format(self.ubyte.unpack(data_hex_b[5])[0] - 1)
        second = "{0:02d}".format(self.ubyte.unpack(data_hex_b[6])[0] - 1)

        date_string = century + year + "-" + month + "-" + day + ":" + hour + ":" + minute + ":" + second
        return date_string

    def decode_string(self, data_hex, characterset=None):
        if characterset is not None:
            str_data = unhexlify(data_hex).decode(characterset)
            return str_data
        else:
            return unhexlify(data_hex)

    def decode_number(self, data_hex):
        data_hex_b = unhexlify(data_hex)
        if data_hex == "80":
            return 0

        if Struct("B").unpack(data_hex_b[-1])[0] != 102:
            exPot = Struct("B").unpack(data_hex_b[0])[0] - 193
            numberValue = "0."
            exPot = exPot * 2 + 2

            for i in range(1, len(data_hex_b)):
                numberValue += "{0:02d}".format(Struct("B").unpack(data_hex_b[i])[0] - 1)
        else:
            exPot = 62 - Struct("B").unpack(data_hex_b[0])[0]
            numberValue = "-0."
            exPot = exPot * 2 + 2

            for i in range(1, len(data_hex_b) - 1):
                numberValue += "{0:02d}".format(101 - Struct("B").unpack(data_hex_b[i])[0])

        fVal = Decimal(numberValue)
        powVal = Decimal(10) ** Decimal(exPot)
        return str(fVal * powVal).rstrip("0").rstrip(".")


class Rico(object):
    def __init__(self):
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
        self.file_names = []

        # type_kcbh, frmt_kcbh, spare1_kcbh, spare2_kcbh, rdba_kcbh, bas_kcbh, wrp_kcbh, seq_kcbh, flg_kcbh,
        # chkval_kcbh, spare3_kcbh
        self.struct_kcbh = Struct("BBBBIIHBBHH")

        # First 44 bytes of ktbbh
        self.struct_ktbbh = Struct("BIIIHBBI")

        # 24 bytes for ITL slot
        self.struct_ktbbhitl = Struct("HHIIHBHHI")

        self.edit_mode = False
        self.current_offset = 0

        self.current_block_desc = {}
        self.kdbr = []
        self.kdbr_data = []

    @staticmethod
    def help():
        print("RICO v2 by Kamil Stawiarski (@ora600pl | www.ora-600.pl)")
        print("This is open source project to map BBED functionality.")
        print("If you know how to use BBED, you will know how to use this one.")
        print("Not everything is documented but in most cases the code is trivial to interpret it.")
        print("So if you don't know how to use this tool - then maybe you shouldn't ;)")
        print("\nUsage: python2.7 rico2.py listfile.txt")
        print("The listfile.txt should contain the list of the DBF files you want to read")

        print("\n !!! CAUTION !!!! \n")
        print("This tool should be used only to learn or in critical situations!")
        print("The usage is not supported!")
        print("If found on production system, this software should be considered as malware and deleted immediately!\n")

    def set_blocksize(self, bs):
        self.block_size = bs

    def set_offset(self,offset):
        self.current_offset = offset

    def add_file(self, dbf):
        dbfs = open(dbf, "r").readlines()
        file_id = 1
        for f in dbfs:
            self.file_names.append(f[:-1])
            print(str(file_id) + "\t" + f[:-1])
            file_id += 1

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
                    row_pointer = self.ushort.unpack(self.block_data[row_pointer_offset:row_pointer_offset + 2])[0]
                    row_pointer += 100 + 24 * (num_of_itls - 2) + self.offset_mod

                    row_header = self.ubyte2.unpack(self.block_data[row_pointer:row_pointer + 2])

                    self.kdbr_data[row]["OFFSET"] = row_pointer
                    self.kdbr_data[row]["FLAG"] = row_header[0]
                    self.kdbr_data[row]["LOCK"] = row_header[1]

                    if row_header[0] == 44 or row_header[0] == 108:
                        actual_rows += 1

                    row_pos = row_pointer + 2
                    if row_header[0] == 44 or row_header[0] == 60:
                        ncols = self.ubyte.unpack(self.block_data[row_pos:row_pos+1])[0]
                        row_pos += 1

                        if ncols == 254:
                            ncols = self.ushort.unpack(self.block_data[row_pos:row_pos + 2])[0]
                            row_pos += 2

                        self.kdbr_data[row]["NCOLS"] = ncols

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

                    row_pointer_offset += 2

                except:
                    row_pointer_offset += 2

        self.current_block_desc["DECLARED_ROWS"] = declared_rows
        self.current_block_desc["NTAB"] = num_of_tables
        self.current_block_desc["ACTUAL_ROWS"] = actual_rows

    def get_block(self, file_id, block_id):
        dbf = open(self.file_names[file_id-1], "rb")
        dbf.seek(block_id * self.block_size)
        self.block_data = dbf.read(self.block_size)
        self.block_data_backup = self.block_data
        dbf.close()
        dba = file_id * self.max_block + block_id

        self.current_block_desc = {"DBA": dba, "FILE_ID": file_id, "FILE_NAME": self.file_names[file_id-1]}
        block_type = self.ubyte.unpack(self.block_data[0:1])[0]
        block_subtype = self.ubyte.unpack(self.block_data[20:21])[0]


        self.kdbr = []
        self.kdbr_data = []
        self.min_rowdata = -1
        self.max_rowdata = -1
        self.current_rowp = 0
        self.current_offset = 0
        self.offset_mod = self.manual_offset

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
                row_pointer_real = row_pointer + 100 + 24 * (num_of_itls - 2) + self.offset_mod
                self.kdbr.append([row_pointer_offset, row_pointer, row_pointer_real])
                if row_pointer_real < self.min_rowdata or self.min_rowdata == -1:
                    self.min_rowdata = row_pointer_real

                if row_pointer_real > self.max_rowdata:
                    self.max_rowdata = row_pointer_real

                row_pointer_offset += 2

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
        print(" " + self.block_type[block_type] + " " + self.block_subtype[block_subtype] + "\n")
        print(" struct kcbh, 20 bytes\t\t\t\t@0\n")
        print(" struct ktbbh, {0:>3s} bytes \t\t\t@20\n".format(str(24 + self.current_block_desc["ITLS"]*24)))

        if block_type == 6 and block_subtype == 1:
            print(" sb2 kdbr[" + str(self.current_block_desc["DECLARED_ROWS"]) + "]\t\t\t\t\t@"
                  + str(self.current_block_desc["FIRST_KDBR"]))
            rowdata_size = self.max_rowdata - self.min_rowdata
            print("\n ub1 rowdata[" + str(rowdata_size) + "]\t\t\t\t@" + str(self.min_rowdata))

        print("\n\n")

    def p_kdbr(self, rowp=-1):
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
        self.current_rowp = rowp
        self.current_offset = self.kdbr_data[rowp]["OFFSET"]
        print("rowdata[" + str(self.kdbr_data[rowp]["OFFSET"] - self.min_rowdata) + "]\t\t\t\t@"
              + str(self.kdbr_data[rowp]["OFFSET"]) +  "\t" + str(hex(self.kdbr_data[rowp]["FLAG"])))
        print("-------------")
        print("flag@" + str(self.kdbr_data[rowp]["OFFSET"]) + ":\t" + str(hex(self.kdbr_data[rowp]["FLAG"])))
        print("lock@" + str(self.kdbr_data[rowp]["OFFSET"] + 1) + ":\t" + str(hex(self.kdbr_data[rowp]["LOCK"])))
        print("cols@" + str(self.kdbr_data[rowp]["OFFSET"] + 2) + ":\t" + str(self.kdbr_data[rowp]["NCOLS"]))
        print("\n")

        for i in range(self.kdbr_data[rowp]["NCOLS"]):
            if types is None:
                print("col{0:>5s}[{1:6s} {2}:  {3}".format(str(i), str(self.kdbr_data[rowp]["COL_DATA"][i][0]) + "]",
                                                           "@" + str(self.kdbr_data[rowp]["COL_DATA"][i][1]),
                                                           self.kdbr_data[rowp]["COL_DATA"][i][2]))
            else:
                if len(types) > i and self.kdbr_data[rowp]["COL_DATA"][i][2] != "*NULL*":
                    ot = OracleType(self.kdbr_data[rowp]["COL_DATA"][i][2], types[i])
                    value_string = ot.value_string
                else:
                    value_string = " "

                print("col{0:>5s}[{1:6s} {2}:  {3:40s} {4}".format(str(i),
                                                                   str(self.kdbr_data[rowp]["COL_DATA"][i][0]) + "]",
                                                                   "@" + str(self.kdbr_data[rowp]["COL_DATA"][i][1]),
                                                                   self.kdbr_data[rowp]["COL_DATA"][i][2],
                                                                   value_string))

        print("\n")


    def examine(self, pattern):
        if pattern[0:2] == "/r":
            self.p_kdbr_data(self.current_rowp, pattern[2:])

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
            print("Block data changed. To save changes set edit mode and type: save")


    def mask_printable(self, in_bytes):
        ret_str = ""
        for c in in_bytes:
            if ord(c) < 128 and ord(c) >= 32:
                ret_str += c
            else:
                ret_str += "."

        return ret_str

    def save(self):
        dbf = open(self.current_block_desc["FILE_NAME"], "r+b")
        block_id = self.current_block_desc["DBA"] & (self.max_block - 1)
        dbf.seek(block_id*self.block_size)
        self.block_data_backup = dbf.read(self.block_size)
        dbf.seek(block_id * self.block_size)
        dbf.write(self.block_data)
        dbf.close()
        print("Current block data successfully saved to disk. To revert changes, type: dupa")

    def dupa(self):
        dbf = open(self.file_names[file_id - 1], "r+b")
        block_id = self.current_block_desc["DBA"] & (self.max_block - 1)
        dbf.seek(block_id * self.block_size)
        dbf.write(self.block_data_backup)
        dbf.close()
        print("Backup of block data successfully saved to disk.")

    def dump(self):
        print(" File: " + self.current_block_desc["FILE_NAME"] + "(" + str(self.current_block_desc["FILE_ID"]) + ")")
        print(" Block: " + str(self.current_block_desc["DBA"] & (self.max_block - 1))
              + " Offsets: " + str(self.current_offset) + " to " + str(self.current_offset + 512)
              + "\t\tDba: " + str(hex(self.current_block_desc["DBA"])))
        print("---------------------------------------------------------------")

        rows = 32
        start = self.current_offset
        for i in range(1, rows):
            if i < len(self.block_data[start:]):
                print("{0:4s} {1:4s} {2:4s} {3:4s} | {4:16s}".format(hexlify(self.block_data[start:start+4]),
                                                                     hexlify(self.block_data[start+4:start+8]),
                                                                     hexlify(self.block_data[start+8:start+12]),
                                                                     hexlify(self.block_data[start+12:start+16]),
                                                                     self.mask_printable(self.block_data[start:start+16])))
            start += 16

        print("\n<16 bytes per line>\n")

    def modify(self, hex_string, byte_string):
        if hex_string != "42bee125":
            byte_string = unhexlify(hex_string)

        block_id = self.current_block_desc["DBA"] & (self.max_block - 1)
        print("You want to modify block: " + str(block_id) + " at offset: " + str(self.current_offset))
        print("New value: " + hexlify(byte_string))

        yesno = raw_input("Are you sure? (Y/N)  ").upper()

        if yesno == "Y":
            new_value_len = len(byte_string)
            block_swap = self.block_data[0:self.current_offset]
            block_swap += byte_string
            block_swap += self.block_data[self.current_offset + new_value_len:]
            self.block_data = block_swap
            print("Block data changed. To save changes set edit mode and type: save")
        else:
            print("Nothing changed. You are annoying.")

    def find(self, file_id, block_id, data_object_id, search_string, search_hex):
        search_in_one_dba = False
        search_only_blocks_for_objd = False

        if search_hex != "42bee125":
            search_string = unhexlify(search_hex)
        elif search_string == ".":
            search_only_blocks_for_objd = True

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
            dbf = open(self.file_names[file_id - 1], "rb")
            dbf.seek(block_id*self.block_size)
            block = dbf.read(self.block_size)
            dbf.close()
            pos = block.find(search_string)
            while pos != -1:
                print("Found at offset: " + str(pos))
                pos = block.find(search_string, pos + 1)

            print("\nSearch finished.\n")

        else:
            dbf = open(self.file_names[file_id - 1], "rb")
            dbf.seek(0, os.SEEK_END)
            fsize = dbf.tell()
            blocks = fsize / self.block_size

            for i in range(1, blocks):
                dbf.seek(i * self.block_size)
                block = dbf.read(self.block_size)
                block_type = self.ubyte.unpack(block[0])[0]
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
            print("\nSearch finished.\n")



if __name__ == '__main__':

    rico = Rico()
    rico.help()
    if len(sys.argv) == 2:
        cnt = True
        rico.add_file(sys.argv[1])
    else:
        cnt = False

    while cnt:
        try:
            command = raw_input("rico2 > ").strip()
            if command == "exit":
                cnt = False
            elif command.startswith("set blocksize"):
                rico.set_blocksize(int(command.split()[2]))
            elif command.startswith("set dba"):
                dba = command.split()[2].split(",")
                rico.get_block(int(dba[0].strip()), int(dba[1].strip()))
            elif command.startswith("p kcbh"):
                rico.p_kcbh()
            elif command.startswith("map"):
                try:
                    rico.map()
                except BaseException as e:
                    print("Wrong kind of block. Currently supported: 6.1")
                    print(str(e))
            elif command.startswith("p kdbr"):
                if len(command.split("[")) == 1:
                    rico.p_kdbr()
                else:
                    rico.p_kdbr(int(command.split("[")[1][:-1]))
            elif command.startswith("p *kdbr"):
                if len(command.split("[")) == 2:
                    rico.p_kdbr_data(int(command.split("[")[1][:-1]))
            elif command.startswith("sum"):
                if len(command.split()) > 1 and command.split()[1] == "apply":
                    rico.checksum(True)
                else:
                    rico.checksum(False)
            elif command.startswith("p ktbbh"):
                rico.p_ktbbh()
            elif command.startswith("x") and command.split() >= 2:
                rico.examine(command.split()[1])
            elif command.startswith("d"):
                rico.dump()
            elif command.startswith("set offset"):
                rico.set_offset(int(command.split()[2]))
            elif command.startswith("set manualoffset"):
                rico.manual_offset = int(command.split()[2])
            elif command.startswith("find"):
                if command == "find":
                    print("Usage: find [-f file_id] [-o data_object_id] [-b block_no] [-s search_string | "
                          "-h search hex]")
                else:
                    file_id = -1
                    block_id = -1
                    data_object_id = -1
                    search_string = "."
                    search_hex = "42bee125"
                    i = 0
                    for w in command.split():
                        if w == "-f":
                            file_id = int(command.split()[i + 1])
                        elif w == "-o":
                            data_object_id = int(command.split()[i + 1])
                        elif w == "-b":
                            block_id = int(command.split()[i + 1])
                        elif w == "-s":
                            search_string = command.split()[i + 1]
                        elif w == "-h":
                            search_hex = command.split()[i + 1]
                        i += 1

                    if search_hex != "42bee125" and search_string != ".":
                        raise Exception("You have to decide - you are looking for HEX or STRING value?")

                    rico.find(file_id, block_id, data_object_id, search_string, search_hex)
            elif command.startswith("modify"):
                if command == "modify":
                    print("Usage: First - set offset to a place that you want to modify. \n"
                          "Then: modify [-s bytestring | -h hex]")
                else:
                    byte_string = "."
                    hex_string = "42bee125"
                    i = 0
                    for w in command.split():
                        if w == "-s":
                            byte_string = command.split()[i + 1]
                        elif w == "-h":
                            hex_string = command.split()[i + 1]

                        i += 1

                    if byte_string != "." and hex_string != "42bee125":
                        raise Exception("You have to decide - you are modifying as HEX or STRING value?")

                    rico.modify(hex_string, byte_string)

            elif command == "save":
                if rico.edit_mode:
                    rico.save()
                else:
                    print("You have to be in edit mode to save block to disk. Type: set mode edit")
            elif command == "set mode edit":
                rico.edit_mode = True
            elif command == "dupa":
                rico.dupa()
            elif command.startswith("select"):
                if command == "select":
                    print("Usage example: select col0=c:dupa blada\n"
                          "\t select col1=n:10\n")
                else:
                    where = command.split("=")[0]
                    what = command.split("=")[1]
                    rico.select(where, what)


        except BaseException as e:
            print("You messed up... Or I messed up. Something is messed up")
            print(str(e))
