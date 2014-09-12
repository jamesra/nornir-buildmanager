title Assemble
nornir-build Assemble -volume %1 -Filters LeveledShadingCorrected -Downsample 2 -NoInterlace -Transform Grid
title CreateBlobFilter
nornir-build CreateBlobFilter -volume %1  -InputFilter LeveledShadingCorrected -OutputFilter Blob -Levels 2,4,8 -Radius 7 -Median 5 -Max 3
title AlignSections
nornir-build AlignSections -volume %1  -Downsample 8 -Filters Blob -OutputStosMap PotentialRegistrationChain
title AssembleStosOverlays
nornir-build AssembleStosOverlays -volume %1 -StosGroup StosBrute -Downsample 8 -StosMap PotentialRegistrationChain
title SelectBestRegistrationChain
nornir-build SelectBestRegistrationChain -volume %1 -StosGroup StosBrute -Downsample 8 -InputStosMap PotentialRegistrationChain -OutputStosMap FinalStosMap
title RefineSectionAlignment
nornir-build RefineSectionAlignment -volume %1  -Filters LeveledShadingCorrected -InputGroup StosBrute -InputDownsample 8 -OutputGroup Grid -OutputDownsample 8
title RefineSectionAlignment
nornir-build RefineSectionAlignment -volume %1  -Filters LeveledShadingCorrected -InputGroup Grid -InputDownsample 8 -OutputGroup Grid -OutputDownsample 2
title ScaleVolumeTransforms
nornir-build ScaleVolumeTransforms -volume %1  -InputGroup Grid -InputDownsample 2 -OutputDownsample 1
title SliceToVolume
nornir-build SliceToVolume -volume %1  -InputDownsample 1 -InputGroup Grid -OutputGroup SliceToVolume
title MosaicToVolume
nornir-build MosaicToVolume -volume %1  -InputTransform Grid -OutputTransform ChannelToVolume
title Assemble
nornir-build Assemble -volume %1  -ChannelPrefix Registered_ -Filter ShadingCorrected -Downsample 1 -NoInterlace -Transform ChannelToVolume -Channels (?!Registered)
title ExportImages
nornir-build ExportImages -volume %1  -Channels Registered -Filters ShadingCorrected -Downsample 1 -Output %1_Registered
