nornir-build CreateBlobFilter -volume %1  -Channels TEM -InputFilter Leveled -Levels 16,32 -OutputFilter Blob
nornir-build AlignSections -volume %1 -NumAdjacentSections 1 -Filters Blob -StosUseMasks True -Downsample 32 -Channels TEM
nornir-build RefineSectionAlignment -volume %1 -InputGroup StosBrute -InputDownsample 32 -OutputGroup Grid -OutputDownsample 32 -Filter Leveled -StosUseMasks True
nornir-build RefineSectionAlignment -volume %1 -InputGroup Grid -InputDownsample 32 -OutputGroup Grid -OutputDownsample 16 -Filter Leveled -StosUseMasks True
nornir-build CreateVikingXML -volume %1 -OutputFile Grid16
nornir-build ScaleVolumeTransforms -volume %1 -InputGroup Grid -InputDownsample 16 -OutputDownsample 1
nornir-build SliceToVolume -volume %1 -InputDownsample 1 -InputGroup Grid -OutputGroup SliceToVolume
nornir-build CreateVikingXML -volume %1 -OutputFile SliceToVolume
nornir-build MosaicToVolume -volume %1 -InputTransform Grid -OutputTransform ChannelToVolume -Channels TEM
nornir-build Assemble -volume %1 -Channels (?!Registered) -Filters Leveled -Downsample 32 -NoInterlace -Transform ChannelToVolume -ChannelPrefix Registered_
nornir-build MosaicReport -volume %1 -PruneFilter Raw8 -ContrastFilter Leveled -AssembleFilter Leveled -AssembleDownsample 32
nornir-build ExportImages -volume %1 -Channels Registered -Filters Leveled -Downsample 32 -Output %1\Registered