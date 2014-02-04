nornir-build -volume %1 -pipeline Assemble -Filter LeveledShadingCorrected -AssembleDownsample 2 -NoInterlace -Transform Grid 
nornir-build -volume %1 -pipeline CreateBlobFilter -InputFilter LeveledShadingCorrected -OutputFilter Blob -Levels 2,4,8
nornir-build -volume %1 -pipeline AlignSections -Downsample 8 -Filters Blob
nornir-build -volume %1 -pipeline RefineSectionAlignment -Filters LeveledShadingCorrected -InputGroup StosBrute -InputDownsample 8 -OutputGroup Grid -OutputDownsample 8
nornir-build -volume %1 -pipeline RefineSectionAlignment -Filters LeveledShadingCorrected -InputGroup Grid -AlignFilters ShadingCorrected -InputDownsample 8 -OutputGroup Grid -OutputDownsample 2
nornir-build -volume %1 -pipeline ScaleVolumeTransforms -InputGroup Grid -InputDownsample 2 -OutputDownsample 1
nornir-build -volume %1 -pipeline SliceToVolume -InputDownsample 1 -InputGroup Grid -OutputGroup SliceToVolume
nornir-build -volume %1 -pipeline MosaicToVolume -InputTransform Grid -OutputTransform ChannelToVolume
nornir-build -volume %1 -pipeline Assemble -Filter ShadingCorrected -AssembleDownsample 1 -Output %1_Registered -NoInterlace -Transform ChannelToVolume
