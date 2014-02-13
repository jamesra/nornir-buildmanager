nornir-build -volume %1 -input %2 
nornir-build -volume %1 -pipeline ShadeCorrect -Channels "(?![D|d]api)" -Filters Raw8 -OutputFilter ShadingCorrected -Correction brightfield
nornir-build -volume %1 -pipeline ShadeCorrect -Channels "([D|d]api)" -Filters Raw8 -OutputFilter ShadingCorrected -Correction darkfield
nornir-build -volume %1 -pipeline Prune -InputFilter ShadingCorrected -Downsample 2 -Threshold 1.0
nornir-build -volume %1 -pipeline Histogram -InputFilter ShadingCorrected -InputTransform Prune -Downsample 2 
nornir-build -volume %1 -pipeline AdjustContrast -InputFilter ShadingCorrected -OutputFilter LeveledShadingCorrected -InputTransform Prune Leveled -Gamma 1
nornir-build -volume %1 -pipeline Mosaic -InputFilter LeveledShadingCorrected -RegistrationDownsample 2 -InputTransform Prune -OutputTransform Grid
nornir-build -volume %1 -pipeline Assemble -Filter ShadingCorrected -Downsample 1 -NoInterlace -Transform Grid
nornir-build -volume %1 -pipeline ExportImages -Filters ShadingCorrected -Downsample 1 -Output %1_Mosaic
nornir-build -volume %1 -pipeline MosaicReport -PruneFilter Raw8 -ContrastFilter LeveledShadingCorrected -AssembleFilter ShadingCorrected -AssembleDownsample 1