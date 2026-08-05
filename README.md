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

## Documentation

- **Full manual and API (umbrella):** [https://nornir.github.io/](https://nornir.github.io/)
- **This package:** [Packages — nornir-buildmanager](https://nornir.github.io/packages/nornir_buildmanager.html)
- **API reference:** [`nornir_buildmanager` module](https://nornir.github.io/api/nornir_buildmanager.html)

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
| `vikingxml:sections` / `vikingxml:stos:*` | CreateVikingXML |

### TEMAlign expected bars (`TEMAlign.sh`)

| Pipeline stage | Expected tracks |
|----------------|-----------------|
| CreateBlobFilter | `iterate:SectionNode`, `iterate:ChannelNode` |
| AlignSections | `iterate:mapping_node` + `stos_brute:pairs` |
| AssembleStosOverlays | `stos_overlays:jobs` |
| SelectBestRegistrationChain | `stos_chain:mappings` |
| RefineSectionAlignment | `iterate:MappingNodeObj` + `stos_refine:files` (+ refine pass tracks) |
| CreateVikingXML | `vikingxml:*` |
| SliceToVolume | `slice_to_volume:sections` (+ block/map/group Iterate) |
| ScaleVolumeTransforms / LinearizeVolume | `scale:*` / `blend:*` |
| MosaicToVolume | `mosaic_to_volume:sections` |
| Assemble | section Iterate + `assemble:rows` |
| MosaicReport | `CurseProgress` op track |
| ExportImages | `iterate:ChannelNode`, `iterate:FilterNodeObj` |

Full stage-by-stage audit: [`docs/temalign_progress.md`](docs/temalign_progress.md).

Use `nornir_buildmanager.progress.report_iterate` for new stage tracks.
