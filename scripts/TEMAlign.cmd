nornir-build -volume %1 -pipeline CreateBlobFilter -InputFilters Leveled -Levels 16,32

nornir-build -volume %1 -pipeline AlignSections -NumAdjacentSections 1 -AlignFilters Blob_Leveled -StosUseMasks True -AlignDownsample 32

nornir-build -volume %1 -pipeline RefineSectionAlignment -InputGroup StosBrute -InputDownsample 32 -OutputGroup Grid -OutputDownsample 32 -Filter Leveled -StosUseMasks True

nornir-build -volume %1 -pipeline RefineSectionAlignment -InputGroup Grid -InputDownsample 32 -OutputGroup Grid -OutputDownsample 16 -Filter Leveled -StosUseMasks True

nornir-build -volume %1 -pipeline ScaleVolumeTransforms -ScaleGroupName Grid -ScaleInputDownsample 16 -ScaleOutputDownsample 1

nornir-build -volume %1 -pipeline SliceToVolume -InputDownsample 1 -InputGroup Grid -OutputGroup SliceToVolume