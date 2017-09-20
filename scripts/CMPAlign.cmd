title Assemble
nornir-build %1 Assemble -Filters LeveledShadingCorrected -Downsample 2 -NoInterlace -Transform Grid
title CreateBlobFilter
nornir-build %1 CreateBlobFilter -InputFilter LeveledShadingCorrected -OutputFilter Blob -Levels 2,4,8 -Radius 7 -Median 5 -Max 3
title AlignSections
nornir-build %1 AlignSections -Downsample 8 -Filters Blob -OutputStosMap PotentialRegistrationChain -UseMasks
title AssembleStosOverlays
nornir-build %1 AssembleStosOverlays  -StosGroup StosBrute -Downsample 8 -StosMap PotentialRegistrationChain
title SelectBestRegistrationChain
nornir-build %1 SelectBestRegistrationChain -StosGroup StosBrute -Downsample 8 -InputStosMap PotentialRegistrationChain -OutputStosMap FinalStosMap
title RefineSectionAlignment
nornir-build %1 RefineSectionAlignment -Filters LeveledShadingCorrected -InputGroup StosBrute -InputDownsample 8 -OutputGroup Grid -OutputDownsample 8 -UseMasks
title RefineSectionAlignment
nornir-build %1 RefineSectionAlignment -Filters LeveledShadingCorrected -InputGroup Grid -InputDownsample 8 -OutputGroup Grid -OutputDownsample 2 -UseMasks
title SliceToVolume
nornir-build %1 SliceToVolume -Downsample 2 -InputGroup Grid -OutputGroup SliceToVolume
title ScaleVolumeTransforms
nornir-build %1 ScaleVolumeTransforms -InputGroup SliceToVolume -InputDownsample 2 -OutputDownsample 1
title MosaicToVolume
nornir-build %1 MosaicToVolume -InputTransform Grid -OutputTransform ChannelToVolume
title Assemble
nornir-build %1 Assemble -ChannelPrefix Registered_ -Filter ShadingCorrected -Downsample 1 -NoInterlace -Transform ChannelToVolume -Channels (?!Registered)
title ExportImages
nornir-build %1 ExportImages -Channels Registered -Filters ShadingCorrected -Downsample 1 -Output %1_Registered
