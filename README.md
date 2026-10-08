# nornir-buildmanager

Constructs 2D and 3D datasets from 2D image mosaics using the Nornir tools.

## Quick start

- CLI entrypoint: `nornir-build`
- Typical import: `nornir-build ImportIDoc /data/volume /data/idoc`
- Typical preparation: `nornir-build Prune /data/volume -InputFilter Raw8 -DefaultThreshold 0.2`
- Typical registration: `nornir-build Mosaic /data/volume -InputFilter Raw8 -InputTransform Prune`

### VolumeData.xml locations (ImportIDoc)

`VolumeData.xml` is hierarchical, not a single file at the volume root:

| Path | What it records |
|------|-----------------|
| `<volume>/VolumeData.xml` | Volume + `Block_Link` (written when the TEM block is created / Volume is dirty) |
| `<volume>/TEM/VolumeData.xml` | Block + `Section_Link` list (written when Block membership/attribs are dirty, e.g. new section) |
| `<volume>/TEM/<section>/VolumeData.xml` | Section container (when Section is dirty) |
| `<volume>/TEM/<section>/TEM/VolumeData.xml` | Channel / transform / filter metadata (when those containers are dirty) |

ImportIDoc uses `yield from` ToMosaic so `_SaveNodes` can write after each meta-data unit (Volume/Block/Section/Channel/Filter), matching MRC/DM4 — not only after the entire idoc finishes. Each **dirty** linked container rewrites its own `VolumeData.xml`. Crash mid-import leaves prior structure recoverable when those nodes were already yielded.

### VolumeData dirty / save ownership

Contract for pipeline stages and volumemanager getters:

| Rule | Behavior |
|------|----------|
| Who creates children | Setters / `GetOrCreate*` / `UpdateOrAddChild*` — **not** ordinary getters |
| Getters | Prefer find-or-empty (e.g. `BlockNode.NonStosSectionNumbers` returns `frozenset()` if missing; does not create) |
| Dirty flags | `_AttributesChanged` / `_ChildrenChanged` on the mutated node; linked children do **not** bubble dirtiness to parents |
| Stage return value | Yield / return the node that should be saved (often Block or Volume after membership changes) |
| `_SaveNodes` | If the object is an `ElementTree.Element` (including `XElementWrapper`), save **that node**, do not iterate its children. Generators/iterables of nodes are saved one-by-one; `None` entries skipped |

Regression coverage: `tests/test_import_volumedata_save.py`.

### Environment variables (XML-to-SQLite metadata port)

All default to off; `VolumeData.xml` stays the source of truth. Read only in `nornir_buildmanager/metadata/feature_flags.py`.

| Variable | Effect when `1` / `true` / `yes` / `on` |
|----------|------------------------------------------|
| `NORNIR_VOLUME_METADATA_SHADOW_SQLITE` | After each successful container `VolumeData.xml` save, upsert that container into `VolumeData.db` at the volume root and compare it with the saved file. Errors and mismatches are logged as warnings naming the container; the XML save never fails because of them. XML bytes are unchanged. |

`VolumeData.db` connections (shadow write and `nornir-migrate-volume`) use the WAL journal only on a local disk. On a network share (`cifs`, `smb3`, `nfs`, `9p`, `drvfs`, `virtiofs`, any `fuse` mount, a UNC `\\server\share` path, or a Windows mapped network drive), or when the filesystem cannot be identified, they use the rollback journal (`DELETE`), because WAL's shared-memory file is unsafe across machines. In the dev container `/workspace` and other host folders are `9p`/`virtiofs` mounts, so a volume there gets `DELETE`. The choice is logged at INFO once per database (`nornir_buildmanager.metadata.sqlite_journal`). Connections wait up to 30 s for another process's lock before failing with `database is locked`.

Every write to `VolumeData.db` (a shadow container upsert, a `nornir-migrate-volume` save) also holds an exclusive lock on `VolumeData.db.lock` beside it (`flock` on Linux/macOS, `msvcrt.locking` on Windows), so writers in different processes or threads take turns; a whole-tree save is one transaction, so a failed save leaves the previous tree. A save writes only the rows that differ from what is stored (unchanged nodes keep their row ids), and a container upsert leaves the rows of the containers it links to alone. A writer waits up to 30 s for the lock; a shadow write that times out logs a warning and the XML save still succeeds. The `.lock` file is left in place and is safe to delete when nothing is writing. Readers do not take it. A Windows process and a dev container writing the same share may not see each other's lock.

Test-only: `tests/test_metadata_sqlite_concurrency.py` runs several writer processes through the shadow write at once. Its network-share test runs only when `NORNIR_VOLUME_METADATA_NET_TEST_PATH` names a writable folder on a network share (in the dev container, a share mounted with `NORNIR_NET_MOUNTS=1`, for example `/storage4`); it creates a `nornir-metadata-net-test-*` subfolder there and deletes it afterwards. Run it on the Windows host against the same share to cover the `msvcrt.locking` path.

## Documentation

- **Full manual and API (umbrella):** [https://nornir.github.io/](https://nornir.github.io/)
- **This package:** [Packages — nornir-buildmanager](https://nornir.github.io/packages/nornir_buildmanager.html)
- **API reference:** [`nornir_buildmanager` module](https://nornir.github.io/api/nornir_buildmanager.html)
- **VikingXML Version 2:** nested `Sections` / `StosGroup` layout — see the package page above

## Dashboard progress tracks (TEMBuild / TEMAlign)

TEM scripts publish MQTT `iterate_progress` events consumed by `nornir-dashboard`.
In addition to automatic PipelineManager `<Iterate>` tracks (`iterate:{VariableName}`),
these stage-specific `track_id`s are emitted:

| Track id | Stage |
|----------|-------|
| `import_idoc:sections` | ImportIDoc (per `.idoc` / section unit) |
| `import_idoc:tiles` | ImportIDoc tile convert (nested, depth 1) |
| `stos_overlays:jobs` | AssembleStosOverlays |
| `stos_chain:mappings` | SelectBestRegistrationChain |
| `stos_refine:files` | RefineSectionAlignment |
| `stos_brute:pairs` | AlignSections / StosBrute (nested filter pairs, depth 1) |
| `slice_to_volume:sections` | SliceToVolume (per registration-tree mapping step) |
| `scale:{group}` / `blend:{group}` | ScaleVolumeTransforms / LinearizeVolume |
| `mosaic_to_volume:sections` | MosaicToVolume (per matching channel) |
| `assemble:rows` | Assemble pyramid row build (depth 1) |
| `vikingxml:sections` / `vikingxml:channels` / `vikingxml:stos:*` | CreateVikingXML (section + nested channel + stos) |

### TEMAlign expected bars (`TEMAlign.sh`)

| Pipeline stage | Expected tracks |
|----------------|-----------------|
| CreateBlobFilter | `iterate:SectionNode`, `iterate:ChannelNode` |
| AlignSections | `iterate:mapping_node` + `stos_brute:pairs` |
| AssembleStosOverlays | `stos_overlays:jobs` |
| SelectBestRegistrationChain | `stos_chain:mappings` |
| RefineSectionAlignment | `iterate:MappingNodeObj` + `stos_refine:files` (+ refine pass tracks) |
| CreateVikingXML | `vikingxml:sections`, `vikingxml:channels`, `vikingxml:stos:*` |
| SliceToVolume | `slice_to_volume:sections` (+ block/map/group Iterate) |
| ScaleVolumeTransforms / LinearizeVolume | `scale:*` / `blend:*` |
| MosaicToVolume | `mosaic_to_volume:sections` |
| Assemble | section Iterate + `assemble:rows` |
| MosaicReport | `CurseProgress` op track |
| ExportImages | `iterate:ChannelNode`, `iterate:FilterNodeObj` |

Full stage-by-stage audit: [`docs/temalign_progress.md`](docs/temalign_progress.md).

Use `nornir_buildmanager.progress.report_iterate` for new stage tracks.
