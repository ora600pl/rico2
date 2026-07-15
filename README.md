# RICO2 — Oracle Block Editor and Analyzer

RICO2 is an interactive tool for low-level analysis and editing of Oracle data file blocks. Its syntax and session model are inspired by the discontinued and unsupported **BBED** utility. RICO2 additionally provides decoders for table, cluster, and B-tree index blocks.

> [!CAUTION]
> RICO2 operates directly on Oracle data files. The `SAVE`, `REVERT`, and `COPY` commands write data to disk and may irreversibly corrupt a database. Work on a copy, with the database shut down, or in an isolated laboratory environment. `SET MODE EDIT` is not a safety mechanism and does not replace a verified backup.

## Table of Contents

- [Features and limitations](#features-and-limitations)
- [Requirements and startup](#requirements-and-startup)
- [Listfile format](#listfile-format)
- [Session model](#session-model)
- [Quick start](#quick-start)
- [Command reference](#command-reference)
- [Supported block structures](#supported-block-structures)
- [Common workflows](#common-workflows)
- [Errors and troubleshooting](#errors-and-troubleshooting)
- [BBED compatibility](#bbed-compatibility)
- [Tests](#tests)
- [License and disclaimer](#license-and-disclaimer)

## Features and limitations

RICO2 supports:

- selecting a file and block through `FILE`, `BLOCK`, or `DBA`;
- raw block dumps in hexadecimal and ASCII form;
- block structure maps;
- decoding `kcbh`, `ktbbh`, `kdbh`, `kdbt`, `kdxle`, and `kdxbr` headers;
- decoding `kdbr` row directories and `kd_off` index entry offsets;
- decoding heap rows, cluster keys, cluster member rows, and B-tree index entries;
- searching for text and hexadecimal byte sequences in one block or an entire file;
- filtering blocks by `DATA_OBJECT_ID` and transaction ID;
- calculating, applying, and verifying Oracle block checksums;
- changing an in-memory block buffer, undoing the change, and saving it to disk;
- copying a complete block to another file and block location.

Important limitations:

- RICO2 is not a complete BBED replacement and does not implement a BBED-style BIFILE journal;
- `ASSIGN` is an alias for byte-level `MODIFY`, not a typed structure-field assignment command;
- the index decoder handles common B-tree leaf and branch blocks; the special leaf entry format marked by flag `0x10` is exposed as raw data only;
- `SELECT` is experimental and depends on the decoded row directory of the current block;
- `WIDTH` and `OBASE` are retained for session compatibility, but most reports use an explicit fixed format;
- RICO2 does not coordinate data file writes with a running Oracle instance;
- it does not repair logical database structures, redo, undo, dictionary metadata, or segment allocation metadata automatically.

## Requirements and startup

Requirements:

- Python 3;
- read access to every data file listed in the listfile;
- for write operations, operating-system write access to the data file and `SET MODE EDIT`;
- a listfile that maps Oracle file numbers to filesystem paths.

Start RICO2 with:

```bash
python3 rico2.py listfile.txt
```

After loading the listfile, RICO2 displays an interactive prompt:

```text
rico2 >
```

Commands are case-insensitive. Text arguments containing spaces must be enclosed in quotes.

Example startup banner:

```text
RICO v2 by Kamil Stawiarski (@ora600pl | www.ora-600.pl)
This is open source project to map BBED functionality.
...
Usage: python3 rico2.py listfile.txt
```

## Listfile format

Each active line has the following format:

```text
<file_id> <absolute_data_file_path>
```

Example:

```text
# Laboratory database files
1 /opt/oracle/oradata/ORCLCDB/system01.dbf
2 /opt/oracle/oradata/ORCLCDB/sysaux01.dbf
7 /opt/oracle/oradata/ORCLCDB/users01.dbf
```

Blank lines and comments beginning with `#` are accepted. `file_id` must match the Oracle file number used in database block addresses and physical row identifiers.

The current listfile parser splits lines on whitespace. Data file paths must therefore not contain spaces.

When the file is loaded, each mapping is printed:

```text
1       /opt/oracle/oradata/ORCLCDB/system01.dbf
7       /opt/oracle/oradata/ORCLCDB/users01.dbf
```

## Session model

RICO2 maintains a BBED-like session state:

| Parameter | Meaning | Initial value |
|---|---|---|
| `FILE` | current Oracle file number | not selected |
| `BLOCK` | current block number | not selected |
| `OFFSET` | current byte offset inside the block | `0` |
| `DBA` | address composed from `FILE` and `BLOCK` | not selected |
| `BLOCKSIZE` | block size in bytes | `8192` |
| `MODE` | `BROWSE` or `EDIT` | `BROWSE` |
| `IBASE` | input number base | `DEC` |
| `OBASE` | declared output number base | `DEC` |
| `WIDTH` | declared report width | `80` |
| `COUNT` | default byte count | `512` |
| `DIRTY` | whether the in-memory buffer was modified | `No` |

Selecting a block loads its complete contents into memory and runs block structure detection. `DUMP`, `PRINT`, `EXAMINE`, `MODIFY`, `SUM`, and `VERIFY` operate on this in-memory buffer.

The buffer is written back to its original location only by `SAVE`. `COPY` and `REVERT` write directly to their target locations.

Some `PRINT` commands move the current `OFFSET` to the displayed structure or record. Always check `SHOW OFFSET` before modifying data.

### Numeric input and IBASE

After changing `IBASE`, unprefixed numeric arguments are interpreted in the selected base:

```text
rico2 > set ibase hex
rico2 > set offset 20
```

The second command sets offset `0x20`, which is decimal 32. To avoid ambiguity, use `SET IBASE DEC` before a sequence of commands unless hexadecimal or octal input is intentional.

## Quick start

A safe, read-only session:

```text
rico2 > info
rico2 > set dba 1,145
rico2 > show
rico2 > map
rico2 > print kcbh
rico2 > print kdbh
rico2 > print *kdbr[0]
rico2 > examine /rnccc
rico2 > verify
rico2 > exit
```

## Command reference

### HELP and `?`

Displays the abbreviated command list and syntax summary.

```text
HELP
?
```

Example output:

```text
BBED-compatible commands:
  SET DBA file,block | SET FILE file | SET BLOCK block | SET OFFSET offset
  SET COUNT count | SET BLOCKSIZE size | SET MODE BROWSE|EDIT
  ...
```

The current implementation prints the complete summary rather than command-specific help.

### EXIT and QUIT

Ends the interactive session:

```text
EXIT
QUIT
```

An unsaved dirty buffer is not written automatically.

### INFO

Displays all listfile entries and their sizes in blocks, calculated using the current `BLOCKSIZE`.

```text
INFO
```

Example output:

```text
 File#  Name                                                        Size(blks)
 -----  ----                                                        ----------
     1  /opt/oracle/oradata/ORCLCDB/system01.dbf                        117760
     7  /opt/oracle/oradata/ORCLCDB/users01.dbf                            640
```

Long paths are shortened for display. The stored path is not modified.

### SHOW

Displays the complete session state or one selected parameter.

```text
SHOW
SHOW FILE|BLOCK|OFFSET|DBA|FILENAME|LISTFILE
SHOW BLOCKSIZE|MODE|IBASE|OBASE|WIDTH|COUNT|DIRTY
```

Example output:

```text
 FILE            1
 BLOCK           145
 OFFSET          0
 DBA             0x400091 (4194449 1,145)
 FILENAME        /opt/oracle/oradata/ORCLCDB/system01.dbf
 LISTFILE        /home/oracle/listfile.txt
 BLOCKSIZE       8192
 MODE            Browse
 IBASE           Dec
 OBASE           Dec
 WIDTH           80
 COUNT           512
 DIRTY           No
```

`DBA` is shown as hexadecimal, decimal, and the corresponding `file,block` pair.

Examples of individual parameters:

```text
rico2 > show offset
 OFFSET          0

rico2 > show dirty
 DIRTY           No
```

### SET

Changes the session state.

```text
SET <parameter> <value>
```

#### Selecting a block

```text
SET DBA <file>,<block>
SET FILE <file>
SET BLOCK <block>
```

Examples:

```text
rico2 > set dba 1,145
rico2 > set file 7
rico2 > set block 32
```

`SET DBA` selects both components at once. `SET FILE` keeps the current block number when one is already selected and otherwise starts with block 1. `SET BLOCK` keeps the current file number; if no file is selected, it uses the lowest file number from the listfile.

Loading another block replaces the current in-memory buffer and its backup snapshot.

#### Session parameters

| Command | Description |
|---|---|
| `SET OFFSET n` | sets a byte offset from `0` through `BLOCKSIZE-1` |
| `SET COUNT n` | sets the default byte count used by `DUMP` and `EXAMINE` |
| `SET BLOCKSIZE n` | sets a positive block size that is a multiple of 512 |
| `SET MODE BROWSE` | prevents commands that require write mode |
| `SET MODE EDIT` | permits `SAVE`, `COPY`, `REVERT`, and `SUM APPLY` |
| `SET IBASE DEC\|HEX\|OCT` | changes the base used for subsequent numeric input |
| `SET OBASE DEC\|HEX\|OCT` | stores the declared output base |
| `SET WIDTH n` | stores the requested report width |
| `SET MANUALOFFSET n` | applies an advanced table-layout offset correction |

`MANUALOFFSET` is a RICO2 extension for unusual table block variants. Reload the block with `SET DBA` after changing it so the block is parsed again with the new value.

Invalid values produce errors such as:

```text
Command failed: Offset must be between 0 and 8191
Command failed: Block size must be a positive multiple of 512
Command failed: MODE must be BROWSE or EDIT
```

### DUMP and D

Displays bytes in hexadecimal and ASCII form, 16 bytes per line.

```text
DUMP [/V] [DBA f,b | FILE f BLOCK b] [OFFSET n] [COUNT n]
D    [/V] [DBA f,b | FILE f BLOCK b] [OFFSET n] [COUNT n]
```

`/V` is accepted for BBED compatibility. If no location is supplied, the current block is used. If `OFFSET` or `COUNT` is omitted, the corresponding session value is used.

Example:

```text
rico2 > dump dba 1,1 offset 0 count 64
 File: /opt/oracle/oradata/ORCLCDB/system01.dbf(1)
 Block: 1 Offsets: 0 to 63        Dba: 0x400001
---------------------------------------------------------------
0ba20000 01004000 00000000 00000104 | ......@.........
00000000 00000000 00000000 00000000 | ................
00000000 00000000 00000000 00000000 | ................
00000000 00000000 00000000 00000000 | ................

<16 bytes per line>
```

The header identifies the data file, Oracle file number, block number, offset range, and DBA. The left side contains hexadecimal bytes. The right side contains printable ASCII characters; other bytes are replaced with dots.

The output is clipped at the end of the block. `COUNT` must be greater than zero.

### MAP

Detects the current block type and displays its physical structure layout.

```text
MAP
```

Example index branch output:

```text
KTB Data Block (Index Branch)
struct kcbh, 20 bytes @0
struct ktbbh, 48 bytes @20
struct kdxbr, 24 bytes @68
sb2 kd_off[5] @92
ub1 freespace[7948] @102
ub1 rowdata[70] @8050
ub4 tailchk @8188
```

Notation:

- `@n` is the byte offset from the start of the block;
- `[n]` is an element or byte count;
- `freespace` is the unused area between the directory and row data;
- `rowdata` contains table rows or index entries growing from the end of the block;
- `tailchk` is the final four-byte block check value.

Typical table or cluster output contains `kcbh`, `ktbbh`, `kdbh`, `kdbt`, `kdbr`, free space, row data, and `tailchk`.

`PRINT` without an argument is an alias for `MAP`.

### PRINT and P

Displays a decoded structure from the current block.

```text
PRINT [structure]
P [structure]
```

#### Common structures

| Command | Output |
|---|---|
| `PRINT` | map of the current block |
| `PRINT kcbh` | common block header, type, format, DBA, SCN, and flags |
| `PRINT ktbbh` | KTB transaction header and interested transaction list |
| `PRINT tailchk` | final block check value |

Example `kcbh` fields:

```text
struct kcbh, 20 bytes                       @0
   ub1 type_kcbh                            @0        0x06
   ub1 frmt_kcbh                            @1        0xa2
   ub1 spare1_kcbh                          @2        0x00
   ub1 spare2_kcbh                          @3        0x00
   ub4 rdba_kcbh                            @4        0x00400091
```

The value following `@` is the field offset inside the block. The final column contains the decoded field value.

#### Table and cluster structures

| Command | Output |
|---|---|
| `PRINT kdbh` | table or cluster data header |
| `PRINT kdbt` | table directories stored in the block |
| `PRINT kdbr` | complete row offset directory |
| `PRINT kdbr[n]` | row directory pointer for slot `n` |
| `PRINT *kdbr[n]` | decoded row in slot `n` |

Example heap row:

```text
rico2 > print *kdbr[0]
rowdata[8058] @8058 0x2c
flag@8058: 0x2c
lock@8059: 0x0
cols@8060: 3
col 0[3] @8062: 414243
col 1[2] @8066: c102
col 2: *NULL*
```

The output contains the row flag, ITL slot in `lock`, column count, and raw hexadecimal column values. `*NULL*` represents a NULL column. RICO2 also makes a conservative per-value guess for printable text, Oracle `NUMBER`, and a valid seven-byte Oracle `DATE`. Guessed values are marked with a question mark, for example `Abel [TEXT?]`; unrecognized values remain raw hexadecimal. Explicit types supplied to `EXAMINE /r...` take precedence over the heuristic.

Example cluster key:

```text
rowdata[8166] @8166 0xac
flag@8166: 0xac
lock@8167: 0x0
cols@8168: 1
cluster key slots: 35 to 35
first rowid: 004000910000 [file: 1 block: 145 slot: 0]
last rowid: 004000910000 [file: 1 block: 145 slot: 0]
col 0[2] @8185: c111
```

Flag `0xac` identifies a cluster key. RICO2 reports the slot range owned by the key and its first and last physical ROWID.

A cluster member row, commonly marked with flag `0x6c`, additionally contains:

```text
cluster table@...: 1
```

The value identifies the table number within the cluster.

An unused directory slot is reported as:

```text
kdbr[12] is unused (-1)
```

If a row cannot be decoded safely, the parser preserves a parse error for verification instead of silently reading beyond the record boundary.

#### B-tree index structures

| Command | Output |
|---|---|
| `PRINT kdxle` | index leaf block header |
| `PRINT kdxbr` | index branch block header |
| `PRINT kd_off` | complete index entry offset table |
| `PRINT kd_off[n]` | offset for index entry `n` |
| `PRINT *kd_off[n]` | decoded leaf or branch entry `n` |
| `PRINT index_entries` | ordered list of logical leaf entries and their physical source |
| `PRINT *index_entry[n]` | decoded logical leaf entry `n`, including entries without a direct `kd_off` pointer |

RICO2 distinguishes leaf and branch blocks using the `kdxcolev` index level. Level `0` means leaf. A value greater than zero means branch.

Requesting an incompatible structure produces a diagnostic error instead of interpreting the block with the wrong layout.

The physical start of `kdxle` or `kdxbr` is not always immediately after the ITL array. RICO2 interprets the `ktbbhflg` extension flags and any variable extension length in the same way as BBED. Consequently, blocks with the same reported `ktbbh` size may place the index header at different offsets, for example `@92` or `@100`.

`kd_off` elements are signed 16-bit values. Initial directory slots may be metadata pointers, pad pointers, zero values, or negative sentinels rather than index records. Therefore, `kd_off[0]` is not guaranteed to be the first decodable entry. `PRINT *kd_off[n]` follows BBED's physical-pointer semantics: a pad pointer prints the addressed `pad`, a zero pointer prints the index header at the base offset, and a negative sentinel cannot be dereferenced. Use `PRINT kd_off` to inspect the physical directory. Use `PRINT index_entries` and `PRINT *index_entry[n]` when the goal is to inspect records in logical key order.

Leaf records are physically packed between `kdxcofeo` and the upper row-data boundary, but not every live record is necessarily referenced by a usable physical `kd_off` element. Such records can occur before or between referenced records. RICO2 walks the complete packed region, excludes purged records, reconstructs logical key order, and marks records without a direct pointer as `source=FEO gap`. The physical `kd_off` values remain visible exactly as stored, including pad, zero, metadata, and negative sentinel values.

Leaf entry layout also depends on `kdxledsz`. With `DSZ=6`, a six-byte physical ROWID precedes the key columns. With `DSZ=8`, the entry contains a six-byte ROWID plus a two-byte data suffix before the key. With `DSZ=0`, the ROWID is normally encoded as the final six-byte item in the `kdxconco` column list, as used by non-unique indexes. An `IOT - TOP` leaf also uses `DSZ=0`, but stores an embedded table row after the key. RICO2 decodes that payload as `iot row` and `iotcol` fields.

Example IOT top entry:

```text
logical index_entry[0] @8115
flag=0x4 lock=0x2
col 0[2] @8117: 4954
iot row flag=0x2c lock=0x0 ncols=2
iotcol 0[5] @8123: 4974616c79
iotcol 1[2] @8129: c102
```

Example leaf entry:

```text
rico2 > print *kd_off[2]
index entry kd_off[2] @4250
flag=0x0 lock=0x0
rowid=004000f20002 [file: 1 block: 242 slot: 2]
col 0[1] @4258: 80
col 1[3] @4260: 4a4f45
```

`rowid` points to a table row as `file`, `block`, and `slot`. Key columns are shown as raw hexadecimal bytes.

Example logical entry recovered from an FEO gap:

```text
rico2 > print *index_entry[106]
logical index_entry[106] @6850
source=FEO gap (no direct kd_off pointer)
flag=0x0 lock=0x0
rowid=030000940008 [file: 12 block: 148 slot: 8]
col 0[3] @6858: c20307
```

Example branch entry:

```text
rico2 > print *kd_off[2]
index entry kd_off[2] @8050
flag=0x0 lock=0x0
child dba=0x410feb [file: 1 block: 69611]
col 0[2] @8058: c102
```

`child dba` identifies the child index block selected by the separator key.

For the special leaf entry format, RICO2 deliberately avoids guessing its semantics:

```text
raw=...
special index entry format; semantic decoding is not available
```

### EXAMINE and X

Interprets bytes beginning at the current or supplied offset.

```text
EXAMINE /X|/C|/D|/U|/O|/r[types] [DBA f,b | FILE f BLOCK b]
        [OFFSET n] [COUNT n]
X       /X|/C|/D|/U|/O|/r<types> [...]
```

Formats:

| Format | Interpretation |
|---|---|
| `/X` | continuous hexadecimal byte string |
| `/C` | bytes decoded as Latin-1 characters |
| `/D` | signed decimal bytes from `-128` through `127` |
| `/U` | unsigned decimal bytes from `0` through `255` |
| `/O` | octal byte values |
| `/r` | columns of the most recently decoded row with inferred values |
| `/r<types>` | columns of the most recently decoded row with explicit types |

Examples:

```text
rico2 > x /x offset 0 count 8
0ba2000001004000

rico2 > x /u offset 0 count 4
11 162 0 0
```

Record format `/r` operates on the row selected by `PRINT *kdbr[n]`. Without a type suffix it uses the same conservative heuristic as `PRINT`. When types are supplied, each character describes one column:

| Character | Oracle type |
|---|---|
| `c` | text decoded as Latin-1 |
| `n` | Oracle `NUMBER` |
| `t` | Oracle `DATE` |

Example:

```text
rico2 > print *kdbr[0]
rico2 > x /rncc
```

The first column is decoded as `NUMBER`, and the next two as text. NULL values remain NULL. The number of type characters should match the row's column count.

A `COUNT` supplied directly to `EXAMINE` is temporary and does not replace the session-level `COUNT`.

### FIND

Searches for text or a hexadecimal byte sequence.

#### BBED-style syntax

```text
FIND /X <hex>
FIND /C <text>
```

Example:

```text
rico2 > find /x 4a4f45
Found at offset: 4260

Search finished.
```

`/X` requires a valid even-length hexadecimal sequence. `/C` encodes the search text as UTF-8. This syntax searches the current block. To search another block, first use `SET DBA` or use the extended `-f/-b` syntax.

All matching offsets in the block are printed.

#### Extended RICO2 syntax

```text
FIND -f <file> [-b <block>] -s <text>
FIND -f <file> [-b <block>] -h <hex>
FIND -f <file> -o <data_object_id>
FIND -f <file> -xo <xid>:<data_object_id>
FIND -f <file> -xo all:<data_object_id>
```

Options:

| Option | Meaning |
|---|---|
| `-f` | Oracle file number from the listfile |
| `-b` | one block; without it, the entire file is scanned |
| `-s` | UTF-8 encoded text |
| `-h` | hexadecimal byte sequence |
| `-o` | filter by `DATA_OBJECT_ID`, or list matching blocks when no search value is supplied |
| `-xo` | transaction ID combined with `DATA_OBJECT_ID` |

Do not combine `-s` and `-h`.

Examples:

```text
rico2 > find -f 7 -b 120 -s SCOTT
Found at offset: 8110

Search finished.
```

```text
rico2 > find -f 7 -o 84231
Found in block: 120 block type: KTB Data Block
Found in block: 121 block type: KTB Data Block
```

`XID` uses the raw transaction identifier assembled from undo segment, slot, and sequence fields in the little-endian order used by RICO2. `all:<objd>` reports every matching ITL transaction for the object.

Scanning a complete file may take a long time because RICO2 reads its blocks sequentially.

### SELECT

Performs an experimental equality search in decoded table rows.

```text
SELECT WHERE col<n>=<type>:<value>
```

The supported types match record-mode `EXAMINE`: `c`, `n`, and `t`.

Example:

```text
rico2 > print kdbr
rico2 > select where col0=c:SCOTT
Found at *kdbr[7]
```

No match:

```text
Nothing
```

`SELECT` is a historical RICO2 extension and is not part of BBED compatibility. It operates only on the decoded row collection of the current block. Use `PRINT *kdbr[n]` and `EXAMINE /r...` for more reliable inspection.

### MODIFY and ASSIGN

Changes the in-memory buffer beginning at the current `OFFSET`. Both commands have the same byte-level semantics in RICO2.

```text
MODIFY -H <hex>
MODIFY -S <text>
MODIFY /X <hex>
MODIFY /C <text>

ASSIGN -H <hex>
ASSIGN -S <text>
ASSIGN /X <hex>
ASSIGN /C <text>
```

Example:

```text
rico2 > set offset 100
rico2 > modify -h deadbeef
You want to modify block: 145 at offset: 100
New value: deadbeef
Are you sure? (Y/N)  y
Block data changed. To save changes set edit mode and type: save
```

After confirmation, the change exists only in memory and `SHOW DIRTY` returns `Yes`. Text is encoded as UTF-8. A modification cannot extend beyond the end of the block.

If the answer is not `Y`, the buffer is unchanged.

> [!IMPORTANT]
> `MODIFY` may change the in-memory buffer while the session is still in `BROWSE` mode. Write mode is checked by disk-writing commands. Use `UNDO` to discard the in-memory change, or use `SET MODE EDIT` followed by `SAVE` to persist it.

### SUM and CHECKSUM

Calculates the Oracle XOR checksum of the current block buffer.

```text
SUM
CHECKSUM
SUM APPLY
CHECKSUM APPLY
```

Example output:

```text
checksum int = 27842
checksum hex = 0x6cc2
```

`SUM APPLY` writes the calculated value into checksum bytes 16 and 17 of the in-memory buffer and marks it dirty. It does not save the file.

`SUM APPLY` requires `SET MODE EDIT`. Use `SAVE` afterward if the checksum update is intended to reach disk.

Output after applying:

```text
Block data changed. To save changes set edit mode and type: save
```

### VERIFY

Checks the current or supplied block for structural consistency without writing it.

```text
VERIFY
VERIFY DBA <file>,<block>
VERIFY FILE <file> BLOCK <block>
```

Checks include:

- in-memory block length;
- agreement between the selected block number and the DBA in `kcbh`; for PDB files, `kcbh` may contain a relative file number while the listfile uses the absolute CDB file number;
- checksum correctness when the block flags require checksum validation;
- `kdbr`, row offset, and record boundaries for table and cluster blocks;
- `kd_off`, entry, and column boundaries for leaf and branch index blocks;
- expected index column counts where semantic decoding is available;
- parser errors recorded for cluster and table rows.

Successful verification:

```text
checksum int = 27842
checksum hex = 0x6cc2
Verification passed for File 1, Block 145
```

Failed verification:

```text
checksum int = 1234
checksum hex = 0x4d2
Verification failed:
  - header DBA is 0x400090, expected 0x400091
  - index entry 3: entry points outside the index row area
```

`VERIFY` does not repair a block. Do not save a failed block until every reported problem is understood.

### UNDO

Restores the in-memory backup snapshot created when the block was loaded and clears `DIRTY`.

```text
UNDO
```

Output:

```text
In-memory changes discarded.
```

`UNDO` does not write the file. After `SAVE`, the backup may contain the disk image captured immediately before that save, but restoring it in memory is still not the same as restoring it on disk.

### SAVE

Writes the current buffer to its current `FILE`,`BLOCK` location.

```text
SET MODE EDIT
SAVE
```

Output:

```text
Current block data successfully saved to disk. To revert changes, type: dupa
```

Before writing, RICO2 reads the previous disk contents into one in-memory backup snapshot. This is not a persistent journal. Selecting another block or terminating the process may replace or lose the snapshot.

After a successful save, `DIRTY` becomes `No`.

### REVERT and DUPA

Writes the most recent in-memory backup snapshot to the current `FILE`,`BLOCK` location.

```text
SET MODE EDIT
REVERT
DUPA
```

Output:

```text
Backup of block data successfully saved to disk.
```

`DUPA` is the historical alias for `REVERT`. The command does not request confirmation and does not use a persistent BIFILE journal.

Before using it, make sure the current location is the same location for which the backup snapshot was created. RICO2 does not attach persistent source-location metadata to the backup.

### COPY

Copies the complete current buffer to another block and immediately writes the target.

```text
SET MODE EDIT
COPY TO DBA <file>,<block>
COPY FILE <file> BLOCK <block>
```

Example:

```text
rico2 > copy to dba 7,50
Copied File 1, Block 145 to File 7, Block 50
```

The target file must exist in the listfile, and the target block must be within the file. The entire `BLOCKSIZE` bytes are copied.

> [!WARNING]
> `COPY` does not request confirmation. It also copies the source header, including its recorded DBA. The target may therefore fail `VERIFY` immediately because the header still identifies the source block. The previous target contents are not stored in a persistent journal.

## Supported block structures

### Common headers

- `kcbh` — block type and format, DBA, SCN, sequence, flags, and checksum;
- `ktbbh` — KTB transaction header, data object information, and ITL entries;
- `tailchk` — final four-byte block check value.

### Table and cluster blocks

RICO2 recognizes KTB data blocks of type 6 with the table or cluster subtype. It decodes:

- `kdbh` — row data header, table count, row count, and data area boundaries;
- `kdbt` — table directories stored in the block;
- `kdbr` — two-byte relative row offsets;
- ordinary heap rows;
- cluster keys, commonly flagged `0xac`, with slot ranges and ROWIDs;
- cluster member rows, commonly flagged `0x6c`, with their cluster table number.

The relationship between the structures is:

```text
kcbh
  -> ktbbh and ITL
    -> kdbh
      -> kdbt table directories
      -> kdbr row pointers
        -> rowdata records
```

### B-tree index blocks

RICO2 recognizes type 6 blocks with the index subtype and distinguishes:

- `kdxle` — level 0 leaf blocks containing table ROWIDs;
- `kdxbr` — branch blocks containing child DBAs;
- `kd_off` — an array of two-byte index entry offsets;
- key column lists, including NULL markers;
- special leaf entries exposed as safe raw output.

The principal relationship is:

```text
kd_off[n] -> index entry -> key columns + table ROWID (leaf)
kd_off[n] -> index entry -> key columns + child DBA (branch)
```

RICO2 validates that offset tables, free space, row data, and entry boundaries remain inside the block.

### Oracle values

Raw column bytes are always displayed in hexadecimal. `PRINT` and bare `EXAMINE /r` heuristically append a value when the bytes form unambiguous printable text, Oracle `NUMBER`, or a valid seven-byte `DATE`. The `?` in labels such as `[NUMBER?]` makes clear that no data dictionary was consulted. Record-mode `EXAMINE` also accepts explicit decoders for:

- character data through type `c`;
- Oracle internal `NUMBER` through type `n`;
- Oracle internal seven-byte `DATE` through type `t`;
- NULL markers represented as `*NULL*`.

RICO2 does not infer a table definition from the data dictionary. The heuristic is best-effort and leaves unknown binary values untouched; use `/r<types>` whenever the expected definition is known.

### DBA and ROWID output

A DBA is shown as a hexadecimal address and a decoded file and block pair:

```text
0x410feb [file: 1 block: 69611]
```

An index leaf entry or cluster structure may show a physical ROWID as raw bytes plus decoded components:

```text
004000f20002 [file: 1 block: 242 slot: 2]
```

The slot identifies the row directory entry within the referenced table block.

## Common workflows

### Inspecting a heap table block

```text
set ibase dec
set dba 7,120
show
map
print kcbh
print ktbbh
print kdbh
print kdbr
print *kdbr[0]
x /rncc
verify
```

Use `PRINT kdbr` to discover available slots and `PRINT *kdbr[n]` to inspect a specific row.

### Inspecting a cluster block

```text
set ibase dec
set dba 1,145
map
print kdbh
print kdbt
print kdbr
print *kdbr[0]
print *kdbr[35]
verify
```

Look for these fields:

- `cluster key slots`;
- `first rowid` and `last rowid`;
- `cluster table` on member rows;
- row flags such as `0xac` and `0x6c`.

### Inspecting an index leaf block

```text
set ibase dec
set dba 1,69610
map
print kcbh
print ktbbh
print kdxle
print kd_off
print *kd_off[0]
verify
```

Each decoded leaf entry should contain a physical table ROWID and its key columns.

### Inspecting an index branch block

```text
set dba 1,69611
map
print kdxbr
print kd_off
print *kd_off[0]
verify
```

Each decoded branch entry should contain a child DBA and separator key columns.

If `MAP` reports `Index Branch`, use `PRINT kdxbr`. If it reports `Index Leaf`, use `PRINT kdxle`.

### Searching a data file

```text
find -f 7 -s "SCOTT"
find -f 7 -h 53434f5454
find -f 7 -o 84231
find -f 7 -xo all:84231
```

When scanning a large file, restrict the operation by `DATA_OBJECT_ID` whenever that value is known.

### Inspecting bytes at a known offset

```text
set dba 7,120
set offset 96
set count 64
dump
x /x count 16
x /u count 16
```

Remember that a temporary `COUNT` passed to `EXAMINE` does not alter the session value, while `SET COUNT` does.

### Controlled laboratory block modification

A minimal recommended sequence in an isolated environment:

```text
set ibase dec
set dba 7,120
verify
dump offset 96 count 32
set offset 100
modify -h deadbeef
show dirty
dump offset 96 count 32
verify
set mode edit
sum apply
verify
save
verify
```

Create an independent filesystem-level backup before `SAVE`. `UNDO` discards a modification before it is written. `REVERT` is only a one-level snapshot held in the current process and is not a replacement for a real backup.

### Abandoning an in-memory modification

```text
set offset 100
modify -h deadbeef
show dirty
undo
show dirty
```

Expected state transition:

```text
DIRTY           Yes
In-memory changes discarded.
DIRTY           No
```

## Errors and troubleshooting

Interactive command failures are printed with a `Command failed:` prefix. Common situations include:

| Message or symptom | Meaning and action |
|---|---|
| `No block selected` | select one with `SET DBA`, `SET FILE/BLOCK`, or a command location |
| `Unknown file number` | verify the requested `FILE` with `INFO` |
| block outside file or truncated | compare the block number with `Size(blks)` from `INFO` |
| unsupported or incompatible structure | run `MAP`; use `kdbh/kdbr` for tables or `kdx*/kd_off` for indexes |
| `is unused (-1)` | the directory slot exists but does not point to an active row |
| invalid hexadecimal input | supply an even number of `0-9a-f` characters without separators |
| write operation requires edit mode | run `SET MODE EDIT` only after confirming the source and target |
| `DIRTY Yes` | the buffer differs from its snapshot; use `UNDO` or intentionally `SAVE` |
| `Verification failed` | DBA, checksum, boundaries, offsets, or decoded structures are inconsistent |
| special index entry format | only `raw=...` is available; semantic decoding is not implemented |
| `kd_off[n]` prints `pad`, the index header, or reports a negative sentinel | the directory slot is metadata, not an index record; this is the same physical dereference behavior as BBED; use `PRINT index_entries` and `PRINT *index_entry[n]` for logical leaf records |

If output appears incorrect:

1. Run `SHOW` and verify `FILE`, `BLOCK`, `BLOCKSIZE`, `IBASE`, and `OFFSET`.
2. Inspect the first bytes with `DUMP OFFSET 0 COUNT 128`.
3. Run `MAP` and confirm that the detected block type is plausible.
4. Run `VERIFY`.
5. Compare the selected `DBA` with `rdba_kcbh` from `PRINT kcbh`.
6. Do not enter `EDIT` mode until the discrepancy is understood.

### No block selected

Example:

```text
rico2 > map
Command failed: No block selected. Use: set dba <file>,<block>
```

Resolution:

```text
rico2 > info
rico2 > set dba 1,145
rico2 > map
```

### Wrong block type

If an index structure is requested from a table block, or a table structure from an index block, RICO2 rejects the operation. Use:

```text
map
print kcbh
```

Then select the appropriate structure family.

### Checksum mismatch

`VERIFY` checks a mismatch only when the relevant block flag indicates that checksum verification applies. A mismatch may mean:

- the block was modified without recalculating its checksum;
- the block was copied from another source;
- the wrong block size was selected;
- the file is corrupt or the read was not from the expected data file.

Do not use `SUM APPLY` merely to silence the warning. First establish why the current checksum differs.

### DBA mismatch after COPY

`COPY` duplicates all bytes, including `rdba_kcbh`. If a source block is copied to a different target DBA, `VERIFY` can report:

```text
header DBA is 0x400091, expected 0x1c00032
```

This is expected for a raw copy to another location. Correcting the header requires expert knowledge and may still leave references, redo expectations, checksums, and higher-level structures inconsistent.

## BBED compatibility

| Area | Status | Notes |
|---|---|---|
| `INFO`, `SHOW`, `SET` | supported | session model with FILE, BLOCK, OFFSET, and DBA |
| `DUMP` / `D` | supported | location, OFFSET, COUNT, and accepted `/V` |
| `MAP` | supported | table, cluster, index leaf, and index branch layouts |
| `PRINT` / `P` | supported subset | selected Oracle structures and directory dereferences |
| `EXAMINE` / `X` | supported | `/X`, `/C`, `/D`, `/U`, `/O`, and record `/r` |
| `FIND /X`, `FIND /C` | supported | searches the current block |
| `MODIFY` | supported | byte changes in the in-memory buffer |
| `ASSIGN` | partial | alias for `MODIFY`; no typed BBED field assignment |
| `SUM` / `CHECKSUM` | supported | calculation and `APPLY` |
| `VERIFY` | supported | additional semantic checks for tables, clusters, and indexes |
| `COPY` | supported | complete block copy without persistent journal or confirmation |
| `UNDO` | supported | current in-memory snapshot only |
| `REVERT` / `DUPA` | supported | one-level snapshot write-back |
| BIFILE and multi-level recovery | not supported | use independent data file backups |
| complete BBED typed structure language | not supported | RICO2 exposes a limited set of Oracle structures |

RICO2 also contains extensions that are not present in classic BBED:

- full-file search by `DATA_OBJECT_ID`;
- XID filtering through `-xo`;
- experimental `SELECT`;
- `SET MANUALOFFSET`;
- semantic verification of index entries and cluster records;
- explicit B-tree leaf and branch decoders.

BBED behavior used by this project was compared with the BBED binary and inspected through static analysis in Ghidra.

## Tests

Run the unit test suite with:

```bash
python3 -m unittest -v test_rico2.py
```

The tests cover:

- command parsing and session parameters;
- block selection and location handling;
- dumps and examination offsets;
- checksum calculation and edit-mode enforcement;
- table and index block verification;
- B-tree leaf headers, offset arrays, and entries;
- B-tree branch child pointers and separator keys;
- special index leaf entries exposed as raw data;
- cluster keys and cluster member rows;
- modification and in-memory undo behavior;
- full-block copy safeguards.

Read-only validation against real Oracle data files has covered:

- ordinary index segments;
- cluster data segments;
- cluster index segments.

Automated tests do not perform write operations against real Oracle data files.

## License and disclaimer

RICO2 source code is licensed under the Apache License, Version 2.0, as stated in the source file header.

RICO2 is a research, diagnostic, and educational tool. The authors and contributors are not responsible for data loss, database inconsistency, or any consequence of using it against a production or otherwise valuable system.

Before every write operation:

1. shut down or isolate the Oracle instance as appropriate;
2. create a verified external backup of the affected data file;
3. record the source file, block, offset, and original bytes;
4. test the complete change and recovery procedure on a disposable copy;
5. verify the block before and after every modification.
