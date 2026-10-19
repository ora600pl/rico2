"""Heap resize contract tests, including Oracle-produced EMPLOYEES2 blocks."""
import contextlib
import copy
import io
from pathlib import Path
import random
import struct
import tempfile
import unittest
from unittest import mock

from rico2 import Rico, execute_command


FIXTURES = Path(__file__).parent / 'research' / 'heap-resize'


def load_image(data):
    rico = Rico()
    rico.block_size = len(data)
    rico.block_data = bytes(data)
    rico.block_data_backup = bytes(data)
    rico.current_block_desc = {'DBA': struct.unpack_from('<I', data, 4)[0],
                               'FILE_ID': 12, 'BLOCK_ID': 45699, 'FILE_NAME': 'fixture'}
    with contextlib.redirect_stdout(io.StringIO()):
        rico.parse_block()
    rico.edit_mode = True
    return rico


def checksum(data):
    struct.pack_into('<H', data, 16, Rico._block_checksum(data))
    return data


def make_heap(rows, gaps=0, free_slots=(), itls=2, extension=0, block_size=8192):
    """Independent little-endian fixture builder; row directory may be shuffled."""
    data = bytearray(block_size)
    data[0:2] = b'\x06\xa2'
    data[4:8] = struct.pack('<I', 12 * 4194304 + 45699)
    data[14:16] = b'\x01\x04'
    data[20] = 1
    data[36] = itls
    base = 44 + 24 * itls
    if extension:
        data[38] = 0x30
        struct.pack_into('<H', data, base + 4, extension - 8)
        base += extension
    nrows = len(rows)
    cursor = block_size - 4
    used = 0
    for slot, columns in enumerate(rows):
        if slot in free_slots:
            continue
        payload = b'\x2c\x00' + bytes([len(columns)])
        for value in columns:
            if value is None:
                payload += b'\xff'
            elif len(value) <= 250:
                payload += bytes([len(value)]) + value
            else:
                payload += b'\xfe' + struct.pack('<H', len(value)) + value
        cursor -= gaps + len(payload)
        data[cursor:cursor + len(payload)] = payload
        struct.pack_into('<H', data, base + 18 + 2 * slot, cursor - base)
        used += len(payload)
    for i, slot in enumerate(free_slots):
        struct.pack_into('<h', data, base + 18 + 2 * slot,
                         free_slots[i + 1] if i + 1 < len(free_slots) else -1)
    fsbo = 18 + 2 * nrows
    free = block_size - 4 - base - fsbo - used
    struct.pack_into('<BBHhHHHHHH', data, base, 0, 1, nrows,
                     free_slots[0] if free_slots else -1, fsbo, cursor - base,
                     free, free, 0, nrows)
    data[-4:] = b'\x01\x06\x00\x00'
    return checksum(data)


def make_cluster(gaps=17, free_slots=(16,), reserved=0, flag=1, mixed=False):
    """Three kdbt ranges with synthetic application data."""
    data = bytearray(8192)
    data[:2] = b'\x06\xa2'
    dba = 4194304 + 215
    struct.pack_into('<I', data, 4, dba)
    data[14:16] = b'\x01\x04'
    data[20] = 1
    data[36:38] = b'\x02\x1f'
    base = 92
    ranges = ((0, 13), (13, 14), (27, 4))
    fsbo = 14 + 4 * len(ranges) + 2 * 31
    cursor = 8188
    used = 0
    rowid = dba.to_bytes(4, 'big') + (6).to_bytes(2, 'big')
    for slot in range(31):
        if slot in free_slots:
            continue
        if slot < 13:
            columns = [b'KEY' + bytes([slot])]
            payload = b'\xac\x00\x01' + struct.pack('<HH', 1, 1) + rowid * 2
        elif mixed and slot in (26, 30):
            columns = []
            payload = b'\x7c\x00\x01\x0c'
        elif mixed and slot == 15:
            columns = [b'_NEXT_USER']
            payload = b'\x4c\x00\x01\x0b' + rowid
        else:
            columns = ([b'HR'] + [None] * 20 + [b'old']
                       if slot == 19 else [b'MEMBER' + bytes([slot])])
            payload = b'\x6c\x00' + bytes([len(columns), 4])
        for value in columns:
            payload += (b'\xff' if value is None else
                        (bytes([len(value)]) + value if len(value) <= 250 else
                         b'\xfe' + struct.pack('<H', len(value)) + value))
        cursor -= gaps + len(payload)
        data[cursor:cursor + len(payload)] = payload
        struct.pack_into('<H', data, base + 26 + 2 * slot, cursor - base)
        used += len(payload)
    for index, slot in enumerate(free_slots):
        struct.pack_into('<h', data, base + 26 + 2 * slot,
                         free_slots[index + 1] if index + 1 < len(free_slots) else -1)
    total_free = 8188 - base - fsbo - used + (8 if mixed else 0)
    struct.pack_into('<BBHhHHHH', data, base, flag, 3, 31,
                     free_slots[0] if free_slots else -1, fsbo, cursor - base,
                     total_free - reserved, total_free)
    for table, pair in enumerate(ranges):
        struct.pack_into('<HH', data, base + 14 + 4 * table, *pair)
    data[-4:] = b'\x01\x06\x00\x00'
    return checksum(data)


def column_values(rico):
    return [[column[2] for column in row.get('COL_DATA', [])] for row in rico.kdbr_data]


class HeapResizeTests(unittest.TestCase):
    def command(self, rico, command):
        with contextlib.redirect_stdout(io.StringIO()):
            execute_command(rico, command)

    def edit(self, rico, slot, column, value):
        with contextlib.redirect_stdout(io.StringIO()):
            rico.set_heap_column(slot, column, value)

    def assert_atomic_rejection(self, rico, operation, exception=ValueError):
        before = copy.deepcopy(rico.__dict__, {
            id(value): value for value in rico.__dict__.values() if isinstance(value, struct.Struct)})
        with self.assertRaises(exception):
            operation()
        self.assertEqual(before, rico.__dict__)

    def test_real_employees2_shrink_grow_null_and_neighbours(self):
        for block in (45699, 45700):
            source = (FIXTURES / ('original-%d.bin' % block)).read_bytes()
            for slot in (0, 4, 9):
                for value in (b'X', b'XXXX', b'TwentyCharactersHere', None, b''):
                    with self.subTest(block=block, slot=slot, value=value):
                        rico = load_image(source)
                        before = column_values(rico)
                        self.edit(rico, slot, 1, value)
                        before[slot][1] = value.hex() if value else '*NULL*'
                        self.assertEqual(before, column_values(rico))
                        self.assertEqual(source[:16], rico.block_data[:16])
                        self.assertEqual(source[18:124], rico.block_data[18:124])
                        self.assertEqual(source[-4:], rico.block_data[-4:])
                        self.assertEqual(8192, len(rico.block_data))
                        self.assertEqual(Rico._block_checksum(rico.block_data),
                                         struct.unpack_from('<H', rico.block_data, 16)[0])
                        self.assertTrue(rico.dirty)

    def test_command_spacing_quotes_unicode_hex_null_and_single_column_shorthand(self):
        rico = load_image(make_heap([[b'old', b'B'], [b'only']]))
        for command, expected in [
                ('set *kdbr[0].col1="A = B"', b'A = B'),
                ("SeT *kdbr[0].col[1] = 'Zażółć'", 'Zażółć'.encode()),
                ("set *kdbr[0].col1 = X'00ff20'", b'\x00\xff '),
                ('set *kdbr[0].col1 = "NULL"', b'NULL'),
                ('set *kdbr[0].col1 = NULL', None),
                ('set *kdbr[0].col1 = ""', None)]:
            self.command(rico, command)
            self.assertEqual(expected.hex() if expected else '*NULL*', column_values(rico)[0][1])
        self.command(rico, 'set *kdbr[1]="XXXX"')
        self.assertEqual(['58585858'], column_values(rico)[1])
        self.assert_atomic_rejection(rico, lambda: self.command(rico, 'set *kdbr[0]="X"'))

    def test_length_prefix_boundary_and_reverse(self):
        rico = load_image(make_heap([[b'X' * 250, b'untouched']]))
        for length in (251, 254, 255, 4000, 250, 1):
            self.edit(rico, 0, 0, b'X' * length)
            offset = rico.kdbr_data[0]['COL_DATA'][0][1]
            expected_prefix = bytes([length]) if length <= 250 else b'\xfe' + struct.pack('<H', length)
            self.assertEqual(expected_prefix, rico.block_data[offset:offset + len(expected_prefix)])
            self.assertEqual((b'X' * length).hex(), column_values(rico)[0][0])
            self.assertEqual(b'untouched'.hex(), column_values(rico)[0][1])

    def test_equal_length_does_not_move_rows_or_compact_gaps(self):
        rico = load_image(make_heap([[b'AAA'], [b'BBB']], gaps=9))
        pointers = copy.deepcopy(rico.kdbr)
        layout = rico._heap_edit_layout(rico.block_data)
        self.edit(rico, 0, 0, b'CCC')
        self.assertEqual(pointers, rico.kdbr)
        self.assertEqual(layout['fseo'], rico._heap_edit_layout(rico.block_data)['fseo'])

    def test_nonzero_ktb_byte_37_does_not_inflate_itl_count(self):
        data = make_heap([[b'old'], [b'untouched']])
        data[37] = 0x1f
        rico = load_image(checksum(data))
        self.assertEqual(2, rico.current_block_desc['ITLS'])
        self.edit(rico, 0, 0, b'longer')
        self.assertEqual(['6c6f6e676572'], column_values(rico)[0])
        self.assertEqual(['756e746f7563686564'], column_values(rico)[1])

    def test_cluster_member_col21_resizes_without_changing_other_rows_or_kdbt(self):
        original = make_cluster()
        rico = load_image(original)
        self.assertTrue(rico.current_block_desc['IS_CLUSTER'])
        self.assertEqual(1, rico.kdbr_data[19]['TABLE_NO'])
        self.assertEqual(21, len(rico.kdbr_data[19]['COL_DATA']) - 1)
        expected = column_values(rico)
        original_rows = rico._heap_edit_layout(original)['rows']
        for value in (b'X' * 260, b'Z', None, b'XXXX'):
            self.command(rico, 'set *kdbr[19].col21 = ' +
                         ('NULL' if value is None else '"' + value.decode() + '"'))
            expected[19][21] = value.hex() if value else '*NULL*'
            self.assertEqual(expected, column_values(rico))
            self.assertEqual(original[106:118], rico.block_data[106:118])
            self.assertEqual(-1, struct.unpack_from('<h', rico.block_data, 150)[0])
            self.assertEqual(original[-4:], rico.block_data[-4:])
            self.assertEqual(Rico._block_checksum(rico.block_data),
                             struct.unpack_from('<H', rico.block_data, 16)[0])
            layout = rico._heap_edit_layout(rico.block_data)
            for slot, item in original_rows.items():
                if slot != 19:
                    moved = layout['rows'][slot]
                    self.assertEqual(original[item['start']:item['end']],
                                     rico.block_data[moved['start']:moved['end']])
        self.assertEqual(6, rico.kdbr_data[6]['FIRST_ROWID']['SLOT'])
        self.assertTrue(rico.dirty)

    def test_cluster_rejects_key_and_unclean_itl_atomically(self):
        rico = load_image(make_cluster())
        self.assert_atomic_rejection(rico, lambda: self.edit(rico, 6, 0, b'new'))
        active = bytearray(rico.block_data)
        struct.pack_into('<H', active, 84, 0x2002)
        rico.block_data = bytes(checksum(active))
        with self.assertRaisesRegex(ValueError, "Active or unclean ITL"):
            self.edit(rico, 19, 21, b'new')
        self.assertEqual(bytes(active), rico.block_data)

        ordinary = bytearray(make_heap([[b'unchanged']]))
        struct.pack_into('<H', ordinary, 84, 0x2002)
        rico.block_data = bytes(checksum(ordinary))
        with self.assertRaisesRegex(ValueError, "Active or unclean ITL"):
            self.edit(rico, 0, 0, b'new')
        self.assertEqual(bytes(ordinary), rico.block_data)

    def test_cluster_reservation_and_corruption_checks(self):
        rico = load_image(make_cluster(reserved=100))
        before = rico._heap_edit_layout(rico.block_data)
        self.edit(rico, 19, 21, b'X' * 300)
        after = rico._heap_edit_layout(rico.block_data)
        self.assertEqual(100, after['tosp'] - after['avsp'])
        self.assertEqual(before['avsp'] - after['avsp'],
                         before['tosp'] - after['tosp'])
        self.assert_atomic_rejection(rico, lambda: self.edit(rico, 19, 21, b'Y' * 8192))
        source = make_cluster()
        for offset, value in ((112, b'\x0c\x00'), (93, b'\x01'), (102, b'\xff\x7f')):
            with self.subTest(offset=offset):
                corrupt = bytearray(source)
                corrupt[offset:offset + len(value)] = value
                target = load_image(source)
                target.block_data = bytes(checksum(corrupt))
                self.assert_atomic_rejection(target,
                                             lambda: self.edit(target, 19, 21, b'XXXX'))

    def test_cluster_save_and_reload_on_disposable_copy(self):
        source = make_cluster()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cluster-copy.dbf'
            with path.open('wb') as output:
                output.seek(215 * 8192)
                output.write(source)
            rico = Rico()
            rico.file_names[1] = str(path)
            self.command(rico, 'set dba 1,215')
            self.command(rico, 'set mode edit')
            self.command(rico, 'set *kdbr[19].col21="LONGER"')
            edited = rico.block_data
            self.command(rico, 'save')
            self.command(rico, 'set dba 1,215')
            self.assertEqual(edited, rico.block_data)
            self.assertEqual('4c4f4e474552', column_values(rico)[19][21])

    def test_cluster_equal_length_and_committed_row_lock(self):
        source = make_cluster()
        layout = load_image(source)._heap_edit_layout(source)
        locked = bytearray(source)
        locked[44] = 1
        struct.pack_into('<H', locked, 60, 0x8000)
        locked[layout['rows'][19]['start'] + 1] = 1
        rico = load_image(checksum(locked))
        pointers = list(rico.kdbr)
        self.edit(rico, 19, 21, b'new')
        self.assertEqual(pointers, rico.kdbr)
        self.assertEqual(1, rico.kdbr_data[19]['LOCK'])
        self.assertEqual(locked[44:68], rico.block_data[44:68])
        self.assertEqual('6e6577', column_values(rico)[19][21])
        self.assert_atomic_rejection(rico, lambda: self.edit(rico, 6, 0, b'bad'))

    def test_cluster_header_flag_zero_or_one(self):
        for flag in (0, 1):
            with self.subTest(flag=flag):
                rico = load_image(make_cluster(flag=flag))
                self.edit(rico, 19, 21, b'XXXX')
                self.assertEqual(flag, rico.block_data[92])
                self.assertEqual('58585858', column_values(rico)[19][21])

    def test_mixed_cluster_preserves_nonhead_and_deleted_rows(self):
        source = make_cluster(mixed=True, reserved=344)
        rico = load_image(source)
        before = rico._heap_edit_layout(source)
        self.assertEqual(8, before['tosp'] - before['packable_free'])
        self.assertEqual(0x4c, before['rows'][15]['flag'])
        self.assertEqual(0x7c, before['rows'][26]['flag'])
        for value in (b'X' * 225, b'XXXX'):
            old = rico.block_data
            previous = rico._heap_edit_layout(old)
            self.edit(rico, 19, 21, value)
            after = rico._heap_edit_layout(rico.block_data)
            for slot in (15, 26, 30):
                original = previous['rows'][slot]
                moved = after['rows'][slot]
                self.assertEqual(old[original['start']:original['end']],
                                 rico.block_data[moved['start']:moved['end']])
            self.assertEqual(8, after['tosp'] - after['packable_free'])
            self.assertEqual(value.hex(), column_values(rico)[19][21])
        self.assert_atomic_rejection(rico, lambda: self.edit(rico, 15, 0, b'X'))
        self.assert_atomic_rejection(rico, lambda: self.edit(rico, 26, 0, b'X'))

    def test_edit_force_skips_only_itl_and_row_lock_checks(self):
        source = make_cluster(reserved=344, mixed=True)
        start = load_image(source)._heap_edit_layout(source)['rows'][19]['start']
        active = bytearray(source)
        struct.pack_into('<HHI', active, 68, 10, 30, 3637)
        struct.pack_into('<H', active, 84, 0x2002)
        active[start + 1] = 2
        for deleted_slot in (26, 30):
            deleted_start = load_image(source)._heap_edit_layout(source)['rows'][deleted_slot]['start']
            active[deleted_start + 1] = 2
        active = checksum(active)
        rico = load_image(active)
        self.assert_atomic_rejection(rico, lambda: self.edit(rico, 19, 21, b'XXXX'))
        self.command(rico, 'set mode edit force')
        self.assertTrue(rico.edit_mode)
        self.assertTrue(rico.force_edit)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            rico.show('mode')
        self.assertIn('Edit Force', output.getvalue())
        self.edit(rico, 19, 21, b'X' * 225)
        self.assertEqual((b'X' * 225).hex(), column_values(rico)[19][21])
        self.assertEqual(active[68:92], rico.block_data[68:92])
        self.assertEqual(2, rico.kdbr_data[19]['LOCK'])
        self.assertEqual(active[106:118], rico.block_data[106:118])
        self.assertEqual(Rico._block_checksum(rico.block_data),
                         struct.unpack_from('<H', rico.block_data, 16)[0])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'forced-copy.dbf'
            with path.open('wb') as output:
                output.seek(215 * 8192)
                output.write(active)
            copy_rico = Rico()
            copy_rico.file_names[1] = str(path)
            self.command(copy_rico, 'set dba 1,215')
            self.command(copy_rico, 'set mode edit force')
            self.command(copy_rico, 'set *kdbr[19].col21="XXXX"')
            edited = copy_rico.block_data
            self.command(copy_rico, 'save')
            self.command(copy_rico, 'set dba 1,215')
            self.assertEqual(edited, copy_rico.block_data)
            self.assertEqual('58585858', column_values(copy_rico)[19][21])
        self.command(rico, 'set mode edit')
        self.assert_atomic_rejection(rico, lambda: self.edit(rico, 19, 21, b'Y'))
        self.command(rico, 'set mode edit force')
        damaged = bytearray(rico.block_data)
        damaged[112] = 12
        rico.block_data = bytes(checksum(damaged))
        self.assert_atomic_rejection(rico, lambda: self.edit(rico, 19, 21, b'Y'))

    def test_edit_force_is_explicit_and_can_be_reset(self):
        rico = load_image(make_cluster())
        for invalid in ('set mode browse force', 'set mode edit forced',
                        'set mode edit force extra'):
            self.assert_atomic_rejection(rico, lambda: self.command(rico, invalid))
        self.command(rico, 'set mode edit force')
        self.command(rico, 'set mode edit')
        self.assertTrue(rico.edit_mode)
        self.assertFalse(rico.force_edit)
        self.command(rico, 'set mode edit force')
        self.command(rico, 'set mode browse')
        self.assertFalse(rico.edit_mode)
        self.assertFalse(rico.force_edit)

    def test_compacts_fragmented_rows_with_shuffled_directory_and_free_links(self):
        data = make_heap([[b'A'], [], [b'B'], [], [b'C']], gaps=80, free_slots=(1, 3))
        base = 92
        a = data[base + 18:base + 20]
        data[base + 18:base + 20] = data[base + 26:base + 28]
        data[base + 26:base + 28] = a
        rico = load_image(checksum(data))
        expected = column_values(rico)
        layout = rico._heap_edit_layout(rico.block_data)
        growth = layout['fseo'] - layout['fsbo'] + 1
        self.edit(rico, 2, 0, b'G' * growth)
        expected[2][0] = (b'G' * growth).hex()
        self.assertEqual(expected, column_values(rico))
        self.assertEqual(data[base + 4:base + 8], rico.block_data[base + 4:base + 8])
        self.assertEqual(data[base + 20:base + 22], rico.block_data[base + 20:base + 22])
        after = rico._heap_edit_layout(rico.block_data)
        self.assertEqual(after['avsp'], after['fseo'] - after['fsbo'])

    def test_more_than_255_slots_and_variable_ktb_extension(self):
        for itls, extension in ((1, 0), (3, 8), (5, 24)):
            rico = load_image(make_heap([[b'A']] * 300, itls=itls, extension=extension))
            self.assertEqual(300, len(rico.kdbr))
            self.edit(rico, 299, 0, b'LAST')
            self.assertEqual([b'LAST'.hex()], column_values(rico)[299])
            self.assertEqual([[b'A'.hex()]] * 299, column_values(rico)[:299])

    def test_capacity_and_invalid_commands_leave_entire_session_unchanged(self):
        rico = load_image(make_heap([[b'A', b'B']]))
        for command in ('set *kdbr[0].col1=X\'abc\'', 'set *kdbr[0].col1=unquoted',
                        'set *kdbr[0].col1="x" "y"', 'set *kdbr[10].col0="x"',
                        'set *kdbr[0].col2="x"'):
            self.assert_atomic_rejection(rico, lambda: self.command(rico, command))
        self.assert_atomic_rejection(rico, lambda: self.edit(rico, 0, 1, b'X' * 8192))
        self.assert_atomic_rejection(rico, lambda: self.edit(rico, 0, 1, b'X' * 65536))
        rico.edit_mode = False
        self.assert_atomic_rejection(rico, lambda: self.edit(rico, 0, 0, b'B'), RuntimeError)

    def test_corruption_and_unsupported_layouts_are_rejected_atomically(self):
        base = 92
        original = make_heap([[b'A'], [b'B']])
        start = base + struct.unpack_from('<H', original, base + 18)[0]
        cases = [(20, b'\x02'), (base, b'\x40'), (base, b'\x20'), (base + 1, b'\x02'),
                 (base + 6, b'\x00\x00'), (base + 8, b'\xff\x7f'),
                 (base + 10, b'\x00\x00'), (base + 14, b'\x01\x00'),
                 (base + 4, b'\x00\x00'), (start, b'\x20'), (start, b'\x3c'),
                 (start + 1, b'\x01'), (start + 2, b'\xfe'), (start + 3, b'\xfc'),
                 (start + 3, b'\xfe\xff\xff'), (44, b'\x01'), (60, b'\x01\x80'),
                 (8190, b'\xff'),
                 (base + 20, original[base + 18:base + 20])]
        for offset, value in cases:
            with self.subTest(offset=offset, value=value):
                rico = load_image(original)
                corrupt = bytearray(original)
                corrupt[offset:offset + len(value)] = value
                rico.block_data = bytes(checksum(corrupt))
                self.assert_atomic_rejection(rico, lambda: self.edit(rico, 0, 0, b'XXX'))
        rico = load_image(original)
        rico.block_data = rico.block_data[:16] + b'\x00\x00' + rico.block_data[18:]
        self.assert_atomic_rejection(rico, lambda: self.edit(rico, 0, 0, b'XXX'))

    def test_seeded_repeated_edits_preserve_every_other_column_and_undo(self):
        rng = random.Random(600)
        data = make_heap([[b'A', None, b'C'] for _ in range(40)], gaps=5)
        rico = load_image(data)
        expected = column_values(rico)
        for _ in range(100):
            slot, column = rng.randrange(40), rng.randrange(3)
            value = bytes([rng.randrange(32, 127)]) * rng.randrange(0, 80)
            self.edit(rico, slot, column, value)
            expected[slot][column] = value.hex() if value else '*NULL*'
            self.assertEqual(expected, column_values(rico))
        self.command(rico, 'undo')
        self.assertEqual(bytes(data), rico.block_data)
        self.assertFalse(rico.dirty)

    def test_committed_itl_reference_is_preserved(self):
        data = make_heap([[b'A']])
        data[44] = 1
        struct.pack_into('<H', data, 60, 0x8000)
        start = 92 + struct.unpack_from('<H', data, 110)[0]
        data[start + 1] = 1
        rico = load_image(checksum(data))
        self.edit(rico, 0, 0, b'LONGER')
        self.assertEqual(1, rico.kdbr_data[0]['LOCK'])
        self.assertEqual(data[44:68], rico.block_data[44:68])

    def test_noop_and_failed_staged_decoder_preserve_session(self):
        rico = load_image(make_heap([[b'A']]))
        source = rico.block_data
        pointers = copy.deepcopy(rico.kdbr)
        self.edit(rico, 0, 0, b'A')
        self.assertEqual(source, rico.block_data)
        self.assertEqual(pointers, rico.kdbr)
        self.assertFalse(rico.dirty)
        with mock.patch.object(Rico, 'parse_block', side_effect=ValueError('decoder failure')):
            self.assert_atomic_rejection(rico, lambda: self.edit(rico, 0, 0, b'LONGER'))
        rico.index_header = {'stale': True}
        self.edit(rico, 0, 0, b'LONGER')
        self.assertIsNone(rico.index_header)
        self.assert_atomic_rejection(rico, lambda: self.edit(rico, 0, 0, b'X' * 8192))

    def test_save_reload_and_revert_on_copy(self):
        source = (FIXTURES / 'original-45699.bin').read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'copy.dbf'
            with path.open('wb') as output:
                output.seek(45699 * 8192)
                output.write(source)
            rico = Rico()
            rico.file_names[12] = str(path)
            self.command(rico, 'set dba 12,45699')
            self.command(rico, 'set mode edit')
            self.command(rico, 'set *kdbr[0].col1="XXXX"')
            self.command(rico, 'save')
            edited = rico.block_data
            self.command(rico, 'revert')
            with path.open('rb') as input_file:
                input_file.seek(45699 * 8192)
                self.assertEqual(source, input_file.read(8192))
            self.command(rico, 'save')
            self.command(rico, 'set dba 12,45699')
            self.assertEqual(edited, rico.block_data)


if __name__ == '__main__':
    unittest.main()
