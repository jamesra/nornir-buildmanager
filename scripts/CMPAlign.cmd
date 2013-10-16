nornir-build -volume %1 -pipeline AlignSections -AlignDownsample 8 -AlignFilters Leveled
nornir-build -volume %1 -pipeline RefineSectionAlignment -Filter Leveled -InputGroup StosBrute -AlignFilters ShadingCorrected -InputDownsample 8 -OutputGroup Grid -OutputDownsample 8
nornir-build -volume %1 -pipeline RefineSectionAlignment -Filter Leveled -InputGroup Grid -AlignFilters ShadingCorrected -InputDownsample 8 -OutputGroup Grid -OutputDownsample 4
nornir-build -volume %1 -pipeline ScaleVolumeTransforms -ScaleGroupName Grid -ScaleInputDownsample 4 -ScaleOutputDownsample 1
nornir-build -volume %1 -pipeline SliceToVolume -InputDownsample 1 -InputGroup Grid -OutputGroup SliceToVolume
nornir-build -volume %1 -pipeline VolumeImage -VolumeImageGroupName SliceToVolume -VolumeImageDownsample 1
