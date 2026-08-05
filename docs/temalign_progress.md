# TEMAlign progress audit

Stage-by-stage map of dashboard `iterate_progress` tracks for the chain in
`scripts/TEMAlign.sh` (TEM Full Alignment). Auto tracks come from PipelineManager
`<Iterate VariableName="…">` as `iterate:{VariableName}`. Explicit tracks use
`nornir_buildmanager.progress.report_iterate` or `publish_run_event`.

| Stage | Auto Iterate tracks | Explicit tracks | Advances during long work? | Notes |
|-------|---------------------|-----------------|----------------------------|-------|
| CreateBlobFilter | `iterate:SectionNode`, `iterate:ChannelNode` | — | Yes (per section/channel) | Blob work is inside the PythonCall; section bar moves between sections. |
| AlignSections (CreateOrUpdateSectionToSectionMapping) | — | — | Stage events only | Usually fast; no nested track. |
| AlignSections (StosBrute) | `iterate:mapping_node` | `stos_brute:pairs` (depth 1) | Yes | Outer = mappings; nested = mapped×control filter pairs. |
| AssembleStosOverlays (×2) | — | `stos_overlays:jobs` | Yes | Pre-counted transform jobs; skips still increment. |
| SelectBestRegistrationChain | — | `stos_chain:mappings` | Yes | `finally` bumps on early continue. |
| RefineSectionAlignment (×2) | `iterate:MappingNodeObj` | `stos_refine:files` + refine pass/locked | Yes | File loop + nested refine reporter. |
| CreateVikingXML (×2) | — | `vikingxml:sections`, `vikingxml:stos:*` | Yes | |
| SliceToVolume | `iterate:BlockNode`, `iterate:StosMapNode`, `iterate:StosGroupNode` | `slice_to_volume:sections` | Yes | Explicit track counts registration-tree mapping steps. |
| ScaleVolumeTransforms | (pipeline Iterate if present) | `scale:{group}` | Yes | |
| LinearizeVolume | (pipeline Iterate if present) | `blend:{group}` | Yes | |
| MosaicToVolume | `iterate:BlockNode` | `mosaic_to_volume:sections` | Yes | Per matching channel; missing transform still increments. |
| Assemble | section/channel/filter Iterate | `assemble:rows` (depth 1) | Yes | |
| MosaicReport | — | `CurseProgress` → `op:*` | Yes (row adds) | Stage events + op track. |
| ExportImages | `iterate:ChannelNode`, `iterate:FilterNodeObj` | — | Yes | |

## Silent stretches (accepted)

- **CreateOrUpdateSectionToSectionMapping** — typically seconds; stage_start/end only.
- **Per-angle FFT inside one StosBrute pair** — intentionally not tracked (MQTT volume); pair-level `stos_brute:pairs` is the nested bar.
- **MosaicReport / ExportImages** — already covered by CurseProgress or Iterate; no further change unless a multi-minute stall is observed.

## Related: ImportIDoc VolumeData.xml

TEMImport (`ImportIDoc`) writes hierarchical `VolumeData.xml` files. After each idoc,
`_SaveNodes` runs `Save` on the yielded volume/block and walks the subtree; each **dirty**
linked container rewrites its own file (Block XML when section membership changed).
See the buildmanager README “VolumeData.xml locations” section. Save logs use
prettyoutput with the container directory (`FullPath`).

