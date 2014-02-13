nornir-build -volume %1 -pipeline CreateBlobFilter -Channels TEM -InputFilter Leveled -Levels 16,32 -OutputFilter Blob
nornir-build -volume %1 -pipeline AlignSections -NumAdjacentSections 1 -Filters Blob -StosUseMasks True -Downsample 32 -Channels TEM
nornir-build -volume %1 -pipeline RefineSectionAlignment -InputGroup StosBrute -InputDownsample 32 -OutputGroup Grid -OutputDownsample 32 -Filter Leveled -StosUseMasks True
nornir-build -volume %1 -pipeline RefineSectionAlignment -InputGroup Grid -InputDownsample 32 -OutputGroup Grid -OutputDownsample 16 -Filter Leveled -StosUseMasks True
nornir-build -volume %1 -pipeline ScaleVolumeTransforms -InputGroup Grid -InputDownsample 16 -OutputDownsample 1
nornir-build -volume %1 -pipeline SliceToVolume -InputDownsample 1 -InputGroup Grid -OutputGroup SliceToVolume
nornir-build -volume %1 -pipeline MosaicToVolume -InputTransform Grid -OutputTransform ChannelToVolume -Channels TEM
nornir-build -volume %1 -pipeline Assemble -Channels TEM -Filters Leveled -Downsample 16,32 -NoInterlace -Transform ChannelToVolume -ChannelPrefix Registered_ -Output Registered  