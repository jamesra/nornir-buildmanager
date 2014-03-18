nornir-build -volume %1 -pipeline CreateBlobFilter -Channels TEM -InputFilter Leveled -Levels 16,32 -OutputFilter Blob
nornir-build -volume %1 -pipeline AlignSections -NumAdjacentSections 1 -Filters Blob -StosUseMasks True -Downsample 32 -Channels TEM
nornir-build -volume %1 -pipeline RefineSectionAlignment -InputGroup StosBrute -InputDownsample 32 -OutputGroup Grid -OutputDownsample 32 -Filter Leveled -StosUseMasks True
nornir-build -volume %1 -pipeline RefineSectionAlignment -InputGroup Grid -InputDownsample 32 -OutputGroup Grid -OutputDownsample 16 -Filter Leveled -StosUseMasks True
nornir-build -volume %1 -pipeline CreateVikingXML -OutputFile Grid16
nornir-build -volume %1 -pipeline ScaleVolumeTransforms -InputGroup Grid -InputDownsample 16 -OutputDownsample 1
nornir-build -volume %1 -pipeline SliceToVolume -InputDownsample 1 -InputGroup Grid -OutputGroup SliceToVolume
nornir-build -volume %1 -pipeline CreateVikingXML -OutputFile SliceToVolume
nornir-build -volume %1 -pipeline MosaicToVolume -InputTransform Grid -OutputTransform ChannelToVolume -Channels TEM
nornir-build -volume %1 -pipeline Assemble -Channels (?!Registered) -Filters Leveled -Downsample 32 -NoInterlace -Transform ChannelToVolume -ChannelPrefix Registered_
nornir-build -volume %1 -pipeline MosaicReport -PruneFilter Raw8 -ContrastFilter Leveled -AssembleFilter Leveled -AssembleDownsample 32
nornir-build -volume %1 -pipeline ExportImages -Channels Registered -Filters Leveled -Downsample 32 -Output %1\Registered