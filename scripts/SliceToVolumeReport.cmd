@nornir-build %1 CreateBlobFilter -BlobFilters Leveled -BlobLevels 16,32

@nornir-build %1 AlignSections -NumAdjacentSections 1 -AlignFilters Blob_Leveled -StosUseMasks True -AlignDownsample 32

@nornir-build %1 RefineSectionAlignment -InputGroup StosBrute -InputDownsample 32 -OutputGroup Grid -OutputDownsample 32 -Filter Leveled -StosUseMasks True

nornir-build %1 SliceToVolume -InputDownsample 32 -InputGroup Grid -OutputGroup SliceToVolume

nornir-build %1 VolumeImage -GroupDownsample 32 -InputGroup SliceToVolume

nornir-build %1 StosReport -StosGroup SliceToVolume32