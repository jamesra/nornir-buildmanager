nornir-build -input %2 -volume %1 
nornir-build -volume %1 -pipeline ShadeCorrect -InputFilter Raw8 -OutputFilter ShadingCorrected
nornir-build -volume %1 -pipeline Prune -InputFilter ShadingCorrected -Downsample 1
nornir-build -volume %1 -pipeline Histogram -InputFilter ShadingCorrected -InputTransform Prune -Downsample 1 
nornir-build -volume %1 -pipeline AdjustContrast -InputFilter ShadingCorrected -OutputFilter LeveledShadingCorrected -InputTransform Prune Leveled -Gamma 1
nornir-build -volume %1 -pipeline Mosaic -InputFilter LeveledShadingCorrected -RegistrationDownsample 2 -InputTransform Prune -OutputTransform Grid
nornir-build -volume %1 -pipeline Assemble -Filter ShadingCorrected -AssembleDownsample 1 -Output %1_Output -NoInterlace -Transform Grid
nornir-build -volume %1 -pipeline MosaicReport -PruneFilter Raw8 -ContrastFilter LeveledShadingCorrected -AssembleFilter ShadingCorrected -AssembleDownsample 1