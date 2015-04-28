title Import
nornir-build %1 ImportPMG %2
title ShadeCorrect 
nornir-build %1 ShadeCorrect -Channels "(?![D|d]api)" -Filters Raw8 -OutputFilter ShadingCorrected -Correction brightfield
title ShadeCorrect
#nornir-build %1 ShadeCorrect -Channels "([D|d]api)" -Filters Raw8 -OutputFilter ShadingCorrected -Correction darkfield
title Prune
nornir-build %1 Prune -InputFilter ShadingCorrected -Downsample 2 -Threshold 1.0
title Histogram
nornir-build %1 Histogram -Filters ShadingCorrected -InputTransform Prune -Downsample 2
title AdjustContrast 
nornir-build %1 AdjustContrast -InputFilter ShadingCorrected -OutputFilter LeveledShadingCorrected -InputTransform Prune -Gamma 1
title Mosaic
nornir-build %1 Mosaic -InputFilter LeveledShadingCorrected -RegistrationDownsample 2 -InputTransform Prune -OutputTransform Grid
title Assemble
nornir-build %1 Assemble -Filters ShadingCorrected -Downsample 1 -NoInterlace -Transform Grid
title ExportImages
nornir-build %1 ExportImages -Filters ShadingCorrected -Downsample 1 -Output %1_Mosaic
title MosaicReport
nornir-build %1 MosaicReport -PruneFilter ShadingCorrected -ContrastFilter Raw8 -AssembleFilter ShadingCorrected -AssembleDownsample 1 -Output ImageReport