title Import
nornir-build %1 ImportPMG %2 -Scale %3
title ShadeCorrect Brightfield
nornir-build %1 ShadeCorrect -Channels "(?!(DAPI$)|(TEM$)|(SEM$))" -Filters Raw8 -OutputFilter ShadingCorrected -Correction brightfield
title ShadeCorrect Darkfield
#nornir-build %1 ShadeCorrect -Channels "([D|d]api)" -Filters Raw8 -OutputFilter ShadingCorrected -Correction darkfield
title Prune
nornir-build %1 Prune -Channels "(?!(DAPI$)|(TEM$)|(SEM$))" -InputFilter ShadingCorrected -Downsample 2 -Threshold 1.0
title Histogram
nornir-build %1 Histogram -Channels "(?!(DAPI$)|(TEM$)|(SEM$))" -Filters ShadingCorrected -InputTransform Prune -Downsample 2
title AdjustContrast 
nornir-build %1 AdjustContrast -Channels "(?!(DAPI$)|(TEM$)|(SEM$))" -InputFilter ShadingCorrected -OutputFilter LeveledShadingCorrected -InputTransform Prune -Gamma 1
title Inverting Output
nornir-build %1 InvertFilter -Channels "(?!(DAPI$)|(TEM$)|(SEM$))" -InputFilter LeveledShadingCorrected -OutputFilter InvertedLeveledShadingCorrected
title Mosaic
nornir-build %1 Mosaic -Channels "(?!(DAPI$)|(TEM$)|(SEM$))" -InputFilter LeveledShadingCorrected -RegistrationDownsample 2 -InputTransform Prune -OutputTransform Grid
title Assemble
nornir-build %1 Assemble -Channels "(?!(DAPI$)|(TEM$)|(SEM$))" -Filters ShadingCorrected -Downsample 1 -NoInterlace -Transform Grid
nornir-build %1 Assemble -Channels "(?!(DAPI$)|(TEM$)|(SEM$))" -Filters InvertedLeveledShadingCorrected -Downsample 1 -NoInterlace -Transform Grid
title MosaicReport
nornir-build %1 MosaicReport -PruneFilter ShadingCorrected -ContrastFilter Raw8 -AssembleFilter ShadingCorrected -AssembleDownsample 1 -Output CMPImageReport