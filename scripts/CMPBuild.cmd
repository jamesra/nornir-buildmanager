nornir-build import %2 -volume %1 
nornir-build ShadeCorrect -volume %1 -Channels "(?![D|d]api)" -Filters Raw8 -OutputFilter ShadingCorrected -Correction brightfield
#nornir-build ShadeCorrect -volume %1  -Channels "([D|d]api)" -Filters Raw8 -OutputFilter ShadingCorrected -Correction darkfield
nornir-build Prune -volume %1   -InputFilter ShadingCorrected -Downsample 2 -Threshold 1.0
nornir-build Histogram -volume %1  -Filters ShadingCorrected -InputTransform Prune -Downsample 2 
nornir-build AdjustContrast -volume %1  -InputFilter ShadingCorrected -OutputFilter LeveledShadingCorrected -InputTransform Prune -Gamma 1
nornir-build Mosaic -volume %1  -InputFilter LeveledShadingCorrected -RegistrationDownsample 2 -InputTransform Prune -OutputTransform Grid
nornir-build Assemble -volume %1  -Filters ShadingCorrected -Downsample 1 -NoInterlace -Transform Grid
nornir-build ExportImages -volume %1  -Filters ShadingCorrected -Downsample 1 -Output %1_Mosaic
nornir-build MosaicReport -volume %1  -PruneFilter ShadingCorrected -ContrastFilter Raw8 -AssembleFilter ShadingCorrected -AssembleDownsample 1