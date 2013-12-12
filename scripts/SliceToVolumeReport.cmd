@nornir-build -volume %1 -pipeline CreateBlobFilter -BlobFilters Leveled -BlobLevels 16,32

@nornir-build -volume %1 -pipeline AlignSections -NumAdjacentSections 1 -AlignFilters Blob_Leveled -StosUseMasks True -AlignDownsample 32

@nornir-build -volume %1 -pipeline RefineSectionAlignment -InputGroup StosBrute -InputDownsample 32 -OutputGroup Grid -OutputDownsample 32 -Filter Leveled -StosUseMasks True

nornir-build -volume %1 -pipeline SliceToVolume -InputDownsample 32 -InputGroup Grid -OutputGroup SliceToVolume

nornir-build -volume %1 -pipeline VolumeImage -GroupDownsample 32 -InputGroup SliceToVolume

nornir-build -volume %1 -pipeline StosReport -StosGroup SliceToVolume32