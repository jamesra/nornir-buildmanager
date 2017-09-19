title CreateBlobFilter
nornir-build %1 CreateBlobFilter -Channels TEM -InputFilter Leveled -Levels 16,32,64 -OutputFilter Blob -Radius 9 -Median 7 -Max 3
title AlignSections
nornir-build %1 AlignSections -NumAdjacentSections 1 -Filters Blob -UseMasks -Downsample 64 -Channels TEM 
title AssembleStosOverlays
nornir-build %1 AssembleStosOverlays -StosGroup StosBrute -Downsample 64 -StosMap PotentialRegistrationChain
title SelectBestRegistrationChain
nornir-build %1 SelectBestRegistrationChain -StosGroup StosBrute -Downsample 64 -InputStosMap PotentialRegistrationChain -OutputStosMap FinalStosMap
title RefineSectionAlignment
nornir-build %1 RefineSectionAlignment -InputGroup StosBrute -InputDownsample 64 -OutputGroup Grid -OutputDownsample 32 -Filter Leveled -UseMasks
title AssembleStosOverlays
nornir-build %1 AssembleStosOverlays -StosGroup Grid -Downsample 32 -StosMap FinalStosMap
title CreateVikingXML
nornir-build %1 CreateVikingXML -StosGroup Grid32 -StosMap FinalStosMap -OutputFile Grid32
title RefineSectionAlignment
nornir-build %1 RefineSectionAlignment -InputGroup Grid -InputDownsample 32 -OutputGroup Grid -OutputDownsample 16 -Filter Leveled -UseMasks
title SliceToVolume
nornir-build %1 SliceToVolume -Downsample 16 -InputGroup Grid -OutputGroup SliceToVolume
title ScaleVolumeTransforms
nornir-build %1 ScaleVolumeTransforms -InputGroup SliceToVolume -InputDownsample 16 -OutputDownsample 1
title CreateVikingXML
nornir-build %1 CreateVikingXML -OutputFile SliceToVolume -StosGroup SliceToVolume1 -StosMap SliceToVolume
title MosaicToVolume
nornir-build %1 MosaicToVolume -InputTransform Grid -OutputTransform ChannelToVolume -Channels (?!Registered)
title Assemble
nornir-build %1 Assemble -Channels (?!Registered) -Filters Leveled -Downsample 32 -NoInterlace -Transform ChannelToVolume -ChannelPrefix Registered_
title MosaicReport
nornir-build %1 MosaicReport -PruneFilter Raw8 -ContrastFilter Raw8 -AssembleFilter Leveled -AssembleDownsample 32 -Output VolumeReport
title ExportImages
nornir-build %1 ExportImages -Channels Registered -Filters Leveled -Downsample 32 -Output %1\Registered