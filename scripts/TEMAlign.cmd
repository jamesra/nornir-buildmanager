nornir-build CreateBlobFilter -volume %1  -Channels TEM -InputFilter Leveled -Levels 16,32 -OutputFilter Blob -Radius 9 -Median 7 -Max 3
nornir-build AlignSections -volume %1 -NumAdjacentSections 1 -Filters Blob -StosUseMasks True -Downsample 32 -Channels TEM
nornir-build AssembleStosOverlays -volume %1 -StosGroup StosBrute -Downsample 32 -StosMap PotentialRegistrationChain
nornir-build SelectBestRegistrationChain -volume %1 -Group StosBrute -Downsample 32 -InputStosMap PotentialRegistrationChain -OutputStosMap FinalStosMap
nornir-build RefineSectionAlignment -volume %1 -InputGroup StosBrute -InputDownsample 32 -OutputGroup Grid -OutputDownsample 32 -Filter Leveled -StosUseMasks True
nornir-build AssembleStosOverlays -volume %1 -StosGroup Grid -Downsample 32 -StosMap FinalStosMap
nornir-build CreateVikingXML -volume %1 -StosGroup Grid32 -StosMap FinalStosMap -OutputFile Grid32
nornir-build RefineSectionAlignment -volume %1 -InputGroup Grid -InputDownsample 32 -OutputGroup Grid -OutputDownsample 16 -Filter Leveled -StosUseMasks True
nornir-build ScaleVolumeTransforms -volume %1 -InputGroup Grid -InputDownsample 16 -OutputDownsample 1
nornir-build SliceToVolume -volume %1 -InputDownsample 1 -InputGroup Grid -OutputGroup SliceToVolume
nornir-build CreateVikingXML -volume %1 -OutputFile SliceToVolume -StosGroup SliceToVolume1 -StosMap SliceToVolume
nornir-build MosaicToVolume -volume %1 -InputTransform Grid -OutputTransform ChannelToVolume -Channels TEM
nornir-build Assemble -volume %1 -Channels (?!Registered) -Filters Leveled -Downsample 32 -NoInterlace -Transform ChannelToVolume -ChannelPrefix Registered_
nornir-build MosaicReport -volume %1 -PruneFilter Raw8 -ContrastFilter Raw8 -AssembleFilter Leveled -AssembleDownsample 32
nornir-build ExportImages -volume %1 -Channels Registered -Filters Leveled -Downsample 32 -Output %1\Registered