nornir-build -volume %1 -pipeline CreateBlobFilter -Channels TEM -InputFilters Leveled -Levels 16,32 -OutputFilter Blob
nornir-build -volume %1 -pipeline AlignSections -NumAdjacentSections 1 -AlignFilters Blob -StosUseMasks True -Downsample 32
nornir-build -volume %1 -pipeline RefineSectionAlignment -InputGroup StosBrute -InputDownsample 32 -OutputGroup Grid -OutputDownsample 32 -Filter Leveled -StosUseMasks True
nornir-build -volume %1 -pipeline RefineSectionAlignment -InputGroup Grid -InputDownsample 32 -OutputGroup Grid -OutputDownsample 16 -Filter Leveled -StosUseMasks True
nornir-build -volume %1 -pipeline ScaleVolumeTransforms -InputGroup Grid -InputDownsample 16 -OutputDownsample 1
nornir-build -volume %1 -pipeline SliceToVolume -InputDownsample 1 -InputGroup Grid -OutputGroup SliceToVolume