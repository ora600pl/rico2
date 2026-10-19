# Heap-column resizing: design and evidence

Implemented command: `set *kdbr[0].col1 = "XXXX"`. Both numbers are zero-based.
This adds a structural operation beside the existing raw `MODIFY`, with a staged
block image and refusal on unsupported or inconsistent input.

## Verified environment

- Date: 2026-10-16. Host `vrick`, instance `rick`, PDB `RICK1`.
- Oracle 19.32.0.0.0, AArch64 little-endian, block size 8192, AL32UTF8.
- Existing `HR.EMPLOYEES2`: 107 rows, no indexes, uncompressed, data object 76194.
- File 12: block 45699 contains 97 rows; block 45700 contains 10 rows.
- `kdbh` starts at byte 124 in both images (three ITLs plus the KTB extension).
- Original free space: 848 and 7328 bytes respectively.
- Live executable and Ghidra `/oracle` import have the same SHA-256:
  `d44f87bd87e324bf4a754e93f9ce1b31fd4f61fe1e477f9ba214e793a547c9a0`.

The user's later instruction to use the existing EMPLOYEES2 superseded creation.
No table or schema was created or replaced. The USERS tablespace also contains
other laboratory objects, so RICO2 writes were confined to isolated file copies.

## Static reconstruction with ghidra-mcp

Selected decompiler excerpts and addresses are retained in
[`static-evidence.json`](static-evidence.json). These are observations about this
specific binary, not a portable Oracle format specification.

| Function | Evidence used |
|---|---|
| `kdbasp`, `kdbfsp` | KTB/ITL extension calculation; row offsets relative to `kdbh`; adjustment of offsets and fields at `kdbh + 8`, `+10`, `+12` |
| `kdb4cpss` | Copying row pieces using their decoded lengths, rebuilding the row directory and free-space boundary; Oracle handles additional row formats outside this implementation's scope |
| `kdr9igtl` | Row length comes from the row header and column lengths, rather than the next row pointer; `FF` NULL and `FE` extended length |
| `kduovw` | Encoding uses one byte below `0xfb` and `FE` plus a two-byte little-endian length at 251 bytes and above |

RICO2 uses an independently implemented repack. It does not claim to reproduce
Oracle's complete update/redo/undo algorithm or its exact choice of physical
placement. It preserves the original physical ordering and logical row slots.

## Algorithm and invariants

1. Decode a fresh layout from the current bytes. Require an ordinary heap or
   uncompressed clustered data block, consistent tail/checksum, clean ITLs in
   normal edit mode, valid
   free-slot links and non-overlapping complete row pieces. A row may retain a
   lock byte only when it references a committed ITL with no remaining lock count.
2. Locate the requested column's **length prefix and payload**. Encode the new
   value and compute `delta = new_encoded_field_size - old_encoded_field_size`.
   Refuse an absent column or an increase larger than available free space.
3. For `delta != 0`, copy all occupied row pieces into a separate buffer packed
   against `block_size - 4`. Copy every unrelated row byte-for-byte. Preserve the
   slot numbers and update the pointers as `new_absolute_offset - kdbh_offset`.
   Preserve free-slot chain entries. Zero only the rebuilt free/row-data region.
4. Leave `fsbo` unchanged; set `fseo` to the new beginning of packed row data and
   subtract `delta` from both `avsp` and `tosp`. The input validator accounts for
   deleted-row header credit in `tosp` and requires `avsp <= tosp`. Their difference is
   preserved; growth is bounded by `avsp`.
   Equal-size edits replace just the column bytes without repacking.
5. Recalculate the checksum, run the strict validator on the candidate, and parse
   it in a separate Rico object. Publish bytes and decoded row offsets together
   only after every check succeeds. `SAVE` remains a separate disk operation.

For `Steven -> XXXX` in slot 0 of block 45699:

| Item | Before | After |
|---|---:|---:|
| Row piece size | 62 | 60 |
| Absolute row offset | 8126 | 8128 |
| `kdbr[0]` relative offset | 8002 | 8004 |
| `fseo` | 1060 | 1062 |
| `avsp`, `tosp` | 848 | 850 |
| `fsbo` | 212 | 212 |
| Moved row pieces | — | 97 |

The full row count is now read as a two-byte value, fixing the previous truncation
at 255 slots. Free directory links below `fseo` are also recognized by the display
decoder; the edit validator separately checks their chain and membership.

## Tests and independent checks

`python3 -m unittest -v` passes 47 tests in the current checkout.
The new tests include 100 seeded repeated edits, all-column comparisons on both
real blocks, short/long/NULL/UTF-8/hex inputs, the 250/251 transition, fragmented
space, non-monotonic row directories, free-slot links, 300 row slots, variable KTB
extensions, committed and active ITLs, checksum/tail/overlap corruption, capacity
failure, failure during staged parsing, no-op, undo, save, reload and revert.

Oracle **DBVERIFY**, run on `vrick`, independently checked copies at their original
block numbers. [`dbverify-results.json`](dbverify-results.json) contains 28 cases:
23 accepted block images, three expected capacity refusals by RICO2, and two
negative controls rejected by DBVERIFY. Valid images each report one data page,
zero failing data pages and zero marked-corrupt pages. Examples are in
[`dbverify-examples.log`](dbverify-examples.log).

Cases include first/middle/last selected slots, shrink, growth, NULL, 250/251/254/255
byte values, a 4000-byte value in the roomier block, and exact exhaustion of free
space. Negative controls change the original column length to 200 without moving
the other fields, then repair the checksum; DBVERIFY still reports a failing data
page. This distinguishes structural validation from checksum validation alone.

The long values deliberately test physical format, not schema validity:
EMPLOYEES2.FIRST_NAME is VARCHAR2(20). DBVERIFY does not enforce that declaration.

Skipper MCP supplied live instance metadata, the block map, row contents, ITL
state and original verification via its `rico2` tool. A separate native SQL test
on the existing table changed FIRST_NAME to four bytes, twenty bytes and NULL,
then rolled back. All 107 rows, all columns and ROWID block/slot pairs match the
captured original block images and match before/after rollback. See
[`native-sql-results.json`](native-sql-results.json) and [`native-sql.log`](native-sql.log).

A Skipper breakpoint attempt at `kduovw` timed out after 30 seconds. No breakpoint
hit or runtime argument evidence is claimed. The diagnostic session was closed,
its SPID was absent, and the final SQL check found no such session. Static
reconstruction and DBVERIFY are the evidence for the RICO2 algorithm.

## Reproduction and boundaries

Original block fixtures are `original-45699.bin` and `original-45700.bin`.
The first has SHA-256 `0da963ed9b91aefd75a733af5498cd96cafd748f55fde253307389ece1d756a9`;
the second has `d3435125478da76cc7d82496e90aa02cb3ccf3bf8bfb7e240c26bae95eebade3`.

To repeat DBVERIFY, create a disposable directory with `rico2.py`,
[`lab_dbverify.py`](lab_dbverify.py), the two fixtures, and `file-headers.bin`
containing the first two blocks of the original datafile. Set ORACLE_HOME and run
`python3 lab_dbverify.py`. It creates sparse files at the original block positions
and writes logs/results; it never opens the live datafile for writing. Copies used
in this run are under `/tmp/rico2-resize-20261016` on vrick.

Native SQL tests and physical DBVERIFY checks on `HR.EMPLOYEES2` copies are
distinct tests. The heap resize path was not installed in the live datafile
during these checks.

Cluster member support uses synthetic, application-style three-table fixtures.
The writer parses `0xac` key, `0x6c` member, `0x4c` non-head and `0x7c`
deleted piece headers, preserves unrelated bytes and `kdbt` ranges, and allows
changes only to complete member columns. Fixtures test a multi-column member
resize, the 250/251 length transition, key and neighbour preservation, free-slot
links, reserved space, mixed row flags and atomic rejection.

The explicit `SET MODE EDIT FORCE` override skips ITL-status and row-lock
checks for column SET while retaining structural validation and staged parsing.
Tests use a synthetic `0x2002` ITL and a row lock referencing it. `SET MODE EDIT`
resets the override. FORCE does not establish whether an Oracle transaction has
committed or make a write to an open datafile transactionally safe.

Unsupported as edit targets: migration/chaining, deleted row pieces, cluster keys,
non-head pieces. Compression, row
dependencies, extended column counts, omitted trailing NULL materialization,
index/constraint/schema maintenance, segment free-space maps, transactions,
redo/undo, and synchronization with an open database. These require additional
algorithms and evidence. The accepted block sizes are 2/4/8/16/32 KiB; only 8 KiB
was exercised against real Oracle images here.

The separately maintained Skipper RICO2 wrapper needs to classify column `SET`
as an in-memory modification and require `confirm_modifications=true` before
adopting this engine. Its existing `allow_disk_writes` policy for SAVE remains
necessary. This checkout does not change or deploy that wrapper.
