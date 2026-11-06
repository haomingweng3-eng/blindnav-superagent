# Harvard FillGrid parity replay

The reference adapter was changed to write both walkable and non-walkable
ground cells, matching the upstream `Vision.cs` boolean grid semantics. The
same 481 SANPO-Real frame keys were replayed before and after the change.

| condition | version | directions | UNKNOWN | any-danger miss | false stop |
|---|---|---:|---:|---:|---:|
| Harvard A | before FillGrid parity | 8/481 | 168/481 | 0/295 | 46/150 |
| Harvard A | after FillGrid parity | 4/481 | 172/481 | 0/295 | 46/150 |
| Harvard D | before FillGrid parity | 71/481 | 347/481 | 39/295 | 21/150 |
| Harvard D | after FillGrid parity | 13/481 | 405/481 | 9/295 | 21/150 |

The change improved hazard blocking in D (39 to 9 missed danger frames), but
did not meet the direction-coverage target. It made the reference arm more
conservative because false ground cells now terminate rays instead of remaining
unknown. This is a correctness result, not a deployment result.

The corrected replay uses the frozen 481 SANPO frames and SANPO recorded pose
as an offline VIO proxy. It does not validate Android RGB+IMU VIO.
