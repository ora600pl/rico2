import contextlib
import io
import os
import struct
import tempfile
import unittest
from unittest import mock

from rico2 import Rico, execute_command


class RicoCommandTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.block_size = 8192
        self.files = {}
        for file_id in (1, 2):
            path = os.path.join(self.tempdir.name, "file{0}.dbf".format(file_id))
            with open(path, "wb") as dbf:
                dbf.write(bytes(self.block_size))
                block = bytearray(self.block_size)
                block[0] = 0
                block[4:8] = struct.pack("I", file_id * 4194304 + 1)
                block[15] = 4
                block[32:48] = b"RICO2-TEST-BLOCK"
                dbf.write(block)
            self.files[file_id] = path

        self.listfile = os.path.join(self.tempdir.name, "listfile.txt")
        with open(self.listfile, "w") as output:
            output.write("# test files\n")
            for file_id, path in self.files.items():
                output.write("{0} {1}\n".format(file_id, path))

        self.rico = Rico()
        with contextlib.redirect_stdout(io.StringIO()):
            self.rico.add_file(self.listfile)
            execute_command(self.rico, "set dba 1,1")

    def run_command(self, command):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = execute_command(self.rico, command)
        return result, output.getvalue()

    def load_synthetic_block(self, block):
        with open(self.files[1], "r+b") as dbf:
            dbf.seek(self.block_size)
            dbf.write(block)
        with contextlib.redirect_stdout(io.StringIO()):
            self.rico.get_block(1, 1)

    def test_show_and_location_setters(self):
        _, output = self.run_command("show")
        self.assertIn("FILE", output)
        self.assertIn("0x400001", output)
        _, output = self.run_command("info")
        self.assertIn("Size(blks)", output)
        self.assertIn("file1.dbf", output)
        self.run_command("set file 2")
        self.assertEqual(2, self.rico.current_block_desc["FILE_ID"])
        self.run_command("set block 1")
        self.assertEqual(1, self.rico.current_block_desc["BLOCK_ID"])

    def test_dump_and_examine_honor_count_and_offset(self):
        _, dump_output = self.run_command("dump /v offset 32 count 16")
        self.assertIn("Offsets: 32 to 47", dump_output)
        self.assertIn("5249434f", dump_output)
        _, examine_output = self.run_command("examine /c offset 32 count 16")
        self.assertIn("RICO2-TEST-BLOCK", examine_output)

    def test_modify_and_undo_are_in_memory(self):
        self.run_command("set offset 32")
        with mock.patch("builtins.input", return_value="Y"):
            self.run_command("modify -h deadbeef")
        self.assertEqual(bytes.fromhex("deadbeef"), self.rico.block_data[32:36])
        self.assertTrue(self.rico.dirty)
        self.run_command("undo")
        self.assertEqual(b"RICO", self.rico.block_data[32:36])
        self.assertFalse(self.rico.dirty)
        self.rico.set_offset(self.block_size - 1)
        with self.assertRaisesRegex(ValueError, "beyond"):
            self.rico.modify("deadbeef", ".")

    def test_sum_apply_and_verify(self):
        self.run_command("set mode edit")
        self.run_command("checksum apply")
        self.assertTrue(self.rico.dirty)
        valid, output = self.run_command("verify")
        self.assertTrue(valid)
        self.assertIn("Verification passed", output)

    def test_copy_requires_edit_mode_and_copies_full_block(self):
        with self.assertRaises(RuntimeError):
            self.run_command("copy to dba 2,1")
        self.run_command("set mode edit")
        self.run_command("copy to dba 2,1")
        with open(self.files[2], "rb") as dbf:
            dbf.seek(self.block_size)
            copied = dbf.read(self.block_size)
        self.assertEqual(self.rico.block_data, copied)

    def test_help_quit_and_unknown_command(self):
        _, output = self.run_command("help")
        self.assertIn("BBED-compatible commands", output)
        self.assertFalse(execute_command(self.rico, "quit"))
        with self.assertRaisesRegex(ValueError, "Unknown command"):
            execute_command(self.rico, "frobnicate")

    def test_index_leaf_header_offsets_and_entries(self):
        block = bytearray(self.block_size)
        block[0] = 6
        block[4:8] = struct.pack("I", 4194305)
        block[20] = 2
        block[36] = 2
        base = 92
        block[base:base + 32] = struct.pack(
            "<BBBBIhhhhhhIIBB2x", 0, 0, 0x80, 2, 2, 3, 42, 100, 58,
            0, 0, 0, 0, 6, 0)
        block[124:130] = struct.pack("HHH", 120, 0, 96)
        rowid = (4194314).to_bytes(4, "big") + (2).to_bytes(2, "big")
        block[188:202] = b"\x00\x00" + rowid + b"\x01\x80\x03ABC"
        self.load_synthetic_block(block)

        self.assertEqual("LEAF", self.rico.index_header["KIND"])
        self.assertEqual(188, self.rico.index_header["ROWDATA_START"])
        self.assertEqual(1, len(self.rico.index_entries))
        entry = self.rico.index_entries[0]
        self.assertEqual(10, entry["ROWID"]["BLOCK_ID"])
        self.assertEqual(["80", "414243"], [column[2] for column in entry["COL_DATA"]])
        _, output = self.run_command("print *kd_off[2]")
        self.assertIn("rowid=", output)
        _, output = self.run_command("verify")
        self.assertIn("Verification passed", output)

    def test_index_leaf_with_ktb_extension_and_signed_sentinels(self):
        block = bytearray(self.block_size)
        block[0] = 6
        block[4:8] = struct.pack("I", 4194305)
        block[20] = 2
        block[36] = 2
        block[38] = 0x32
        base = 100
        block[base:base + 32] = struct.pack(
            "<BBBBIhhhhhhIIBB2x", 0, 0, 0x80, 1, 0, 3, 42, 100, 58,
            0, 0, 0, 0, 6, 0)
        block[132:138] = struct.pack("<hhh", 120, -16205, 96)
        rowid = (4194314).to_bytes(4, "big") + (2).to_bytes(2, "big")
        block[196:207] = b"\x00\x00" + rowid + b"\x02\xc2\x02"
        self.load_synthetic_block(block)

        self.assertEqual(100, self.rico.index_header["OFFSET"])
        self.assertEqual(132, self.rico.index_header["OFFSETS_START"])
        self.assertEqual(196, self.rico.index_header["ROWDATA_START"])
        self.assertEqual(216, self.rico.index_header["ROWDATA_END"])
        self.assertEqual(-16205, self.rico.index_offsets[1][1])
        self.assertIsNone(self.rico.index_offsets[1][2])
        self.assertEqual([2], [entry["SLOT"] for entry in self.rico.index_entries])
        self.assertEqual(["c202"], [column[2] for column in self.rico.index_entries[0]["COL_DATA"]])
        _, output = self.run_command("map")
        self.assertIn("kdxle, 32 bytes", output)
        self.assertIn("@100", output)
        _, output = self.run_command("print *kd_off[2]")
        self.assertIn("rowid=", output)
        self.assertIn("c202", output)
        with self.assertRaisesRegex(ValueError, "points to pad"):
            self.run_command("print *kd_off[0]")
        with self.assertRaisesRegex(ValueError, "negative sentinel"):
            self.run_command("print *kd_off[1]")
        _, output = self.run_command("verify")
        self.assertIn("Verification passed", output)

    def test_index_leaf_with_rowid_encoded_as_last_column(self):
        block = bytearray(self.block_size)
        block[0] = 6
        block[4:8] = struct.pack("I", 4194305)
        block[20] = 2
        block[36] = 2
        base = 92
        block[base:base + 32] = struct.pack(
            "<BBBBIhhhhhhIIBB2x", 0, 0, 0x80, 2, 0, 3, 42, 100, 58,
            0, 0, 0, 0, 0, 0)
        block[124:130] = struct.pack("<hhh", 120, 0, 96)
        rowid = (4194314).to_bytes(4, "big") + (2).to_bytes(2, "big")
        block[188:200] = b"\x00\x00\x02\xc1\x02\x06" + rowid
        self.load_synthetic_block(block)

        self.assertEqual(1, len(self.rico.index_entries))
        entry = self.rico.index_entries[0]
        self.assertEqual(2, entry["PARSED_NCOLS"])
        self.assertEqual(["c102"], [column[2] for column in entry["COL_DATA"]])
        self.assertEqual(10, entry["ROWID"]["BLOCK_ID"])
        _, output = self.run_command("print *kd_off[2]")
        self.assertIn("rowid=", output)
        self.assertIn("c102", output)
        _, output = self.run_command("verify")
        self.assertIn("Verification passed", output)

    def test_index_branch_child_and_separator_key(self):
        block = bytearray(self.block_size)
        block[0] = 6
        block[4:8] = struct.pack("I", 4194305)
        block[20] = 2
        block[36] = 1
        base = 68
        block[base:base + 24] = struct.pack(
            "<BBBBIhhhhIh2x", 1, 0, 0x80, 2, 1, 3, 34, 100, 66,
            4194404, 2)
        block[92:98] = struct.pack("HHH", 120, 0, 96)
        block[164:176] = struct.pack("I", 4194504) + b"\x01\x80\x03KEY\xfe"
        self.load_synthetic_block(block)

        self.assertEqual("BRANCH", self.rico.index_header["KIND"])
        self.assertEqual(1, len(self.rico.index_entries))
        entry = self.rico.index_entries[0]
        self.assertEqual(200, entry["CHILD_BLOCK"])
        self.assertEqual(["80", "4b4559"], [column[2] for column in entry["COL_DATA"]])
        _, output = self.run_command("map")
        self.assertIn("Index Branch", output)
        self.assertIn("kdxbr", output)

    def test_special_index_leaf_entries_are_exposed_as_raw(self):
        block = bytearray(self.block_size)
        block[0] = 6
        block[4:8] = struct.pack("I", 4194305)
        block[20] = 2
        block[36] = 2
        base = 92
        block[base:base + 32] = struct.pack(
            "<BBBBIhhhhhhIIBB2x", 0, 0, 0x80, 1, 2, 4, 44, 100, 56,
            0, 0, 0, 0, 8, 0x10)
        block[124:132] = struct.pack("HHHH", 120, 0, 96, 100)
        block[188:192] = b"\x00\x00\x00\x40"
        self.load_synthetic_block(block)

        self.assertEqual("RAW", self.rico.index_header["ENTRY_FORMAT"])
        self.assertEqual("00000040", self.rico.index_entries[0]["RAW"])
        _, output = self.run_command("print *kd_off[2]")
        self.assertIn("special index entry format", output)
        _, output = self.run_command("verify")
        self.assertIn("Verification passed", output)

    def test_cluster_key_and_member_rows(self):
        block = bytearray(self.block_size)
        block[0] = 6
        block[4:8] = struct.pack("I", 4194305)
        block[20] = 1
        block[36] = 2
        block[92:106] = struct.pack("Bbhhhhhh", 0, 2, 3, -1, 20, 80, 80, 80)
        block[114:120] = struct.pack("HHH", 108, 138, 0xffff)
        block[200:209] = b"\x6c\x00\x02\x01\x01A\x02BC"
        first_rowid = (4194305).to_bytes(4, "big") + (7).to_bytes(2, "big")
        last_rowid = (4194305).to_bytes(4, "big") + (8).to_bytes(2, "big")
        block[230:251] = (b"\xac\x00\x01" + struct.pack("HH", 5, 6) +
                          first_rowid + last_rowid + b"\x01K")
        self.load_synthetic_block(block)

        self.assertTrue(self.rico.current_block_desc["IS_CLUSTER"])
        member = self.rico.kdbr_data[0]
        key = self.rico.kdbr_data[1]
        self.assertEqual(1, member["TABNO"])
        self.assertEqual(["41", "4243"], [column[2] for column in member["COL_DATA"]])
        self.assertEqual(5, key["FIRST_SLOT"])
        self.assertEqual(8, key["LAST_ROWID"]["SLOT"])
        self.assertEqual("4b", key["COL_DATA"][0][2])
        self.assertTrue(self.rico.kdbr_data[2]["UNUSED"])
        _, output = self.run_command("print *kdbr[1]")
        self.assertIn("cluster key slots", output)
        _, output = self.run_command("verify")
        self.assertIn("Verification passed", output)


if __name__ == "__main__":
    unittest.main()
