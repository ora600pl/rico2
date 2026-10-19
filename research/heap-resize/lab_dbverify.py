"""Validate resized copies with Oracle DBVERIFY. Never writes a live datafile.

Run in a directory containing rico2.py, file-headers.bin (the first two
blocks of the source file), and original-{45699,45700}.bin,
with ORACLE_HOME set. Sparse copies retain the original block number.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rico2 import Rico, execute_command


def run(directory):
    results = []
    for block in (45699, 45700):
        source = (directory / ('original-%d.bin' % block)).read_bytes()
        for label, slot, length in [('baseline', 0, None), ('shrink', 0, 4),
                                    ('grow', 0, 20), ('middle', 4, 1), ('last', 9, 18),
                                    ('null', 0, 0), ('len250', 0, 250), ('len251', 0, 251),
                                    ('len254', 0, 254), ('len255', 0, 255),
                                    ('len4000', 0, 4000), ('full', 0, -1),
                                    ('overflow', 0, -2), ('negative-control', 0, None)]:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rico = Rico()
                rico.get_block_memdump(str(directory / ('original-%d.bin' % block)), 0)
                rico.edit_mode = True
                before = [[col[2] for col in row['COL_DATA']] for row in rico.kdbr_data]
                old_offsets = [item[2] for item in rico.kdbr]
                layout = rico._heap_edit_layout(source)
                expected = [row[:] for row in before]
                if length in (-1, -2):
                    old_length = rico.kdbr_data[slot]['COL_DATA'][1][0]
                    length = layout['avsp'] + old_length - 2 + (label == 'overflow')
                try:
                    if length is not None:
                        execute_command(rico, 'set *kdbr[%d].col1 = "%s"' % (slot, 'X' * length))
                        expected[slot][1] = (b'X' * length).hex() if length else '*NULL*'
                except ValueError as error:
                    assert label in ('overflow', 'len4000') and rico.block_data == source
                    results.append({'block': block, 'case': label, 'rejected': str(error),
                                    'bytes_unchanged': True})
                    continue
                assert label != 'overflow'
                assert expected == [[col[2] for col in row['COL_DATA']] for row in rico.kdbr_data]
                if label == 'negative-control':
                    corrupted = bytearray(source)
                    corrupted[rico.kdbr_data[0]['COL_DATA'][1][1]] = 200
                    struct.pack_into('<H', corrupted, 16, Rico._block_checksum(corrupted))
                    rico.block_data = bytes(corrupted)
                else:
                    assert rico.verify()
            name = '%d-%s' % (block, label)
            file = directory / (name + '.dbf')
            with file.open('wb') as target:
                target.write((directory / 'file-headers.bin').read_bytes())
                target.seek(block * 8192)
                target.write(rico.block_data)
            env = dict(os.environ, NLS_LANG='AMERICAN_AMERICA.AL32UTF8', LC_ALL='C')
            result = subprocess.run([os.path.join(os.environ['ORACLE_HOME'], 'bin', 'dbv'),
                                     'file=' + str(file), 'blocksize=8192',
                                     'start=%d' % block, 'end=%d' % block],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    universal_newlines=True, env=env, timeout=60)
            (directory / (name + '.log')).write_text(output.getvalue() + '\n' + result.stdout)
            counts = {}
            for key, pattern in [('examined', r'Total Pages Examined\s*:\s*(\d+)'),
                                 ('data', r'Total Pages Processed \(Data\)\s*:\s*(\d+)'),
                                 ('failed_data', r'Total Pages Failing\s*\(Data\)\s*:\s*(\d+)'),
                                 ('corrupt', r'Total Pages Marked Corrupt\s*:\s*(\d+)')]:
                match = re.search(pattern, result.stdout)
                if not match:
                    raise RuntimeError('Unrecognized DBVERIFY output: ' + result.stdout)
                counts[key] = int(match.group(1))
            if label == 'negative-control':
                assert counts['failed_data'] + counts['corrupt'] > 0, result.stdout
            else:
                assert result.returncode == 0 and counts == {
                    'examined': 1, 'data': 1, 'failed_data': 0, 'corrupt': 0}, result.stdout
            results.append({'block': block, 'case': label, 'dbverify': counts,
                            'sha256': hashlib.sha256(rico.block_data).hexdigest(),
                            'moved_rows': sum(old != new[2] for old, new in zip(old_offsets, rico.kdbr)),
                            'all_column_values_match': label != 'negative-control'})
            print(name, counts, flush=True)
    (directory / 'dbverify-results.json').write_text(json.dumps(results, indent=2) + '\n')
    print('PASS: %d cases; live datafile never opened for writing' % len(results))


if __name__ == '__main__':
    run(Path(__file__).resolve().parent)
