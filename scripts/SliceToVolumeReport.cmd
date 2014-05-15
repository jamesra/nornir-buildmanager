@nornir-build CreateBlobFilter -volume %1 -BlobFilters Leveled -BlobLevels 16,32

@nornir-build AlignSections -volume %1 -NumAdjacentSections 1 -AlignFilters Blob_Leveled -StosUseMasks True -AlignDownsample 32

@nornir-build RefineSectionAlignment -volume %1 -InputGroup StosBrute -InputDownsample 32 -OutputGroup Grid -OutputDownsample 32 -Filter Leveled -StosUseMasks True

nornir-build SliceToVolume -volume %1 -InputDownsample 32 -InputGroup Grid -OutputGroup SliceToVolume

nornir-build VolumeImage -volume %1 -GroupDownsample 32 -InputGroup SliceToVolume

nornir-build StosReport -volume %1 -StosGroup SliceToVolume32