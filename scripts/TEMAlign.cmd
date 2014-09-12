title CreateBlobFilter
nornir-build CreateBlobFilter -volume %1  -Channels TEM -InputFilter Leveled -Levels 16,32 -OutputFilter Blob -Radius 9 -Median 7 -Max 3
title AlignSections
nornir-build AlignSections -volume %1 -NumAdjacentSections 1 -Filters Blob -StosUseMasks True -Downsample 32 -Channels TEM
title AssembleStosOverlays
nornir-build AssembleStosOverlays -volume %1 -StosGroup StosBrute -Downsample 32 -StosMap PotentialRegistrationChain
title SelectBestRegistrationChain
nornir-build SelectBestRegistrationChain -volume %1 -StosGroup StosBrute -Downsample 32 -InputStosMap PotentialRegistrationChain -OutputStosMap FinalStosMap
title RefineSectionAlignment
nornir-build RefineSectionAlignment -volume %1 -InputGroup StosBrute -InputDownsample 32 -OutputGroup Grid -OutputDownsample 32 -Filter Leveled -StosUseMasks True
title AssembleStosOverlays
nornir-build AssembleStosOverlays -volume %1 -StosGroup Grid -Downsample 32 -StosMap FinalStosMap
title CreateVikingXML
nornir-build CreateVikingXML -volume %1 -StosGroup Grid32 -StosMap FinalStosMap -OutputFile Grid32
title RefineSectionAlignment
nornir-build RefineSectionAlignment -volume %1 -InputGroup Grid -InputDownsample 32 -OutputGroup Grid -OutputDownsample 16 -Filter Leveled -StosUseMasks True
title ScaleVolumeTransforms
nornir-build ScaleVolumeTransforms -volume %1 -InputGroup Grid -InputDownsample 16 -OutputDownsample 1
title SliceToVolume
nornir-build SliceToVolume -volume %1 -InputDownsample 1 -InputGroup Grid -OutputGroup SliceToVolume
title CreateVikingXML
nornir-build CreateVikingXML -volume %1 -OutputFile SliceToVolume -StosGroup SliceToVolume1 -StosMap SliceToVolume
title MosaicToVolume
nornir-build MosaicToVolume -volume %1 -InputTransform Grid -OutputTransform ChannelToVolume -Channels TEM
title Assemble
nornir-build Assemble -volume %1 -Channels (?!Registered) -Filters Leveled -Downsample 32 -NoInterlace -Transform ChannelToVolume -ChannelPrefix Registered_
title MosaicReport
nornir-build MosaicReport -volume %1 -PruneFilter Raw8 -ContrastFilter Raw8 -AssembleFilter Leveled -AssembleDownsample 32 -Output VolumeReport
title ExportImages
nornir-build ExportImages -volume %1 -Channels Registered -Filters Leveled -Downsample 32 -Output %1\Registered